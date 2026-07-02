# 门锁适配器状态

| 品牌 | 适配器文件 | detect | initialize | 发客卡 | 发系统卡 | 状态 |
|:---|:---|:---:|:---:|:---:|:---:|:---|
| proUSB V9 | prousb_v9.py | ✅ | ✅ | ✅ | ✅ | 完整可用 |
| proUSB V10 | prousb_v10.py | ✅ | ✅ | ✅ | ❌ | 部分可用 |
| proUSB V11 | prousb_v11.py | ✅ | ✅ | ✅ | ❌ | 部分可用 |
| 通用 Profile 适配器 | generic_adapter.py | ✅ | ✅ | ✅ | ✅ | 部分可用 |
| CardLockAuto（pywinauto 寄生） | cardlock_auto.py | ✅ | ✅ | ✅ | ✅ | 部分可用 |
| 爱迪尔 Lock9200 | aidier_9200.py | ✅ | ❌ | ❌ | ❌ | 骨架 |
| 爱迪尔 Lock3200 | aidier_3200.py | ✅ | ❌ | ❌ | ❌ | 骨架 |
| 爱迪尔 通用 (MAINDLL) | aidier_maindll.py | ✅ | ❌ | ❌ | ❌ | 骨架 |
| 必达 IB | bida_ib.py | ✅ | ❌ | ❌ | ❌ | 骨架 |
| 力维 LevelLock | level_lock.py | ✅ | ❌ | ❌ | ❌ | 骨架 |
| 西容 SYRON | syron.py | ✅ | ❌ | ❌ | ❌ | 骨架 |
| 雅迪顿 | yadidun.py | ✅ | ❌ | ❌ | ❌ | 骨架 |
| 同创新佳 | tongchuang.py | ✅ | ❌ | ❌ | ❌ | 骨架 |
| 宝迅达 | baoxunda.py | ✅ | ❌ | ❌ | ❌ | 骨架 |

## 状态说明

- **完整可用** — 所有 5 列功能均正常工作
- **部分可用** — 核心功能可用（发客卡），部分卡类型或系统功能未实现
- **骨架** — 仅能 detect（识别到安装目录），initialize / 发卡等操作需通过通用 Profile 桥接完成，依赖现场配置
