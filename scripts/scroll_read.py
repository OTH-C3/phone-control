#!/usr/bin/env python3
"""多屏聚合读取：把"滑一屏、读一屏"拼成一份完整长内容。

probe.py 只能读【当前一屏】。读聊天记录、长列表、长文章这类跨屏内容，
需要滚动 + 把相邻屏的重叠部分接起来 + 滑到没有新内容为止。
这个脚本只干这一件事，不点按钮、不填表单。

用法:
  scroll_read.py                        默认向下读 5 屏，结果打到 stdout
  scroll_read.py --dir up               反方向（读更早/更上面的内容）
  scroll_read.py --max-screens 20       最多读 20 屏
  scroll_read.py --stop-text "已经是底部"  读到某文本出现就停
  scroll_read.py --out chat.txt         同时存一份到文件
  scroll_read.py --device SERIAL        指定设备

方向说明（记不住就记手指动作）:
  --dir down   手指【向上】滑，看后面 / 更下面 / 更新的内容（默认）
  --dir up     手指【向下】滑，看前面 / 更上面 / 更早的内容

读聊天记录的典型用法：打开聊天窗口，默认停在最新一条，此时要读历史 →
  scroll_read.py --dir up --max-screens 30 --out chat.txt

退出码: 0 = 正常结束；1 = 一屏都没读到（多半是焦点不在应用窗口）。
"""
import argparse
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _config  # noqa: E402
import probe as P  # noqa: E402


def screen_size(adb, device):
    """从 `wm size` 拿分辨率，用来算滑动起止点。"""
    out = P.run(adb, "shell", "wm", "size", device=device)
    for line in out.splitlines():
        if "size:" in line:
            wh = line.split("size:", 1)[1].strip().split("x")
            if len(wh) == 2:
                try:
                    return int(wh[0]), int(wh[1].split()[0])
                except ValueError:
                    pass
    return 1080, 2400


def swipe(adb, device, w, h, direction):
    """滑动一屏。

    起止点都避开顶部/底部 25% —— 那两带会被状态栏、吸顶栏、吸底按钮吞掉，
    而且贴边滑动容易误触发"下拉刷新"或"右滑返回"。
    """
    x = w // 2
    if direction == "up":       # 手指向下滑 → 看更早/更上面的内容
        y1, y2 = int(h * 0.28), int(h * 0.72)
    else:                       # down: 手指向上滑 → 看更下面/更新的内容
        y1, y2 = int(h * 0.72), int(h * 0.28)
    subprocess.run([adb] + (["-s", device] if device else []) +
                   ["shell", "input", "swipe",
                    str(x), str(y1), str(x), str(y2), "300"],
                   capture_output=True, timeout=30)


def visible_lines(adb, device, h):
    """当前屏的文本行，按可见位置从上到下排序。

    为什么不能直接照 dump 顺序用，实测踩过：
    列表滚动后，dump 会混进**回收复用的残留元素**——它们的 bounds 是错的，
    例如 `[210,380][746,4]`（y1=4 居然比 y0=380 小），位置整体落在屏幕外。
    这些行不是当前可见内容，而且会打乱顺序、让相邻屏拼不上。

    处理办法：只保留 bounds **合法且落在屏幕内**的行，按 y0 从上到下排序。
    bounds 非法的（y1<=y0 或超出屏幕）一律丢弃——实测过，它们就是上一层
    列表滚走之后的残影：屏幕明明滚到了 Storage/Battery，dump 里却还挂着
    上一屏的 Dark theme/Wallpaper 且 bounds 颠倒。留着它们会把已经读过的
    内容再混进来一次。
    """
    raw = P.fetch_ui(adb, device, P.dev_dump_path())
    if not raw:
        return []
    good = []
    for t, b in P.parse_ui(raw):
        y0, y1 = b_y(b)
        if y1 > y0 and 0 <= y0 and y1 <= h:
            good.append((y0, t))
    good.sort()
    return [t for _, t in good]


def b_y(bounds):
    """从 `[x0,y0][x1,y1]` 取 y0,y1。"""
    m = re.match(r"\[\d+,(\d+)\]\[\d+,(\d+)\]", bounds or "")
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def fp(lines):
    return (len(lines), hash("|".join(lines)))


def common_pinned(screens):
    """找出所有屏共有的【顶部前缀】和【底部后缀】，返回 (top行数, bottom行数)。

    吸顶搜索栏、底部标签栏这类元素，位置不动、内容不变，在每一屏都存在。
    做法：拿所有屏的公共前缀（逐行比对，任何一屏不同就停）当吸顶；
    公共后缀当吸底。

    为什么用"所有屏的公共部分"而不是"相邻两屏"：只有第一屏没有上一屏可比，
    用它跟第二屏比会把吸顶算进内容里；而"所有屏都相同"这个更强的条件，
    天然把吸顶/吸底捞出来，又不会误伤真实内容（真实内容不可能每屏都出现在
    同一个位置且完全相同——除非整个页面压根没滚动，那种情况下也读不出新内容）。
    """
    if len(screens) < 2:
        return 0, 0
    shortest = min(len(s) for s in screens)
    top = 0
    while top < shortest and all(s[top] == screens[0][top] for s in screens):
        top += 1
    bottom = 0
    while (bottom < shortest - top and
           all(s[-1 - bottom] == screens[0][-1 - bottom] for s in screens)):
        bottom += 1
    return top, bottom


def stitch(acc, new, direction):
    """把新一屏接进已累积内容，去掉重叠的部分。

    方向决定往哪边接，接错方向顺序就全乱：
    - down（往下读、读更新的内容）：新内容在 acc 之后 → 比对 acc 尾部 == new 头部，接在后面。
    - up（往上读、读更早的内容）：新内容在 acc 之前 → 比对 acc 头部 == new 尾部，接在前面。

    从长到短试重叠（最多 40 行），避免"两条一模一样的消息"造成短重叠误判。
    返回 (新增行, 重叠行数)。
    """
    if not acc:
        return new, 0
    limit = min(len(acc), len(new), 40)
    if direction == "up":
        for k in range(limit, 0, -1):
            if acc[:k] == new[-k:]:
                return new[:-k], k
        return new, 0
    for k in range(limit, 0, -1):
        if acc[-k:] == new[:k]:
            return new[k:], k
    return new, 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", choices=["down", "up"], default="down",
                    help="down=手指上滑看更下面（默认）；up=手指下滑看更上面")
    ap.add_argument("--max-screens", type=int, default=5)
    ap.add_argument("--stop-text", help="读到包含该字符串的内容就停")
    ap.add_argument("--settle", type=float, default=1.5,
                    help="滑动后等界面稳定的最长秒数（默认 1.5）")
    ap.add_argument("--out", help="把结果另存到文件")
    ap.add_argument("--device")
    ap.add_argument("--adb")
    ap.add_argument("--quiet", action="store_true", help="不打印进度")
    args = ap.parse_args()

    def log(msg):
        if not args.quiet:
            print(msg, file=sys.stderr)

    adb = P.find_adb(args)
    device = args.device or _config.config_value("DEFAULT_DEVICE") or \
        P.probe_device(adb, None)
    w, h = screen_size(adb, device)

    def read_settled(prev_fp):
        """等到界面稳定，返回 (行列表, 是否发生了移动)。

        为什么要"稳定"而不是"一变就返回"：列表滚动有惯性动画，只在"看到变化"
        的那一刻读，拿到的多半是**滚动中间态**——元素位置错乱、部分元素滚出
        屏幕外，拼起来会多出重复段。所以必须连续两次快照完全一致才算数。
        """
        deadline = time.time() + args.settle
        last = None
        while True:
            cur = visible_lines(adb, device, h)
            cur_fp = fp(cur)
            if cur and last is not None and cur_fp == last:
                return cur, (prev_fp is None or cur_fp != prev_fp)
            last = cur_fp
            if time.time() >= deadline:
                return cur, (prev_fp is None or cur_fp != prev_fp)
            time.sleep(0.25)

    # 第一遍：把每屏都读下来（此时先不拼接，因为要先找出所有屏共有的吸顶/吸底）。
    screens = []
    prev_fp = None
    for i in range(1, args.max_screens + 1):
        try:
            new, moved = read_settled(prev_fp)
        except P.DumpFailed as e:
            log("[错误] %s" % e)
            break

        # 第 1 屏之后，若等稳定后仍与上一屏完全一致，说明已经到头。
        if i > 1 and not moved:
            log("[停] 滑动后界面没变化，已经到%s了。"
                % ("底" if args.dir == "down" else "顶"))
            break

        if not new:
            log("[提示] 第 %d 屏读不到内容。焦点可能不在应用窗口 —— "
                "先跑 probe.py --focus-only 确认。" % i)
            break

        screens.append(new)
        if args.stop_text and any(args.stop_text in t for t in new):
            log("[停] 命中 stop-text：%r（第 %d 屏）" % (args.stop_text, i))
            break
        if i >= args.max_screens:
            break

        prev_fp = fp(new)
        swipe(adb, device, w, h, args.dir)

    if not screens:
        print("[错误] 一屏都没读到。先确认手机亮屏、焦点在目标 App 上。")
        return 1

    # 第二遍：剥掉所有屏共有的吸顶/吸底，再按方向拼接。
    top, bottom = common_pinned(screens)
    if top or bottom:
        log("[剥] 识别到吸顶 %d 行 / 吸底 %d 行（每屏都在，不重复计入）" % (top, bottom))
    trimmed = [s[top:len(s) - bottom if bottom else len(s)] for s in screens]

    acc = []
    for i, new in enumerate(trimmed, 1):
        if not new:
            continue
        added, overlap = stitch(acc, new, args.dir)
        # up 方向读到的是更早的内容，要插在前面，不能往后面接
        if args.dir == "up":
            acc = added + acc
        else:
            acc.extend(added)
        log("[第 %d 屏] %d 行，新增 %d 行（重叠 %d），累计 %d 行"
            % (i, len(new), len(added), overlap, len(acc)))

    text = "\n".join(acc)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        log("[保存] %s（%d 行）" % (args.out, len(acc)))
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())