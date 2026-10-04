# 数据层：直接读应用原始数据（高门槛备选，**默认不走**）

本 skill 主链路是**界面层**（dump → 坐标 → 操作）。这份 reference 只作为「界面层实在读不出来时的备选」写在这里，**不要进主流程**。

## 1. 思路

不经过界面，直接把 App 自己的数据读出来：SQLite、`shared_prefs`、私有 JSON、日志目录。
好处是结构化、稳定、不怕动画；坏处是要 root 或 App 可调试，且 App 一改版就失效。

## 2. 可行性速判（30 秒能判完就判）

```bash
adb shell pm path <pkg>                     # 有 base.apk 说明是真 App
adb shell run-as <pkg> ls /data/data/<pkg>  # 报 "not debuggable" → 正式包，多半拿不到
adb shell ls /sdcard/Android/data/<pkg>     # 外置私有目录，通常只有 cache/files
```

判定：

| 现象 | 结论 |
|---|---|
| `run-as` 能进（输出 `run-as: package not debuggable` 的反面） | 可调试包，有机会 |
| `run-as` 报 `not debuggable` | **正式签名包，数据层基本不可得** → 老老实实回界面层 |
| 有 root | 直接 `su -c cat /data/user/0/<pkg>/databases/*.db`，走 sqlite3 解析 |
| 只有 `.apk` 权限 | 回到界面层 |

## 3. 实测流程：一款正式签名的 App（未 root 机器，2026-10-02）

把命令跑一遍看它停在哪一步。**这几行命令是通用的，换成任何包名都照跑**，跑完就知道数据层通不通。

| 命令 | 这台机器上的结果 | 说明 |
|---|---|---|
| `pm path <pkg>` | 拿到 `/data/app/.../base.apk` | 正式包，不是可调试包 |
| `run-as <pkg> ls` | `run-as: package not debuggable` | **正式签名包，到这里就可以判死了** |
| `ls /data/data/<pkg>` | 无权限（未 root） | 正常 |
| `ls /sdcard/Android/data/<pkg>/` | 只有 `cache` + `files` | 外置私有目录没业务数据 |
| 在这些目录里 find `.db`/`.json` | 无 | 没有可结构化读的东西 |

结论：**正式发布的 App + 未 root 机器 = 数据层不可得**，别在这上面花时间。直接回界面层。

> 判定要点：`run-as` 那行输出里的 `not debuggable` 就是判据，不用真去翻目录。

## 4. 什么时候才值得走数据层

只在下面两种情况之一：

1. 机器 **root** 了（能直接读 `/data/user/0/<pkg>`）；
2. 目标 **是你自己开发/可调试的 App**（`android:debuggable` 或用了 debuggable 签名）。

否则代价（root 权限、解包、已知漏洞、改版即废）远超界面层 dump 那 2.5KB。

> 一句话：**能 dump 就别想数据层。**
