---
name: phone-control
agent_created: true
description: 操作 Android 手机的屏幕，也能读手机上的内容。用户说「帮我操作手机」「点一下」「点开这个按钮」「看看我现在手机上开着什么」「帮我看看这条聊天记录/这段对话」「读一下这个页面」「把这页内容读出来」「翻上去看看更早的消息」「把这个列表从头读一遍」「填一下这个表单」「点完等它跳到下一页」「这个按钮点不动 / 点完没反应」「帮我签到 / 打卡 / 提交」这类话时用。能做：读当前屏幕的内容、连续滚动读长内容（聊天记录/长列表/长文章）、按名字点按或输入、滑动翻页、点完等页面真的变了、提交前核对不可逆操作。边界：只做 Android 手机，不写 Android 代码、不管 iOS。技术上以界面树读屏（含一个可选的加速小程序，缺失时自动退回原通道）+ 原生 input 操作 + 轮询等待为主，截图视觉只作兜底。
whenToUse: 用户要操作或读取 Android 手机时：点按钮、填表单、读当前页面、翻长列表或聊天记录、提交签到、滑动、切前后台。手机上任何"帮我点/帮我看/帮我填/帮我发"的话都算。
metadata:
  version: 0.6.0
  author: OTH-C3
  license: MIT
  dsh_compatible: true
  platforms:
    - android
---

# 手机界面控制

## 0. 定位与能力

**定位**：只用 shell 调 adb，不依赖任何 Agent 框架的专有工具或 MCP，可原样装到任意支持 SKILL.md 的 agent（Claude Code / Codex / Cursor 等）。与具体 App 无关，某个 App 的交互经验按 §6 分册存放，不写进这里。

**它做两件事，是一个闭环**：

1. **读** —— 把手机当前屏幕的内容变成 AI 能读的文字（既是点按的前提，也是独立能力本身：读聊天记录、读列表、读长文章）。
2. **操作** —— 按读到的坐标点按、输入、翻页，并在操作后重新读一遍确认生效。

读和操作共用同一套通道，没有单独的"读工具"或"操作工具"。

**触发机制（改 description 前先读这个）**：是否调用本 skill **只看 frontmatter 的 `description`**，SKILL.md 正文是触发之后才读的。所以 description 必须写成**用户会说的人话**（「帮我点一下」「帮我看看这条聊天记录」），不能只写机制名（`uiautomator dump`、轮询）——只写机制名，用户用正常口语提问时就匹配不上，skill 根本不会被调起来。写之前先自问：*"What would a user say that should trigger this skill?"*

## 1. 读屏幕的默认与兜底

**默认走文字，视觉是备用。**

手机屏幕本身是像素。把整屏像素交给 AI 有两个代价：token 大，且要自己从图里估坐标、认文字。所以本 skill 的**默认**做法是让系统把界面导出成结构化文字（带坐标）直接读：

> 文字通道能读到时，就别动截图；确实读不出内容时，截图视觉是**备用方案**，不是被禁用的方案。

AI 能看图，只是这件事的上限和代价都不如文字通道，所以放在最后。

## 2. 读取：通道分级（开销从低到高）

**始终按此顺序尝试，不跳级。** 每一步都是"上一步不够用"才升级。

### 2.0 先确认两件事：屏幕亮着、焦点层对（各几字节，必做）

**① 屏幕必须是 Awake 的**（屏灭时 input 事件被静默吞掉）
> 屏灭时 `input tap` / `keyevent` 全部无效且**不报错**，dump 只剩约 20 个空壳节点 ——
> 看起来像"界面读不到"，很容易误判成焦点问题（实测踩过：按键前后焦点毫无变化）。

```bash
adb shell dumpsys power | grep mWakefulness   # 必须是 Awake
```

不是 `Awake` 就先唤醒：`adb shell input keyevent 26`，等几秒复查。
**只发一次**：亮屏时同一个命令是关屏，连发会变成"亮一下又熄"，用户那边像手机坏了。
锁屏（`isKeyguardShowing=true`）时返回键退不出去是系统正常行为，**解锁只能由人在真机上做**，
别瞎试手势；这种情况直接把控制权交回用户。

**② 焦点层**（导出的是当前焦点层，不是目标 App 的窗口）
焦点被系统层/通知栏/弹窗抢走时，导出的是一堆空 `text` 的容器节点，一条内容都读不到。

```bash
adb shell dumpsys window | grep mCurrentFocus
```

注意：`mCurrentFocus=NotificationShade` **在亮屏和屏灭时都会出现**，
**不能**拿它当"屏灭了"的判据 —— 判屏灭只看上面的 `mWakefulness`。

### 2.1 首选：界面树

**用 `scripts/probe.py` 读屏就行，别手写 dump 命令。** 它内部已经处理好了通道选择：

- 手机上装了 `bin/yadb`（那个小程序）时走它，一次约 **0.8 秒**
- 没装、装失败、或读不出内容时**自动切回 `uiautomator dump`**，约 2.3 秒
- 两条通道内容等价，**对你是透明的**，不用判断走的是哪条

```bash
python3 scripts/probe.py --focus-only    # 只看设备/焦点/栈顶（几字节）
python3 scripts/probe.py --limit 20      # 读当前屏，最多打印 20 条
```

想手动走底层命令时（原语，不是日常用法）：

```bash
adb shell uiautomator dump <落盘目录>/probe_<唯一名>.xml
adb pull <落盘目录>/probe_<唯一名>.xml ./probe.xml
```

**为什么必须落盘再 pull、目录怎么选、为什么"dumped to"不等于成功、并发为什么要唯一文件名**——全在 **`references/facts.md` F1~F9**，逐条给了实测依据。加速通道的实测数据与两个必须遵守的纪律见 **F30 / F31**。

一句话：`exec-out` 不返回 stdout；手机上不能写 `/tmp`；拉回来前必须确认文件非空。

**输出巨大（实测 8~42KB），不得整份读进上下文。** `probe.py` 已压成 `文字 @ 中心坐标 [bounds]`：42KB 压到约 2.5KB（**5.9%**）。

### 2.2 读长内容：多屏聚合（`scroll_read.py`）

**一屏读不完的内容（聊天记录、长列表、长文章），用它自动翻页拼接。**

```bash
python3 scripts/scroll_read.py --max-screens 30 --out chat.txt           # 往后读（更下面/更新的）
python3 scripts/scroll_read.py --dir up --max-screens 30 --out chat.txt # 往前读（更上面/更早的）
python3 scripts/scroll_read.py --stop-text "已经是底部"                   # 读到某句就停
```

它自动处理四件容易出错的事：**相邻屏重叠去重**（两屏交界处的内容不会出现两次）、**吸顶/吸底剥离**（搜索栏、底部标签栏不会每屏重复）、**到底判定**（滑动后界面不再变化即停）、**方向接法**（往上读时新内容插到前面）。每件事各自对应一条实测事实，见 **F14~F20**。

方向口诀：`--dir down` 手指上滑看更下面，`--dir up` 手指下滑看更上面。

**读聊天记录**：打开聊天窗口默认停在最新一条，直接 `--dir up` 往上翻历史。

### 2.3 兜底：截图像素分析（`pixels.py`）

只有 2.1 拿不到内容时才用。定位**位置**（内容行段、状态色块）：

```bash
python3 scripts/pixels.py shot.png --rows    # 内容行段（找选项/输入框在哪几行）
python3 scripts/pixels.py shot.png --dots    # 蓝/红/绿/青绿色块（状态标记、选中态）
```

`pixels.py` **不读内容**，只输出坐标；具体内容回退到 2.4。阈值默认 `0.01`，见 **F21**。

### 2.4 最后手段：截图视觉读取

AI 直接看整张图。付整图 token，且要自己估坐标。只有前面全失败、且非看画面不可时才用（如内容画在 Canvas 里、纯粹是图形或视频帧）。

**先排除两种假性"读不到"**，别急着判死刑：

- **焦点被抢**：读到的是系统弹窗（权限框、兼容性提示），目标 App 内容一条没有 → 先看 §2.0 的焦点；
- **读太早**：只读到标题栏/地址栏这类外壳元素、没有任何正文 → 多半是页面（尤其 WebView）还没渲染完，
  **等 1~2 秒或轮询到元素数稳定再读一次**（实测 `example.com`：+2.8s 只回 4 条外壳，+5.1s 回 13 条含正文，见 **F25**）。

真的读不到的是**本来就没有文字节点的画面**（画布 / 游戏 / 视频帧 / 纯图片列表），
这类才走截图视觉。**WebView 网页正文是读得到的**（读渲染后的文字），不要因为"它是网页"就放弃主通道。

### 2.5 最小完备工具集

**工具就是这几个脚本 + 原生 adb，每个只干一件事。**

| 脚本 | 干什么 | 明确不干什么 |
|---|---|---|
| `probe.py` | 把**当前一屏**压成文字表（文字 + 点击坐标 + 可点范围）；自动剔除滚动残留的非法元素 | 不翻页、不操作、不等；仅确认焦点用 `--focus-only` |
| `scroll_read.py` | **多屏**滚动 + 去重 + 拼接，读长内容 | 不点按钮、不填表单 |
| `wait_ui.py` | 轮询**等**界面变化 / 等文本出现或消失 | 不输出元素位置，只报告"变了 / 到了" |
| `pixels.py` | 从截图**算位置** | **不读内容**，只给坐标 |
| `check_env.py` | **不操作手机**，只回答"现在能开始用吗" | 不 dump、不点击 |

`_config.py` 不是工具，是几个脚本共用的配置层（adb 探测 + `config.env` 读取）。

**最小完备原则 —— 想加新脚本前先回答一句：**

> 现有这些 + 原生 adb（`uiautomator dump` / `input tap` / `input swipe,text` / `keyevent`）**组合起来能否解决？能则不新增。**

每加一个工具就多一处职责重叠，而重叠正是歧义的源头。`pixels.py` 是唯一需要 Pillow 的（纯兜底），**主链路零第三方依赖**。

`check_env.py` 为什么不算"多出来的工具"：它没有半点操作手机的能力，只读环境状态、用退出码回答"能不能开始"，没碰能力边界。

## 3. 等待界面变化（强制替代固定 sleep）

**禁止点击后写死 `sleep 1.2` 再继续操作。** 快的时候白等，慢的时候（动画未落定）会点到**下一屏的元素**，造成静默答错。

```bash
python3 scripts/wait_ui.py                  # 界面一变就返回
python3 scripts/wait_ui.py -t "下一步"       # 等文本出现（推荐：等进度号/标题）
python3 scripts/wait_ui.py -d "提交中"       # 等文本消失（loading / 确认框 / 旧区块）
```

每 300ms 取一次界面指纹（元素数 + 文本集合），变化即返回；超时（默认 5s）返回非零并打印最后界面，用以区分"未生效"与"超时过短"。

**注意顺序**：先启动 `wait_ui.py`（它先取基准快照），再执行点击；反过来会让"已消失"被当成初始状态。

**每次操作后都要做这一步**，特别是：点选项、提交表单、进入下一页、弹确认框。

## 4. 操作

- 点击：`adb shell input tap x y`（坐标取 `probe.py` 输出的 bounds 中心）
- 滑动：`adb shell input swipe x1 y1 x2 y2 ms`
- **顶部和底部都会吞点击**：状态栏 + 标题栏 + 吸底按钮区域（1080x2400 上 y≈200，约 **8% 高度**）的按钮 tap 过去没反应。点不动时换 y 值试下方，别对同一坐标反复点（**F10**）
- **坐标一律取当前 `probe.py` 输出**，不沿用别的设备或旧记录（**F11**）
- **滚动过的页面上，`probe.py` 的坐标不能整份照抄**：列表滚动后 dump 会混进上一屏的残留元素，
  其 bounds 是**颠倒**的，照它点击会**点到别的条目**（实测点到了另一个 App）。`probe.py` 已自动
  丢弃这类非法行并提示「丢弃了 N 条残留元素」；最稳妥是**先滑回列表顶部再 probe**（**F14、F24**）
- 输入文本：`adb shell input text "xxx"`（走输入子系统，绕过输入法）—— **仅限 ASCII**
- 收键盘：`adb shell input keyevent 4`

**输入中文（必读）**：`input text` **发不了中文**（报 `NullPointerException`），`input keyevent` 也不行
（只出英文候选）。

**推荐：仓库自带，不用另外下载** —— `bin/yadb`（第三方程序，LGPL-3.0，见 `NOTICE.md`）。
把 jar 推到手机，用 `app_process` 跑起来注入文本（走系统输入管理接口，
**支持中文、不装 App、不改输入法**）：

```bash
# 首次使用推一次（手机上已有就跳过）
adb push bin/yadb /data/local/tmp/yadb

# 之后每次注入
adb shell app_process -Djava.class.path=/data/local/tmp/yadb /system/bin \
    com.ysbing.yadb.Main -keyboard "要发的中文"
adb shell app_process -Djava.class.path=/data/local/tmp/yadb /system/bin \
    com.ysbing.yadb.Main -keyboardClear        # 清空输入框
```

**两个必须注意的点**：
1. **输入框必须先真正聚焦**（dump 里该 `EditText` 的 `focused="true"`），否则文本只进剪贴板、不进输入框；
   先 `input tap` 点进输入框（坐标取 dump 的 bounds 中心），确认 focused 再注入。
2. 注入完**读一次 dump 核对 `EditText.text`** 与目标文案逐字一致，再点发送。

**备选**：装能接收广播文本的第三方输入法，用完**务必切回原输入法**（否则用户键盘变空壳）。

**本仓库不附下载地址或镜像**（会失效），`bin/yadb` 已随仓库分发；来源与许可见 `NOTICE.md`。
删除 `bin/yadb` 只会失去中文输入，其余功能不受影响。
输入法键盘是独立窗口，**dump 读不到候选词**（**F29**），要看候选得截图。

## 5. 不可逆操作纪律（提交/确认/支付/删除）

这类操作**只记第一次结果，改不回来**：

1. 操作前**先回读当前界面**，确认所处步骤正确；
2. 操作后**用 `wait_ui.py` 确认界面真的变了**，并读一遍新界面核对；
3. 提交类**逐项核对**再点确认（要选的项真的选中了、状态标识真的变了）；
4. 确认框按钮位置在**实际读到的输出**里定位，不以估算的 y 值落点。

不确定时**停下并向用户确认**，不赌。

## 6. 经验文件（按 App + 页面类型分册）

`references/app-profiles/` 下每个文件对应一个 `包名--页面类型.md`，分三类写：**① 平台特征**（这页天生什么样）、**② 有效模式**（怎么做才对）、**③ 已知陷阱**（踩过的坑）。SKILL.md 不内联具体坐标，只放索引。

用法：进入某个 App 的页面**前，先 grep 索引找文件**，有就读（几百字节），没有就走通用流程。

**经验要能长出来（流程，不是自觉）**：每次操作出问题（点错、没反应、焦点跑偏、界面没按预期变）之后，第一反应是"这条坑值不值得存"。写前三问，全过才写：**跨机器不变吗？换 App 还用得上吗？下个人读了能少踩一次坑吗？** 坐标一律不写死，只写**现象 → 原因 → 做法**。写完**当场用 `probe.py` / `wait_ui.py` 验一遍**。

完整流程、合格/不合格对照、失效规则、自检清单 → **`references/app-profiles/GROWTH.md`**。

## 7. 配置（config.env）

优先级：命令行参数 > 环境变量 > `scripts/config.env` > 脚本默认值。首次运行自动从 `templates/config.env.template` 生成 `scripts/config.env`（已 gitignore）。

| 键 | 作用 | 留空时 |
|---|---|---|
| `ADB_PATH` | adb 完整路径 | 自动探测（`$ANDROID_HOME` > `~/Android/Sdk` > `PATH`） |
| `DEFAULT_DEVICE` | 默认设备 serial | 自动取在线的第一台 |
| `WAIT_TIMEOUT` | `wait_ui.py` 默认超时（秒） | 5.0 |
| `WAIT_INTERVAL` | `wait_ui.py` 轮询间隔（秒） | 0.3 |
| `DUMP_DIR` | dump 在手机上的落盘目录 | `/data/local/tmp` |
| `PIXELS_MIN_RATIO` | 像素兜底的内容行阈值 | 0.01 |

**留空是合法的**，等于每次走自动探测。只有自动探测猜错（多设备、adb 不在默认路径）时才填。

## 8. 前置检查（check_env.py）

装完或换机器先跑一次，把"adb 装没装 / 设备连没连 / dump 目录写不写得进"固化下来。**退出码就是结论**：

| 退出码 | 含义 | 该怎么做 |
|---|---|---|
| `0` | 可用 | 直接开始 |
| `1` | 可用 + 有提醒 | 能用，看输出里 `[提醒]` 那几行 |
| `2` | 可用 + 需拍板 | 能用，但有多设备没指定 serial 这类要人定的项 |
| `3` | 不可用 | 致命（adb 不在 / 没设备 / 设备 offline），别开始 |

```bash
python3 scripts/check_env.py
python3 scripts/check_env.py --device SERIAL
```

## 9. References 索引（按需加载）

| 文件 | 何时读 |
|---|---|
| `references/facts.md` | **动手读内容/点击前查一遍**（跨机器不变的硬事实：exec-out 不返回 stdout、滚动后的残留元素、顶部吞点击…） |
| `references/app-profiles/index.md` | **进入任何 App 页面前先看这张表** |
| `references/app-profiles/GROWTH.md` | **要写/改/复核经验时读** |
| `references/channels.md` | 需要无线调试、scrcpy、uiautomator2 等**非 USB-adb 通道**时 |
| `references/data-layer.md` | 目标是拿**应用原始数据**（非界面）时；高门槛，默认不走 |
| `docs/SPEC-yadb-重铸读屏通道.md` | 想知道**读屏换 yadb 后怎么兜底**、以及哪些通道千万别换时 |

> 顺序：先看 `app-profiles/index.md` → 有对应文件就读 → 没有走本文件的通用流程 → 踩到坑就新建一个加回索引。