# 通道：除了 USB-adb 还能怎么控手机

默认就用 **USB adb**（`adb shell input` + `uiautomator dump`），它最稳、零安装、每次十几毫秒。
下面这些是「USB 线不方便 / 需要更快 / dump 拿不到」时才考虑的路。

## 1. 通道清单与开销

| 通道 | 怎么起 | 单次开销 | 适合 | 坑 |
|---|---|---|---|---|
| **adb USB（默认）** | USB 线 | ~20ms | 一切常规操作 | 要插线、要授权 |
| **adb 无线调试** | 设置→开发者选项→**无线调试**（Android 11+） | ~30ms，握手一次 | 线在床边/机子在别的房间 | Wi-Fi 切网络或锁屏会掉线，IP 会变；配错端口表现为 `connect` 后立刻 offline |
| **`adb tcpip 5555`** | 必须先 USB 连着再切 | 一次性 | 想把现有机器变成无线 | **会重启 adbd，所有正在跑的 adb shell 会话全断**。别的任务正在用 adb 时不能跑 |
| **scrcpy** | `scrcpy` / `scrcpy -s SER` | 视频流，帧级 | 看画面、演示、录屏 | 对「读文字」没有优势，还是得 OCR 或看帧；延迟比 dump 高 |
| **uiautomator2 (u2/atx)** | `pip install uiautomator2` + 装一个 tester APK | ~50ms，但**直接给 Python API** | 长期脚本、要 `find_element` 语义匹配 | 要多装几 MB APK；版本升级会漂 |
| **AccessibilityService** | 用户手开无障碍服务 | 实时事件 | 系统级/游戏界面 | 要人工开开关，自动化流程里很烦，默认别用 |

## 2. 怎么用无线调试（Android 11+）

```bash
# 手机上：设置 → 开发者选项 → 无线调试 → 使用配对码配对设备
# 记下 配对码、IP、端口（形如 192.168.1.7:37211），和 已连接里的 192.168.1.7:5555

adb pair 192.168.1.7:37211      # 输入 6 位配对码
adb connect 192.168.1.7:5555
adb devices                      # 出现 192.168.1.7:5555 device 即成
```

坑位：

- `pair` 和 `connect` 是**两步**，配对成功后 `connect` 的端口是 **5555**，不是配对时那个端口。
- 断线重连：`adb disconnect` 后重新 `pair` + `connect`（配对码会刷新，别复用旧码）。
- 手上有 USB 时也能无线连，两者不冲突；但 `-s` 要写**无线那串**（`adb -s 192.168.1.7:5555 shell ...`），否则同机双线路会报 `more than one device`。

## 3. `adb tcpip 5555` 的中断风险（重点）

```bash
adb tcpip 5555        # 重启 adbd，切到 TCP 模式
adb connect 192.168.1.x:5555
```

**副作用**：`adb tcpip` 会 kill 并重启 `adbd`，所以：

- 所有 `adb shell` 长连接会断开；
- 别的会话正在 dump（写 `/sdcard/ui.xml`）会写一半失败；
- 手机上正在跑的自动化（含本 skill 的轮询）会集体报错。

> 有人在用 adb 时（多任务并行、有后台自检脚本）**不要**执行这条。无线调试是更好的选择，因为它不需要重启 adbd。

## 4. WebView 里的 DOM：一般拿不到

顺带一个常踩的坑：很多人想「用 CDP 直接读 WebView 的 DOM」，路子是

```bash
adb forward tcp:9333 localabstract:chrome_devtools_remote
curl http://localhost:9333/json
```

但这条路要求 App **开了 WebContentsDebugging**（即用了 `webView.setWebContentsDebuggingEnabled(true)`）。
实测普通发布版 App 拿不到 —— `forward` 成功，但 `/json` 返回空。
**退路**：绝大多数情况 `uiautomator dump` 已经能拿到 WebView 渲染后的 `text` 和 `bounds`，压根不需要 DOM。

> 遇到"这是个 WebView 页面"就先放弃走 DOM 的念头，直接 dump。
> **但要注意时机**：dump 读到的是 WebView **渲染完成后**的文字，页面没加载完时只会回
> 工具栏/地址栏这类外壳元素，**看着像读不到，其实是读太早**。等 1~2 秒或轮询到元素数
> 稳定再读（实测 `example.com`：+2.8s 只有 4 条外壳，+5.1s 出现 13 条含正文，见 `facts.md` F25）。

## 5. 到底哪类界面读不到（实测，别凭印象）

`uiautomator dump` **能读绝大多数 App 的文字**：原生控件、WebView 网页正文、系统弹窗都在内。
真正读不到的，是**本来就没有文字节点的画面**：

| 界面类型 | dump 读得到吗 | 说明 |
|---|---|---|
| 原生控件（设置、短信、联系人、时钟、文件管理器…） | **能** | 文字 + 坐标齐全，主通道 |
| WebView 网页 | **能**（等加载完） | 读渲染后的文字；读太早只回外壳，见 F25 |
| 系统弹窗 / 权限框 | **能** | 整段说明 + 按钮坐标都有 |
| 画布 / 游戏 / 视频 / 纯图片列表 | **不能** | 没有文字节点，走 §2.4 截图视觉兜底 |

> 换句话说：**"读不到"多数时候是"焦点被抢"或"读太早"，不是这个 App 不支持。**
> 先排除这两种，再谈"真的读不到"。

## 6. 选择规则（一句话）

> 先 USB adb；线不方便就无线调试；要语义匹配元素就 u2；要看画面才 scrcpy；
> `tcpip 5555` 只在确认没人并发用 adb 时执行。
