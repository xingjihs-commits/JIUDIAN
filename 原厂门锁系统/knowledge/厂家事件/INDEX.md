# 厂家事件 — 华尔顿厂家远程操作全记录

> 记录华尔顿（Walton）proUSB V9门锁厂家通过UltraViewer远程锁机/解锁酒店系统的完整过程。

---

## 文件列表

| 文件 | 内容 |
|:-----|:------|
| [远程时间线.md](远程时间线.md) | 2026-06-03 ~ 06-04 厂家远程全过程 |
| [N系列EXE分析.md](N系列EXE分析.md) | CardLock-N8.9.1 / N02_d 授权注入器分析 |

---

## 一句话

厂家通过UltraViewer远程连接前台电脑，发送 **N系列EXE**（CardLock-N8.9.1 / N02_d）到桌面，
通过bat脚本执行修改 `System.ini` 的LD/LDO字段来控制授权到期/续期。
文件名本身是密钥体系的组成部分，跑完后bat自毁删除，只剩Windows Prefetch痕迹。

---

## 资源文件索引

| 资源 | 路径 |
|:-----|:------|
| UltraViewer连接日志 | `remote_assist/远程/UltraViewer/ConnectionLog.log` |
| UltraViewer聊天记录 | `remote_assist/远程/UltraViewer/聊天记录/` |
| N系列Prefetch碎片 | `samples/working_copy/` |
| 被锁System.ini备份 | `remote_assist/远程/proUSB_DBBak/N022B_5EE69782.TXT` |
| 视频录屏 | 前台电脑(PC-202508271126)上的 cardlock_cut_12min.mp4 |

> 原始综合分析报告已被 `knowledge/综合成果.md` 覆盖
