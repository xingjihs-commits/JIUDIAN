# 知识库阅读指南

> 这是"原厂门锁系统"的知识核心。你只需要读综合成果.md 一个文件就能掌握全貌。

## 阅读顺序

1. **`综合成果.md`** ← 必读。一份文档看完所有（42KB，20-30 分钟）
2. **`卡数据体系.md`** ← 需要看卡结构细节时读
3. **子目录**（按需阅读）：
   - `加密体系/` — 六层加密细节
   - `卡型样本/` — 14 种卡型的真实样本 payload
   - `厂家事件/` — 远程锁机/解锁全过程记录
   - `破壁档案/` — 破解历程（v1.0 → v5.0）

## 新旧文件对照

| 新位置（权威） | 旧位置（已删除） |
|:---------------|:---------------|
| `knowledge/综合成果.md` | 旧: `tools/dev/_v9_knowledge_base/INDEX.md` |
| `knowledge/加密体系/` | 旧: `tools/dev/_v9_crypto/` |
| `knowledge/卡型样本/samples/` | 旧: `tools/dev/_v9_knowledge_base/samples/` |
| `knowledge/破壁档案/` | 旧: `reports/card_lock_wall_history/` |

## 注意

- `tools/dev/_v9_crypto/_cards_all_types.py` 是研发工具，**不是生产代码**。生产用 `BrandPayloadFactory`
- `documents/` 目录是中间分析报告，知识已被综合成果覆盖

> 最后更新：2026-06-08
