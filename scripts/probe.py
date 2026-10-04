#!/usr/bin/env python3
"""手机界面探测：设备 -> 焦点层 -> 界面树压缩输出。

用法:
  probe.py                      设备 + 焦点 + 界面元素（默认全输出）
  probe.py --focus-only         只输出设备与焦点层（几字节的快速确认）
  probe.py --device SERIAL      指定设备（多设备时）
  probe.py --limit N            最多打印 N 条元素（省 token）

输出格式（每行 = 屏幕上的一个元素）:
  文字 @ 中心x,中心y  [x0,y0][x1,y1]
"""
import argparse
import os
import random
import re
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _config import find_adb, load_config  # noqa: E402


class DumpFailed(RuntimeError):
    """dump 没拿到可用文件。调用方要把它翻译成人话，别抛 traceback。"""
# adb 的多来源探测（--adb / ADB_PATH / ANDROID_HOME / 常见路径 / PATH）
# 集中放在 _config.py，本脚本不再重复实现一份。


def run(adb, *cmd, device=None):
    base = [adb]
    if device:
        base += ["-s", device]
    base += list(cmd)
    return subprocess.run(base, capture_output=True, text=True,
                          timeout=60).stdout


def probe_device(adb, device):
    out = run(adb, "devices", "-l", device=device).strip().splitlines()[1:]
    if not out:
        sys.exit("设备未连接：检查 USB / 无线调试")
    line = out[0]
    return line.split("\t")[0] if "\t" in line else line.split()[0]


def _component(value):
    """ActivityRecord{... u0 pkg/Act t123} / Window{... u0 pkg/Act} -> pkg/Act"""
    parts = value.split()
    return parts[2] if len(parts) >= 3 else value


def probe_focus(adb, device):
    """焦点层 + 栈顶 Activity。dump 读的是焦点层，所以这步必须先做。

    注意：dumpsys window 就够（1336 行的 `dumpsys activity activities` 是另一回事，别用）。
    坑：`topResumedActivity=` 这行也含 "ResumedActivity" 且没有冒号，直接 split(":") 会越界。
    """
    win = run(adb, "shell", "dumpsys", "window", device=device)
    focus = resumed = None
    for l in win.splitlines():
        if "mCurrentFocus=" in l:
            v = l.split("=", 1)[1].strip()
            if v and v != "null":
                focus = v
        if "mFocusedApp=" in l:
            v = l.split("=", 1)[1].strip()
            if "ActivityRecord{" in v:
                resumed = _component(v)
    if not resumed:  # 兜底：换成 ResumedActivity: 那行
        for l in win.splitlines():
            s = l.strip()
            if s.startswith("ResumedActivity:"):
                resumed = _component(s.split(":", 1)[1].strip())
                break
    return focus, resumed


def center(bounds):
    m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds or "")
    if not m:
        return None
    x0, y0, x1, y1 = map(int, m.groups())
    return (x0 + x1) // 2, (y0 + y1) // 2


def dev_dump_path():
    """唯一文件名：并发 adb 时避免互相覆盖。

    目录取自 config.env 的 DUMP_DIR（默认 /data/local/tmp）。
    文件名必须唯一——两个任务 dump 到同名文件会互相覆盖，
    拉回来的是对方那一刻的界面，这种错误很难发现。
    """
    d = load_config().get("DUMP_DIR") or "/data/local/tmp"
    return "%s/probe_%d_%d.xml" % (d.rstrip("/"), int(time.time()),
                                   random.randint(100, 999))


def _dev_flag(device):
    """多设备时插入 -s SERIAL。抽出来是因为下面每条 adb 命令都要用。"""
    return ["-s", device] if device else []


# yadb（可选增强）在设备上的可用性缓存：{"ok": bool}，空 = 还没查过。
# 每次 dump 都多跑一条 `ls` 不值当 —— fetch_ui 在滚动读取里是每屏调一次。
_YADB_STATE = {}


def yadb_jar_path():
    """yadb jar 在手机上的路径。可用 config.env 的 YADB_JAR 覆盖。"""
    return load_config().get("YADB_JAR") or "/data/local/tmp/yadb"


def yadb_available(adb, device, refresh=False):
    """jar 是否已在设备上。结果缓存（refresh=True 强制重查）。

    没有它就返回 False，fetch_ui 自动走 uiautomator 兜底 —— yadb 是可选增强
    不是必需依赖（删掉 bin/yadb 只影响读屏速度，不影响还能不能用）。
    """
    if not refresh and _YADB_STATE:
        return _YADB_STATE["ok"]
    jar = yadb_jar_path()
    try:
        r = subprocess.run([adb] + _dev_flag(device) + ["shell", "ls", jar],
                           capture_output=True, text=True, timeout=30)
        ok = r.returncode == 0 and jar in (r.stdout or "")
    except (subprocess.SubprocessError, OSError):
        ok = False
    _YADB_STATE["ok"] = ok
    return ok


def _try_yadb_layout(adb, device, dev_path):
    """主通道：yadb -layout。成功返回界面树字节，失败返回 None（交回调用方兜底）。

    为什么主通道用它：实测 0.47~0.81s，比 uiautomator dump 的 2.34~2.45s 快 3~5 倍。
    两条通道产出的 XML 结构等价（同一界面 105 节点逐一对齐，103 个可比节点零属性差异），
    yadb 只少 drawing-order 和 hint 两个属性，本脚本都没用到（grep 确认零命中）。

    两条必须遵守的判定纪律（都是实测踩出来的）：
      1. **按文本判定，不看退出码** —— jar 缺失时输出是 `Aborted`，
         而 returncode 可能是 0 也可能是 134，取决于有没有被管道截断。
      2. **调用前必须先 rm 目标文件** —— yadb 失败时不会删掉上一次的输出，
         不先删就会把上一屏当成这一屏（见 fetch_ui）。
    """
    if not yadb_available(adb, device):
        return None
    jar = yadb_jar_path()
    try:
        r = subprocess.run(
            [adb] + _dev_flag(device) +
            ["shell", "app_process", "-Djava.class.path=" + jar,
             "/system/bin", "com.ysbing.yadb.Main", "-layout", dev_path],
            capture_output=True, text=True, timeout=30)
    except (subprocess.SubprocessError, OSError):
        return None
    if "layout dumped to:" not in ((r.stdout or "") + (r.stderr or "")):
        return None
    # exec-out cat 是流式直传，比 adb pull 快且不用先 push 到 /sdcard。
    # 用 pull 会退化到 2 秒以上，等于白换通道。
    # 这里必须拿 bytes：界面树是 UTF-8，走 text=True 会被本地编码（Windows 上
    # 可能是 GBK）搞坏，中文变乱码。
    try:
        got = subprocess.run([adb] + _dev_flag(device) +
                             ["exec-out", "cat", dev_path],
                             capture_output=True, timeout=30)
    except (subprocess.SubprocessError, OSError):
        return None
    return got.stdout or None


def _try_uiautomator(adb, device, dev_path):
    """兜底通道：uiautomator dump。

    这里是从原 fetch_ui 原样搬过来的，**逻辑一个字都不要改** ——
    yadb 不可用时使用者（agent）必须察觉不到，行为与改造前完全一致。
    """
    r = subprocess.run([adb] + _dev_flag(device) +
                       ["shell", "uiautomator", "dump", dev_path],
                       capture_output=True, text=True, timeout=90)

    def cleanup_dev_file():
        """删掉手机上那个 dump 落盘文件，只清自己这一个。

        必须走 adb，不能用 os.remove —— dev_path 是【手机上】的路径，
        本地文件系统里没这份文件，os.remove 只会抛 OSError 被吞掉，
        于是文件全留在设备上越攒越多（实测攒到 100+ 个）。
        """
        subprocess.run([adb] + _dev_flag(device) +
                       ["shell", "rm", "-f", dev_path],
                       capture_output=True, timeout=30)

    if "dumped to" not in (r.stdout + r.stderr):
        # 失败路径也要清：dump 可能已经写了一部分文件在那儿，
        # 不清就会在手机上越攒越多，而且下次并发时容易被别人拉走。
        cleanup_dev_file()
        return None
    local = os.path.join(tempfile.mkdtemp(prefix="probe_"), "ui.xml")
    subprocess.run([adb] + _dev_flag(device) +
                   ["pull", dev_path, local], capture_output=True, timeout=60)
    cleanup_dev_file()
    # dump 打印了 "dumped to" 不代表文件真的落到那个目录了
    # （DUMP_DIR 配错、目录不可写时命令照样回这句）。所以必须再确认一次拉回来的文件。
    if not os.path.exists(local) or os.path.getsize(local) == 0:
        raise DumpFailed(
            "uiautomator dump 说成功了，但 %s 里没拉到文件。\n"
            "        多半是 config.env 的 DUMP_DIR=%s 不对或不可写 —— 改成 /sdcard 再试。"
            % (dev_path, load_config().get("DUMP_DIR") or "/data/local/tmp"))
    with open(local, "rb") as f:
        return f.read()


def fetch_ui(adb, device, dev_path):
    """读界面树：优先 yadb -layout（快 3~5 倍），失败自动回退 uiautomator dump。

    两条通道产出等价，所以对调用方透明 —— 返回的字节一样，
    后面的解析、合法性过滤、坐标计算全不用管走的是哪条。

    **先删目标文件再 dump**：yadb 失败时不会覆盖上一次的输出文件，
    不先删就会拿到上一屏的内容（实测确认），这种错极难察觉 ——
    界面"看起来读到了"，实际是上一屏。
    """
    subprocess.run([adb] + _dev_flag(device) + ["shell", "rm", "-f", dev_path],
                   capture_output=True, timeout=30)
    data = _try_yadb_layout(adb, device, dev_path)
    # 两条路径都要清理：yadb 成功时它自己不删文件，上面那次 rm 只清了旧的
    subprocess.run([adb] + _dev_flag(device) + ["shell", "rm", "-f", dev_path],
                   capture_output=True, timeout=30)
    if data:
        return data
    return _try_uiautomator(adb, device, dev_path)


def _screen_h(root):
    """从界面树根节点取屏幕高度。根上的第一个 node 的 bounds 就是整屏。"""
    cand = root.get("bounds")
    if not cand:
        n = root.find("node")
        cand = (n.get("bounds") or "") if n is not None else ""
    m = re.match(r"\[\d+,\d+\]\[\d+,(\d+)\]", cand or "")
    return int(m.group(1)) if m else None


def bounds_ok(bounds, screen_h):
    """bounds 是否合法且落在屏幕内（`[x0,y0][x1,y1]`，y1>y0、x1>x0、不超出屏幕）。

    为什么必须过滤：列表滚动后，dump 会混进**回收复用的残留元素**，它们的
    bounds 是反的——实测见过 `[210,2360][378,2274]`（y1 比 y0 还小）和
    `[210,380][746,4]`。这些行不是当前可见内容，它们的"中心点"落在上一屏，
    **照它去 tap 会点到完全不相干的条目**（实测按这种坐标点击，跳进了另一个
    App）。合法性检查是这类错误的第一道闸。
    """
    m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds or "")
    if not m:
        return False
    x0, y0, x1, y1 = map(int, m.groups())
    if x1 <= x0 or y1 <= y0:
        return False
    if screen_h is not None and y1 > screen_h:
        return False
    return True


def parse_ui_full(raw):
    """提取 (文字, bounds)，返回 (合法项列表, 被丢弃的非法项数)。

    用 ET 解析，属性顺序无关。非法 bounds（残留元素）一律丢弃并计数，
    让调用方能提示"这页有残留，坐标别全信"。
    """
    items = []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return items, 0
    screen_h = _screen_h(root)
    dropped = 0
    for node in root.iter("node"):
        text = (node.get("text") or "").strip()
        if not text:
            text = (node.get("content-desc") or "").strip()
        bounds = node.get("bounds") or ""
        if not (text and bounds):
            continue
        if bounds_ok(bounds, screen_h):
            items.append((text, bounds))
        else:
            dropped += 1
    return items, dropped


def parse_ui(raw):
    """只要 (文字, bounds) 列表（合法项）。"""
    return parse_ui_full(raw)[0]


def fingerprint(items):
    return (len(items), hash("|".join(t for t, _ in items)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--focus-only", action="store_true")
    ap.add_argument("--device")
    ap.add_argument("--adb")
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--quiet", action="store_true", help="只输出元素行，不打印设备/焦点头")
    args = ap.parse_args()

    adb = find_adb(args)
    device = args.device or probe_device(adb, None)

    if args.focus_only:
        focus, resumed = probe_focus(adb, device)
        print("设备: %s" % device)
        print("焦点: %s" % (focus or "(无，可能熄屏或焦点丢失)"))
        print("栈顶: %s" % (resumed or "(未知)"))
        return

    focus, resumed = probe_focus(adb, device)
    print("设备: %s" % device)
    print("焦点: %s" % (focus or "(无，可能熄屏或焦点丢失)"))
    print("栈顶: %s" % (resumed or "(未知)"))

    if not focus or "NotificationShade" in focus or focus.startswith("null"):
        print("\n[警告] 焦点不在应用窗口上，dump 大概率拿到空壳。先切回目标 App。")
        return

    try:
        raw = fetch_ui(adb, device, dev_dump_path())
    except DumpFailed as e:
        print("\n[失败] %s" % e)
        sys.exit(1)

    items, dropped = parse_ui_full(raw)
    size = len(raw)
    print("\n界面元素 %d 条，原始 XML %d 字节" % (len(items), size))
    if dropped:
        print("[注意] 丢弃了 %d 条 bounds 非法的残留元素（上一屏滚走的残影）。"
              % dropped)
        print("       本输出已剔除它们；若还有坐标对不上，滑到列表顶再 probe 一次。")
    if not args.quiet:
        print("-" * 48)
    for text, bounds in items[:args.limit]:
        c = center(bounds)
        if c:
            print("%s @ (%d,%d)  %s" % (text, c[0], c[1], bounds))
        else:
            print("%s  %s" % (text, bounds))


if __name__ == "__main__":
    main()
