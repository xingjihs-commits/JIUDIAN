# UI 探针报告

- 时间: 2026-07-02T15:00:57
- 上下文: tab:系统设置
- 扫描控件: 2442
- 问题数: 7
- 分类: {'LAYOUT_TRAILING_STRETCH': 5, 'UNNAMED_LARGE': 2}

> **说明**：`NO_BG_IN_SURFACE` / `L0_SHOWTHROUGH` / `SCROLL_FILL_NO_SURFACE` 为代码推断的无背景/露底，不依赖截图。

## [INFO] LAYOUT_TRAILING_STRETCH — 200x72
- 路径: `MainWindow > AppRoot > LeftSidebar > BrandArea`
- 布局末尾 addStretch，内容少时易留空带

## [INFO] LAYOUT_TRAILING_STRETCH — 656x727
- 路径: `MainWindow > AppRoot > LeftSidebar > SidebarScroll > SidebarScrollViewport > SidebarScrollContainer`
- 布局末尾 addStretch，内容少时易留空带

## [INFO] LAYOUT_TRAILING_STRETCH — 414x553
- 路径: `MainWindow > AppRoot > RightContent > BodyContainer > CommandSplitter > MatrixPage > SurfacePanel > MatrixStack > RoomMatrixRoot > MatrixScroll > qt_scrollarea_viewport > MatrixScrollContainer > MatrixEmptyState`
- 布局末尾 addStretch，内容少时易留空带

## [INFO] LAYOUT_TRAILING_STRETCH — 882x678
- 路径: `MainWindow > AppRoot > RightContent > BodyContainer > CommandSplitter > WorkspaceDockPanel > QTabWidget > qt_tabwidget_stackedwidget > QScrollArea > qt_scrollarea_viewport > SystemConsolePage`
- 布局末尾 addStretch，内容少时易留空带

## [INFO] LAYOUT_TRAILING_STRETCH — 1186x40
- 路径: `MainWindow > AppRoot > RightContent > MiniTabStrip > MiniTabScrollArea > qt_scrollarea_viewport > MiniTabContainer`
- 布局末尾 addStretch，内容少时易留空带

## [INFO] UNNAMED_LARGE — 1366x800
- 路径: `MainWindow`
- 大面积控件无 objectName，难定位 QSS/探针

## [INFO] UNNAMED_LARGE — 882x678
- 路径: `MainWindow > AppRoot > RightContent > BodyContainer > CommandSplitter > WorkspaceDockPanel > QTabWidget`
- 大面积控件无 objectName，难定位 QSS/探针
