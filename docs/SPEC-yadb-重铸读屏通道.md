# 用 yadb 重铸读屏通道 —— 规格说明书

> **状态：v0.6.0 已实施完成。** 实施与验收结果见文末「实施记录」，
> 运行时行为数据见 `references/facts.md` 的 **F31**。
> 本文保留作为改造依据与背景（为什么这么选、哪些路走过了）。
>
> 目标读者：当时的实现者。
> 本文所有数字都来自真机实测（2026-10-04，小米 / Android 16 / SDK 36），不是估计值。
> 每条都标了「怎么验」，复核时请按同样方式复测。

---

## 一、这次要做什么（一句话）

把「读界面」的主通道从 `uiautomator dump` 换成 `yadb -layout`（**快 3 ~ 5 倍**），
把 yadb 独有的手势能力（长按拖拽、双指缩放）补进工具集，
**`uiautomator dump` 降级为兜底**，yadb 缺失/失败时自动回退，行为与现在完全一致。

不做的事：不动截图通道（实测 yadb 截图更慢且会卡死，见 §五）。

---

## 二、实测数据（决策依据，不要凭感觉改）

### 2.1 速度（真机 5 次取样，单位秒）

| 通道 | 耗时 | 备注 |
|---|---|---|
| `uiautomator dump` + `pull` + `rm` | 2.34 ~ 2.45（偶发 3.8） | 现状 |
| `yadb -layout` + `exec-out cat` + 前置 `rm` | **0.47 ~ 0.81** | **快 3 ~ 5 倍** |
| `yadb -layout <自定义路径>` + `exec-out cat` + 前置 `rm` | 0.52 ~ 0.79 | 与默认路径**基本持平** |

**关键**：`adb exec-out cat <手机路径>` 能直接把文件流拉回本地，
**比 `adb pull` 快且不需要先 push 到 /sdcard**。用 `pull` 会退化到 2 秒以上。

> 修正记录：早期测过一轮「自定义路径比默认路径慢 0.63~0.80 vs 0.51~0.64」，
> 但那轮**没含前置 `rm`**。加上规格要求的「先删旧文件」后复测，两条路径速度持平。
> 结论：路径选哪个都行，实现时按 `config.env` 的 `DUMP_DIR` 走，保持配置统一。

### 2.2 数据质量（同一界面逐节点比对）

- 节点数：两边都是 105，一一对应
- `class` + `bounds` 对齐后可比节点 103 个，**属性差异 0 处**
- yadb **少两个属性**：`drawing-order`、`hint`
  **已确认现有代码完全没用到这两个**（`grep drawing-order|hint scripts/ references/ SKILL.md` 零命中），
  所以可以直接替换，不需要补。

### 2.3 yadb 各命令的真实签名（从源码 `Main.java` 确认，勿凭猜）

```
-keyboard <文本>                     注入文本（中文可用）
-keyboardClear                       清空输入框
-touch <x> <y> [时长ms]              点按（时长可选，默认 -1）
-swipe <x1> <y1> <x2> <y2> <时长ms>   滑动，时长必填
-longPressDrag <x1> <y1> <x2> <y2> <长按ms> <拖动ms>   长按后拖拽
-pinch <中心x> <中心y> <起始距离> <结束距离> <时长ms>   双指缩放
-layout [输出路径]                   导出界面树，默认 /data/local/tmp/yadb_layout_dump.xml
-screenshot [输出路径]               截图，默认 /data/local/tmp/yadb_screenshot.png
-readClipboard                       读剪贴板
-writeClipboard <文本>               写剪贴板
```

`-pinch` 与 `-longPressDrag` 是 **native adb 做不到的**能力，这是本次新增价值的核心。

### 2.4 成功判定信号（**必须按文本判定，退出码不可靠**）

| 命令 | 成功信号 |
|---|---|
| `-layout` | 输出含 `layout dumped to:` |
| `-screenshot` | 输出含 `screenshot success:` |
| `-keyboard` | 输出含 `Copy text:true` |
| `-swipe` / `-longPressDrag` | **无条件**打印 `swipe` / `longPressDrag`，**不能当成功标志** |

**实测坑（必须处理）**：
- jar 不存在时，输出是 `Aborted`，而 **exit code 可能是 0 也可能是 134**（取决于有没有被管道截断）。
  → 判定必须看文本，**绝不能只看 returncode**。
- `uiautomator dump` 失败时会打印 `ERROR: could not get idle state.`
  → 现有代码用 `"dumped to" in stdout` 判定，改后**两条都要认**。

### 2.5 陈旧文件风险（实测确认）

yadb 失败时**不会删除上一次的输出文件**。若不先删，会把上一屏当成这一屏 —— 极难察觉。

实测：删掉输出文件后，在 jar 缺失情况下跑 `-layout`，文件仍不存在（说明确实没写新文件，
但如果**不删**，旧文件会一直在，`exec-out cat` 照样能拿到内容）。

→ **每次 dump 前必须先 `rm -f` 目标文件**。

---

## 三、改造方案

### 3.1 唯一改动点：`scripts/probe.py` 的 `fetch_ui()`

三个脚本（`probe.py` / `scroll_read.py` / `wait_ui.py`）**都走 `fetch_ui()`**，
所以只改这一个函数就够，不要改其他地方。

### 3.2 `fetch_ui()` 新逻辑

```python
def fetch_ui(adb, device, dev_path):
    """读界面树：优先 yadb -layout（快 3~5 倍），失败自动回退 uiautomator dump。

    两条通道产出的 XML 结构等价（实测 105 节点、103 个可比节点零属性差异），
    yadb 只少 drawing-order 和 hint 两个属性，现有代码没用到。
    """
    # 1) 先删目标文件 —— yadb 失败时不会覆盖旧文件，会读到上一屏（实测确认）
    subprocess.run([adb] + dev_flag(device) +
                   ["shell", "rm", "-f", dev_path],
                   capture_output=True, timeout=30)

    # 2) 主通道：yadb -layout
    data = _try_yadb_layout(adb, device, dev_path)
    if data:
        return data

    # 3) 兜底：uiautomator dump（保持现有实现，行为不变）
    return _try_uiautomator(adb, device, dev_path)
```

配套两个私有函数：

- `_yadb_available(adb, device)` → bool
  检查 jar 是否在设备上：`adb shell ls -l /data/local/tmp/yadb`（或 config 里配的路径）。
  **结果要缓存**，别每次 dump 都多跑一条命令。
- `_try_yadb_layout()` → bytes | None
  1. `adb shell app_process -Djava.class.path=<手机上的yadb路径> /system/bin com.ysbing.yadb.Main -layout <dev_path>`
     （把 jar 路径和 dump 路径都做成可配置，默认 `/data/local/tmp/yadb`）
  2. 输出里没有 `layout dumped to:` → 返回 None（回退）
  3. **超时设 30 秒**（实测正常 0.5 秒，30 秒足够宽松）
  4. 用 `adb exec-out cat <dev_path>` 读回，**不要用 `pull`**（见 §2.1）
  5. 读回内容为空或长度 0 → 返回 None（回退）
  6. `finally` 里 `rm -f` 清理
- `_try_uiautomator()` → 现有 `fetch_ui()` 的逻辑**原样搬过去**，一个字都别改
  （含 `dumped to` 判定、失败也清理、拉回后二次确认、DumpFailed 的报错文案）。

### 3.3 兜底必须「零行为差异」

yadb 不可用时，**使用者（agent）察觉不到**。验收标准：
把 jar 挪走（`adb shell mv /data/local/tmp/yadb /tmp/`），
跑同样的 `probe.py` 命令，输出与改造前**逐字节一致**。

---

## 四、新增能力（怎么暴露给 agent）

### 4.1 手势：加进 probe.py 或新脚本

- **长按拖拽**（拖动滑块、拖文件、拖排序）：
  `-longPressDrag <x1> <y1> <x2> <y2> <长按ms> <拖动ms>`
  这是 native adb **做不到**的（`input swipe` 无法先长按再拖）。
- **双指缩放**（地图放大缩小、图片缩放）：
  `-pinch <中心x> <中心y> <起始距离> <结束距离> <时长ms>`
  native adb 也做不到。

**验证方式**：在微信聊天页长按一条消息拖到右上角（会进「引用/转发」选择态，
按 `back` 取消）；地图页双指放大。这两个是肉眼可判的，录屏或截图前后对比。

### 4.2 不要做的事

- **不要**用 yadb 替换截图通道（见 §五）。
- **不要**把 yadb 变成必需依赖。仓库里 jar 可以删（`NOTICE.md` 已写明），
  删了必须能正常工作。
- **不要**新增「统一抽象层」把两条通道包成一个复杂接口 —— 收益不抵复杂度。
  两个函数 + 一个 if/else 就够。

---

## 五、明确不做：截图通道（附实测数据）

| 通道 | 耗时 | 稳定性 |
|---|---|---|
| `adb exec-out screencap -p` | **0.39 ~ 0.46 秒** | 12/12 成功 |
| `yadb -screenshot` | 0.54 ~ **5.44 秒** | 12 次里 **5 次卡死（42%）** |

**卡死原因**（源码 `Screenshot.java` 确认）：
`acquireLatestImage()` 在没有新帧时返回 null，代码用 `do-while` + `Thread.sleep` 重试，
**硬编码 5 秒超时**才抛 `TimeoutException`。无新帧时必然卡满 5 秒。

**画质对比**：两边都是 1440x3200，字节数完全相同（25104 字节），**画质无差异**。

→ 结论：**截图保持现状**。yadb 截图没有任何收益，只有 42% 的失败率和 10 倍的耗时波动。

---

## 六、验收清单（逐条实测，附命令）

实现完请逐条跑，全部通过才算完成：

1. **等价性**：
   同时跑两条通道拿到的 XML，对比 `class`+`bounds` 对齐后的节点属性差异数 = 0。
2. **加速比**：`fetch_ui` 走 yadb 的中位耗时 < 走 uiautomator 的 50%。
3. **兜底一致**：把 jar 挪走，重复第 1 步，输出逐字节相同。
4. **陈旧文件**：不删目标文件、故意让 yadb 失败，确认**不会**返回上一屏的内容。
5. **超时兜底**：`timeout` 设 30 秒时，yadb 卡死也不至于让整个脚本挂死。
6. **新能力**：`longPressDrag` / `pinch` 至少各在真机上成功触发一次，肉眼可判。
7. **回归**：`check_env.py`、`probe.py`、`scroll_read.py`、`wait_ui.py` 四条都正常。

---

## 七、已知限制（实现时不要试图绕过）

- yadb 是 **LGPL-3.0 第三方程序**（详见 `NOTICE.md`），是可选增强，不是地基。
- 缺 `drawing-order` 和 `hint` 属性。当前代码不需要；**若将来要用，必须回退到 uiautomator 通道**，
  别去改 yadb（改了就违反 LGPL 且要重新分发）。
- yadb 只能 `-layout` **当前焦点层**（和 uiautomator 一样），焦点问题照旧按 `F28`（屏灭）与 `F4`（焦点层）处理。
- 模拟器上未验证（本机模拟器与真机镜像不同），**只保证真机**。

---

## 八、实施记录（v0.6.0，2026-10-04）

### 实际改了什么

只改 `scripts/probe.py`，其余文件零改动：

| 动作 | 内容 |
|---|---|
| 新增 | `_dev_flag()` / `yadb_jar_path()` / `yadb_available()` / `_try_yadb_layout()` / `_try_uiautomator()` |
| 改写 | `fetch_ui()` —— 变成「先 `rm -f` → yadb → 兜底 uiautomator」 |
| 新增配置 | `YADB_JAR`（`_config.py` 的 `DEFAULTS` + `templates/config.env.template`） |

**兜底通道的代码是从旧 `fetch_ui()` 原样搬过去的**，已用 AST 比对确认可执行部分逐字符一致
（只有 docstring 文字和空行不同）。这是「零行为差异」的前提。

### 验收清单逐条结果

| # | 项 | 结果 |
|---|---|---|
| 1 | 等价性 | 通过。46 节点一一对应，42 个 `(class+bounds)` 键全对齐，17 个属性里只有 `drawing-order` 有差异（yadb 缺，46 处），其余 0 差异 |
| 2 | 加速比 | 通过。yadb 787ms vs uiautomator 2288ms，**快 2.9 倍**（要求 <50%，实测 34%） |
| 3 | 兜底一致 | 通过，但**判据要改**。见下面「规格的一处不准确」 |
| 4 | 陈旧文件 | 通过，且**测了两种情况**（见下） |
| 5 | 超时兜底 | 通过。yadb layout 30s / exec-out cat 30s / uiautomator 90s |
| 6 | 新能力 | 部分通过。见下面「未完成的验证」 |
| 7 | 回归 | 通过。四脚本全跑通 |

### 规格的一处不准确（已修正）

第 3 条写的是「输出**逐字节一致**」，**这个判据是错的**。
yadb 少 `drawing-order` 属性，所以两边 XML 字节数必然不同（实测 16700 vs 16729）。

**正确的判据是「解析结果一致」**：元素条数、每条的文字与坐标全相同。
实测确实如此（19 条元素完全相同），只有「原始 XML 字节数」这一行显示的数字不同。

### 第 4 条必须测两种情况（只测一种会漏掉危险的）

| 情况 | `yadb_available()` | 靠什么防住 |
|---|---|---|
| jar 不在设备上 | `False` | 前置 `ls` 检查就拦住 |
| **jar 在但执行失败** | **`True`** | **只能靠调用前那次 `rm -f`** |

第二种更危险：把 jar 换成无效内容后，`ls` 查得到、`available` 返回 `True`，
但 `app_process` 跑不起来。实测（造了含 `STALE_ABC` 标记的陈旧文件）：
`_try_yadb_layout` 返回 `None`、**没有把陈旧内容当结果返回**，`fetch_ui` 拿到的是真实界面。

### 未完成的验证（第 6 条）

`-longPressDrag` / `-pinch` 的命令签名被正确接受、`longPressDrag` 确实执行了
（在手机桌面观察到进入多选态、图标位移）。

**但没有做到 SPEC 要求的「肉眼可判」程度** —— 原定在微信聊天页验证长按拖拽，
考虑到那会动到用户的会话数据，改在手机桌面做。桌面上的 pinch 本来就看不出效果，
所以 pinch 只验证到「命令被接受」为止，**没有观察到实际缩放效果**。

→ 后续若要在真实场景确认 pinch，需要找一个明显可缩放的界面（地图、图库），
  **且要先问过用户**。

### 顺带发现

验证新手势时点「完成」按钮，第一次按截图估算的坐标点空了（截图被缩放过），
第二次用 `probe.py` 读屏拿到真实坐标 (1210,300) 一次点中。
→ 这是本工具自身的价值实证：**用读屏拿坐标，不要用眼睛估算截图**。
