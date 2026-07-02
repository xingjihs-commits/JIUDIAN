# 🏨 Solid PMS 项目地图

> 思维导图格式 | 256 个 .py 文件 | 91,793 行
> 最后更新：2026-06-30
> **死命令**：每次增删改文件后立即更新此文件

---

```
酒店系统/                                          (256 个 .py | 91,793 行)
│
├── 🚀 入口层 ─────────────────────────────────────────────────────────────
│   ├── app_main.py                  353行  主入口 + 主题 + 后台服务
│   ├── main_window_impl.py         1602行  主窗口（侧栏/分屏/快捷键）
│   ├── startup_splash.py            603行  启动闪屏
│   ├── single_instance.py             -   单实例锁
│   └── deploy_paths.py                -   部署路径
│
├── 🧠 核心业务层 core/ ─────────────────────────────────────────────────
│   ├── __init__.py                   60行  单例入口(guest_svc等4个)
│   ├── exceptions.py                45行  统一异常体系(9个子类)
│   ├── guests.py                   172行  客人服务(入住/退房/押金)
│   ├── inventory.py                159行  库存服务(出入库/盘点)
│   ├── ledger.py                   250行  账本服务+哈希链
│   └── pricing.py                  137行  定价服务(节假日/会员折扣)
│
├── 🔗 业务编排层 services/ ─────────────────────────────────────────────
│   ├── __init__.py                   50行  单例入口(checkout_svc等3个)
│   ├── bootstrap.py                431行  启动4阶段编排
│   ├── checkout_service.py         320行  结账原子事务
│   ├── card_service.py             177行  发卡编排
│   ├── report_service.py           195行  日结/月结/房态报表
│   ├── payment_complete.py         119行  收款完成编排
│   ├── exchange_rate.py            125行  汇率服务
│   ├── reconciliation_report.py     17行  对账报告
│   ├── bill_detail.py               40行  账单明细
│   ├── booking_link.py              35行  预订链接
│   ├── event_queue.py               55行  事件队列
│   └── ota_reserve.py               23行  OTA预订
│
├── 🗄️ 数据库层 ─────────────────────────────────────────────────────────
│   ├── database.py                 1920行  数据库核心(SQLCipher/缓存/哈希链)
│   ├── db_schema.py                 615行  表结构定义
│   ├── db_migration.py              550行  版本迁移
│   ├── secure_db.py                   -     加密数据库连接
│   ├── db_access/                         按域拆分的查询层
│   │   ├── __init__.py               21行  统一导出
│   │   ├── shop_db.py               108行  超市/库存CRUD
│   │   └── migration_db.py          214行  迁移操作(ALTER/CREATE INDEX)
│   └── transactions/                      原子事务
│       └── checkout.py                -     结账事务
│
├── 🎨 UI层 ──────────────────────────────────────────────────────────────
│   ├── design_tokens.py               -    v8八维设计令牌核心
│   ├── design_tokens_v4.py            -    v4兼容层
│   ├── theme_palette.py            553行  四时主题色板
│   ├── theme_motion.py             749行  主题切换动画
│   ├── brand_config_v4.py             -    品牌配置
│   ├── ui_helpers.py              1544行  弹窗工厂/卡片工厂
│   ├── ui_surface.py              1631行  表格/滚动区/分隔线
│   ├── ui_probe.py                503行  UI运行时探针
│   ├── frontdesk_ui.py               -    前台主界面
│   ├── frontdesk_ui_v4.py            -    前台界面v4
│   ├── frontdesk_layers.py           -    前台分层架构
│   ├── frontdesk_flow_strip.py       -    前台操作流
│   ├── frontdesk_ledger_strip.py     -    前台账本行
│   ├── smart_header.py                -    智能页头
│   ├── mini_tab_strip.py             -    迷你标签栏
│   ├── enhanced_status_bar.py        -    增强状态栏
│   ├── command_palette.py            -    命令面板(Ctrl+K)
│   ├── overlay_widgets.py            -    浮层组件
│   ├── toast_widget.py               -    轻提示组件
│   ├── toast_notify.py               -    通知入口
│   ├── workspace_dock.py             -    工作台侧栏
│   ├── motion_gate.py                -    动画门控
│   └── sound_helper.py               -    音效
│
├── 🏠 房态矩阵 ──────────────────────────────────────────────────────────
│   ├── room_matrix.py             1524行  房态矩阵(房卡网格)
│   ├── unified_room_page.py       1689行  统一房态页
│   ├── room_standee_renderer.py      -    房态立牌渲染
│   ├── timeline_view.py            928行  时间线视图
│   ├── batch_create_dialog.py      645行  批量创建弹窗
│   ├── overview_data.py              -    总览页数据
│   └── consumable_standards.py       -    消耗品标准
│
├── 📑 标签页 tabs/ ──────────────────────────────────────────────────────
│   ├── vendor_console_tab.py      1664行  厂家控制台(导入握手包)
│   ├── finance_tab.py             1295行  财务标签页
│   ├── hotel_overview_tab_v4.py    754行  酒店总览v4
│   ├── pricing_tab.py              584行  定价标签页
│   ├── system_console_tab.py       568行  系统控制台
│   ├── member_tab.py               479行  会员标签页
│   ├── inventory_tab.py            446行  库存标签页
│   ├── staff_tab.py                346行  员工标签页
│   ├── night_audit_tab.py          301行  夜审标签页
│   ├── guest_list_tab.py           242行  客人列表
│   ├── cloud_config_dialog.py      122行  云端配置
│   ├── energy_audit_group.py        22行  能耗审计组
│   ├── frontdesk_tab.py             17行  前台标签页入口
│   │
│   ├── 🏪 前台子页 tabs/frontdesk/ ───────────────────────────────────
│   │   ├── checkin_tab.py             -   入住登记
│   │   ├── checkout.py                -   退房结算
│   │   ├── guest_info.py              -   客人信息
│   │   ├── payment.py                 -   收款
│   │   ├── payment_v4.py              -   收款v4(2×4磁贴)
│   │   ├── refund.py                  -   退款
│   │   ├── refunds_tab.py             -   退款标签页
│   │   ├── roster_tab.py              -   在住名单
│   │   ├── shift_dock.py              -   交班操作窗
│   │   ├── shift_tab.py               -   交班标签页
│   │   ├── shop_tab.py                -   商店标签页
│   │   ├── borrow_items.py            -   借物管理
│   │   ├── service_tab.py             -   服务标签页
│   │   ├── hub_widget.py              -   前台枢纽
│   │   ├── team.py                    -   团队操作
│   │   ├── bill_detail_dialog.py      -   账单详情弹窗
│   │   └── _shared.py              27行  前台共享常量
│   │
│   └── 💳 卡片系统 tabs/card_system/ ─────────────────────────────────
│       ├── card_system_tab.py     1061行  卡片系统总控
│       ├── card_issue.py              -   发卡操作
│       ├── card_service.py            -   卡片服务
│       ├── card_driver.py             -   卡片驱动
│       ├── card_settings.py           -   卡片设置
│       ├── card_open_history.py       -   开门记录
│       └── _shared.py                 -   **新增** 共享常量
│
├── 🧩 组件库 components/ ───────────────────────────────────────────────
│   ├── __init__.py                    -   **修复** 导出ButtonSystem/OptimizedButton
│   └── button_optimized.py            -   优化按钮系统
│
├── 🔐 权限系统 ──────────────────────────────────────────────────────────
│   ├── permission_system.py       1430行  权限核心(角色/操作)
│   ├── permission_legacy_map.py      -    权限旧系统映射
│   ├── role_ui.py                    -    角色管理界面
│   ├── role_navigation.py            -    角色导航路由
│   ├── role_permissions_page.py      -    角色权限管理页
│   └── nav_manifest.py               -    导航清单
│
├── 🔒 安全与许可 ────────────────────────────────────────────────────────
│   ├── license_manager.py            -    许可证管理(激活锁)
│   ├── vendor_lockdown.py         522行  厂家锁定机制
│   ├── vendor_gate.py                -    厂家门禁
│   ├── vendor_activation_screen.py 526行  厂家激活界面
│   ├── cloud_security.py             -    云端安全
│   ├── crypto_utils.py               -    加密工具
│   ├── money_utils.py                -    金额工具(Decimal)
│   └── input_validation.py           -    输入验证
│
├── 🔑 门锁系统 ──────────────────────────────────────────────────────────
│   ├── card_system.py                -   卡片系统总控
│   ├── card_data.py                  -   卡片数据模型
│   ├── card_sniffer.py            753行  卡片嗅探器
│   ├── card_ritual_dialog.py      919行  发卡仪式弹窗
│   ├── cardlock_frontdesk.py      955行  前台门锁快捷操作
│   ├── lock_legacy_bridge.py         -   旧门锁桥接
│   ├── lock_issue_service.py      574行  门锁发卡服务
│   ├── usb_lock_scanner.py        846行  USB门锁扫描
│   │
│   ├── 🧩 门锁适配器 lock_adapters/ ──────────────────────────────────
│   │   ├── base.py                    -   适配器基类
│   │   ├── bridge_client.py           -   桥接客户端(32位通信)
│   │   ├── card_corpus.py             -   卡片语料库
│   │   ├── cardlock_auto.py           -   自动发卡(读配置发卡)
│   │   ├── generic_adapter.py         -   通用适配器
│   │   ├── hardware_support.py        -   硬件支持检测
│   │   ├── prousb_v9.py           1588行  V9适配器(已完整吃掉)
│   │   ├── prousb_v10.py              -   V10适配器
│   │   ├── prousb_v11.py              -   V11适配器
│   │   ├── rfl_bridge_32.py       1405行  32位桥接子进程(DLL直调)
│   │   ├── seq_manager.py             -   序列号管理
│   │   ├── profile/payload_factory.py -   卡数据载荷工厂
│   │   ├── profile/brand_analyzer.py  -   品牌分析器
│   │   ├── middleware/config_extractor.py - 配置提取器
│   │   └── middleware/verifier.py     -   验证器
│   │
│   └── 🌉 桥接核心 bridgecore/ ───────────────────────────────────────
│       ├── __init__.py               25行  导出PanicRecovery
│       ├── panic_recovery.py        829行  恐慌恢复(Level1软复位+Level2断电)
│       └── ARCHITECTURE.md            -   五层架构文档
│
├── 📊 报表与审计 ────────────────────────────────────────────────────────
│   ├── report_engine.py           1520行  报表引擎
│   ├── audit_engine.py             671行  审计引擎(夜审/日报/月报)
│   ├── energy_audit_engine.py        -    能耗审计引擎
│   ├── inventory_audit_engine.py   464行  库存审计引擎
│   ├── inventory_baseline.py       610行  库存基线
│   ├── inventory_diff_page.py      479行  库存差异页
│   ├── integrity_report.py           -    诚信报表(C0闭环)
│   ├── reconciliation_checks.py      -    对账检查
│   ├── reconciliation_service.py     -    对账服务
│   ├── takeover_report.py            -    接管报告
│   ├── ledger_format.py              -    账本格式化
│   └── initial_stocktake_wizard.py 818行  期初盘点向导
│
├── 🛒 超市与库存 ────────────────────────────────────────────────────────
│   ├── shop_frontdesk.py          1045行  前台商店
│   ├── shop_catalog.py               -    商品目录
│   ├── shop_inventory.py             -    商店库存
│   ├── shop_assets.py                -    商店资源
│   ├── shop_icon_pack.py             -    图标包
│   ├── stocktake_scheduler.py        -    盘点调度器
│   ├── item_dictionary_page.py       -    物品字典
│   └── energy_scheduler.py           -    能耗调度器
│
├── 📡 通信与集成 ────────────────────────────────────────────────────────
│   ├── telegram_handlers.py       1677行  Telegram消息处理
│   ├── telegram_messages.py          -    Telegram消息模板
│   ├── telegram_notify.py            -    Telegram通知发送
│   ├── telegram_shadow.py            -    Telegram影子模式
│   ├── telegram_bot_config.py        -    Telegram机器人配置
│   ├── live_qr_client.py             -    实时二维码客户端
│   ├── qr_code_service.py         859行  二维码服务
│   ├── ota_connector.py           1194行  OTA连接器
│   ├── event_bus.py                  -    事件总线
│   ├── local_adapter.py           689行  本地适配器
│   └── manufacturer_comm.py          -    厂家通信协议
│
├── 🔄 旧系统迁移 ────────────────────────────────────────────────────────
│   ├── one_click_migration.py     1104行  一键迁移
│   ├── data_import_service.py      915行  数据导入服务
│   ├── mdb_import_backend.py         -    MDB导入后端
│   ├── legacy_migration_guide.py   711行  迁移指南
│   ├── legacy_flow_guide.py          -    迁移流程引导
│   ├── legacy_lock_cards_page.py     -    旧门锁卡片页
│   ├── legacy_postimport.py          -    导入后处理
│   ├── legacy_preflight.py           -    导入前检查
│   ├── legacy_takeover_hub.py        -    旧系统接管中心
│   ├── migration_guide_panel.py      -    迁移引导面板
│   ├── legacy_migration/                  旧系统迁移工具
│   │   ├── cardlock_scanner.py       -    旧门锁扫描
│   │   ├── data_importer.py       1514行  数据导入器
│   │   └── schema_analyzer.py        -    数据库结构分析
│   └── components/                        UI组件库
│       └── button_optimized.py       -    优化按钮
│
├── ⚙️ 后台服务 ──────────────────────────────────────────────────────────
│   ├── health_monitor.py             -    健康监控
│   ├── heartbeat_service.py          -    心跳服务
│   ├── task_queue.py                 -    任务队列
│   ├── offline_queue.py              -    离线队列
│   ├── backup_service.py             -    备份服务
│   ├── auto_updater.py               -    自动更新
│   ├── telemetry.py                  -    遥测数据
│   ├── remote_diag.py                -    远程诊断
│   ├── production_defaults.py        -    生产环境默认值
│   ├── power_controller_config.py    -    取电控制器配置
│   └── install_checks.py             -    安装环境检查
│
├── 🌐 云端 cloud-worker/ ────────────────────────────────────────────────
│   ├── worker.js                      -   Cloudflare Worker(全部端点)
│   ├── package.json                   -   npm配置
│   └── wrangler.toml                  -   Wrangler部署配置
│
├── 🧪 测试 tests/ ────────────────────────────────────────────────────────
│   ├── test_report_engine.py       221行
│   ├── test_lock_payload.py        215行
│   ├── test_checkin.py             198行
│   ├── test_database.py            144行
│   ├── test_permission.py          134行
│   ├── test_design_tokens.py       115行
│   ├── test_handover_import.py     109行
│   ├── test_reconciliation.py       98行
│   ├── test_handover_merge_p0.py    71行
│   ├── test_shop_catalog.py         37行
│   ├── test_phase1_integration.py   29行
│   ├── test_heartbeat_once.py       27行
│   ├── test_nav_manifest.py         24行
│   ├── test_consumable_standards.py 19行
│   ├── test_matrix_density.py       32行
│   ├── test_theme_discipline.py      6行
│   ├── ui_ux_tests.py               42行
│   └── manual/register_v9_cardlock.py  -  手动V9注册脚本
│
├── 🛠️ 工具 ──────────────────────────────────────────────────────────────
│   ├── setup_wizard.py            1364行  初始化设置向导
│   ├── debug_panel.py             1175行  调试面板
│   ├── brand_assets.py               -    品牌资源
│   ├── energy_entry_page.py          -    能耗录入页
│   ├── _generate_themes.py           -    QSS主题生成器
│   ├── _check_init.py                -    初始化检查
│   ├── _scan_imports.py              -    import扫描
│   ├── ruff.toml                     -    Ruff lint规则
│   ├── runtime_deps.py               -    运行时依赖
│   ├── i18n.py                       -    多语言翻译引擎
│   ├── tools/                             构建工具
│   │   ├── build_app_icon.py         -    图标构建
│   │   ├── cashier_canvas.py         -    收银画布
│   │   └── matrix_density.py         -    房态密度分析
│   └── themes/                            四时QSS主题(4份硬编码)
│
└── 📦 打包与部署 ────────────────────────────────────────────────────────
    ├── Solid_onefile.spec              PyInstaller单文件配置
    └── installer/                      Inno Setup安装包
```

---

## 📊 分层统计

| 层 | 文件数 | 行数 | 说明 |
|:---|:---|:---|:---|
| 入口层 | 5 | ~2,100 | main/app_main/splash |
| core/ | 6 | 823 | 纯业务逻辑，零Qt依赖 |
| services/ | 12 | 1,587 | 业务编排 |
| 数据库层 | 7 | ~3,400 | database+db_access+db_schema |
| UI层 | 21 | ~8,500 | design_tokens/ui_helpers/ui_surface等 |
| 房态矩阵 | 7 | ~5,100 | room_matrix/unified_room_page/等 |
| tabs/ | 30 | ~8,000 | 标签页(含frontdesk/card_system) |
| 权限系统 | 6 | ~1,900 | permission/role/nav |
| 安全许可 | 8 | ~1,600 | license/vendor/crypto |
| 门锁系统 | 24 | ~8,000 | 适配器+桥接+卡系统+panic_recovery |
| 报表审计 | 11 | ~5,500 | report_engine/audit_engine等 |
| 超市库存 | 9 | ~2,400 | shop/stocktake/inventory |
| 通信集成 | 10 | ~4,300 | telegram/qr/ota/event_bus |
| 旧系统迁移 | 11 | ~4,700 | migration/legacy/data_import |
| 后台服务 | 11 | ~1,500 | health/heartbeat/backup/task |
| 测试 | 18 | 1,557 | unit/integration |
| 工具 | 10 | ~3,500 | setup_wizard/debug/i18n |
| **合计** | **256** | **91,793** | |

---

## 🔗 数据流向

```
tabs/ (UI层)
  ↓ 调用
services/ (业务编排)
  ↓ 调用
core/ (纯业务逻辑)
  ↓ 调用
database.py (SQLCipher)
  ↓
SQLite 加密数据库

门锁发卡:
tabs/card_system/ → services/card_service.py → lock_adapters/ → bridge_client → rfl_bridge_32 → DLL
```

---

## 规则

1. **每次增删改文件 → 立即更新此地图**
2. **思维导图格式（缩进树），不是表格**
3. **每模块标注：文件数、行数、一句话职责**
4. **对话结束时自动统计所有 .py 文件行数并更新统计区块**
