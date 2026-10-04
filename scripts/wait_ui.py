#!/usr/bin/env python3
"""轮询等待界面变化（detect_ui_change），替代固定的 sleep。

为什么不用固定 sleep：点击后马上操作，可能落在动画未落定的旧界面上，
静默点到下一行的元素（表现为"我点了 A 却记成了 B"），事后才发现。
轮询判断界面是否真的变了，既快又准。

用法:
  wait_ui.py                      界面一变就返回（默认 5s 超时）
  wait_ui.py -t "11/15"           等到某个文本出现（推荐：等进度号/标题）
  wait_ui.py -d "提交中"           等到某个文本消失（loading / 确认框 / 旧区块被替换）
  wait_ui.py --timeout 8 --interval 0.3
  wait_ui.py --device SERIAL

退出码: 0 = 等到；1 = 超时未等到。
"""
import argparse
import subprocess
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")
import _config  # noqa: E402
import probe as P  # noqa: E402


def snapshot(adb, device):
    """当前界面的指纹 + 元素列表。"""
    path = P.dev_dump_path()
    raw = P.fetch_ui(adb, device, path)
    items = P.parse_ui(raw) if raw else []
    texts = tuple(t for t, _ in items)
    return (len(items), texts), items


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-t", "--text", help="等待包含该字符串的文本出现")
    ap.add_argument("-d", "--disappear", metavar="TEXT",
                    help="等待包含该字符串的文本消失（loading、确认框、旧区块被替换）")
    # 超时/间隔的默认值取自 config.env，没配就用脚本默认
    cfg = _config.load_config()
    ap.add_argument("--timeout", type=float,
                    default=float(cfg.get("WAIT_TIMEOUT") or 5.0))
    ap.add_argument("--interval", type=float,
                    default=float(cfg.get("WAIT_INTERVAL") or 0.3))
    ap.add_argument("--device")
    ap.add_argument("--adb")
    args = ap.parse_args()

    adb = P.find_adb(args)
    # 显式给的设备直接用；否则先读 config.env 的 DEFAULT_DEVICE，再退化成自动取第一个
    device = args.device or _config.config_value("DEFAULT_DEVICE") or P.probe_device(adb, None)

    base, base_items = snapshot(adb, device)

    # -d 的快捷路径：目标一开始就不在，视同已达成，不必空等一个超时周期。
    if args.disappear and not any(args.disappear in t for t, _ in base_items):
        print("[已消失] %r 初始快照中不存在，无需等待" % args.disappear)
        return 0

    deadline = args.timeout
    waited = 0.0
    while waited < deadline:
        import time
        time.sleep(args.interval)
        waited += args.interval
        cur, items = snapshot(adb, device)
        if args.disappear:
            if not any(args.disappear in t for t, _ in items):
                print("[等到] %r 已消失，用时 %.1fs" % (args.disappear, waited))
                return 0
        elif args.text:
            hit = any(args.text in t for t, _ in items)
            if hit:
                print("[等到] %r，用时 %.1fs" % (args.text, waited))
                return 0
        elif cur != base:
            print("[变化] 界面已变，用时 %.1fs（%d -> %d 个元素）"
                  % (waited, base[0], cur[0]))
            return 0

    print("[超时] %.1fs 内%s" % (
        deadline,
        ("没等到 %r" % args.text) if args.text
        else ("%r 一直没消失" % args.disappear) if args.disappear
        else "界面没变化"))
    print("--- 当前界面（最后快照，前 12 条）---")
    for t, b in items[:12]:
        c = P.center(b)
        print("%s @ (%d,%d)" % (t, c[0], c[1]) if c else t)
    return 1


if __name__ == "__main__":
    sys.exit(main())
