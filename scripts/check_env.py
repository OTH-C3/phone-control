#!/usr/bin/env python3
"""前置环境检查：装完之后第一件事，跑一遍就知道哪里没通。

不是能力工具，不能操作手机。它只回答一个问题——现在能不能开始用。
把「adb 装没装、设备连没连、dump 目录可不可写」这类每次都要凭感觉试一遍的
判断固化下来，退出码就把结论说清楚，不用人去读一堆输出猜。

用法:
  python3 scripts/check_env.py
  python3 scripts/check_env.py --device SERIAL    只看某一台
  python3 scripts/check_env.py --fix              发现配置缺失时帮着生成 config.env

退出码（调用方按这个分流，别只看 0/非 0）:
  0  可用               一切正常，直接开始
  1  可用 + 有提醒       能用但有非致命问题（如兜底通道没装 Pillow），按 stdout 说明继续
  2  可用 + 需你拍板     有需要人工选择的项（多设备没指定 serial），你定了再跑
  3  不可用              致命（adb 不在/设备没连/目录写不了），别开始
"""
import argparse
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _config  # noqa: E402


def run(adb, *cmd, timeout=30):
    try:
        r = subprocess.run([adb] + list(cmd), capture_output=True,
                           text=True, timeout=timeout)
        return r.returncode, (r.stdout + r.stderr).strip()
    except subprocess.TimeoutExpired:
        return -1, "超时 %ss" % timeout


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--device")
    ap.add_argument("--fix", action="store_true", help="配置缺失时自动生成 config.env")
    ap.add_argument("--adb")
    args = ap.parse_args()

    notes = []      # 非致命提醒 -> 最后汇成 1
    blockers = []   # 致命 -> 3
    choices = []    # 需要人拍板 -> 2

    # 1. config.env
    if _config.ensure_config() or args.fix:
        notes.append("已生成 scripts/config.env（从模板复制）。想改偏好就编辑它，"
                     "留空的项目沿用脚本默认值。")
        if args.fix:
            # --fix 的意义就在于别让它悄悄生成后就不管了
            notes.append("--fix 已执行：如需改动请先编辑 config.env 再重跑。")

    # 2. adb 本体
    try:
        adb = _config.find_adb(args)
    except SystemExit as e:
        print("[不可用] 找不到 adb。%s" % e)
        print("        → 装 Android SDK Platform Tools，或设 ADB_PATH / ANDROID_HOME")
        return 3
    print("adb   : %s" % adb)
    rc, out = run(adb, "version")
    if rc != 0 or not out:
        blockers.append("adb 跑不起来（exit %s）: %s" % (rc, out or "无输出"))
    else:
        first = out.splitlines()[0] if out else ""
        print("       %s" % first)

    # 3. 设备列表
    rc, out = run(adb, "devices")
    lines = [l for l in (out or "").splitlines()[1:] if l.strip()]
    devices = []
    for l in lines:
        parts = l.split()   # adb devices 用制表符分隔，默认空白切分即可
        if len(parts) >= 2:
            devices.append((parts[0], parts[1]))

    if not devices:
        blockers.append("adb devices 里没有设备。检查 USB 连线 / 无线调试端口 / 手机是否已解锁并允许调试。")
    else:
        print("设备  : %d 台" % len(devices))
        for serial, state in devices:
            mark = {"device": "就绪", "offline": "未就绪(offline)",
                    "unauthorized": "未授权(点手机上同意调试)"}.get(state, state)
            flag = " " if state == "device" else "x"
            print("  [%s] %-24s %s" % (flag, serial, mark))
            if state != "device":
                blockers.append("设备 %s 状态是 %s，不是 device" % (serial, state))

        target = args.device or _config.load_config().get("DEFAULT_DEVICE", "")
        online = [s for s, st in devices if st == "device"]
        if args.device:
            if args.device not in online:
                blockers.append("--device %s 不是已连接的设备" % args.device)
        elif len(online) > 1:
            choices.append("有 %d 台在线设备却没指定 serial。跑的时候加 --device SERIAL，"
                           "或在 config.env 里写 DEFAULT_DEVICE，否则脚本只会取第一个。" % len(online))
        elif len(online) == 1:
            print("选用  : %s（自动，唯一在线设备）" % online[0])
        elif target:
            choices.append("config.env 的 DEFAULT_DEVICE=%s 当前不在线，请换 serial 或留空。"
                           % target)

        # 4. dump 落盘目录在手机上可写吗（决定主通道能不能走）
        dump_dir = _config.load_config().get("DUMP_DIR") or _config.DEFAULTS["DUMP_DIR"]
        serial = args.device or (online[0] if len(online) == 1 else None)
        if serial:
            rc, out = run(adb, "-s", serial, "shell",
                          "[ -w %s ] && echo writable" % dump_dir)
            if "writable" not in (out or ""):
                notes.append("手机上 %s 不可写，uiautomator dump 大概率失败。"
                             "把 config.env 的 DUMP_DIR 改成 /sdcard 再试。" % dump_dir)
            else:
                print("落盘  : %s 可写" % dump_dir)

    # 5. 兜底通道依赖（非主链路，缺了只用像素兜底会报错）
    try:
        import PIL  # noqa: F401
        print("Pillow: 已装（像素兜底可用）")
    except ImportError:
        notes.append("没装 Pillow —— 主通道不受影响，只有像素兜底 pixels.py 用不了。"
                     "需要时: python3 -m pip install --user Pillow")

    print("-" * 56)
    for n in notes:
        print("[提醒 ] %s" % n)
    for c in choices:
        print("[待定 ] %s" % c)
    for b in blockers:
        print("[不可用] %s" % b)

    if blockers:
        print("结论  : 不可用（退出码 3），先解决上面标「不可用」的项")
        return 3
    if choices:
        print("结论  : 可用，但上面有需要你定的项（退出码 2）")
        return 2
    if notes:
        print("结论  : 可用，上面是提醒（退出码 1）")
        return 1
    print("结论  : 可用（退出码 0），可以开始。先跑一句 probe.py 看界面。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
