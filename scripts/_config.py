#!/usr/bin/env python3
"""共享配置：adb 路径 + 用户偏好。被 probe.py / wait_ui.py / check_env.py 引用。

为什么不把配置塞进各个脚本：adb 探测路径有三四处来源、超时和设备号在三个脚本
里都要用，各自复制一遍就会改一处漏三处。这里做唯一来源。

优先级: 命令行参数 > 环境变量 > config.env > 脚本默认值。
"""
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(HERE, "config.env")
TEMPLATE_FILE = os.path.join(HERE, os.pardir, "templates", "config.env.template")

DEFAULTS = {
    "ADB_PATH": "",              # 留空则自动探测
    "DEFAULT_DEVICE": "",        # 默认设备 serial，留空则自动取第一个
    "WAIT_TIMEOUT": "5.0",       # wait_ui.py 默认超时（秒）
    "WAIT_INTERVAL": "0.3",      # wait_ui.py 轮询间隔（秒）
    "DUMP_DIR": "/data/local/tmp",  # uiautomator dump 落盘目录（手机上）
    "PIXELS_MIN_RATIO": "0.01",  # pixels.py 内容行阈值
    "YADB_JAR": "",              # 读屏加速用的 jar 在手机上的位置，留空则用 /data/local/tmp/yadb
}

ADB_CANDIDATES = [
    os.path.expanduser("~/Android/Sdk/platform-tools/adb"),
    os.path.expanduser("~/AppData/Local/Android/Sdk/platform-tools/adb.exe"),
    "adb",
]


def ensure_config():
    """首次运行把 templates/config.env.template 复制成 scripts/config.env。

    返回 True 表示这次是刚新建（调用方应提示用户可以编辑它）。
    模板不存在（比如只拷了 scripts/ 而没拷 templates/）时安静跳过，不挡路。
    """
    if os.path.exists(CONFIG_FILE) or not os.path.exists(TEMPLATE_FILE):
        return False
    shutil.copyfile(TEMPLATE_FILE, CONFIG_FILE)
    return True


def load_config():
    """读 config.env（KEY=VALUE，# 注释）。文件不存在返回空表，不报错。"""
    cfg = dict(DEFAULTS)
    if not os.path.exists(CONFIG_FILE):
        return cfg
    with open(CONFIG_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k and v:
                cfg[k] = v
    return cfg


def config_value(key, default=""):
    """按 环境变量 > config.env > 默认值 的顺序取一个值。"""
    env = os.environ.get(key)
    if env:
        return env
    return load_config().get(key, default)


def find_adb(args=None):
    """跨 agent 通用：多来源探测 adb，不写死路径。

    顺序: --adb > config.env 的 ADB_PATH > $ANDROID_HOME > 常见安装路径 > PATH。
    找不到直接 sys.exit，给出可执行的下一步，不说空话。
    """
    if args is not None and getattr(args, "adb", None):
        return args.adb
    cfg = load_config()
    if cfg.get("ADB_PATH"):
        p = os.path.expanduser(cfg["ADB_PATH"])
        if os.path.exists(p):
            return p
        sys.exit("config.env 里的 ADB_PATH 指向的文件不存在: %s" % p)
    env = os.environ.get("ANDROID_HOME")
    if env:
        p = os.path.join(env, "platform-tools", "adb")
        if os.path.exists(p):
            return p
    for c in ADB_CANDIDATES:
        if os.path.exists(c):
            return c
    found = shutil.which("adb")
    if found:
        return found
    sys.exit("找不到 adb：装 Android SDK Platform Tools，或设 ADB_PATH / ANDROID_HOME / --adb")
