# 项目变更日志

> 由所有 AI 共同维护，每次代码改动后追加一条。
> 老板说"固化"时，从此文件生成 checkpoint 地图。

---

## [2026-06-08 01:30] 本对话成果 — 黑盒架构梳理 + 跨品牌自动探测规划

### 做了什么
- **梳理了V9之外的所有"调DLL测卡"相关代码**
- **明确"吃掉下一个品牌"仅需 8 小时工程时间**（桥泛化2-3h + DLL探针4-5h + 端到端测试1h）
- **产出全架构规划**：详见 `.cursor/plans/黑盒对接pms全架构规划.plan.md`

### 新 AI 执行入口
1. 读 `PLAYBOOK.md` 获取六步法背景
2. 读 `.cursor/plans/黑盒对接pms全架构规划.plan.md` 执行

## [2026-06-08 01:10] 目录整理 + 文档体系重建 — 新 AI 进门即知全局

### 做了什么
- **新建 3 个核心入口文档**：
  - `PLAYBOOK.md` — 六步法标准化"吃掉新品牌"的作战手册
  - `README.md` — 目录说明和新 AI 入门指引
  - `knowledge/README.md` — 知识库阅读指南，标注新旧文件对照
- **更新全局路由**：`context-router.mdc` V9 路由从 `tools/dev/_v9_knowledge_base/INDEX.md` → `knowledge/综合成果.md`
- **更新 AGENTS.md**：所有旧路径指向 `knowledge/`，补充"吃掉战略当前状态"关键事实
- **更新 综合成果.md 第十二节**："吃掉战略"从未实现的 FrontDeskService 方案 → 当前实际架构（嵌入式桥接）
- **清理重复文件**：
  - 删除 `reports/card_lock_wall_history/`（与 `knowledge/破壁档案/` 重复）
  - 删除 `reports/expired_old_memory/`、`reports/theme_shots/`
  - 删除 `reports_extra/`（V6.0 补丁，已被 v12 取代）
  - 删除 `tools/dev/_v9_knowledge_base/`（已声明废弃）
  - 删除 `tools/dev/_v9_crypto/`（与 `knowledge/加密体系/` 重复）
  - 删除 `factory_software/华尔顿门锁系统2021版/`（与 `doorlock_system_2021/` 重复）
  - 删除 3 个 `.log` 文件和 `CardLock.lnk`
- **归档废弃文件**：
  - `tools/dev/_v9_archive/`（76 个已废弃实验脚本）→ `code/ARCHIVE/`
  - `documents/`（8 个中间分析报告）→ `code/ARCHIVE/`
  - `reports/` 中 40+ jsonl/txt 分析输出 → `code/ARCHIVE/`
- **更新引用**：卡型样本 INDEX、厂家事件 INDEX 指向 knowledge/ 新路径
- **新建 `code/README.md`** 说明历史工具用途

### 新 AI 标准流程
```
进原厂门锁系统 → README.md → knowledge/综合成果.md → PLAYBOOK.md → 故事线.md
```

## [2026-06-04 12:00] hotel_profile.json 导入路径 + 指纹库三轮识别

- **hotel_profile.json 导入路径打通**：
  - `LockTakeoverImporter.from_json()` — 读 Solid_Field_Box 产出 JSON 直接写 Solid 配置
  - `import_hotel_profile_json()` — 便捷一键导入函数
  - 接管向导新增「📄 导入 hotel_profile.json」按钮，带预览+确认
- **指纹库扩至三轮**：
  - 第三轮 `COM_PORT_FRAME_PROFILES`：17 种非 proUSB 品牌 COM 口帧前导码
  - `match_com_frame_preamble()` 前导码匹配函数
- 文档 `AI_handover_doorlock.md` 状态更新

## [2026-06-04 09:10] 对话存档

- 完成主控协议部署：`AGENTS.md` + `.cursor/rules/master-protocol.mdc`
- 清理根目录过时 MD → `archive/_legacy_docs/`
- 扫除 90 个一次性实验脚本，`tools/dev/` 从 150 减至 61
- 地图文件就绪：`PROJECT_MAP_HOTEL.md` / `PROJECT_MAP_DOORLOCK.md`
- 记忆持久化目录就绪：`memory/sessions/changelog.md` + `memory/checkpoints/`
- 老板纠偏指令就位：走向A / 走向B / 读地图 / 别拆
- 协议确认：新对话无需手动传背景，直接说任务即可

---

## [2026-06-04 09:08] AGENTS.md 新增老板纠偏指令

- 顶部新增四个三字指令：**走向A** / **走向B** / **读地图** / **别拆**。
- 老板说任意一个，AI 立即执行对应动作，无需记文件名。
- "走向A" = 老酒店套壳，"别拆" = 停止硬件操作。

---

## [2026-06-04 09:03] AGENTS.md 写入关键事实

- 把"套壳吃掉原厂"双轨战略直接写入 AGENTS.md（自动注入层），不再依赖 AI 主动翻阅外部地图文件。
- 补齐：已完成清单 / 门锁待完成 / 关键路径 / 禁区。新 AI 不读地图也能知道核心事实。

---

## [2026-06-04 08:57] 确认协议生效规则

- **新对话无需手动传背景**：`AGENTS.md` + `master-protocol.mdc` 在 AI 看到用户第一句话之前已自动注入。
- 用户只需在新对话中直接说任务，AI 从协议中自动获取身份、项目根、地图路径、禁区。

---

## [2026-06-04 08:52] 部署主控协议

- 创建 `AGENTS.md` — 每次对话自动注入的协议入口
- 创建 `.cursor/rules/master-protocol.mdc` — 全域行为约束（alwaysApply: true）
- 创建 `memory/` — 记忆持久化目录（sessions + checkpoints）
- C0/P0/P1/P2/P3 全部完成。门锁 14 卡已 10 张可发。地图见根目录 `PROJECT_MAP_*.md`
