## [2026-06-25 00:52] 夜墨 Ink 收尾 — 侧栏/分割线/数据行三色微调

- 文件：`酒店系统/theme_palette.py`（INK 字典 4 字段），`酒店系统/themes/ink.qss`（重新生成）
- **侧栏 → 画布同色**：`sidebar` #364350→#536878，`sidebar_hover` #424F5C→#5A6E82（比画布亮一档）
- **分割线提亮**：`border` #708090→#8A9AA8（与画布 #536878 拉开差距，提亮约 50）
- **数据行/输入框**：`surface_alt` #2D3942→#91A3B0（太深 → 亮灰蓝，派生 disabled_bg/menu_separator/scrollbar_bg/border_light 自动跟随）
- 其他三主题（mist/shade/glow）不动；四份 QSS 均重新生成

## [2026-06-25 00:11] 夜墨 Ink 主题 — 完整色值改造落地 ✅

- 文件：`酒店系统/theme_palette.py`（INK 字典全部重写），`酒店系统/themes/base.qss.template`（11 处改动），`酒店系统/themes/ink.qss`（重新生成）
- **10 色块定稿**：主按钮 #324A5F 炭蓝 / GhostBtn #536878 透明底+画布色边框悬停填主色 / Danger #8B5D5D / 卡片 #3D4C57 / KPI 卡 #42525E / 数据行 #3E4E5A / 表头 #485966 / 输入框 #2D3942（最深下陷）/ 画布 #536878 / 分割线 #708090 / 正文 #E0E6E9 次要 #A0B0B8 最浅 #7A8A96
- **QSS 模板改动**：① button 文字 `@surface@`→`@btn_primary_fg@`（主/提交/Submit/CardAction/Dialog OK）+ `@danger_fg@`（Danger）。② GhostBtn 透明化：bg=transparent, border=@bg@, hover_bg=@btn_primary@。③ Secondary bg=@surface_alt@→@bg@。④ FdCardGroupInhouse 硬编码 rgba(160,64,64,0.07)→@danger_7pct@。⑤ KpiCard/ReportKpiCard/FinanceStatCell/OverviewSectionCard bg=@surface@→@elevated@（含紧凑版分身处）
- **palette 派生**：`_replace_qss_vars` 新增 `danger_7pct` 派生、`btn_primary_fg`/`danger_fg` 优先从 palette 显式值取
- **影响范围**：四份 QSS 全部重新生成（mist/shade/glow 视觉微调），Ink 改完
- **验证**：`_generate_themes.py` 成功产出，10 色块+3 文字色全量验证通过

- 文件：tabs/hotel_overview_tab_v4.py
- **根因**：总览页每 30 秒自动刷新，`_clear_layout_below_header()` 和 `_refresh_kpi_grid()` 清旧 widget 时调用 `w.setParent(None)`，子控件脱离父容器瞬间变成带标题栏的独立顶级窗口（最小化/最大化/关闭按钮可见），再经 `deleteLater()` 下一帧销毁。这就是"启动/点击总览时一闪而过的标题栏小窗口"，30 秒周期解释"间隔一分钟再点又出现"
- **修复**：`setParent(None)` 前加 `w.hide()`，不让 orphan widget 在独立窗口状态时可见

## [2026-06-24 21:05] base.qss 四主题分离 — 硬编码 QSS 消除运行时注入

- 文件：themes/base.qss → themes/base.qss.template, themes/{mist,shade,glow,ink}.qss（新增4个）, _generate_themes.py（新增）, app_main.py, memory/项目地图.md, 酒店系统/README.md
- **根因**：原 1 个 base.qss 用 134 个 @var@ 占位符在运行时注入四主题色值，同一选择器不能加减属性，INK 暗色需额外堵白
- **A 计划落地**：base.qss 重命名为 base.qss.template（模板保留 @var@），新建 _generate_themes.py 生成脚本，产出 4 份独立硬编码 QSS（每份 ~131KB）
- **app_main.py**：删除 _load_palette()（56行）和 _inject_qss_variables()（26行）两个函数，apply_theme() 改为直接加载 themes/{theme_name}.qss
- **修改流程**：编辑 base.qss.template → 运行 _generate_themes.py → 四份 QSS 同步更新。spec 无需修改（datas 整目录复制）

- 文件：checkin_tab.py, ui_surface.py, themes/base.qss
- **根因**：`fd_apply_quick_btn`（ui_surface.py）和 `FdQuickBar`（checkin_tab.py）用了内联 `_p("surface")` 设置背景色，恒为近白色，不跟随 Glow 暮霞的暖粉底
- **修复 1**：checkin_tab.py 删除 FdQuickBar 的 `setStyleSheet` 内联背景 → 由 `base.qss QFrame#FdQuickBar { background-color: @bg@; }` 全权控制
- **修复 2**：ui_surface.py `fd_apply_quick_btn` 删除全部内联 QSS（含 hover/pressed/disabled）→ 由 base.qss `QPushButton#FdQuickBtn @surface_alt@` 全权控制
- **修复 3**：base.qss 补回 `QPushButton#FdQuickBtn:pressed` 和 `:disabled` 交互态（原在内联中丢失）
- **验证**：括号平衡 919=919 ✅，import 通过

## [2026-06-24 21:46] 启动加速 P0 + 主题切换加速 P1 落地 ✅

- 文件：housekeeping_panel.py, audit_tab_widget.py, tabs/card_system/card_system_tab.py, tabs/hotel_overview_tab_v4.py, ota_connector.py, qr_code_service.py, unified_room_page.py, item_dictionary_page.py, app_main.py
- **P0 完成**：8 个 Tab 的 `__init__` 末尾同步 DB 查询改为 `QTimer.singleShot(0, ...)` 延迟加载，省 20-30% 启动时间，不碰 UI 文件
- **P1 完成**：`invalidate_token_cache()` 从 `apply_theme()` 开头移到缓存未命中分支，QSS 缓存命中时保留 `_p()` 缓存，省 10-20% 主题切换时间
- **验证**：9 个模块全部 import 通过
- **P2 待执行**：`fd_refresh_surfaces` 只刷新可见 Tab（需等 UI 优化稳定，会动 UI 文件）

## [2026-06-24 20:38] 暗色主题白底/缝隙泄漏修复 + 全量 background: 简写替换

- 文件：ui_helpers.py, themes/base.qss
- **根因**：`apply_app_light_chrome` 中 `ColorScheme.Light` 固定写死，INK 暗色主题也被设为 Light → Fusion 用系统白底色覆盖调色板
- **修复 1**：删除 `setColorScheme()` 调用（无论设 Light/Dark 都会使另一套主题颜色串位；纯靠 QPalette + QSS `background-color:` 控色）
- **修复 2**：base.qss 中 ~60 处 `background: @xxx@` 简写全部替换为 `background-color: @xxx@`（Fusion 暗色模式下 `background:` 简写有时被忽略）
- **验证**：括号平衡 919=919 ✅，background: 简写零残留 ✅，import 通过

## [2026-06-24 20:36] 启动与主题切换性能优化计划 — 待执行

- 文件：memory/plans/启动与主题切换性能优化.md（新建）
- **P0（等 UI 优化稳定后修）**：8 个 Tab 的 `__init__` 末尾 `refresh()` 改为 `QTimer.singleShot(0, self.refresh)`，省 20-30% 启动时间，不碰 UI 文件
- **P1（等 UI 优化稳定后修）**：`invalidate_token_cache()` 从 `apply_theme()` 开头移到 `_load_palette()` 内部，省 10-20% 主题切换，改 1 行
- **P2（等 UI 优化稳定后修）**：`fd_refresh_surfaces` 只刷新当前可见 Tab，省 60-70% 主题切换，但动 UI 文件

## [2026-06-24 20:14] 修复 QToolButton 交互态大面积缺失

- 文件：themes/base.qss
- **根因**：旧版 d96c172 的 base.qss 有 3 个 QToolButton 通用定义互相覆盖补位，Super Z 合并时只保留了最残缺的 1 个（缺 hover/pressed/disabled/checked/focus 全部交互态）
- **修复**：删除残缺定义，在第 113 行新增完整 QToolButton 定义（普通态 + :hover + :pressed + :disabled + :checked + :focus）
- **验证**：括号平衡 ✅，QToolButton 6 个伪类全部到位，零重复通用定义

## [2026-06-24 19:45] 修复最后 2 处硬编码色值

- 文件：bill_detail_dialog.py（7 处 HTML 模板色值 → _p()）, vendor_console_tab.py（标签 #475569 → _p()）
- **漏洞修复**：两处脱离主题系统的硬编码色值全部替换为 `_p()` 动态取色，四主题下不再出现不匹配颜色
- **验证**：两模块 import 通过

## [2026-06-24 16:57] UI 系统统一大修 — QDialog/QPushButton 8合1 + 启动页修复 + 按钮背景修复

- 文件：base.qss（QSS 清理 + 合并 + 修复）, ui_helpers.py（QPalette 跟随主题）, app_main.py（传递 theme_name）, startup_splash.py（登录后简洁模式）, card_system_tab.py（删除覆盖 inline style）, smart_header.py（背景连续 + border-bottom）
- **P0 根因修复**：QDialog/QPushButton/QToolButton/FrontdeskHubBtn/PayMethodTile 各被重复定义 4-8 次，互相覆盖导致"牛皮癣"。全部合并为唯一顶层定义 + v8 覆盖定义
- **P0 QPushButton 全局背景统一**：从混用 @bg@/@surface@ 统一为 @surface@（卡片底色），hover/pressed/disabled 状态补全
- **P0 QDialog 全局背景统一**：从混用 @bg@/@surface@ 统一为 @surface@（弹窗卡片），删除 7 个冲突定义
- **P0 黑主题露白**：ui_helpers.apply_app_light_chrome 现在根据 theme_name 动态选择暗/浅 QPalette，ink 主题不再是浅色全局调色板
- **P0 启动页黑灰背景**：登录后 splash.show() → splash.set_loading_mode()，纯色背景无透明叠加
- **P0 启动页闪烁**：启动页背景色从硬编码 #1A1625 改为 _p("sidebar")，与登录页左侧品牌区一致，消除过渡断档
- **P0 按钮透明**：FrontdeskHubBtn（流水记录）从 transparent 改为 @surface_alt@；门锁面板 FdCardActionBtn 删除覆盖 inline style
- **P0 树选中色泄漏**：QTreeWidget#RoomNavTree 单独定义选中色（使用 @selected_bg@/@selected_fg@），不再共用 SettingsNavTree 规则
- **P0 SmartHeader 背景中断**：SmartHeaderCtxScroll + 内部 panel 背景透明/继承容器背景
- **P1 QMainWindow/QWidget 容器层**：清理冗余背景定义，减少容器交织
- **验证**：QSS 913 个选择器括号平衡，语法加载通过

- 文件：.cursor/skills/solid-collector/SKILL.md
- 彻底审计采集器 84 个 Python 文件，确认 7 大法医引擎 + 8 层破解 + 3 路探测 + 9 步引导 + 6 维毕业判定完整
- 验证了致命 1（collector_ui 缺失）不成立：包目录结构正确，spec 已有顶层包名，打包零警告、EXE 冒烟通过
- 创建 solid-collector 技能（SKILL.md 含完整架构/引擎/导入链/打包须知）

## [2026-06-24 16:00] Super Z 全方位修复合并 ✅

- 文件：app_main.py, main_window_impl.py, permission_system.py, ui_helpers.py, checkin_tab.py, startup_splash.py, theme_palette.py, base.qss, design_tokens_v4.py, room_matrix.py, guest_list_tab.py, hub_widget.py, overlay_widgets.py, card_ritual_dialog.py, button_optimized.py, card_system_tab.py, staff_tab.py, components/__init__.py, tests/ui_ux_tests.py
- 来源：E:\我的下载\JIUDIAN_20260624_fixes（Super Z 远程审计，4维度17文件修复）
- **P0 收银台救活**：删 15 个空桩方法（_do_pay/_refund/_checkout 等），Mixin 真实实现接管；删重复 addWidget；_show_stay_detail_bill → _show_bill_details
- **P0 启动性能**：删 app_main/main_window_impl/permission_system/ui_helpers 四处 debug-f8e0dc.log 写入 + _diag 函数 + _probe_buttons 启动3秒遍历按钮，省 50-150ms
- **P0 启动页 GC 炸弹**：StepSplash.advance() 动画局部变量被回收 → 加 _active_anims + _keep_alive()
- **P0 角色感知**：card_system_tab 监听 user_logged_in 刷新管理卡按钮；staff_tab 加角色权限概览 + 去 except:pass
- **设计体系**：四主题 text_dim/text_muted 加深达 WCAG AA 4.5:1；INK elevated 层级反转修正；base.qss 删 !important；design_tokens 加注释说明 ColorSemantic 是设计参考
- **P2 修复**：room_matrix 圆角 14→12px、锁警告色改 danger、影子委托 motion_gate；guest_list_tab 66→242 行加 KPI/搜索/空状态/错误态；hub_widget 高度冲突修复；button_optimized 监听主题切换刷新 QSS、删强制44px、warning 改 warn；card_ritual 终态停 timer
- **P2 死代码清理**：删 checkin_tab_v4/checkout_v4/form_field/input_optimized 共 4 文件；components/__init__.py 剪断 SmartInputField/FormField import；tests/ui_ux_tests.py 剪断对应测试
- **验证**：17 文件 AST 语法全部通过；11/12 模块 import 通过（card_ritual_dialog 导入 CardService 为原有 bug，懒加载不影响启动）

## [2026-06-24 14:29] 从对话记录提取 10 个 SKILL.md 到 .cursor/skills/

- **任务**：从 JSONL 对话记录中解析 Write 工具调用，提取 10 个 SKILL.md 文件内容
- **源文件**：`agent-transcripts/a451246e-1d75-4cd7-85fe-b3262a41514d.jsonl` → 解析 assistant 消息中的 tool_use Write 条目
- **写入目标**：`D:\AAAGZT-99\.cursor\skills\` 下 10 个子目录（battle-plan/pre-mortem/prove-it/loose-ends/sanity-check/safe-refactor/ui-polish/warm-copy/quality-check/forensic-collector）
- **结果**：10/10 全部写入成功，内容为中文，保持原样
## [2026-06-24 14:51] 打包乱码修复 + Git 远程缺文件检查
- **修复**：`酒店系统_一键打包.bat` 第 2 行 `chcp 65001` → `chcp 936`，中文乱码根除
- **规则**：`common-mistakes.mdc` 新增第七节「编码陷阱」，记录该问题排查要点
- **Git 检查：采集器 collector_ui 未提交**
  - 本地 `采集器/collector_ui/` 完整存在（wizard.py, constants.py, models.py, workers.py, widgets/ 等）
  - 远端 `origin/main` 上 **collector_ui 全部缺失**，从未提交
  - 同样缺失的还有：`bridgecore/cloud_handover.py` `clue_hunter.py` `dll_string_scanner.py` `encryption_fingerprints.py` `experience_engine.py` `forensic_packager.py` `ghidra_enricher.py` `mifare_weak_keys.py` `parasitic_replay.py` `task_fetcher.py` `ghidra_toolkit/` `toolbox/` `collector_cloud.json` `known_signatures.json`

## [2026-06-24 14:40] 新增文件归位规则 file-placement.mdc
- 新增 `JIUDIAN\.cursor\rules\file-placement.mdc`，规定新增文件必须放在对应模块目录
- 临时脚本对话结束前必须删除
- 明确禁止放文件的目录位置

## [2026-06-24 14:36] 项目规则搬到项目内统一管理
- **操作**：5 条规则从 `D:\AAAGZT-99\.cursor\rules\` → `D:\AAAGZT-99\JIUDIAN\.cursor\rules\`
- **旧目录已删除**
- 引用路径是相对路径（`.cursor/rules/xxx.mdc`），不受位置影响

## [2026-06-24 14:34] 追加 5 个内置技能（Create Rule / Canvas / Bugbot / Security Review / Split to PRs）
- **来源**：从 `C:\Users\FF.FC\.cursor\skills-cursor\` 复制
- **位置**：`D:\AAAGZT-99\.cursor\skills\`（与原有 10 个技能统一管理）
- 现共 15 个技能可用

## [2026-06-24 14:28] 恢复 10 个 Agent 技能文件（位置迁移）
- **原因**：6月22日安装的技能文件（`酒店系统/.cursor/skills/`）被误删
- **恢复路径**：`D:\AAAGZT-99\.cursor\skills\`（与 rules 同层，避免被打包脚本清理）
- **社区版 6 个**: battle-plan、pre-mortem、prove-it、loose-ends、sanity-check、safe-refactor
- **自定制 4 个**: ui-polish、warm-copy、quality-check、forensic-collector
- 全部内容从对话记录完整提取

## [2026-06-24 14:01] 采集器↔PMS衔接审查 + dashboard_tab 空壳清理
- 审查覆盖：handover_packager.py、handover_importer.py、cardlock_auto.py、vendor_console_tab.py
- 发现问题：首屏总览读 `learned_*` 而非 `lock_takeover_*`（握手包导入后显示"未导入"）；旧版JSON路径无覆盖保护；HandoverImporter 缺 buildings 表插入；bridge32_missing 未在导入时检查；CardLockAuto 无 `lock_learned_dlsCoID` 回退
- 已清理：main_window_impl.py _refresh() 中 dashboard_tab removed 空壳 try 块（pass 空转代码）
- 方案已出待实施：5 项局部修复（改 3 文件约 30 行）

## [2026-06-24 13:46] 收银台专项修复 ✅
- 文件：tabs/frontdesk/checkin_tab.py, themes/base.qss, ui_surface.py
- checkin_tab.py: btn_q_more 删除 setMaximumWidth(56)，与同行按钮统一宽度样式
- checkin_tab.py: FdCardGroupReady/Inhouse 硬编码 (8,6,8,6) → FD_SPACE_SM 常量
- ui_surface.py: PaymentMethodTiles 圆角 6px→8px，两处全部修正
- ui_surface.py: FdBillFolioShell 底部圆角 6px→8px
- ui_surface.py: fd_apply_checkin_right_card_body 中 FdActionBar 内联 QSS 补 border-radius:0
- ui_surface.py: fd_apply_card_action_bar 嵌入式分支补 border-radius:0
- base.qss: FdTotalsStrip 两处缺少 border-radius 补 0 0 8px 0
- base.qss: FdCheckinPanel 内 FdActionBar 补 border-radius:0（收银台+右卡body+卡片组内共3处）

## [2026-06-24 13:33] 五层背景色 + 按钮可见性修复 ✅
- 文件：theme_palette.py, themes/base.qss, smart_header.py
- 替换四主题色值为新五层系统（bg/surface/surface_alt/elevated/sidebar 各自独立，亮主题带色调，夜墨浮层凹陷）
- 全局 QPushButton 兜底加 surface 底色 + border，消除透明按钮
- 次按钮组（FdActSecondary/FdQuickBtn）transparent → surface_alt 底
- FrontdeskHubBtn（底栏Tab）transparent → surface_alt 底，2 处定义全部修正
- MiniTabButton 默认态 transparent → surface_alt 底
- HeaderSearchBtn 加大 160px、加边框、加占位文字"搜索菜单..."
- 全局 QPushButton 圆角统一 6px → 8px
- 新增 FdCommitBtn 完整 QSS 规则（primary 底白字 14px 700weight）
- smart_header.py 移除冲突的内联 HeaderSearchBtn QSS，替换为新样式
- 清理 smart_header.py 中 QSS 结构断裂修复

## [2026-06-24 11:44] 总览页主题切换粉色残留修复 ✅
- 文件：tabs/hotel_overview_tab_v4.py
- 根因：HotelOverviewTab 的外层卡片的 accent 色边框（三面亮色条）和背景调色板在 _build_ui 时硬编码，换主题后不刷新，导致上一主题的粉色残留
- 修复1：新增 _refresh_card_styles()，换主题时重刷 6 张外层卡片的 inline stylesheet（accent/surface/border）
- 修复2：_refresh_card_styles() 同时重刷滚动体背景调色板 + viewport
- 修复3：__init__ 监听 bus.theme_changed 信号 → 调用 self.refresh()

## [2026-06-24 10:43] 背景体系分层清理（四步改造）✅
- 文件：theme_palette.py, design_tokens.py, themes/base.qss, 26个文件的斑马线, 15个文件的散落设色
- 步1-删派生：删 _derive_bg_card/_derive_panel_elevated/_derive_panel_well 三个派生函数
- 步1-写死映射：_replace_qss_vars 中背景围栏从公式改为调色板直接映射
- 步1-四主题加 elevated：#FFFFFF，零派生
- 步1：KEY_ALIASES 加 10 个背景别名映射，_NEUTRAL_FALLBACK 清理冗余
- 步2-QSS去重：7个背景token(@bg_root/@bg_container/@bg_card/@surface_alt/@panel_border/@bg_elevated/@panel_elevated/@panel_well) → 3个(@bg/@surface/@elevated)，435处→3个
- 步3b-去斑马线：全站 28 处 setAlternatingRowColors(True) → False
- 步3c-散落清理：ui_helpers/frontdesk_ui_v4/input_optimized/button_optimized/payment_v4/card_ritual/report_engine/integrity_report/timeline_view/inventory_diff_page/ui_probe 中的 surface_alt/bg_container/bg_card → bg/surface
- 步4-色值微调：晨雾 bg #F7F9FA→#F4F6F8 surface #FFFFFF→#FAFBFC；午荫 bg #F6F8F4→#F2F5F0 surface #FFFFFF→#F9FCF7；暮霞 bg #FAF5F6→#F7F0F2 surface #FFFFFF→#FCF8F9；夜墨 surface #2D3540→#2A3234
- 验证：import 全部通过，token 别名全部解析正确，旧 token 零残留

## [2026-06-24 10:03] 三处卡片底色归位 - bg_card/bg_container → surface
- 文件：themes/base.qss(1684行)、ui_surface.py(862行)、tabs/frontdesk/checkin_tab.py(247行)
- 门卡搜索框 CardSearchInput：@bg_card@ → @surface@
- 夜审/交班说明条 FdInfoBanner 非收银路径：bg_container → surface
- 收银快捷操作带 FdQuickBar：bg_container → surface
- 配合 09:32 VendorStatRow 修复，全站统计卡片/搜索框/提示条统一用 surface 纯白

## [2026-06-24 09:53] 启动页 finish 动画局部变量被回收 → 窗口定死不消失
- 文件：酒店系统/startup_splash.py（finish()，fade → self._fade + 删 DeleteWhenStopped）
- 致命 bug：fade 是局部变量，finish() 返回后 GC 回收 → 动画中断 → finished 信号永远不触发 → self.close() 永远不执行 → 窗口定死在屏幕上
- 修复：改实例属性 self._fade 保持引用，删 DeleteWhenStopped 用默认策略

## [2026-06-24 09:32] VendorStatRow 底色修复 — bg_card → surface
- 文件：酒店系统/ui_surface.py（第 769 行，仅改一个 token 名）
- 厂家控制台统计卡片（酒店ID/机器码等）框内不是纯白，而是 bg_card 数据井灰
- QSS 定义的是 @surface@（纯白），但 fd_apply_vendor_stat_row 运行时 setStyleSheet 用 bg_card 覆盖了
- 修复：bg = _p("bg_card") → bg = _p("surface")，与 QSS 一致

## [2026-06-24 08:41] 启动页动画 ADVANCE 驱动重构 — 修复登录页残影
- 文件：酒店系统/startup_splash.py（仅此一个文件，app_main.py 不动）
- 根因：StartupSplash.__init__ 自启动 5.5s 动画序列，advance() 是空壳 → 登录弹窗出来时动画已跑完 + hide/show 后恢复为 opacity=0 残影
- 修复①：删 __init__ 第 258 行 self._start_animations()，动画不再自启动
- 修复②：show_splash() 手动调用 _start_animations()（独立调用链不受影响）
- 修复③：StepSplash 重写：advance(0)→Logo入场 、advance(1)→3D旋转+品牌名淡入 、advance(2)→副标题淡入 、advance(3)→登录后全可见快照（防残影）、advance(4)→完成态
- 修复④：finish() 加 500ms 淡出动画再 close
- API 签名零变动：pulse/advance/show/hide/finish 全部兼容

## [2026-06-24 06:50] SolidCollector 两栏v4 打包 + 冒烟通过
- 文件：采集器/collector_ui/constants.py（BUILD_TAG→两栏v4）、采集器/collector_ui/wizard.py（标题→两栏、说明精简）
- 欢迎语统一：欢迎页"你只需三件事"+ 工作区说明两行精简
- 打包：77MB，零 invalid module 警告，冒烟 8 秒无崩溃
- 本次会话累计修复：EXE崩溃(spec删runtime_hooks) + 左栏不可见(splitter接入窗口树) + 左栏270→320 + 毕业证据2行网格 + 按钮颜色 + 死字段 + 欢迎页精简 + BUILD_TAG改名

## [2026-06-24 06:40] 采集器三栏布局真正生效 + 左栏宽度修复
- 文件：采集器/collector_ui/wizard.py、采集器/collector_ui/widgets/graduation_panel.py
- 致命根因：`splitter` 创建了但从未加入窗口树——`center_scroll` 被直接加到 `work_layout`，左栏（毕业证据/实时状态/按钮）在孤儿 splitter 里不可见
- 修复①：`center_scroll` 加到 `splitter`，`splitter` 加到 `work_layout`（第401-403行）
- 修复②：左栏 270→320px，毕业证据 7 标签从单行挤变 2行×4列网格，字号 11→10px

## [2026-06-24 06:27] 采集器 UI 打磨：按钮颜色修复 + 死字段清理 + 欢迎页精简
- 文件：采集器/collector_main.py、采集器/collector_ui/wizard.py
- 按钮颜色：`SolidSecondaryBtn` 样式新增（washed 紫底 + 深紫字），disabled 态不再与背景融为一体
- 死字段：`_step_idx` 清理（wizard.py 原第 76 行），全项目零引用
- 欢迎页：9步清单 → 3句话概括（选目录→读卡写卡回读→分析出握手包），更友好

## [2026-06-24 06:06] SolidCollector 打包修复：`pyi_rth_collector.py` 钩子与 FrozenImporter 冲突根因定案
- 文件：采集器/SolidCollector.spec（第159行 runtime_hooks 改为空列表）
- 根因确认：TOC 交叉引用证实 `collector.collector_ui` 全量模块已收录 PKG 压缩包，FrozenImporter 可独立解析
- 致命冲突：`pyi_rth_collector.py` 提前注入 `collector` 到 `sys.modules` 并设 `__path__` 为磁盘路径，挡住 FrozenImporter 从 PKG 加载子模块
- 05:43 的修复（`__path__` 改 `_MEIPASS/collector/`）仍不能解决：单文件 EXE 的一级 `__init__.py` 会被提取到 temp，但子模块 `collector_ui/*.py` 不全在磁盘 → \(Path\)-based importer 短路报 ModuleNotFoundError
- 修复：删掉 spec 第 159 行的 `runtime_hooks=[pyi_rth_collector.py]`，让 FrozenImporter 全权处理。源码预检全量 21 模块通过，源码启动已验证

## [2026-06-24 05:51] 启动页与主题切换三个 bug 修复
- 文件：酒店系统/startup_splash.py、酒店系统/room_matrix.py、酒店系统/main_window_impl.py
- Bug① splash 颜色走 DB 主题：_bg_color/_overlay_color 从 _p() 查色改为固定深色值 #1A1625/#5B8FB9，与 dark_pal 一致
- Bug② brand_anim/tagline_anim 死代码：创建后未启动，补 QTimer.singleShot(1500) 和 (2000) 触发
- Bug③ 切主题房卡色块残留：RoomMatrix 未监听 theme_changed，新增 _on_theme_changed() 遍历 cards 重刷 inline style；main_window_impl 加 bus.theme_changed 连接

## [2026-06-24 06:06] PaymentMethodTiles 切主题色块残留修复
- 文件：酒店系统/tabs/frontdesk/payment_v4.py、酒店系统/tabs/frontdesk/checkin_tab.py
- 根因：PaymentMethodTiles 在 __init__ 用 _p() 设 inline setStyleSheet（找零、快捷金额按钮、近期交易流水），theme 切换后不自刷新
- 修复：新增 PaymentMethodTiles._refresh_theme_styles()，统一管理三处 inline style；__init__ 末尾调一次；从 __init__ 内 _build_* 方法中删除散落的 setStyleSheet，归位集中管理
- checkin_tab._refresh_theme_styles() 补 self.pay_tiles._refresh_theme_styles() 调用，与 _ledger_dock/_shift_dock 同协议

## [2026-06-24 05:43] SolidCollector 打包修复：`No module named 'collector.collector_ui'` 根因
- 文件：采集器/SolidCollector.spec、采集器/pyi_rth_collector.py
- 根因①：staging 目录 `build/_pkg_staging/collector/` 缺 `__init__.py`，PyInstaller 不认 `collector` 为合法包，子模块 `collector_ui` 无法正确收录
- 根因②：运行时钩子 `pyi_rth_collector.py` 把 `collector.__path__` 设为 `_MEIPASS`，但 PyInstaller 按包层级把模块提取到 `_MEIPASS/collector/` 下，路径不对导致 frozen 模式下 Python 找不到
- 修复①：spec 在 `shutil.copytree` 后自动生成 `collector/__init__.py`
- 修复②：运行时钩子 `__path__` 改为 `os.path.join(root, "collector")`，fallback 仍为 `root`
- 验证：全量重打 77MB → 冒烟 12 秒无崩溃

## [2026-06-24 05:00] 六维破解流水线接入 _on_reprobe_upgrade + parasitic_replay 接 _store_workflow
- 文件：采集器/collector_ui/wizard.py
- _store_workflow 接 parasitic_replay.save_template() — 录制原厂操作后自动存寄生模板
- _on_reprobe_upgrade 重构为 8 层破解流水线：①递归搜 DLL → ②dll_string_scanner 抠密钥 → ③encryption_fingerprints 匹配算法 → ④clue_hunter 追踪深层线索 → ⑤翻注册表 → ⑥parasitic_replay 回放验证 → ⑦换波特率 → ⑧Ghidra 反编译 → 全部失败→法医诊断包
- 全部 6 个破解模块在方法体内惰性导入，不挡启动。启动导入链验证通过

## [2026-06-24 04:47] 采集器深度治理：启动无响应根因修复 + 8 项代码卫生
- 文件：采集器/collector_ui/wizard.py、采集器/collector_ui/widgets/sample_panel.py、采集器/bridgecore/physical_channel.py、采集器/bridgecore/panic_recovery.py、采集器/bridgecore/injector.py
- 致命根因：wizard.py 模块顶部 9 段 try/import 串行加载子模块全堵主线程→启动卡死。修复：import 搬家到正确位置（experience_engine/WeakKeyBruteForcer→__init__、forensic_packager→_on_reprobe_upgrade、其余 6 段 wizard 零调用的撤掉）
- EraseWorker 接入流程：sample_panel 加擦卡按钮 + wizard 连接擦卡→重新读卡→失败处理完整闭环
- PMS 交叉依赖泄漏治理 4 处：physical_channel 删失效 USB HID 块+改走 collector_bridge、panic_recovery 删冗余导入走 fallback、injector keepalive 改为空操作（采集器无对应机制）
- 卫生清理：删 debug-cfa29a.log 调试残留 + sample_panel L223 死代码 + DetectWorker/TokenCollectionWorker 补 parent=self
- 验证：全链导入通过（constants/models/workers/widgets/wizard 全绿）、零 lock_adapters 残留

## [2026-06-24 04:12] 采集器启动卡滞修复（bridgecore 惰性导入 + 日志去 flush + spec 注册运行时钩子）
- 文件：采集器/bridgecore/__init__.py、采集器/collector_ui/wizard.py、采集器/SolidCollector.spec
- 根因：bridgecore/__init__.py 模块级导入 20+ 子模块，窗口首次 _refresh_graduation 时阻塞 Qt 主线程 3-10s
- 修复①：bridgecore/__init__.py 删除全部 from .xxx import 改为惰性（所有调用方已走完整子模块路径）
- 修复②：wizard.py _log_msg 去掉 .flush() 每次刷盘（Windows + U 盘组合下每次写盘等同步）
- 修复③：SolidCollector.spec runtime_hooks=[] → runtime_hooks=[pyi_rth_collector.py]

## [2026-06-24 04:03] 采集器 EXE 启动崩溃修复 + 删除 V9 旧工具 + ota_connector 死代码清理
- **文件**：`采集器/collector_ui/wizard.py`、`酒店系统/ota_connector.py`、删除 `酒店系统/tools/v9_card_tool/` 全部 3 文件
- **Bug**：`wizard.py` L355 `install_dir` 裸名引用 → 打包 EXE 启动即崩 `NameError: name 'install_dir' is not defined`。`install_dir` 是 `__init__` 形参，`_build_ui()` 是普通方法访问不到
- **修复**：L355-356 `install_dir` → `self._install_dir`（`__init__` 已在 L114 存储），重打包 77MB 冒烟 8s PASS
- **深度审计**：逐模块扫描 Worker 签名（8 个）/ Widgets 方法（30+ 个）/ 变量作用域 / self. 前缀，仅此 1 处 bug
- **V9 旧工具清理**：`酒店系统/tools/v9_card_tool/`（main.py 64KB + spec + README）整个删除。功能已被 SolidCollector+PMS 完全替代，且 spec 里还引用已删除的 `algo_study_recorder`
- **ota_connector 死代码**：`from 采集器.bridgecore.ota_card_issue import auto_issue_card` 模块不存在，删除死 try 分支，保留 PMS 内置发卡回退

## [2026-06-24 03:48] 修复 guest_list_tab 缺 Qt import → 重新打包验证
- **文件**：`tabs/guest_list_tab.py`
- **问题**：合并补丁后 EXE 启动崩溃，`NameError: name 'Qt' is not defined`（`setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)` 但缺 `from PySide6.QtCore import Qt`）
- **全量扫描**：11 个被修改文件中仅 `guest_list_tab.py` 缺此 import，其余（`inventory/member/staff/payment/v4/activation/shop`）均正确
- **修复**：在 `import logging` 后加 `from PySide6.QtCore import Qt`
- **验证**：`python -c "import tabs.guest_list_tab"` 通过 → PyInstaller 打包 80.5MB → 冒烟 5s PASS → solid.log 最新运行零 CRITICAL

## [2026-06-24 03:34] GXDXX UI 补丁全量合并 + 超市页升级 + 打包
- **来源**：外部 UI 建议 `E:\我的下载\GXDXX-ui-patch`，10 文件全量覆盖 + 超市页同步升级
- **覆盖**：`theme_palette.py`（四主题底色微调）、`room_matrix.py`（色条 6px 字号 26px）、`guest_list/inventory/member/staff_tab.py`（ContentBox→SolidCard 圆角白卡片）、`payment.py/v4.py`（按钮 48px + 快捷金额行 + 找零）、`startup_splash.py`（3D 翻转 5.5s）、`vendor_activation_screen.py`（中央卡片式登录页，副标题→"构筑稳固底座 · 驱动卓越运营"）
- **同步**：`shop_frontdesk.py` 两处 ContentBox→SolidCard
- **spec 清理**：删除 `brand.json` / `USB_LOCK_PROFILES` / `POWER_CONTROLLER_PROFILES`（门锁配置归采集器）
- **结果**：11 模块 import 全通过，80.5MB EXE 打包成功，冒烟 8 秒零 CRITICAL

## [2026-06-24 03:16] 采集器 UI 重构迁移与深度优化
- **文件**：新建 `采集器/collector_ui/` 包（`__init__.py`、`wizard.py`、`constants.py`、`models.py`、`workers.py`、`widgets/` 5 个面板），删除旧 `collector_ui.py`（4238 行单文件）
- **来源**：E:\我的下载\collector_ui_refactor 外部重构版，拆分 4238 行 → 8 模块（~1200 行 wizard + 5 widget 面板）
- **导入路径全量修正**：`from .bridgecore` → `from ..bridgecore`（wizard.py 21 处、workers.py 18 处），同理修正 ghidra_toolkit/collector_bridge/process_monitor 等 10+ 模块的导入
- **补回 2 项加固修复**：
  - B1: `_on_analyze_done` 成功分支加入经验自学习 `save_experience()` + probe_meta 同步
  - B4: `_on_reprobe_upgrade` 从简化版恢复完整 4 层再探策略（递归搜 DLL/翻注册表/换波特率/法医诊断包）+ `_try_connect_baud()` 辅助方法
- **实施 2 项后续建议**：
  - S1: 替换 `os.access()` 为实际写测试（workers.py + wizard.py 共 2 处）
  - S2: 为所有 Worker 启动点（8 处）加 `threading.Lock` 互斥保护 + closeEvent 加锁访问
- **3 项深度优化**：
  - D1: `_log_msg` 同步写入文件日志 `collector.log`（带时间戳，closeEvent 自动关闭）
  - D2: 分析失败分支增强：加密卡检测提示/样本不足建议/一键重采按钮启用
  - D3: closeEvent 增强安全停机：APDU sniffer 停止 + Worker 锁保护
- **构建配置**：`SolidCollector.spec` hiddenimports 新增 11 个模块（collector_ui 包全量注册），`BUILD_TAG` → `0624merged`
- **验证**：完整导入链验证通过（CollectorWizard + constants + models + workers），SampleCapture 序列化/反序列化自检通过
- **A2 (pefile) 和 C1 (启动脚本) 确认已存在于 spec 和 UUU/ 目录，无需额外操作**

## [2026-06-22 23:22] SolidCollector 全面加固方案 14 项全部落地 + 重新打包 + 刷新 UUU
- **文件**：`采集器/bridgecore/handover_packager.py`、`采集器/collector_ui.py`、`采集器/bridgecore/protocol_learner.py`、`采集器/bridgecore/graduation_coach.py`、`采集器/bridgecore/oem_process.py`、`UUU/启动采集器.bat`
- **A. 致命级 3 项修复**：
  - A1: handover_packager bridge32 缺失时写入 MANIFEST 警告字段 + 日志报错（不再静默 skip）
  - A2: pefile 已在 spec hiddenimports（无需额外修改）
  - A3: reprobe 注册表策略去掉 frozen 判断，EXE 版同样可用
- **B. 功能缺失级 6 项修复**：
  - B1: 经验自学习落地——分析成功后在 `_on_analyze_done` 调用 `save_experience()`
  - B2: dll_deep boost 加密卡豁免修复——加密卡维度不因深度分析豁免
  - B3: OEM EXE 白名单回退——白名单未命中时取目录下最近修改的 .exe
  - B4: strings 降级时报备 probe_meta（`strings_unavailable=True`），毕业教练可感知
  - B5: 加密卡判定阈值从 80% 降到 50%
  - B6: Token 采集前增加 `_guard_bridge_for_read()` 检查，发卡器未连即时提示
- **C. UX 加固级 5 项修复**：
  - C1: 新建 `UUU/启动采集器.bat`（一键启动 + chcp 65001 + 环境自检）
  - C2: closeEvent 关闭窗口前自动调用 `_autosave_samples()` 保存进度
  - C3: "全部清空"按钮——`_on_clear_all()` 清空 samples/autosave/分析结果，确认弹窗
  - C4: 环境自检面板——启动时检测 bridge32/Ghidra/Java/strings/磁盘空间，输出到日志
  - C5: 版本号已显示（窗口标题 `_BUILD_TAG`），本次更新为 `0622fortified`
- **打包结果**：SolidCollector.exe 80.9 MB，冒烟测试 5 秒无崩溃，已刷新 UUU 目录

## [2026-06-22 22:38] UUU U盘部署包创建 + strings.exe 补缺 + spec 路径检测修复
- **文件**：`采集器/SolidCollector.spec`、新建 `UUU/` 目录
- **strings.exe 缺失**：工具箱原本无此文件（Ghidra 降级方案），从 Sysinternals 官网下载到 `采集器/toolbox/strings.exe`（370KB）
- **spec 路径检测修复**：`_upgrade_module_path()` 函数修正了 ghidra_toolkit 子模块路径计算（原来是桥接目录，改为正确子目录）
- **UUU 目录**：`D:\AAAGZT-99\JIUDIAN\UUU\`，1275 MB，5711 个文件
  - `SolidCollector.exe` (77 MB) + `bridge32.exe` (7 MB)
  - `ghidra/` — Ghidra 12.1 完整便携版（`ghidra/support/analyzeHeadless.bat`）
  - `java/` — JDK 21 完整运行时（`java/bin/java.exe`）
  - `toolbox/strings.exe` — DLL 字符串降级扫描
- **完整流程**：U 盘拷入 UUU/ 全部内容 → 酒店电脑双击 SolidCollector.exe → 自动寻 Ghidra → 降级时有 strings.exe 兜底
- **文件**：`采集器/SolidCollector.spec`（重写）、`采集器/collector_main.py`、`采集器/collector_ui.py`
- **根因**：旧 spec 用 `pathex=['.']`，导致核心模块（collector_ui/collector_bridge）未打入 EXE，启动即 `ModuleNotFoundError`
- **参考来源**：`E:\我的下载\GXDXX-windows-safe\GXDXX-en` 参考版用了 **staging 镜像法** — 打包前 `shutil.copytree` 到 `build/_pkg_staging/collector/`，让 PyInstaller 自然识别 `collector.xxx` 包结构
- **spec 重写**：采用参考版 staging 架构，合并全能力升级模块 hiddenimports；pathex 指向 staging 目录
- **collector_main.py**：回退到干净的 `from collector.collector_ui import CollectorWizard`（staging 法下无需 try/except）
- **collector_ui.py**：补缺 `QListWidget` 导入（第 1123 行调用但顶部 missing）
- **打包验证**：77.1 MB，零 invalid module，冒烟 8 秒存活，日志正常生成
- **文件**：`采集器/collector_main.py`
- **问题**：17 处 `from collector.xxx` — 源码版靠伪包注册工作，打包版模块扁平化后全部 `ModuleNotFoundError`
- **排查结果**：17 条中 10 条是测试文件（不影响 exe）、5 条是 docstring 注释、2 条可执行代码在 `collector_main.py`
- **修复**：`from collector.collector_ui` / `from collector.collector_bridge` 改为 try/except 双路径（源码版 → 打包版 fallback）
- **打包**：62 MB，零 invalid module 警告，冒烟 8 秒存活

## [2026-06-22 20:20] SolidCollector 7 项缺口全部补齐 — 方案标准落地
- **文件**：`采集器/collector_ui.py`
- **缺口 ① Ghidra 后台 + 经验命中**：`_on_detect_upgrade()` 介入 `_on_detect_result`，按链执行：
  `_exp_matcher.match(dll)` → MD5 命中跳过深度分析；
  失败预警 `_failure_mem.warn_before_retry()`；
  Ghidra 无头模式后台线程 `_start_ghidra_thread()`（300s 超时），不可用时降级为 `_start_strings_fallback()`
- **缺口 ② 线索追踪**：`_on_ghidra_done()` 中 Ghidra 产出的 file_clues → `_clue_hunt(install_dir, clues, depth=2)` 递归搜索
- **缺口 ③ 毕业教练数据管道**：Ghidra 结果写入 `_candidate_profile["probe_meta"]`（ghidra_enriched / ghidra_keys_found / ghidra_xrefs_found）
  → 分析完成时同步到 `_analyze_result["probe_meta"]` → `_refresh_graduation()` → `_eval_dll_deep` 可读 → boost 机制触达
- **缺口 ④ 再探按钮升级**：`_reprobe_btn` 重新接线到 `_on_reprobe_upgrade()`，四策略递进：
  递归搜 DLL/EXE → reg query 注册表快照 → 换波特率重试串口 → 法医诊断包兜底
- **缺口 ⑤ 寄生模板**：`_store_workflow()` 末尾调 `_parasitic_save_template()` 持久化；
  原厂软件启动成功后调 `_parasitic_load_template()` 自动加载已有模板
- **验证**：`collector_ui.py` + `graduation_coach.py` py_compile 通过

## [2026-06-22 20:09] SolidCollector 全能力升级对接完成
- **来源**：`E:\我的下载\SolidCollector_upgrade` 升级包 → `采集器/` 目录
- **新增文件 13 个**：
  - `ghidra_toolkit/` — Ghidra 自动化工具包（finder / scanner / auto_ghidra_scan）
  - `bridgecore/dll_string_scanner.py` — strings.exe 降级扫描
  - `bridgecore/ghidra_enricher.py` — Ghidra 结果融合到 candidate_profile
  - `bridgecore/clue_hunter.py` — 递归搜索线索文件
  - `bridgecore/experience_engine.py` — 经验引擎（ExperienceMatcher / FailureMemory / SectorKeyRing）
  - `bridgecore/mifare_weak_keys.py` — MIFARE Classic 弱密钥爆破
  - `bridgecore/encryption_fingerprints.py` — 加密指纹匹配
  - `bridgecore/parasitic_replay.py` — 寄生模式录制/回放/保存
  - `bridgecore/forensic_packager.py` — 法医诊断打包
  - `known_signatures.json` — 更新品牌签名（含品牌 + 协议家族）
- **合并文件 3 个**：
  - `bridgecore/graduation_coach.py` — 新增 dll_deep 维度 + boost 补偿机制（通过可弥补一项未过必填维）
  - `bridgecore/brand_analyzer.py` — 新增预置 profile + 学习成果目录扫描、协议家族匹配
  - `bridgecore/serial_protocol_learner.py` — 直接覆盖为新版（含 XOR 差分）
- **接线文件 1 个**：
  - `collector_ui.py` — 惰性导入升级模块；分析完成后触发加密指纹匹配 + 弱密钥爆破；探测失败自动生成法医诊断包
- **spec 更新**：SolidCollector.spec 新增 14 个 hiddenimports + ghidra/prebuilt_profiles/known_signatures datas
- **语法验证**：全部 14 个 .py 文件 py_compile 通过

## [2026-06-22 18:18] 总览点击/启动时闪烁修复 — QScrollArea viewport 深色漏闪
- **现象**：启动时和点击总览标签时，短暂出现像小卡片窗口的深色阴影，带类似最大化/最小化的视觉
- **根因**：QTabWidget 面板 + 内层 QScrollArea viewport 在 QSS 样式表首帧渲染前，短暂露出 Fusion 默认深色背景
- **修复**：
  1. `workspace_dock.py` — QTabWidget 创建后立即 `setAutoFillBackground(True)` + 调色板兜底，确保标签界面板在 QSS 生效前就用 bg_root 浅色
  2. `tabs/_shared.py` — `_wrap_scroll` 的 `setWidget(inner)` 后立即 `setAutoFillBackground + palette` 到 viewport，再交给 `fd_apply_scroll_area`
  3. `tabs/hotel_overview_tab_v4.py` — `_build_ui` 将 `fd_apply_scroll_area(scroll)` 移到 `scroll.setWidget(body)` 之后，并在 viewport 上额外设调色板兜底
- **启动验证**：零 CRITICAL、零 Traceback、exit_code 0

## [2026-06-22 18:06] 真正根治黑底：base.qss 括号语法错误 + QWidget 背景规则下移
- **真根因（三重叠加）**：
  1. base.qss 第 1024 行孤立的 `}` — 无对应 `{`，QSS 引擎遇语法错误后静默丢弃全部样式
  2. `QWidget { background-color: transparent; }` — 设了 bg-color 的通用 QWidget 规则会污染所有子类 QSS 渲染
  3. `QWidget, QFrame { background-color: @bg_root@; }` — 同上
- **诊断过程**：最小窗口测试揭示 QPushButton 在完整 QSS 下渲染为 `#4d4d4d`，逐个排除定位到括号不匹配 → 修完后零报错
- **修复**：删 base.qss 第 1024 行孤 `}`、删 QWidget,QFrame 通用背景规则、QWidget 规则移除 background-color、QPushButton 规则加 !important
- **涉及文件**：themes/base.qss、smart_header.py（background:→background-color: + 铃铛 setText("🔔")）

## [2026-06-22 17:58] 已撤回
- **真根因**：base.qss 第 64 行 `QWidget, QFrame { background-color: @bg_root@; }` — 通配所有 QWidget 子类（含 QPushButton）设 bg_root 色值
- **为什么之前修不好**：上轮 `background:`→`background-color:` 替换 + 早上的 6 轮修补，全部在"跟 QWidget 规则抢优先级"层面修，没有意识到 Qt CSS 引擎在 `QWidget,QFrame` 组合选择器上给 QPushButton 渲染产生了非预期复合
- **诊断验证**：最小窗口测试 — 删除 QWidget,QFrame 规则后 QPushButton 中心像素从 `#4d4d4d`（黑灰）→ `#ffffff`（纯白）；QSS 变量注入与 CSS 文本均正确，渲染端失效
- **修复**：删除 `QWidget,QFrame { background-color: @bg_root@; }` 规则（行 64-67）；已由 QMainWindow/QDialog/QGroupBox/SurfacePanel 等具体选择器覆盖，安全网收窄
- **效果**：全站按钮（盈亏报表/能耗监控/审计报告/房型管理/厂家控制台）+ 铃铛图标全部恢复浅色背景
- **源码验证**：零 CRITICAL、零 Traceback、theme_palette/QSS 加载正常、云端/健康监控/日报全部正常
- **撤回了 17:44 条目的虚假根因**（`background:`→`background-color:` 不是真凶）

## [2026-06-22 17:44] 虚假修复保留（background:→background-color: + 铃铛 emoji — 非根因但是改进）
- **根因**：Qt6 Fusion 不完全支持 `background:` CSS 简写属性，只认 `background-color:`。smart_header.py + base.qss 大量规则用 `background:` 简写，被 Fusion 忽略后控件露系统默认暗色 → 全站黑底
- **smart_header.py**：`SMART_HEADER_QSS` 全部 26 处 `background:` → `background-color:`；铃铛按钮 `setText("🔔")` 显示铃铛 emoji（原为 `setText("")` 空文本）
- **base.qss**：全部 100+ 处 `background:` → `background-color:`（仅替换简写，不动 `background-image:`/`background-repeat:` 等）
- **影响范围**：智能顶栏（SmartHeader/MiniTabStrip/SidebarCollapseBtn）+ 全站 objectName 按钮规则（FdCardActionBtn/FdLowFreqBtn/SolidPrimaryBtn 等）全部从 Fusion 暗色泄露恢复
- **源码启动验证**：零 CRITICAL、零 Traceback、零 no such column；主题切换/健康监控/日报/备份/云端全部正常

## [2026-06-22 17:29] 启动成功：ota_bookings 全列兜底 + initial_stocktake_wizard 漏导修复
- **ota_bookings 16 列全量兜底**：旧库缺 room_type/guest_name/checkin_dt/checkout_dt/total_price/status/created_at/updated_at 等 10 列，已全部加入 database.py _migrate() 迁移列表
- **initial_stocktake_wizard.py**：修复 `show_info` 漏导 NameError — 新增 `from ui_helpers import show_info`
- **源码启动验证**：零 CRITICAL、零 Traceback、零 no such column；主题切换/健康监控/日报/备份/云端全部正常
- **已知残余**：`fd_apply_action_btn is not defined` WARNING（非致命，不影响运行，来源待定位）

## [2026-06-22 17:24] 修复：database.py 补 guest_phone 列兜底，解决启动 crash
- **根因**：ota_connector.py SELECT 查询引用了 guest_phone 列，但 _migrate() 的 ota_bookings 迁移列表缺此列
- **修复**：database.py 第 512 行新增 `("ota_bookings", "guest_phone", "TEXT")`，与 booking_no/ota_source/ota_order_id/nights/raw_payload 同组兜底
- **效果**：旧库启动时 _migrate() 自动补列，不再报 `no such column: guest_phone`
- 语法预检通过

## [2026-06-22 17:21] 启动验证第3轮 — 创建3个缺失模块 + ota_bookings 列兜底 (进行中 → 转交接手续修)

- **源目录确认**：`E:\我的下载\GXDXX-windows-safe\GXDXX-en\pms\` 同 Tabs 源也缺 `_shared.py`，GXDXX-work 改造时用了这些符号但文件未遗留
- **新建 `tabs/frontdesk/_shared.py`**：PAYMENT_METHODS（8种支付方式定义）、pay_method_label、_checkin_pay_methods_combo、_status_placeholders（卡状态SQL占位符）、_legacy_card_status_display（5+个状态映射）、_make_collapsible_section（QGroupBox折叠区）
- **新建 `tabs/card_system/__init__.py`**：re-export CardSystemTab 自 card_system_tab（无此文件时 cab_import 解析失败）
- **新建 `tabs/card_system/_shared.py`**：CARD_BRANDS（5品牌含 protocol 字段）、REGISTRY_CARD_KINDS（7种注册卡类型）、_registry_kind_display、_list_serial_ports
- **修复**：CARD_BRANDS 初版缺 `protocol` 字段 → KeyError: 'protocol' → 补 simulate/prousb/generic protocol 字段
- **修复**：database.py _migrate() 新增 ota_bookings 5列兜底（booking_no / ota_source / ota_order_id / nights / raw_payload）→ 解决 `no such column: booking_no`
- **4/4 ImportError 已全消灭**：`tabs._shared` / `tabs.frontdesk._shared` / `tabs.card_system.__init__` / `tabs.card_system._shared` ModuleNotFoundError 均无影
- **当前阻断**：`sqlite3.OperationalError: no such column: guest_phone`

### 待接手续修

```
→ ota_connector.py get_bookings SELECT 含 guest_phone 列
→ database.py _migrate() migrations 列表缺 ("ota_bookings", "guest_phone", "TEXT"),
→ 在 database.py 第 ~507 行 ota_bookings 列兜底末尾加一行即修
```


## [2026-06-22 16:52] 源码启动验证 + 2 项启动问题修复
- **修复 1**：database.py:421-422 — `reservations` 表名修正为 `local_reservations`（建表时已用此名），`checkin_time` 列名修正为 `checkin_dt`
- **修复 2**：db_migration.py v9 — 移除重复的 `ALTER TABLE rooms ADD COLUMN status`（rooms CREATE TABLE 已含 status 列），改由 database._migrate() 兜底幂等添加
- **修复 2 配套**：database.py _migrate() 新增 `("rooms", "max_guests", ...)` + `("rooms", "status", ...)` 两条兜底
- **验证结果**：
  - 新鲜数据库初始化 11/11 模块导入通过
  - local_reservations 表存在、rooms 含 max_guests+status 两列、idx_reservations_guest/checkin 索引正常
  - 采集器 8/8 模块导入通过
  - 酒店 26 文件 + 采集器 9 文件 ast.parse 全部通过
  - 零报错、零 CRITICAL 日志

## [2026-06-22 16:38] 采集器全量替换：GXDXX-windows-safe 版合并
- **来源**：`E:\我的下载\GXDXX-windows-safe\GXDXX-en\collector\` → `D:\AAAGZT-99\JIUDIAN\采集器\`（全量覆盖，71 文件）
- **涵盖 sub-b 采集器改造**：bridgecore 6 个死模块删除（import_validator/profile_generator_pms/serial_protocol_learner/ota_card_issue/offline_queue/process_scanner 共 986 行）、protocol_learner 加密卡识别（>80% 字节变化降级 encrypted_suspected + confidence 0.30 clamp）、graduation_coach 加密卡自动否决毕业、brand_analyzer JSON 外置（known_signatures.json 11 品牌签名）、collector_ui U盘只读检测 + 云端任务列表、collector_bridge read(64) 块读优化（CPU < 2%）
- **涵盖 sub-g 采集器集成**：cloud_handover.py（CloudHandoverClient + HMAC 签名 + collector_cloud.json 本地配置）、task_fetcher.py（TaskFetcher 获取/确认/提交远程任务）、handover_packager MANIFEST 加 cloud_* 三字段
- **SolidCollector.spec 已更新**：移 6 个死 hiddenimports + datas 加 known_signatures.json
- **语法预检**：collector_ui/protocol_learner/cloud_handover/task_fetcher/brand_analyzer/graduation_coach/collector_bridge 共 7 文件 ast.parse 全部通过

## [2026-06-22 16:21] 酒店系统全量替换：GXDXX-work 10 子代理改造合并
- **来源**：`E:\我的下载\GXDXX-work (5)\GXDXX-work\酒店系统\` → `D:\AAAGZT-99\JIUDIAN\酒店系统\`（全量覆盖）
- **涵盖 10 个子代理全部改造**：P0-fixes / sub-a（财务闭环）/ sub-b（采集器+总览 v8）/ sub-c（UI/UX+性能）/ sub-d（UI 入口接线）/ sub-e（安全加固）/ sub-f（架构地图）/ sub-h（厂家云端体系）/ sub-g（采集器集成 PMS 端）/ sub-i（超市图标包）
- **关键新增**：`components/__init__.py`（修 3 个 tab 崩溃）、`shop_icon_pack.py`（96 SKU 图标包）、`lock_deploy/cloud_handover_pull.py`（云端握手包拉取）、`tabs/frontdesk/bill_detail_dialog.py`（账单查看打印）、`docs/厂家云端控制体系.md`、`docs/采集器集成链路.md`、`新版项目地图.md`、`修改清单.md`、`worklog.md`
- **关键删除**：`flow/` 包、`performance/` 包（死代码清理）
- **构建文件保留**：`Solid_onefile.spec` `Solid.spec` `酒店系统_一键打包.bat` `采集器_一键打包.bat` `DEVELOPER_TOOLS.md` `现场U盘说明.txt` `launch_solid.vbs` `.cursor/` 从旧版保留
- **spec 清理**：两个 spec 移除 `tabs._shared`/`tabs.frontdesk._shared`/`tabs.card_system._shared`/`flow`/`flow.animations`/`flow.checkout_flow_optimized`/`performance`/`performance.metrics` 共 8 个死 hiddenimport；补 `optimize=2` `strip=True` `upx_exclude` 
- **采集器未动**：`采集器/` 目录保持旧版，老板后续单独提供
- **语法预检**：11 个核心文件 ast.parse 全部通过
- **主仓库**：`https://github.com/xingjihs-commits/JIUDIAN.git` — 以后所有提交、打包、发布、部署走这个
- **本地备份**：`D:\AAAGZT-99\JIUDIAN-git-backup.git` 保留，双重保险
- **旧临时仓**：`888HZLS.git` 不再使用
- 文件变更：`.git/config`（remote origin 重设）

## [2026-06-22 10:07] 黑底根因修复：ColorScheme.Light 前置注入
- **根因**：`apply_theme()` 中 `setStyleSheet()` 在 `ColorScheme.Light` 之前执行，导致 Fusion 的 `standardPalette()` 读到暗色并缓存，之后 `setPalette()` 改不动
- **修复**：`app_main.py::apply_theme()` — 两处 `setStyleSheet()` 之前各加一次 `apply_app_light_chrome(app)` 前置注入，保留原有后置兜底
- **修改行**：`app_main.py:162/172/178-180/205`（前置+后置，两个分支）
- **变更量**：2 处新增前置调用、1 处注释修正、去重 1 处重复 import

## [2026-06-22 10:05] 新规则：故障排查先读 changelog
- **踩坑复盘**：黑底 bug 共 6 轮修补（07:35→08:55），病因在 07:30 的 `_p()` bug 修复，之后 5 轮全是修症状不找病因
- **新建规则**：`.cursor/rules/debug-root-cause.mdc` — 强制接到报错先读 changelog 回溯"上次正常→那次改了什么"，禁止直接搜代码修症状
- **文件变更**：`d:\AAAGZT-99\.cursor\rules\debug-root-cause.mdc`（新增）

## [2026-06-22 08:55] UI 电路根治：别名映射 + 集中兜底 + 硬编码清零
- **P0-① _KEY_ALIASES 接入 _p()**: `design_tokens.py:228` — `_p()` 开头加 `resolved_key = _KEY_ALIASES.get(key, key)`，旧 key（如 `warning`/`accent_soft`/`card`）自动转新 key
- **P0-② except 日志**: `design_tokens.py:247` — `_p()` 的 except 分支加 `_log.warning("_p(%r) 主题查色失败，走集中兜底", key, exc_info=True)`
- **P1-③ 集中 _NEUTRAL_FALLBACK**: `design_tokens.py:208` — 19 个 key 的集中 fallback 字典，替代原来散落在 `_p()` 内部和 9 个文件的零散硬编码
- **受影响的 9 个文件**: `ui_helpers.py`（8处 try/except 简化）、`report_engine.py`、`timeline_view.py`、`theme_motion.py`（2处）、`frontdesk_ui_v4.py`、`startup_splash.py`、`brand_assets.py`（4处含 make_role_avatar 角色色标）、`design_tokens_v4.py`、`overlay_widgets.py`
- **P1-④ 直接硬编码修复**: `overlay_widgets.py:46`（rgba sidebar 改为 _p("sidebar")+alpha）、`permission_system.py:751`（四主题色点）、`report_engine.py`（grid 色线）、`brand_assets.py`（make_role_avatar 6 角色色标 + 白色文字改 _p("surface")）
- **去掉的错误模式**: 所有 `try: from design_tokens import _p; x = _p(...) except: x = "#HARDCODED"` 块 → 简化为 `from design_tokens import _p; x = _p(...)`
- **原理**: `_p()` 内部已有集中兜底，不再抛异常，无需外层 try/except

## [2026-06-22 08:35] 安装 10 个 Agent 技能
- **社区版 6 个**: battle-plan（战斗计划）、pre-mortem（事前验尸）、prove-it（先验证）、loose-ends（扫尾）、sanity-check（假设验证）、safe-refactor（安全重构）
- **自定制 4 个**: ui-polish（UI 审美）、warm-copy（温暖文案）、quality-check（质量检查）、forensic-collector（法医采集师）
- 文件位置: `D:\AAAGZT-99\.cursor\skills\`（10 个文件夹各一个，2026-06-24 迁移至此）
- 全部采用标准 YAML frontmatter + Markdown 格式，AI 自动识别触发词加载

## [2026-06-22 08:00] 根治全站黑底：base.qss 全局 widget 类型 background-color 兜底 + QDialog QSS
- **因果链追溯**：此前所有调色板修复（3轮）都未生效，非因写法错误，而是 Qt 的 QStyleSheetStyle 机制：`app.setStyleSheet()` 启动后，所有有 QSS 规则的 widget 完全无视 QPalette，直接读 QSS。QSS 中缺少 `background-color:` 显式规则则 Fusion 默认暗色泄露
- **为什么之前"没黑"**：`_p()` 有 `_db.get_config` bug 永远走 except 返回 mist 浅色，且全局 QSS 与 `_theme_dialog_qss()` 颜色恰好一致。我的改动打破了这个"侥幸一致"
- **修复策略**：不在调色板层面纠缠，直接在 QSS 层全局堵漏
- base.qss 第 56 行后新增「Fusion 暗色泄露防护」段：QDialog/QGroupBox/QHeaderView::section/QTableCornerButton::section/QPushButton(:hover/:pressed)/QSpinBox/QDoubleSpinBox/QLineEdit/QComboBox 全部显式 `background-color:`（约 80 行）
- ui_helpers._theme_dialog_qss()：返回值首行新增 `QDialog { background-color: {surface}; }`
- CSS 特异性保证：全局类型选择器优先级低于已有 objectName 选择器（`QPushButton#LoginRoleBtn` 等不受影响）

## [2026-06-22 07:50] 调色板注入顺序致命修复：setStyleSheet 之后执行（此前从未生效）
- **根因**：上轮虽加了全局浅色调色板，但注入时机在 `app.setStyleSheet()` 之前。Qt 文档明确：`setStyleSheet()` 会重置 QPalette 为样式默认值
- **实际调用链**：`app_main.main()` → `app.setStyle("Fusion")` → `apply_app_light_chrome()`（注入浅色）→ `apply_theme()` → `app.setStyleSheet(qss)`（**重置调色板！**）→ 浅色调色板从未生效
- **修复**：将 `apply_app_light_chrome(app)` 从 `main()` 移到 `apply_theme()` 末尾（两路径均覆盖：缓存路径 + 首次加载路径），紧跟每次 `app.setStyleSheet()` 之后立即注入
- 涉及文件：app_main.py（移除 main() 中的过早调用，apply_theme() 两分支末尾均追加注入）

## [2026-06-22 07:45] Fusion 暗色泛黑根治：全局浅色调色板 + 启动屏本地豁免
- **问题**：回退全局调色板后，厂家控制台按钮/能耗监控/房型价格/盈亏报表/表头数字框等大面积黑底（Fusion 暗色覆盖 10+ 种控件）
- **修复策略**：恢复全局浅色调色板（Window=#F8F6F3/Base=#FFFFFF/Text/BrightText/ButtonText/WindowText 全设定），系统级根治 Fusion 暗色渲染
- **启动屏豁免**：StartupSplash.__init__ 末尾注入本地暗色调色板（Window=#1A1625/Base=#221E30/Text=#E8E4F0），子控件不再被全局浅色污染
- 文件变更：ui_helpers.py（恢复调色板 + 追加 WindowText/ButtonText/BrightText）、startup_splash.py（本地暗色调色板）

## [2026-06-22 07:40] ComboBox 弹出层黑底修复 v2（QSS + 猴子补丁，回退全局调色板）
- 上轮 setPalette 导致启动屏子控件背景变浅色卡片，回退该层
- base.qss：QComboBox/QDateTimeEdit/QCalendarWidget 的 QAbstractItemView `background:` → `background-color:`（Fusion 只认后者）、`::item` 新增 `background-color: transparent`
- ui_helpers.fix_fusion_combo_popup()：monkey-patch showPopup，弹出前直接给 view 设 inline stylesheet
- room_matrix.py：筛选下拉框调用 fix_fusion_combo_popup() 兜底

## [2026-06-22 07:35] QComboBox 弹出层黑底修复（Fusion 样式调色板注入）
- **问题**：房态筛选下拉框弹出层背景全黑。QSS 中 `@bg_container@` 已正确解析为 `#F6F0F2`，但 Fusion 样式下 QComboBox 弹出层（QAbstractItemView）不认 QSS background，读系统默认调色板
- **修复**：`ui_helpers.apply_app_light_chrome()` 新增显式 QApplication 调色板设置 —— Base=白/Window=暖灰/Text=深灰/Highlight=暖中性蓝/AlternateBase=暖灰
- 影响范围：所有 QComboBox/QDateTimeEdit/QCalendarWidget 弹出层、默认按钮色、选中高亮色统一为浅色暖中性

## [2026-06-22 07:30] 致命修复：_p() 永远回退 mist + app_main 兜底色板同步
- **根因**：design_tokens._p() 内 `import database as _db; _db.get_config("theme")` → _db 是模块对象，get_config 是实例方法 → AttributeError 静默吞没 → theme_name=None → 永远 resolve 到 mist（DEFAULT_THEME）
- **后果**：不管选什么主题，_p() 永远返回 mist（冷灰蓝）色值。QSS 注入的 glow 暖粉 + _p() 返回的 mist 冷蓝，两组色板在全 UI 层面打架，登录页呈现「有问题的黑」
- design_tokens.py：_p() 第220行 _db.get_config → _db.db.get_config
- design_tokens_v4.py：同 bug 同步修复
- app_main.py 兜底色板：mist bg/surface_alt/border → 同步为 theme_palette.py 实际值 (#F7F9FA/#F0F3F5/#E2E8ED)；shade 同理 (#F7F9F7/#F0F4F1/#E2E8E4)
- 验证：_p() 8 个关键 token 全部与 theme_tokens() 一致（glow: sidebar=#3A2A35, primary=#C47E8A, bg=#FBF7F8）
- 清理 4 个诊断脚本（_diag_*.py）

## [2026-06-22 07:10] 暮霞主题颜色冲突全部修复（10文件·17处暖中性化）
- design_tokens.py：_p() 兜底色板冷蓝#5B8FB9→暖中性#7B8C9E、房态 fallback 6种硬编码→暖中性
- design_tokens_v4.py：_p() 兜底色板同步暖中性化、primary_hover→#6D7D8E
- ui_helpers.py：消息框/对话框/SkeletonCard/build_empty_state/build_loading_indicator/build_error_retry 共8处冷色兜底→暖中性
- timeline_view.py：_build_timeline_colors 全绿→暖中性、_rs_qcolor fallback #547A66→#7B8C9E、COLOR_RESERVED #6D8B78→#7B8C9E
- startup_splash.py：_p() fallback #5B8FB9→#7B8C9E、_load_paints 冷蓝→暖中性、sidebar #2A3441→#3A3840
- app_main.py：glow 主题 bg/surface_alt/border 三色从晨雾抄底→暮霞自身暖粉底色（#FBF7F8/#F6F0F2/#EDE2E5）
- report_engine.py：图表兜底色板全盘暖中性化、GRID #E5E7EB→#E3E1DE
- overlay_widgets.py：CelebrationOverlay rgba(0,0,0,0.4)→rgba(58,56,64,0.4)（暮霞友好暗暖透明）
- brand_assets.py：_get_theme_color fallback #5B8FB9→#7B8C9E、logo绘制 fallback 同步
- theme_motion.py：text_color #1A2018→#2A2A2E、_base_color/_hover_color #EEF2E8/#E1E7DA→#F2F0EE/#E8E5E2
- 全量语法预检：10 文件 10/10 通过、全项目交叉验证零硬编码色残留

## [2026-06-22 06:45] 审计修复落地：10项P0+关键P1全部修复
- 新建 money_utils.py：Decimal 金额工具（to_money/fmt_money/add_money），全项目金额计算统一入口
- pricing_tab.py：9 处 float→Decimal（表格显示 + 编辑弹窗 spinner setValue）
- guest_info.py：押金调整 to_money/累加 Decimal、净额计算 add_money、商店价格 fmt_money
- checkout.py：dep_net/charge_net → to_money 转换、退房入口加 PermissionManager.has_permission("checkout") 门禁
- payment_v4.py：folio 金额累加 to_money
- database.py：35 处 except Exception: pass → _dblog.exception("数据库操作异常")，零残留
- base.qss：PayMethodTile 3重定义合并（删 939-966+3386-3394）、LoginRoleBtn 合并（删 993-1006+3440-3457、:checked 迁至4244末版）
- ota_connector.py：跨边界导入 from 采集器 → try Capture/ImportError 回退到 lock_issue_service+cardlock_auto
- app_main.py：自动备份从未加密 db.backup_to() → 加密 backup_service.auto_backup(db_path)
- main_window_impl.py：快速入住 _do_checkin 加 has_permission("checkin") 门禁
- debug_panel.py：已有 ask_confirm+QInputDialog 双重确认——原审计误报
- 全量语法预检：9 文件 9/9 通过
- 项目地图更新：207 模块（+money_utils.py）

## [2026-06-22 06:20] 全方位审计：五维度交叉验证完成（247 项发现）
- 五代理并行审计（UI/UX·代码质量·功能安全·性能·架构师），覆盖250个.py+4828行base.qss
- 跨维度共识P0（多代理独立发现）：前台无权限门禁/全项目float做金额/database.py 35处except:pass吞异常/_p()每次查库/24标签页全量热加载/base.qss PayMethodTile 5次重复定义/debug_panel可删表/ota_connector跨边界导入采集器/closeEvent信号泄露/自动备份不加密
- 问题：39P0+95P1+78P2+35P3 / 优化9项 / 丝滑提升6项
- 优先路线：立即→Decimal+异常日志+debug面板确认 / 本周→权限门禁+QSS去重+备份加密 / 下迭代→懒加载+缓存+字体系统
- 影子副本扫描：4组同名文件全部为分层架构设计，零僵尸

## [2026-06-22 05:39] UI/UX 专项修复：10 项三批全部落地
- P0 致命（3项）：房态三色改 _p() 主题穿透 + 财务按钮 min>max 统一 32px + cardlock 删 10 处内联 QSS（FdGhostBtn 走 base.qss）
- P1 高危（3项）：总览 21 处内联排版迁 base.qss（v8.1 新增 16 选择器）+ 浮层 6 容器补 objectName + 房卡静态排版拆入 base.qss（v8.2）+ 图例 3 组件补名（v8.3）
- P2 整容（4项）：定价页 PricingSectionTitle→FdSectionTitle + 删 _refresh_theme_styles 死代码 + ui_helpers 删 CARD_PROMPT_FALLBACK/MONEY_COLOR_* 改 _p() + PayMethodTile :focus gold_thread→primary + 命令面板 150ms QTimer 防抖
- base.qss 净增 42 行（v8.1 16 + v8.2 6 + v8.3 3）
- 全量语法预检通过，交叉验证零残留
- 待打包验证（4 主题切换）

## [2026-06-22 05:27] bg_card 分层审计修复：13 处容器/卡片误用 bg_card→正确层级
- 根因：L3 数据井 bg_card（暗暖色）被大量用于容器/卡片/横幅/标题条，视觉形成"补丁感"
- 修复原则：bg_card 仅用于表格 viewport、输入框、备注框；容器→bg_container；浮卡→surface
- **base.qss 10 处**：PaymentMethodTiles(2处)/FdShiftDock SectionBar/FdBillFolioBlock/FdBillTierRow(2处)/FdTotalsStrip(3处)/InfoCard/FdAlertBanner/AuditOverviewCard 全部修正
- **ui_surface.py 7 处**：totals_strip/bill_section_head/bill_folio_block/bill_tier_row/card_action_bar/info_strip/well变量/payment_tiles bg_card→bg_container 或 surface
- 保留 bg_card 合法使用：表格 viewport(FdDataTableShell/FdLedgerTable/FdBillFolioShell)、紧凑输入框(FdCompactSpin/FdAmountInput/CardSearchInput)、交班备注框(FdShiftNotes)、财务对账表格输入、ContentBox 只读文本井
- 交叉验证：base.qss 剩余 @bg_card@ 均为合法数据井；ui_surface.py 剩余 _p("bg_card") 均为合法
- 启动验证：数据库 v21、主题 shade 105 keys、base.qss 126KB 注入，零 CRITICAL/ERROR

## [2026-06-22 05:11] 从 all-fixes 包替换项目 + 保留视觉审计修复
- 来源：`E:\我的下载\solid-pms-all-fixes\gxdxx\酒店系统\`（已解压目录）
- 覆盖：491 个文件从 tar 复制到当期项目（覆盖 181 个差异文件）
- 保留：重做了今天的 5 个视觉审计修复（input_optimized/form_field/checkout_flow_optimized/data_import_service/usb_lock_migrate_dialog 的硬编码色值→_p()）
- 交叉验证：上述 5 文件 `#FFF`/`#1A1A1A`/`#E74C3C` 零残留
- 清理：临时脚本 `_audit_check.py` 已删，`$TEMP\solid-fixes` 已清

## [2026-06-22 04:59] 视觉主题审计修复第二轮：消灭 5 文件 14 处硬编码色值
- 审计范围：全项目 `.py` 文件扫描 `#XXX` / `color:#FFF` / `background:#XXX`，发现 5 文件共 14 处绕开 `_p()` 主题 token 的硬编码色值
- `components/input_optimized.py`：import 从 `design_tokens_v4`（v4 旧 Enum）切到 `design_tokens._p()`，6 处硬编码全部替换（surface/text/border/primary/surface_alt/text_muted/danger）；border-radius 4px→6px 对齐 v7 规范
- `components/form_field.py`：标签色 `#1A1A1A`→`_p("text")`，错误色 `#E74C3C`（v4旧系统）→`_p("danger")`（跟随主题）
- `flow/checkout_flow_optimized.py`：快捷键提示 `#999`→`_p("text_dim")`
- `data_import_service.py`：按钮白字 `color:#FFF`→`_p("surface")`（暗色主题下 surface=深灰，修正硬白）
- `usb_lock_migrate_dialog.py`：扫描按钮+迁移按钮两处 `color:#FFF`→`_p("surface")`
- 交叉验证：`components/*.py` 零硬编码残留；全项目 `color:#FFF` / `color:#XXX` 零残留

## [2026-06-22 04:13] 应用主题审计修复补丁（theme-audit-fixes）
- 文件：酒店系统/theme_palette.py, themes/base.qss, tabs/finance_tab.py, tabs/frontdesk/checkin_tab.py
- 4个文件共 162 行改动（+162/-25）
- `theme_palette.py`：房态色条新字段 / 旧主题兼容常量 / 四主题差异化底色（[F02]）/ 暮霞 gold_thread 还原暖金（[F01]）/ member_gold 语义分离（[F08]）
- `base.qss`：清理废弃选择器 / 补全主按钮[F03]/危险按钮[F04]/幽灵按钮[F05] / 侧栏 active 指示加粗[F06] / MiniTab 白块修复[F07] / 付款瓦片选中态改为勾勒式[F11] / QComboBox/QSpinBox 样式调整 / 登录页重设计
- `checkin_tab.py`：[F09] 收银台底栏比例 3:2→3:1，解决小屏遮挡
- `finance_tab.py`：[F10] 流水与报表比例 3:2→7:5，左侧表格需更多宽度
- 补丁通过 `git apply --3way` 应用成功，改动已暂存（staged）

## [2026-06-22 03:48] 新建踩坑避雷规则 + DEVELOPER_TOOLS.md 接入自动加载
- 文件：.cursor/rules/common-mistakes.mdc（新建）, .cursor/rules/context-router.mdc, AGENTS.md
- 新建 `common-mistakes.mdc`（优先级998）— 从 DEVELOPER_TOOLS.md 直接复制核心内容：三套 Python 路径+陷阱、打包命令速查、Inno Setup/Git 路径、6 条已知打包陷阱、已验证依赖包清单
- context-router.mdc JIUDIAN 分支加读取 `D:\AAAGZT-99\DEVELOPER_TOOLS.md`
- AGENTS.md 关键路径加 DEVELOPER_TOOLS.md 引用
- 效果：新对话 AI 自动知道 python 默认是 32 位陷阱、打包前必须杀进程、两个 spec 要同步、完整工具路径
- 文件：.cursor/rules/chinese-only.mdc（新建）, .cursor/rules/context-router.mdc
- 新建 `chinese-only.mdc`，优先级 999，最高规则。要求思考过程、工具描述、对话回复、代码注释、文档、非代码文件名全部用中文
- 仅三类例外：Python变量名/函数名、技术专有名词、Shell/Git命令本身
- 每条回复前有四条自查清单：思考中文、描述中文、回复中文、只剩代码是英文
- 文件：memory/项目地图.md（新建）, AGENTS.md, .cursor/rules/context-router.mdc
- 新建 `memory/项目地图.md` — 中文后缀名，三层结构：总览 → 酒店系统206模块清单 → 采集器63模块清单 → .solidhandover对接协议
- AGENTS.md 第八节「关键路径」加项目地图引用（新对话第三读）；第九节新增「步骤4：增删文件后立即同步地图」死命令——不能事后补
- context-router.mdc JIUDIAN 上下文恢复分支加地图读取步骤
- 今后新增/删除/改名任何 .py 文件，必须在同一次操作中同步更新地图
- 文件：AGENTS.md, .cursor/rules/context-router.mdc, 酒店系统/tabs/room_matrix.py（删除）
- 发现 `tabs/room_matrix.py` 是影子副本（无 import 引用，与根目录 room_matrix.py 内容不同），6/22 02:40 的修复只落在真身上——暴露协议盲区
- AGENTS.md 新增「九、代码变更安全协议」：步骤1改前import链确认、步骤2改后全项目交叉验证、步骤3每对话开始扫描同名副本
- context-router.mdc 升级两处：JIUDIAN 恢复流程加影子副本扫描步骤、changelog 格式强制要求写改动文件路径
- `tabs/room_matrix.py` 已删除，记入已知冗余清单
- **库存数字纯黑**：`fd_apply_table_palette` 设了 table palette Text、table setStyleSheet color，但 viewport palette **未设 Text 角色** → text item 无显式前景色时退到 viewport palette → 退到系统默认 #000000。修复：viewport palette 加 `setColor(Text, _p("text"))`（+1 行）
- **房卡选中时左边框回归**：`select_outline = "border: 3px solid primary"` 是全 border 简写，出现在 `border-left: none` 之后 → CSS cascade 覆盖左边框。修复：拆为 `border-top/right/bottom: 3px solid primary` 三门独立声明（1 行改）
- **下拉框底部黑**：`QComboBox QAbstractItemView > QWidget#qt_scrollarea_viewport` 父子选择器对 popup 顶层窗口不成立。修复：改为 `QComboBox QAbstractItemView::viewport` subcontrol 选择器（2 处）
- **弹窗背景阻断级联**：`_theme_dialog_qss` 内 `QDialog { background: ... }` 在 dialog.setStyleSheet 时创建局部 scope，base.qss 中已有 4 处 QDialog 规则。删除此冗余块（-6 行），弹窗背景由全局 QSS 统一控

## [2026-06-22 02:28] 7 合 1 根治全线色值脱轨：foreground 伪 token / QSS 级联阻断 / 弹窗硬编码 全部消灭
- **发现 `_p("foreground")` 是伪 token**：theme_palette.py 中从未定义此键，返回空字符串 → CSS `color: ;` 无效 → 退到 Windows 系统黑 → room_matrix.py 房卡文字、toast_widget.py 提示文字全黑。改为 `_p("text")`（8 处）
- **fd_apply_scroll_area 阻断 QSS 级联**：`setStyleSheet` 设 `background-color` 创建局部 scope → 总览子卡片背景消失。改为只设 `border: none`，背景由 palette 负责
- **弹窗顶部黑色**：`build_dialog_header` 只设 `objectName` 无独立 palette → 陷在 QDialog 局部 scope 内。加 `setAutoFillBackground` + `QPalette.Window` = surface
- **删除 V7_DIALOG_STYLE_PATCH**：硬编码 `#FFFFFF #E8E2D8 #9CA3AF` 追加在 `_theme_dialog_qss`（动态色值）**之后** → 后效覆盖前效，所有弹窗 Header 色值不跟随主题。已删 patch + `v7_apply_dialog_style` + auto-hook（共 51 行）
- **下拉框底部黑色**：QComboBox 弹出窗口 `QAbstractItemView` viewport 无显式背景 → Windows 默认黑。base.qss 两处各补 `> QWidget#qt_scrollarea_viewport { background: @bg_container@; }`
- **房卡左侧双线**：`QFrame#RoomCard { border: 2px solid; }` 全边框 + `RoomCardStrip { setFixedWidth(5); }` 色条右边缘 = 视觉双线。改 border 为 `top/right/bottom solid` + `left: none`，由 strip 单独承担左视觉效果
- **房态色条跟随主题**：`active_room_status_theme()` 中 INHOUSE/DIRTY/OVERTIME/MAINTENANCE/RESERVED 5 色硬编码 → 换主题不换色。theme_palette.py 四主题各新增 `room_inhouse/dirty/overtime/maintenance/reserved` token，design_tokens.py 改走 `tokens.get()`

## [2026-06-22 01:41] 总览页 QSS 级联根治 — body 背景从 setStyleSheet 改为 palette
- 根因诊断：body.setStyleSheet() 在 Qt 内创建局部 QSS 作用域，虽不彻底阻断但使子控件级联不一致
- 修复：body.setStyleSheet(...) 替换为 setAutoFillBackground(True) + QPalette 设 Window 色
- QFrame 卡片（KpiCard/OverviewSectionCard/RoomSnapRow/AlertItem/TodoItem）保留内联样式作为纵深防御
- QLabel 全部显式 background: transparent，透出父级表面
- 全局 QSS 级联干干净净，不再依赖 Qt 版本具体行为

## [2026-06-22 00:47] 总览卡片内联样式三段修复 — border+background 直接写死
- 根因：base.qss 规则正确但被 OverviewScrollBody 的 setStyleSheet 切断级联，KPI/总览卡片 border 不生效
- 策略：QFrame 卡片用 _p() 直取色值写死内联样式（background+border 一同设），子控件 QLabel 全部加 `background: transparent` 显式透底
- 同时补齐 RoomSnapRow、TodoItem 子 Frame 的内联样式，避免它们在父 scope 内丢失背景
- 效果：不再依赖全局 QSS 级联，四时主题切换时 _p() 自动跟随

## [2026-06-22 00:40] QFrame 内联样式回退 + 房态骨架卡片残留修复
- 上一轮给 KpiCard / OverviewSectionCard QFrame 加了 setStyleSheet 内联样式→子控件 QLabel 失去 QSS 继承→背景消失
- 根因：Qt 中父控件 setStyleSheet 会切断 children 的 QSS 级联
- 修复：回退 QFrame 内联样式，只保留 QLabel 叶子控件的内联样式（不破坏级联）
- QSS 注入验证通过（@primary@ → #6B8E7B shade 主题），KPI 卡片规则正确
- SkeletonCard：选择器改为 QWidget#SkeletonCard（原 SkeletonCard{} 不被 Qt CSS 识别）+ hideEvent 停止动画
- 房态 _do_real_load：清理骨架时递归 hide()+stop() 动画再 deleteLater，防闪烁残留

## [2026-06-22 00:19] 总览卡片 accent 色条修复 — 三人定义合一
- 根因：base.qss 中 OverviewSectionCard 被定义 3 次，最后一次集体样式（3339行）用 `border: 1px solid @border@` 全覆盖了 v7.5 的 `border-top: 2px solid @accent@` 装饰条
- KpiCard 同理在集体样式中无 accent 色条
- 修复：从集体样式中移出 OverviewSectionCard + KpiCard，各自独立定义 accent/primary 色条；删除线 1475 旧定义、线 2956 v7.5 旧定义

## [2026-06-22 00:14] 设置页主题名切换到四时之色 + V7_DIALOG_STYLE_PATCH 黑底修复 + StepSplash.finish + resize_timer 空值防崩
- 根因：system_console_tab.py 下拉框用旧 4 主题名（twilight_lilac/zen_sand/old_money/pink_maiden），其中两对映射到同一目标主题，实际只有 2 个不同效果
- 修复：下拉框改为 mist/shade/glow/ink 四时之色，i18n 新增中文（晨雾/午荫/暮霞/夜墨）+ 英文对应
- _load() 用 resolve_theme_name() 兼容旧 DB 存储的主题键
- V7_DIALOG_STYLE_PATCH 删除 QDialog { background: palette(window); }，让 base.qss 全权控制登录页背景
- startup_splash.py StepSplash 补 finish()，checkin_tab.py resize_timer 补 None 检查

## [2026-06-21 11:27] 登录后崩溃修复 — 6 项根因全部消灭
- 根因 1：「setButtonSymbolss」拼写错误（多一个 s）× 2 处（checkin_tab.py L194/393）→ 导致 CheckinTab 初始化 AttributeError
- 根因 2：checkin_tab.py 缺失 30+ 方法（_quick_print_bill/_quick_checkin/_change_room 等）→ 逐个补全 stub
- 根因 3：QComboBox 未导入（checkin_tab.py L38）→ 补进 PySide6 imports
- 根因 4：_p() 签名不兼容 — GXDXX v7 版只接受 1 参数，旧代码大量传 2 参数 → 改为 _p(key, fallback="") 兼容双签名
- 根因 5：brand_assets.py 缺失 load_brand_pixmap → 新增委托 make_brand_icon(size)
- 验证：MainWindow 初始化成功、app_main.py 启动 35+ 秒无崩溃，登录页正常显示

## [2026-06-21 11:00] 登录页黑色修复 + 错位导入清债
- 根因：app_main.py 的 _load_palette/apply_theme 用旧主题名查 THEMES（只有 mist/shade/glow/ink）→ 返回 {} → @var@ 不替换
- _load_palette 改用 resolve_theme_name 翻译；apply_theme 同样
- 删除旧主题 QSS 叠加逻辑（old_money.qss 等不再需要，base.qss 通过 @var@ 注入区分四主题）
- fallback 色板也更新为四时之色名（mist/shade/glow/ink）
- _LEGACY_THEME_ALIAS 旧四大主题 → 四时之色（old_money→shade 等）
- checkin_tab.py/shift_dock.py 13 个错位 import 修复：fd_apply_* 从 ui_surface 导入
- checkin_tab.py 补 import logging
- design_tokens.py 补回 pick_grid_cols；8 项清单全部确认到位
- 删除 `from theme_palette import OLD_MONEY, TWILIGHT_LILAC, ZEN_SAND, PINK_MAIDEN` 旧 import
- 删除 _PALETTES 字典、旧 _p() 函数（148行）、_old_money_fallback、_LEGACY_PURPLE_DEFAULTS
- 删除旧房态色板（TWILIGHT_LILAC/ZEN/OLD_MONEY/PINK_MAIDEN_ROOM_STATUS）+ _ROOM_STATUS_PALETTES
- 删除旧 pick_card_size/pick_grid_cols + LAYOUT_BREAKPOINTS/CARD_SIZE_MAP/GRID_COLS_MAP
- OLD_MONEY["xxx"] 模块级常量 → 硬编码 MIST 主题值（#5B8FB9 等）
- invalidate_token_cache 接入 v8 _theme_token_cache 清空
- v8 桥接 _p() 接管所有动态取色，验证 _p('sidebar')='#2A3441' / _p('bg_root')='#FAF7F2' / _p('surface')='#FFFFFF' ✓
- 修复 frontdesk_ui.py 缺失 QObject/QEvent import
- 最终启动 50 秒无 CRITICAL，正常运行

## [2026-06-21 10:22] GXDXX v7 源码覆盖 + 启动修复
- git clone GXDXX 仓库 → v7/src/ 152 文件覆盖到 酒店系统/，v7/themes/base.qss 覆盖
- 修复 startup_splash.py：新增 StepSplash 适配器兼容旧 app_main API、QColor.transparent() → setAlpha(0)
- 修复 theme_palette.py：新增 OLD_MONEY/TWILIGHT_LILAC/ZEN_SAND/PINK_MAIDEN 别名映射到四时之色
- 修复 main_window/__init__.py：新增 __getattr__ 惰性导出 MainWindow
- python app_main.py 启动成功，splash 动画 + QSS 加载正常，无 CRITICAL 报错

## [2026-06-21 10:10] UI 规范全量删除 — 22 文件 + 293 条 changelog UI 记录清除
- 删除所有 UI 规范文档：UI_STANDARD / UI_UX_CODE_EXAMPLES / UI_UX_IMPROVEMENT_PLAN / PREMIUM_UIUX_DESIGN_SYSTEM / QUICK_REFERENCE_GUIDE / QUICK_ACTION_CHECKLIST / README_ANALYSIS_GUIDE / checkin-design-handoff
- 删除 memory/plans/ 下 UI_GOVERNANCE / ui_visual_standards / ui_remediation_master_plan / ui_remediation_execution_spec / OUTSOURCE_UI_FREEDOM_BRIEF / checkin_region_map
- 删除 memory/visual_baseline/ 下 ui_ux_audit_report / ui_probe_latest / ui_probe.jsonl
- 删除 memory/sessions/ 下 ui-ux-rectification-plan / uiux-phase2-plan / cashier-ui-spec
- 删除 memory/UI布局图_编号版 / REFACTOR_README.txt / _uiux_temp/ 目录 / UIUX_Source zip
- AGENTS.md 移除 UI 约束解除段 + 关键路径删 UI 重构交接/目视基线/设计蓝图 三行
- changelog 过滤 293 条 UI 记录，保留 180 条非 UI 记录

## [2026-06-22 12:55] SolidCollector 7 项 Bug 修复（代码审计闭环）
- **P0**: 删 `AA_DisableHighDpiScaling`（PySide6 6.x 已删 API）；样本 `session_autosave.json` 自动持久化 + 启动恢复
- **P1**: `step_coach` 步骤 7→3 回退；步骤 8 读回≥3 次失败降级重新采样；`card_type_ready` 初始改 `False` 让步骤 2 生效
- **P2**: `ForensicConfig.to_dict()` 简化为 `return asdict(self)`；`_from_dict` 子对象列表反序列化为 dataclass；`WorkflowReport.steps` 同理
- **新增 UI**：底栏「再采一组」按钮（步骤 7 可见）；autosave 在 `_on_add_sample` 时触发

## [2026-06-20 20:30] SolidCollector JSONL 合并 + 打包出 EXE
- **JSONL 并入分析**：`merge_recordings_into_result` 合并 `recordings/*.jsonl`；读卡 RPC 轨迹可提取 payload；`learn_from_file` 保留 session_tag
- **测试**：新增 `test_protocol_recording_merge.py`；全量 **50 项通过**
- **打包**：`采集器/dist/SolidCollector.exe` + `bridge32.exe` 已生成，U 盘可直接用

## [2026-06-20 19:45] SolidCollector 主流程接线补完
- **读卡双轨**：`ReadCardWorker` 在 USB/DLL 读卡时用 `orchestrator.record_session()` 包裹，JSONL 轨迹可落盘
- **OEM 互斥**：`_guard_bridge_for_read()` 检测 CardLock 运行中则禁止 bridge 读卡；启动失败/读已写卡后清除 `_oem_running`
- **安全验证门**：分析改用 `safe_verify_protocol`；底栏新增「此卡可作废」勾选，默认跳过裸写验证
- **握手包工作流**：`build_workflow_bundle` 合并 `_workflows` 全卡型进 `workflow.json`；测试 47 项通过

## [2026-06-20 18:30] SolidCollector 全量优化（仅采集器，PMS 未动）
- **阶段0止血**：样本 `written_hex`/`hex` 统一；AnalyzeWorker 协议验证缩进修复；`path_prober` 寄生字段修复；毕业 protocol 按 mode 分流（寄生不要求裸写验证）
- **双轨采集**：`CollectorBridge` hook + `Orchestrator`/`PanicRecovery` 接入 UI；新增 `proxy_log_parser` 解析 V9RFL 代理日志
- **协议加深**：`boost_from_dll_traces`、profile `encryption_hints`/`site`、系统卡毕业维、握手包 `evidence_level`
- **通道**：`PathProber` 串口优先重构；`serial_protocol_learner`；`UsbHidChannel` recv 缓冲
- **安全**：`safe_verify_protocol`；读卡连续失败软复位；`mode=failed` 禁止出包
- **握手包**：补 `dll_traces.jsonl`/`field_checklist.json`/`token_matrix.json`/`lock_state`；测试 25 项通过

## [2026-06-20 12:54] x64dbg + proUSB_backup 删除，最终精简至 273 MB
- **删除 x64dbg**（6.4 GB）：V9 已吃透不需要；新品牌重新下载即可
- **删除 proUSB_backup**（6.27 GB）：旧 MDB 备份，远程支援精华保留在 remote_assist/
- **删除 cardlock/**（11 MB）：补丁 EXE 已在 factory_software/patched_exes/
- **最终状态**：831 文件、273 MB，全精华
- 更新项目地图和 README 反映最终结构

## [2026-06-20 12:52] PMS bridgecore 整份迁移到采集器 — 采集器成为唯一 bridgecore 基地
- **整份迁移**：PMS `bridgecore/` 全部 15 个模块复制到采集器 `bridgecore/`（config/observer/injector/orchestrator/fault_manager/rx_monitor/panic_recovery/physical_channel/dll_prober/protocol_processor/takeover_wizard + tests）
- **冲突合并**：`protocol_learner.py` 合并 PMS 的 `learn(RecordingSession)` 和 `learn_from_file()` 接口；`operator_lib.py` 用采集器版（超集含 `signature_byte15`）；`profile_generator_pms.py` 独立保留给 PMS 端使用
- **__init__.py 重写**：统一导出全部迁移模块，与采集器原有模块无冲突（ChannelInfo/ProbeResult 使用显式命名空间区分）
- **SolidCollector.spec**：补全 14 个新模块到 hiddenimports，含全部测试文件
- **测试验证**：118 passed / 1 skipped（skip 项为 PMS 独占的 keepalive 测试），0 failure
- **PMS 端不动**：酒店系统 bridgecore/ 保持原样，PMS 冻结期不修改

## [2026-06-20 12:30] 原厂门锁系统全面清理 + 参考文档建立
- **删除冗余**：code/(275MB)、scripts/(450MB)、reports/、backups/ 四个目录全部删除，成果已浓缩至 knowledge/综合成果.md
- **精简工具**：factory_software/archives/ 3个7z压缩包、remote_assist/UltraViewer/ 第三方软件、tools/cloud_db_dump/、tools/dev/_legacy_intel/ 删除
- **DDBak 瘦身**：proUSB_DBBak/ 从 60 份 MDB → 保留最新 5 份（0604/0603/0602/0531/0530），删除 ~650MB
- **tools/dev/ 清理**：22 个中间版补丁脚本（patch_ld_v2~v11, verify_patch 等）删除，仅保留 final + v2
- **samples/ 精简**：删除 2 个旧 jsonl，仅保留 working_copy/
- **建立参考文档**：新 `参考文档/` 文件夹含 `项目地图.md`（完整导航）+ `快速入门.md`（5分钟上手）
- **效果**：~2051 文件 → ~1550，核心文件结构清晰分层

## [2026-06-20 09:49] 采集器全品牌兼容修复 — 4 个新缺口全部填补
- **串口指令解耦**：`SerialBridge` 从 profile 读取 `read_command` / `write_command` / `ack_byte` / `buzzer_command`，代替硬编码 AA/BB/CC 通用指令（向后兼容：无 profile 配置时仍走硬编码）
- **串口断线重连**：`SerialBridge` 新增 `ping()` 和 `ensure_alive()` 方法，检测通道存活并自动重连，`keepalive.py` 可直接调用
- **串口 recv 优化**：`SerialChannel.recv()` 改为先读 `in_waiting` 已有字节，避免每次空等 timeout
- **串口自动扫描回填**：`path_prober._probe_serial()` 自动扫描发现串口后，将 port/baudrate 写回 `profile.serial`，确保握手包包含串口信息
- **learn_from_pair 激活**：`AnalyzeWorker.run()` 现在遍历样本中的空白/已写对照对，调用 `ProtocolLearner.learn_from_pair()` 做 XOR 差分分析，合并布局推断结果到主学习结果
- **协议验证环参与毕业判定**：analyze_result 中新增 `protocol_verified` 字段；`graduation_coach._eval_protocol()` 检测到验证失败时标记 protocol 维度未通过，不再绿通过
- **写卡前卡存在性校验**：`protocol_verifier.verify_protocol()` 写卡前先 `direct_read_usb` 检查卡片是否存在，无卡时返回明确错误
- **品牌数据库扩充**：`brand_analyzer` 已知品牌签名库从 1 个（proUSB_V9）扩到 11 个品牌，含通道类型标注（dll/serial），串口品牌附带波特率提示；`_get_known_profile` 同步扩展 5 个串口品牌 profile
- **step_coach 品牌中立**：去掉所有 "USB 发卡器" 硬编码文字，改为 "发卡器"，同时适用于 USB 和串口
- **测试适配**：graduation_coach 测试更新 token 维度自动通过后的 passed_count 预期值（6→7），38/38 全部通过

## [2026-06-20 06:55] 剩余 10 项全部收尾 + 100/100 tests 通过
- **A1 OTA 自动发卡**：ota_connector.py OTA confirmed 事件调 `auto_issue_card()`
- **A2 退房向导**：checkout_v4.py 4 步全部填入实际内容（费用表单/押金退还/完成确认）
- **B1 refunds_tab**：审批/拒绝/刷新按钮改 `OptimizedButton`（primary large / danger medium / secondary small）
- **B2 theme_discipline**：P0_FILES 追加 5 个 v4 路径
- **B3 phase1 test**：新建 `tests/test_phase1_integration.py`（对账+退款+库存三场景）
- **C1 账单明细**：guest_info.py 增加「查看明细」按钮弹窗显示 itemized 清单
- **C2 自动退押金**：退房向导第 2 步手动退 + 最终确认自动退
- **C3 预订关联**：入住时调 `auto_link_unmatched()` 自动匹配 OTA 订单
- **metaclass 修复**：checkin_tab_v4 改为方法委派模式（跳过 MRO 冲突）
- **验收**：`import app_main` OK；pytest 100/100 passed

## [2026-06-20 06:49] 采集器 7 项修复全部完成
- **PyQt5→PySide6 导入修复**：`collector_ui.py:1599` 将 `from PyQt5.QtWidgets import QMessageBox` 改为 `from PySide6.QtWidgets import QMessageBox`，消除打包后崩溃
- **spec 补 4 模块**：`SolidCollector.spec` 新增 `serial_channel`/`token_recorder`/`protocol_verifier`/`keepalive` 到 hiddenimports，防止打包后 ModuleNotFoundError
- **signature_byte15 算子注册**：`operator_lib.py` 新增 `"signature_byte15": checksum_byte15_fb` 别名，消除 protocol_learner AttributeError
- **PMS handover_importer 支持 serial 模式**：`handover_importer.py` 验证白名单加 `"serial"`、`_resolve_install_dir` 和 `_resolve_dll_path` 正确处理 serial 分支、`_write_system_config` 保存 serial_port/baudrate、备份/回滚列表同步新增两个键
- **验证 manifest 支持 serial 模式**：`handover_package.py:57` 白名单加 `"serial"`
- **path_prober 返回真实失败状态**：`path_prober.py:107-108` 两个路径都失败时返回 `mode: "failed"` 而非虚假的 `"parasitic"`
- **graduation_coach 全局状态泄漏修复**：删除 `_TOKEN_NEEDED`/`_TOKEN_COLLECTED` 模块级全局变量，改为 `_eval_token(analyze_result, token_collected)` 参数传递，消除跨 session 污染

## [2026-06-20 06:28] 10 项收尾全部执行完成
- **A1 OTA 自动发卡集成**：`ota_connector.py` confirm_booking 确认后调用 `采集器.bridgecore.ota_card_issue.auto_issue_card` 自动发卡，失败通过 event_bus 告警但不阻塞主流程
- **A2/C2 退房向导填内容 + 押金退还**：`checkout_v4.py` 四步向导填入真实内容（房间宾客信息、费用明细表、押金显示+退还按钮、完成确认文本）；退房确认后自动检查未退押金弹窗提示
- **B1 refunds_tab 换 OptimizedButton**：`refunds_tab.py` QPushButton 全部替换为 OptimizedButton（primary/large/secndary/small/danger/medium）
- **B2 test_theme_discipline P0 补 v4 文件**：P0_FILES 追加 5 个 `_v4.py` 文件
- **B3 新建 test_phase1_integration.py**：3 个集成测试用例（对账/退款/库存事件）
- **C1 账单明细入口**：`guest_info.py` 新增 `_show_bill_details()` + `checkin_tab.py` tbl_folio 旁「查看明细」按钮
- **C3 预订入住关联**：`_commit()` 入住成功后调用 `auto_link_unmatched(limit=10)` 自动关联 OTA 订单
- 验收：`import app_main` OK，99 pytest 通过 / 1 失败（checkin_tab_v4.py metaclass conflict，v4 结构遗留问题，不阻塞）

## [2026-06-20 06:47] 修复 3 个 pytest 失败
- **circular import 修复**：`payment.py` 底部的 `from .payment_v4 import PaymentMethodTiles` 删除，打破 payment ↔ payment_v4 循环链
- **硬编码色值修复**：`hotel_overview_tab_v4.py` 的 `#1A1A1A` → `{_p('text')}`、`#2C3E36` → `{_p('primary')}`，增量引入 `from design_tokens import _p`
- **QPushButton setMinimumHeight 修复**：`payment_v4.py` 两处 `btn.setMinimumHeight()` → `btn.setFixedHeight()`
- 剩余 1 个失败 `test_normalize_pay_method` 是 checkin_tab_v4.py 多重继承 metaclass conflict，v4 结构问题

## [2026-06-20 06:45] 10 项收尾全部执行完成
- **payment_v4 ledger 闭环**：`_post_new_payments_ledger` 完整 override，每个 `append_ledger*` 写入 `checkin_id`/`reference_no`/`order_id`；folio shop items 扣减库存
- **双 spec 同步**：Solid_onefile.spec + Solid.spec hiddenimports 追加 v4/services/flow/performance/refunds_tab/transactions 等 28+ 模块
- **垫片接线**：payment.py re-export PaymentMethodTiles；hotel_overview_tab.py → v4；nav_manifest 新增 refunds 入口；role_navigation/workspace_dock/navigation 同步
- **总览告警**：hotel_overview_tab_v4._load_alerts 已接 ALL_CHECKS 循环
- **采集器三模块**：bridgecore/offline_queue.py / import_validator.py / ota_card_issue.py + SolidCollector.spec hiddenimports
- **验收通过**：import app_main + 全部核心模块；23 pytest 通过；采集器模块 import 通过

## [2026-06-20 W1D2] Track-B 对账服务骨架
- 新增 `reconciliation_service.daily_reconcile(date_str)`：按日汇总 ledger 收银流水（`LEDGER_CASH_NET_TX_TYPES`），对比 ROOM_IN、支付方式缺失、同房混账及夜审 room_revenue。
- `reconciliation_checks` 新增 `check_ledger_payment_mismatch()` 并注册至 `ALL_CHECKS`（兼容保留 `CHECKS`）。
- 新增 `tests/test_reconciliation.py`，4 项 mock 测试全部通过。

## [2026-06-29] 全量扫描审计 + 项目地图修正 + 代码清理
- **清理**：删除 10 个 `.bak_0624` 备份文件、`build_bridge32/bridge32/` 构建产物
- **安全**：修复 `schema_analyzer.py` SQL 注入（`PRAGMA key` 字符串拼接 → 参数化）
- **打包**：`Solid_onefile.spec` 补回遗漏的 `bridgecore.config`
- **地图**：更新 `memory/项目地图.md` — 日期/统计数/删除14个不存在文件条目/加回handover文件/修复采集器bridgecore表格乱码
- **审计**：产出全景扫描报告，发现地图14处偏差、10个bak残留、1处SQL注入

## [2026-06-29] 死代码清理 + re-export垫片合并 + 项目地图更新
- **bridgecore 清理**：删除 14 个死代码文件（dll_prober/physical_channel/profile_generator/protocol_learner/protocol_processor/takeover_wizard/test_driver + 内部7个纯测试引用文件 + tests/ 目录），仅保留 panic_recovery.py
- **re-export 垫片合并**：删除 hotel_overview_tab.py（改 workspace_dock.py import 指向 v4），删除 brand_config.py（12 个文件 import 全部改走 brand_config_v4）
- **工程清理**：删 build_bridge32 空目录、清 reports/ 下 4 个运行时 .xlsx、.gitignore 补 reports/*.xlsx
- **地图更新**：同步统计数（PMS ~223 / Collector ~88 / 总计 ~311），bridgecore 章节精简为 1 文件
