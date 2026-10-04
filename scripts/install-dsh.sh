#!/usr/bin/env bash
# 把本技能装进 DeepSeek Harness（dsh），让 dsh 能直接用它。
#
# 用法：
#   bash scripts/install-dsh.sh                 # 装到 ~/.dsh/skills/phone-control
#   bash scripts/install-dsh.sh /path/to/dsh-home  # 装到指定 DSH_HOME
#
# 装完在 dsh 里说一句「帮我看看手机上开着什么」就能触发。
# 想验证装没装上：确认下面的 SKILL.md 在位即可（dsh 启动时扫这个目录）。
set -euo pipefail

DSH_HOME="${1:-$HOME/.dsh}"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAME="phone-control"
DEST="$DSH_HOME/skills/$NAME"

# 1) 目标根目录存在吗
if [ ! -d "$DSH_HOME" ]; then
  echo "找不到 dsh 配置目录：$DSH_HOME" >&2
  echo "如果还没装 dsh，先跑：npm install -g @deepseek-ai/dsh" >&2
  exit 1
fi

# 2) adb 有没有（没 adb 这个技能装了也用不了）
if ! command -v adb >/dev/null 2>&1 \
   && [ ! -x "$HOME/Android/Sdk/platform-tools/adb" ] \
   && [ ! -x "$HOME/AppData/Local/Android/Sdk/platform-tools/adb.exe" ]; then
  echo "警告：没找到 adb。技能已装上，但要操作真机得先装 Android SDK Platform Tools。" >&2
  echo "     装完记得在 config.env 里设 ADB_PATH（见 templates/config.env.template）。" >&2
fi

# 3) 装。已存在的 config.env 是用户自己的机器配置，不能覆盖
mkdir -p "$DEST"
if [ -f "$DEST/scripts/config.env" ]; then
  echo "保留你已有的 scripts/config.env，不覆盖"
fi

for item in SKILL.md README.md NOTICE.md LICENSE scripts references templates bin docs .claude-plugin; do
  [ -e "$SRC/$item" ] || continue
  cp -r "$SRC/$item" "$DEST/"
done

# 4) 清掉不该进安装目录的东西
find "$DEST" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true

echo "已装到：$DEST"
echo
echo "下一步："
echo "  1) 确认 adb 能连上手机：adb devices"
echo "  2) 在 dsh 里直接说「帮我看看手机上开着什么」"
echo "  3) 改配置（超时/设备号等）：编辑 $DEST/scripts/config.env"
echo
echo "确认装上了：$DEST/SKILL.md 存在即可（dsh 启动时扫这个目录）。"
