"""全站 UI 实底设色 — Qt 表格/viewport 不吃 QSS 时的唯一权威入口。

规则：
- 凡 QTableWidget / DataTableShell / ContentBox / 信息条 → 创建后必须调本模块 fd_apply_*
- 勿在单页散落 palette 逻辑；勿只靠 base.qss 透明继承
- 换主题后须调 fd_refresh_surfaces(root) 重刷 palette/内联 QSS
- 所有匿名面板/容器 → 必须用 SurfacePanel / SurfaceFrame，禁止直接 QWidget() / QFrame()
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QTableWidget,
    QVBoxLayout, QWidget, QSizePolicy,
    QVBoxLayout, QHBoxLayout,
)

from design_tokens import _p


# ═══════════════════════════════════════════════════════════════
# 全站统一容器 — 消灭匿名面板黑底
# ═══════════════════════════════════════════════════════════════

class SurfacePanel(QWidget):
    """全站统一容器 — 自带 SurfacePanel objectName，QSS 保证 @bg_root@ 底色。

    替代所有裸 QWidget() / QWidget(parent) 创建的面板/容器。
    已有 objectName 的容器（AppRoot/BodyContainer 等）无需替换。
    """

    def __init__(self, parent=None, layout_dir: str = "v"):
        super().__init__(parent)
        self.setObjectName("SurfacePanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        if layout_dir == "h":
            self._lay = QHBoxLayout(self)
        else:
            self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(0)

    def layout_(self):
        return self._lay


class SurfaceFrame(QFrame):
    """全站统一 QFrame 容器 — 自带 SurfacePanel objectName，QSS 保证 @bg_root@ 底色。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("SurfacePanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)


def _in_checkin_panel(widget: QWidget) -> bool:
    p = widget
    while p is not None:
        if p.objectName() == "FdCheckinPanel":
            return True
        p = p.parentWidget()
    return False


def _in_bottom_dock(widget: QWidget) -> bool:
    p = widget
    while p is not None:
        if p.objectName() == "FdCheckinBottomDock":
            return True
        p = p.parentWidget()
    return False


def _in_content_box(widget: QWidget) -> bool:
    p = widget.parentWidget()
    while p is not None:
        if p.objectName() == "ContentBox":
            return True
        p = p.parentWidget()
    return False


def _checkin_canvas() -> str:
    """收银 L0 — 与全站 bg_root 同源，不再单独配色。"""
    return _p("bg_root")


def _checkin_card() -> str:
    """收银 L2 浮卡 — 与全站 surface 同源。"""
    return _p("surface")


def _frame_solid(frame: QFrame, bg_hex: str) -> None:
    frame.setAutoFillBackground(True)
    frame.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    pal = frame.palette()
    pal.setColor(QPalette.ColorRole.Window, QColor(bg_hex))
    frame.setPalette(pal)


def _visible_border() -> str:
    """卡片/表格/输入框外框 — panel_border 比 border 深，分割细条肉眼可辨。"""
    return _p("panel_border")




# v7.6 表格样式缓存（按主题哈希）
_TABLE_STYLE_CACHE: dict[str, str] = {}


def _get_table_style_cache_key() -> str:
    """缓存键 — 主题切换后自动失效。"""
    try:
        return _p("primary") + _p("surface") + _p("bg_container")
    except (KeyError, ValueError):
        # KeyError: 主题色板缺该 key；ValueError: 主题名非法
        return "default"


def clear_surface_cache() -> None:
    """清除 ui_surface 样式缓存（换主题后调用）。"""
    _TABLE_STYLE_CACHE.clear()


def fd_apply_h_divider(sep: QFrame) -> None:
    """横向分割线 — 2px 实线。"""
    sep.setObjectName("FdHDivider")
    sep.setFixedHeight(2)
    border = _p("border")
    _frame_solid(sep, border)


def _table_header_style() -> str:
    """v7 表头 — 大写小字 + 字距 0.8px + 36px 高。"""
    header_bg = _p("bg_container")
    border = _p("panel_border")
    text_muted = _p("text_muted")
    return (
        f"QHeaderView::section {{"
        f" background-color: {header_bg}; color: {text_muted};"
        f" border: none; border-bottom: 1px solid {border};"
        f" font-weight: 600; font-size: 11px; letter-spacing: 0.8px;"
        f" padding: 9px 12px; min-height: 36px;"
        f"}}"
    )


def fd_apply_table_palette(table: QTableWidget, *, inset: bool = False) -> None:
    """任意表格 — 收银数据井用 L3 bg_card，其余 surface + 可控斑马线。"""
    oname = table.objectName() or ""
    if _in_checkin_panel(table) and oname in ("FdFolioTable", "FdLedgerTable"):
        bg = _p("bg_card")
        alt_bg = _p("surface")
        hover_bg = _p("surface")
    elif inset:
        bg = _p("bg_card")
        alt_bg = _p("surface")
        hover_bg = _p("surface")
    else:
        bg = _p("surface")
        alt_bg = _p("surface_alt")
        hover_bg = _p("surface_alt")
    border = _p("panel_border")
    text = _p("text")

    table.setAlternatingRowColors(False)
    table.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    vp = table.viewport()
    vp.setAutoFillBackground(True)
    vp.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

    tp = table.palette()
    tp.setColor(QPalette.ColorRole.Base, QColor(bg))
    tp.setColor(QPalette.ColorRole.AlternateBase, QColor(alt_bg))
    tp.setColor(QPalette.ColorRole.Text, QColor(text))
    table.setPalette(tp)

    vpp = vp.palette()
    vpp.setColor(QPalette.ColorRole.Base, QColor(bg))
    vpp.setColor(QPalette.ColorRole.Window, QColor(bg))
    vpp.setColor(QPalette.ColorRole.Text, QColor(text))
    vp.setPalette(vpp)

    table.setStyleSheet(
        f"QTableWidget {{"
        f" background-color: {bg};"
        f" alternate-background-color: {alt_bg};"
        f" gridline-color: {border};"
        f" border: none;"
        f"}}"
        f"QTableWidget::item {{ border-bottom: 1px solid {border}; color: {text}; background: transparent; padding: 8px 12px; }}"
        f"QTableWidget::item:hover {{ background-color: {hover_bg}; }}"
        f"QTableWidget::item:selected {{ background-color: {_p('primary')}; color: {_p('surface')}; }}"
        f"QTableWidget::viewport {{ background-color: {bg}; }}"
    )
    hdr = table.horizontalHeader()
    header_bg = _p("bg_container")
    hdr.setAutoFillBackground(True)
    hdr.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    hdr_vp = hdr.viewport()
    hdr_vp.setAutoFillBackground(True)
    hdr_vp.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    hp = hdr.palette()
    hp.setColor(QPalette.ColorRole.Window, QColor(header_bg))
    hdr.setPalette(hp)
    hpp = hdr_vp.palette()
    hpp.setColor(QPalette.ColorRole.Window, QColor(header_bg))
    hdr_vp.setPalette(hpp)
    hdr.setStyleSheet(
        _table_header_style()
        + f" QHeaderView {{ background-color: {header_bg}; }}"
        + f" QHeaderView::viewport {{ background-color: {header_bg}; }}"
    )


def fd_sync_table_height(
    table: QTableWidget,
    *,
    min_rows: int = 2,
    max_rows: int = 16,
    header_h: int | None = None,
    row_h: int | None = None,
) -> None:
    """表格高度随实际行数收缩 — 非收银页用，避免空数据时大块浅色表体。"""
    if _in_checkin_panel(table):
        return
    rows = max(table.rowCount(), min_rows)
    if max_rows > 0:
        rows = min(rows, max_rows)
    hdr = table.horizontalHeader()
    h_hdr = header_h if header_h is not None else max(hdr.minimumHeight(), hdr.height() or 34)
    v_hdr = table.verticalHeader()
    h_row = row_h if row_h is not None else (v_hdr.defaultSectionSize() or 36)
    total = h_hdr + h_row * rows + 2
    table.setFixedHeight(total)
    table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)


def fd_apply_page_tab_root(page: QWidget) -> None:
    """工作台 Tab 根 — L0 bg_root 实底（QSS  alone 不够，须 palette + WA_StyledBackground）。"""
    bg = _p("bg_root")
    if not page.objectName():
        page.setObjectName("WorkspacePage")
    oname = page.objectName()
    page.setAutoFillBackground(True)
    page.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    pal = page.palette()
    pal.setColor(QPalette.ColorRole.Window, QColor(bg))
    page.setPalette(pal)
    page.setStyleSheet(f"QWidget#{oname} {{ background-color: {bg}; }}")


def fd_apply_panel_container(panel: QWidget, *, fallback_name: str = "PanelContainer") -> None:
    """侧栏 / 栈区 — L1 bg_container 实底。"""
    bg = _p("bg_container")
    if not panel.objectName():
        panel.setObjectName(fallback_name)
    oname = panel.objectName()
    panel.setAutoFillBackground(True)
    panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    pal = panel.palette()
    pal.setColor(QPalette.ColorRole.Window, QColor(bg))
    panel.setPalette(pal)
    panel.setStyleSheet(f"QWidget#{oname} {{ background-color: {bg}; border: none; }}")


def fd_apply_settings_page(page: QWidget) -> None:
    """系统设置栈页 — 滚动区内层实底 surface，避免透明页露 L0 绿。"""
    fd_apply_workspace_surface_page(page, fallback_name="ConsoleSettingsPage")


def fd_apply_workspace_surface_page(page: QWidget, *, fallback_name: str = "WorkspacePage") -> None:
    """工作台整页/子页 — L1 surface 实底（设置、厂家控制台等）。"""
    bg = _p("surface")
    if not page.objectName():
        page.setObjectName(fallback_name)
    oname = page.objectName()
    page.setAutoFillBackground(True)
    page.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    pal = page.palette()
    pal.setColor(QPalette.ColorRole.Window, QColor(bg))
    page.setPalette(pal)
    page.setStyleSheet(f"QWidget#{oname} {{ background-color: {bg}; }}")


def fd_apply_panel_groupbox(box, *, fallback_name: str = "PanelGroupBox") -> None:
    """QGroupBox — L2 surface 实底（base.qss 全局 QGroupBox transparent 须代码补）。"""
    from PySide6.QtWidgets import QGroupBox

    if not isinstance(box, QGroupBox):
        return
    if not box.objectName():
        box.setObjectName(fallback_name)
    bg = _p("surface")
    border = _p("panel_border")
    text = _p("text")
    box.setAutoFillBackground(True)
    box.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    oname = box.objectName()
    box.setStyleSheet(
        f"QGroupBox#{oname} {{"
        f" background-color: {bg}; color: {text};"
        f" border: 1px solid {border}; border-radius: 8px;"
        f" margin-top: 12px; padding-top: 8px;"
        f"}}"
        f"QGroupBox#{oname}::title {{ subcontrol-origin: margin; left: 10px; padding: 0 6px; }}"
    )


def fd_apply_settings_groupbox(box) -> None:
    """设置页 QGroupBox — L2 surface（与 base.qss 同步，探针/实底双保险）。"""
    fd_apply_panel_groupbox(box, fallback_name="ConsoleSettingsGroup")


def _data_table_shell_wants_gold(shell: QFrame) -> bool:
    """左金线只留外层容器一根；流水 dock / 账单 folio 内壳不再叠金线。"""
    p = shell.parentWidget()
    while p is not None:
        on = p.objectName()
        if on in ("FrontdeskLedgerDock", "FdBillFolioShell"):
            return False
        p = p.parentWidget()
    return True


def fd_apply_data_table_shell(
    shell: QFrame,
    table: QTableWidget,
    *,
    gold_line: bool = True,
) -> None:
    """数据井 — L2 surface 围栏 + 内表 L3 bg_card。"""
    if gold_line and not _data_table_shell_wants_gold(shell):
        gold_line = False
    shell_bg = _p("surface")
    gold = _p("gold_thread")
    obj = shell.objectName() or "DataTableShell"

    shell.setAutoFillBackground(True)
    shell.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    sp = shell.palette()
    sp.setColor(QPalette.ColorRole.Window, QColor(shell_bg))
    shell.setPalette(sp)
    left = f"border-left: 4px solid {gold};" if gold_line else "border-left: none;"
    shell.setStyleSheet(
        f"QFrame#{obj} {{ background-color: {shell_bg}; border: none; {left} }}"
    )
    fd_apply_table_palette(table, inset=False)


def fd_apply_content_box(box: QFrame) -> None:
    """ContentBox — L2 surface 实底 + 左金线（浮于 L0 画布）。"""
    from frontdesk_ui import FD_CONTENT_BOX_MARGINS

    bg = _p("surface")
    gold = _p("gold_thread")
    frame_border = _p("panel_border")
    obj = box.objectName() or "ContentBox"
    box.setAutoFillBackground(True)
    box.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    pal = box.palette()
    pal.setColor(QPalette.ColorRole.Window, QColor(bg))
    box.setPalette(pal)
    box.setStyleSheet(
        f"QFrame#{obj} {{"
        f" background-color: {bg};"
        f" border: 1px solid {frame_border};"
        f" border-left: 4px solid {gold}; border-radius: 0;"
        f"}}"
    )
    lay = box.layout()
    if lay is not None:
        lay.setContentsMargins(*FD_CONTENT_BOX_MARGINS)


def fd_apply_empty_state(frame: QFrame) -> None:
    """空状态面板 — L2 surface 实底 + 边框（MatrixEmptyState / PageEmptyState）。"""
    obj = frame.objectName() or "MatrixEmptyState"
    bg = _p("surface")
    border = _p("panel_border")
    _frame_solid(frame, bg)
    frame.setStyleSheet(
        f"QFrame#{obj} {{"
        f" background-color: {bg};"
        f" border: 1px solid {border};"
        f" border-radius: 14px;"
        f"}}"
    )


def fd_apply_totals_strip(frame: QFrame) -> None:
    """收银合计条 — 与 tier/明细同色 bg_container，贴底无 margin 防露 L2 surface 缝。"""
    ck = _in_checkin_panel(frame)
    bg = _p("bg_container") if ck else _p("surface_alt")
    border = _visible_border() if ck else _p("border")
    obj = frame.objectName() or "FdTotalsStrip"
    _frame_solid(frame, bg)
    parent = frame.parentWidget()
    in_bill_card = (
        ck
        and isinstance(parent, QFrame)
        and (parent.objectName() or "") == "FdCard"
        and _card_in_bill_rail(parent)
    )
    if ck:
        radius = " border-radius: 0 0 8px 0;" if in_bill_card else " border-radius: 0;"
        frame.setStyleSheet(
            f"QFrame#{obj} {{"
            f" background-color: {bg};"
            f" border: none;"
            f" border-top: 1px solid {border};"
            f" margin: 0; padding: 0;"
            f"{radius}"
            f"}}"
        )
    else:
        frame.setStyleSheet(
            f"QFrame#{obj} {{"
            f" background-color: {bg}; border: 1px solid {border};"
            f" border-radius: 6px; margin: 0;"
            f"}}"
        )


def fd_apply_gold_line(line: QFrame) -> None:
    """FdGoldLine 子控件 — palette 实色，防 QSS 未命中时透明缝。"""
    gold = _p("gold_thread")
    line.setObjectName("FdGoldLine")
    _frame_solid(line, gold)
    line.setStyleSheet(
        f"QFrame#FdGoldLine {{ background-color: {gold}; border: none; border-radius: 2px; }}"
    )


def fd_apply_section_bar_embedded(bar: QFrame, *, bg_key: str = "surface") -> None:
    """底栏/贴边分区标题 — 背景与父面板同色，底边 panel_border（禁止 border:none 盖 QSS）。"""
    bg = _p(bg_key)
    border = _p("panel_border")
    obj = bar.objectName() or "FdSectionBar"
    _frame_solid(bar, bg)
    bar.setStyleSheet(
        f"QFrame#{obj} {{"
        f" background-color: {bg};"
        f" border: none; border-bottom: 1px solid {border};"
        f" margin: 0; padding: 0;"
        f"}}"
    )
    # agent log removed — debug_visual_probe deleted
    for line in bar.findChildren(QFrame):
        if line.objectName() == "FdGoldLine":
            fd_apply_gold_line(line)


def fd_apply_card_panel(
    card: QFrame,
    *,
    flush_left: bool = False,
    gold_left: bool = True,
) -> None:
    """卡片分区 — elevated 浮卡色 + 可选左金线。

    莫兰迪层次：bg(L0画布) < surface_alt(L1面板) < elevated(L2浮卡) < surface(L3数据井)
    左右收银卡片统一走 elevated 底色，金线仅左栏 rail 持有，卡片内不叠金线。
    """
    ck = _in_checkin_panel(card)
    in_right = ck and _card_in_right_rail(card)
    if in_right:
        bg = _p("elevated")         # 右栏卡片 — 浮卡层 L2
    elif ck:
        bg = _p("elevated")         # 左栏账单卡片 — 同样浮卡层 L2，左右一致
    elif _in_content_box(card):
        bg = _p("surface_alt")
    else:
        bg = _p("elevated")
    frame_border = _visible_border()
    gold = _p("gold_thread")
    obj = card.objectName() or "FdCard"
    card.setAutoFillBackground(True)
    card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    pal = card.palette()
    pal.setColor(QPalette.ColorRole.Window, QColor(bg))
    card.setPalette(pal)
    if flush_left:
        card.setStyleSheet(
            f"QFrame#{obj} {{"
            f" background-color: {bg};"
            f" border: none;"
            f" border-top: 1px solid {frame_border};"
            f" border-right: 1px solid {frame_border};"
            f" border-bottom: 1px solid {frame_border};"
            f" border-left: none;"
            f" border-radius: 0 8px 8px 0;"
            f" margin: 0; padding: 0;"
            f"}}"
        )
    elif in_right or not gold_left:
        card.setStyleSheet(
            f"QFrame#{obj} {{"
            f" background-color: {bg}; border: 1px solid {frame_border};"
            f" border-radius: 8px; margin: 0; padding: 0;"
            f"}}"
        )
    elif gold_left:
        card.setStyleSheet(
            f"QFrame#{obj} {{"
            f" background-color: {bg}; border: 1px solid {frame_border};"
            f" border-left: 4px solid {gold}; border-radius: 8px;"
            f" margin: 0; padding: 0;"
            f"}}"
        )
    else:
        card.setStyleSheet(
            f"QFrame#{obj} {{"
            f" background-color: {bg}; border: 1px solid {frame_border};"
            f" border-radius: 8px; margin: 0; padding: 0;"
            f"}}"
        )
    # 禁止 refresh 时重复挂透明度动效（曾导致 #16 右卡整片透底）
    try:
        from PySide6.QtWidgets import QGraphicsOpacityEffect
        eff = card.graphicsEffect()
        if isinstance(eff, QGraphicsOpacityEffect):
            card.setGraphicsEffect(None)
    except (RuntimeError, AttributeError):
        # RuntimeError: card C++ 已销毁；AttributeError: graphicsEffect 缺失
        pass
    # 收银卡片禁用阴影 effect — 曾干扰实底绘制
    if not ck:
        try:
            from motion_gate import attach_card_shadow
            attach_card_shadow(card, "sm")
        except (RuntimeError, ImportError):
            # RuntimeError: card 已销毁；ImportError: motion_gate 缺失
            pass


def _card_in_bill_rail(card: QFrame) -> bool:
    p = card.parentWidget()
    while p is not None:
        if p.objectName() == "BillRail":
            return True
        p = p.parentWidget()
    return False


def _card_in_right_rail(card: QFrame) -> bool:
    p = card.parentWidget()
    while p is not None:
        if p.objectName() == "FdCheckinRightRail":
            return True
        p = p.parentWidget()
    return False


def fd_apply_bill_section_head(bar: QFrame) -> None:
    """账单明细等分区标题条 — 收银内与容器同色 bg_container。"""
    ck = _in_checkin_panel(bar)
    bg = _p("bg_container") if ck else _p("surface_alt")
    border = _visible_border() if ck else _p("border")
    obj = bar.objectName() or "FdBillSectionHead"
    _frame_solid(bar, bg)
    bar.setStyleSheet(
        f"QFrame#{obj} {{"
        f" background-color: {bg};"
        f" border: none; border-bottom: 1px solid {border};"
        f" border-radius: 0 6px 0 0;"
        f" margin: 0; padding: 0;"
        f"}}"
        f"QFrame#{obj} QLabel#FdSectionTitle {{ background: transparent; }}"
    )


def fd_apply_bill_folio_block(block: QFrame) -> None:
    """账单明细块 — 收银内 L1 bg_container；其它页 surface_alt。"""
    bg = _p("bg_container") if _in_checkin_panel(block) else _p("surface_alt")
    obj = block.objectName() or "FdBillFolioBlock"
    _frame_solid(block, bg)
    block.setStyleSheet(
        f"QFrame#{obj} {{ background-color: {bg}; border: none; margin: 0; padding: 0; }}"
    )


def fd_apply_bill_folio_shell(shell: QFrame, table: QTableWidget) -> None:
    """账单明细数据井 — 收银内壳与块同 L3 bg_card；表 viewport 同色。"""
    ck = _in_checkin_panel(shell)
    shell_bg = _p("bg_card") if ck else _p("surface")
    border = _p("panel_border")
    obj = shell.objectName() or "FdBillFolioShell"
    shell.setAutoFillBackground(True)
    shell.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    sp = shell.palette()
    sp.setColor(QPalette.ColorRole.Window, QColor(shell_bg))
    shell.setPalette(sp)
    shell.setStyleSheet(
        f"QFrame#{obj} {{"
        f" background-color: {shell_bg};"
        f" border: none;"
        f" border-right: 1px solid {border};"
        f" border-bottom: 1px solid {border};"
        f" border-top: none;"
        f" border-left: none;"
        f" border-radius: 0 0 8px 8px;"
        f" margin: 0; padding: 0;"
        f"}}"
        f"QFrame#{obj} QTableWidget::viewport {{"
        f" background: {_p('bg_card')};"
        f"}}"
    )
    fd_apply_table_palette(table, inset=True)


def fd_apply_bill_rail(rail: QFrame) -> None:
    """账单左栏 — L0 canvas + 左金线；内层 FdCard 为 L2 card。"""
    bg = _checkin_canvas()
    gold = _p("gold_thread")
    _frame_solid(rail, bg)
    rail.setStyleSheet(
        f"QFrame#BillRail {{"
        f" background-color: {bg}; border: none;"
        f" border-left: 4px solid {gold};"
        f"}}"
    )


def fd_apply_dock_divider(sep: QFrame) -> None:
    """收银/底栏竖分隔 — 1px 可见线，贯通上下分屏。"""
    from PySide6.QtWidgets import QSizePolicy

    sep.setObjectName("FdDockDivider")
    sep.setFixedWidth(1)
    sep.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
    border = _visible_border()
    sep.setAutoFillBackground(True)
    sep.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    pal = sep.palette()
    pal.setColor(QPalette.ColorRole.Window, QColor(border))
    sep.setPalette(pal)
    sep.setStyleSheet(f"QFrame#FdDockDivider {{ background-color: {border}; border: none; }}")


def fd_apply_checkin_top_panel(panel: QFrame) -> None:
    """收银上区 — L0 canvas，内层 FdCard 浮出 card 色块。"""
    bg = _checkin_canvas()
    obj = panel.objectName() or "FdCheckinTopPanel"
    _frame_solid(panel, bg)
    panel.setStyleSheet(f"QFrame#{obj} {{ background-color: {bg}; border: none; }}")


def fd_apply_checkin_right_rail(rail: QFrame) -> None:
    """收银右栏 — L0 画布，与左栏 BillRail 同色，左右背景一致（莫兰迪统一底色）。"""
    bg = _checkin_canvas()  # 同左栏 — bg(L0)
    obj = rail.objectName() or "FdCheckinRightRail"
    _frame_solid(rail, bg)
    rail.setStyleSheet(f"QFrame#{obj} {{ background-color: {bg}; border: none; }}")


def fd_apply_card_action_bar(panel: QFrame) -> None:
    """③ 门锁/卡片操作区 — 收银卡片内用 surface 浮卡层。"""
    embedded = _aux_in_card(panel)
    if embedded and _in_checkin_panel(panel):
        p = panel.parentWidget()
        on_right = False
        while p is not None:
            if p.objectName() == "FdCheckinRightRail":
                on_right = True
                break
            p = p.parentWidget()
        bg = _p("surface") if on_right else _p("surface")
        border = _visible_border()
    elif embedded:
        bg = _p("surface")
        border = _p("border")
    else:
        bg = _p("surface_alt")
        border = _p("border")
    obj = panel.objectName() or "FdActionBar"
    _frame_solid(panel, bg)
    if embedded:
        sep = f" border-top: 1px solid {border};" if _in_checkin_panel(panel) else ""
        panel.setStyleSheet(
            f"QFrame#{obj} {{"
            f" background-color: {bg};"
            f" border: none;{sep}"
            f" border-radius: 0;"
            f" margin: 4px 0 0 0; padding: 8px 10px 0 10px;"
            f"}}"
        )
    else:
        panel.setStyleSheet(
            f"QFrame#{obj} {{"
            f" background-color: {bg};"
            f" border: 1px solid {border}; border-radius: 8px;"
            f" padding: 8px 10px;"
            f"}}"
        )


def fd_apply_checkin_bottom_dock(dock: QFrame) -> None:
    """收银底栏容器 — L1 surface_alt；流水/交班 dock 贴边铺满，零边距防露白缝。"""
    bg = _p("surface_alt")
    obj = dock.objectName() or "FdCheckinBottomDock"
    dock.setAutoFillBackground(True)
    dock.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    pal = dock.palette()
    pal.setColor(QPalette.ColorRole.Window, QColor(bg))
    dock.setPalette(pal)
    dock.setStyleSheet(
        f"QFrame#{obj} {{ background-color: {bg}; border: none; margin: 0; padding: 0; }}"
        f"QFrame#{obj} > QWidget {{ background: transparent; }}"
    )


def fd_apply_ledger_dock(dock: QFrame, *, flush_right: bool = False) -> None:
    """流水底栏 — 面板左缘金线 + 实底外框可辨。"""
    bg = _checkin_card()
    gold = _p("gold_thread")
    frame_border = _visible_border()
    obj = dock.objectName() or "FrontdeskLedgerDock"
    dock.setAutoFillBackground(True)
    dock.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    pal = dock.palette()
    pal.setColor(QPalette.ColorRole.Window, QColor(bg))
    dock.setPalette(pal)
    right = "border-right: none;" if flush_right else f"border-right: 1px solid {frame_border};"
    radius = "border-radius: 0;" if flush_right else "border-radius: 6px 0 0 6px;"
    dock.setStyleSheet(
        f"QFrame#{obj} {{"
        f" background-color: {bg};"
        f" border: 1px solid {frame_border};"
        f" border-left: 4px solid {gold};"
        f" {right}"
        f" {radius}"
        f" margin: 0; padding: 0;"
        f"}}"
    )


def fd_apply_shift_dock(dock: QFrame) -> None:
    """交班底栏 — 收银内 L1 bg_container 实底（surface 过浅肉眼像白底），无左金线。"""
    ck = _in_checkin_panel(dock)
    in_bottom = _in_bottom_dock(dock)
    bg = _p("bg_container") if ck else _p("surface")
    border = _visible_border() if ck else _p("border")
    note_bg = _p("bg_card") if ck else _p("surface_alt")
    text = _p("text")
    obj = dock.objectName() or "FdShiftDock"
    radius = "border-radius: 0;" if in_bottom else "border-radius: 0 6px 6px 0;"
    dock.setAutoFillBackground(True)
    dock.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    _frame_solid(dock, bg)
    dock.setStyleSheet(
        f"QFrame#{obj} {{"
        f" background-color: {bg};"
        f" border: 1px solid {border};"
        f" border-left: 1px solid {border};"
        f" margin: 0; padding: 0; {radius}"
        f"}}"
        f"QFrame#{obj} QPlainTextEdit#FdShiftNotes {{"
        f" background-color: {note_bg}; color: {text};"
        f" border: 1px solid {border}; border-radius: 6px; padding: 6px;"
        f"}}"
        f"QFrame#{obj} QPlainTextEdit {{"
        f" background-color: {note_bg}; color: {text};"
        f" border: 1px solid {border}; border-radius: 6px; padding: 6px;"
        f"}}"
    )


def fd_apply_vendor_stat_row(row: QFrame, *, value_color: str | None = None) -> None:
    """厂家控制台状态行。"""
    bg = _p("surface")
    border = _p("border")
    text = _p("text")
    vc = value_color or _p("primary")
    obj = row.objectName() or "VendorStatRow"
    row.setAutoFillBackground(True)
    row.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    pal = row.palette()
    pal.setColor(QPalette.ColorRole.Window, QColor(bg))
    row.setPalette(pal)
    row.setStyleSheet(
        f"QFrame#{obj} {{"
        f" background-color: {bg}; border: 1px solid {border};"
        f" border-radius: 6px; margin: 4px 0; padding: 2px 4px;"
        f"}}"
        f"QFrame#{obj} QLabel#VendorStatLabel {{"
        f" color: {text}; font-weight: 600; font-size: 12px; background: transparent;"
        f"}}"
        f"QFrame#{obj} QLabel#VendorStatValue {{"
        f" color: {vc}; font-weight: 700; font-family: Consolas, monospace; background: transparent;"
        f"}}"
    )


def fd_apply_ledger_filter_bar(bar: QFrame) -> None:
    """流水筛选条 — 实底条带，承载「全部/入账」等芯片。"""
    ck = _in_checkin_panel(bar)
    bg = _p("bg_container") if ck else _p("surface_alt")
    border = _visible_border() if ck else _p("border")
    obj = bar.objectName() or "FdLedgerFilterBar"
    _frame_solid(bar, bg)
    bar.setStyleSheet(
        f"QFrame#{obj} {{"
        f" background-color: {bg};"
        f" border-bottom: 1px solid {border};"
        f" border-radius: 0;"
        f"}}"
    )


def fd_apply_solid_primary_btn(btn, *, min_height: int = 36) -> None:
    """主按钮实色 — 防止父级 QSS 盖成透明。"""
    primary = _p("btn_primary")
    primary_hover = _p("btn_primary_hover")
    surface = _p("surface")
    obj = btn.objectName() or "SolidPrimaryBtn"
    btn.setStyleSheet(
        f"QPushButton#{obj} {{"
        f" background-color: {primary}; color: {surface};"
        f" border: 2px solid {primary}; border-radius: 8px;"
        f" padding: 0 16px; font-weight: 600; min-height: {min_height}px;"
        f"}}"
        f"QPushButton#{obj}:hover {{"
        f" background-color: {primary_hover}; border-color: {primary_hover};"
        f"}}"
    )


def fd_apply_ghost_btn(btn, *, min_height: int = 36) -> None:
    """次要/幽灵按钮 — 浅底有框。"""
    text = _p("text")
    border = _p("border")
    bg = _p("surface")
    obj = btn.objectName() or "FdGhostBtn"
    btn.setStyleSheet(
        f"QPushButton#{obj} {{"
        f" background-color: {bg}; color: {text};"
        f" border: 1px solid {border}; border-radius: 8px;"
        f" padding: 0 12px; min-height: {min_height}px;"
        f"}}"
    )


def fd_apply_info_banner(lbl) -> None:
    """信息条 — 实底 + 左强调线；卡片内用 surface 浮卡反差。"""
    if _in_checkin_panel(lbl):
        on_card_well = False
        p = lbl.parentWidget()
        while p is not None:
            if p.objectName() in ("FdShiftDock", "FdCard"):
                on_card_well = True
                break
            p = p.parentWidget()
        on_shift = False
        p2 = lbl.parentWidget()
        while p2 is not None:
            if p2.objectName() == "FdShiftDock":
                on_shift = True
                break
            p2 = p2.parentWidget()
        bg = _p("surface") if (on_shift or on_card_well) else _p("bg_container")
        border = _visible_border()
    else:
        bg = _p("surface")
        border = _p("border")
    text = _p("text")
    primary = _p("primary")
    lbl.setAutoFillBackground(True)
    lbl.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    pal = lbl.palette()
    pal.setColor(QPalette.ColorRole.Window, QColor(bg))
    lbl.setPalette(pal)
    lbl.setStyleSheet(
        f"QLabel#FdInfoBanner {{"
        f" background-color: {bg}; color: {text};"
        f" border: 1px solid {border}; border-left: 3px solid {primary};"
        f" padding: 8px 10px; font-size: 12px;"
        f"}}"
    )


def _aux_in_card(bar: QFrame) -> bool:
    p = bar.parentWidget()
    while p is not None:
        if p.objectName() == "FdCard":
            return True
        p = p.parentWidget()
    return False


def _checkin_bg_for(widget: QWidget) -> str:
    """收银 L0 canvas / L2 card — 嵌在 FdCard 内用 card，否则 canvas。"""
    p = widget
    while p is not None:
        if p.objectName() == "FdCard":
            return _checkin_card()
        if p.objectName() == "FdCheckinPanel":
            return _checkin_canvas()
        p = p.parentWidget()
    return _checkin_canvas()


def fd_apply_checkin_panel(panel: QFrame) -> None:
    """收银主面板 — L0 canvas 实底；内层 FdCard 另铺 L2 card。"""
    bg = _checkin_canvas()
    obj = panel.objectName() or "FdCheckinPanel"
    _frame_solid(panel, bg)
    panel.setStyleSheet(
        f"QFrame#{obj} {{ background-color: {bg}; border: none; margin: 0; padding: 0; }}"
    )


def fd_apply_panel_banner(banner: QFrame) -> None:
    """收银顶栏 — canvas 实底 + panel_border 底边。"""
    bg = _checkin_canvas()
    border = _visible_border()
    obj = banner.objectName() or "FdPanelBanner"
    _frame_solid(banner, bg)
    banner.setStyleSheet(
        f"QFrame#{obj} {{ background-color: {bg}; border: none;"
        f" border-bottom: 1px solid {border}; }}"
    )


def fd_apply_bill_tier_row(row: QFrame) -> None:
    """左账单费率/押金横条 — 与容器同色 bg_container。"""
    bg = _p("bg_container") if _in_checkin_panel(row) else _p("surface_alt")
    border = _visible_border() if _in_checkin_panel(row) else _p("border")
    obj = row.objectName() or "FdBillTierRow"
    _frame_solid(row, bg)
    row.setStyleSheet(
        f"QFrame#{obj} {{"
        f" background-color: {bg};"
        f" border: none;"
        f" border-top: 1px solid {border};"
        f" margin: 0; padding: 4px 8px;"
        f"}}"
    )


def fd_apply_compact_checkin_control(widget: QWidget, *, flat: bool = False) -> None:
    """收银 compact 输入 — palette 实底；flat=True 时直角，消除圆角缝露白。"""
    from PySide6.QtWidgets import QComboBox, QDoubleSpinBox, QLineEdit, QSpinBox

    bg = _p("bg_card")
    border = _p("panel_border")
    text = _p("text")
    radius = "0px" if flat else "4px"
    widget.setAutoFillBackground(True)
    widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    obj = widget.objectName() or "FdCompactInput"
    cls = widget.metaObject().className()
    base = (
        f"background-color: {bg}; color: {text};"
        f" border: 1px solid {border}; border-radius: {radius};"
        f" padding: 2px 8px; min-height: 28px;"
    )
    if isinstance(widget, QComboBox):
        widget.setStyleSheet(
            f"QComboBox#{obj} {{ {base} }}"
            f"QComboBox#{obj}::drop-down {{"
            f" background-color: {bg}; border: none;"
            f" border-left: 1px solid {border}; width: 20px;"
            f"}}"
            f"QComboBox#{obj} QAbstractItemView {{ background-color: {_p('bg_container')}; }}"
        )
    elif isinstance(widget, (QDoubleSpinBox, QSpinBox)):
        widget.setStyleSheet(f"QDoubleSpinBox#{obj}, QSpinBox#{obj} {{ {base} }}")
    elif isinstance(widget, QLineEdit):
        widget.setStyleSheet(f"QLineEdit#{obj} {{ {base} }}")
    else:
        widget.setStyleSheet(f"{cls}#{obj} {{ {base} }}")


def fd_apply_compact_filter_control(widget: QWidget) -> None:
    """全站紧凑筛选条 — surface 实底；去掉下拉竖线与清除钮分隔黑线。"""
    from PySide6.QtWidgets import QComboBox, QDoubleSpinBox, QLineEdit, QSpinBox

    if _in_checkin_panel(widget):
        return

    bg = _p("surface")
    border = _visible_border()
    text = _p("text")
    muted = _p("text_muted")
    accent = _p("accent")
    widget.setAutoFillBackground(True)
    widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    obj = widget.objectName() or "FdCompactField"
    pal = widget.palette()
    pal.setColor(QPalette.ColorRole.Base, QColor(bg))
    pal.setColor(QPalette.ColorRole.Text, QColor(text))
    widget.setPalette(pal)
    base = (
        f"background-color: {bg}; color: {text};"
        f" border: 2px solid {border}; border-radius: 8px;"
        f" padding: 0 10px; min-height: 32px; max-height: 36px;"
    )
    if isinstance(widget, QComboBox):
        widget.setStyleSheet(
            f"QComboBox#{obj} {{ {base} padding-right: 26px; }}"
            f"QComboBox#{obj}:hover {{ border-color: {muted}; }}"
            f"QComboBox#{obj}:focus {{ border: 2px solid {accent}; padding-right: 25px; }}"
            f"QComboBox#{obj}::drop-down {{"
            f" subcontrol-origin: padding; subcontrol-position: center right;"
            f" width: 22px; border: none; background: transparent;"
            f"}}"
            f"QComboBox#{obj}::down-arrow {{"
            f" width: 0; height: 0; border: none;"
            f" border-left: 5px solid transparent;"
            f" border-right: 5px solid transparent;"
            f" border-top: 6px solid {muted};"
            f" margin-right: 6px;"
            f"}}"
            f"QComboBox#{obj} QAbstractItemView {{"
            f" background-color: {_p('bg_container')}; border: 1px solid {border};"
            f" selection-background-color: {_p('selected_bg')};"
            f" selection-color: {_p('selected_fg')};"
            f"}}"
        )
    elif isinstance(widget, QLineEdit):
        widget.setStyleSheet(
            f"QLineEdit#{obj} {{ {base} }}"
            f"QLineEdit#{obj}:hover {{ border-color: {muted}; }}"
            f"QLineEdit#{obj}:focus {{ border: 2px solid {accent}; }}"
        )
    elif isinstance(widget, (QSpinBox, QDoubleSpinBox)):
        widget.setStyleSheet(
            f"QSpinBox#{obj}, QDoubleSpinBox#{obj} {{ {base} }}"
            f"QSpinBox#{obj}:focus, QDoubleSpinBox#{obj}:focus {{ border: 2px solid {accent}; }}"
        )


def fd_apply_console_control(widget: QWidget) -> None:
    """系统设置页输入控件 — palette+内联 QSS 实底（Windows 下全局 QSS 常不画边框）。"""
    from PySide6.QtWidgets import QComboBox, QDoubleSpinBox, QLineEdit, QSpinBox, QTimeEdit

    bg = _p("surface")
    border = _visible_border()
    text = _p("text")
    accent = _p("accent")
    widget.setAutoFillBackground(True)
    widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    if not widget.objectName():
        cls = widget.metaObject().className().replace("::", "")
        widget.setObjectName(f"Console{cls}")
    obj = widget.objectName()
    pal = widget.palette()
    pal.setColor(QPalette.ColorRole.Base, QColor(bg))
    pal.setColor(QPalette.ColorRole.Text, QColor(text))
    widget.setPalette(pal)
    base = (
        f"background-color: {bg}; color: {text};"
        f" border: 2px solid {border}; border-radius: 8px;"
        f" padding: 6px 10px; min-height: 32px;"
    )
    if isinstance(widget, QComboBox):
        widget.setStyleSheet(
            f"QComboBox#{obj} {{ {base} }}"
            f"QComboBox#{obj}:hover {{ border-color: {_p('text_muted')}; }}"
            f"QComboBox#{obj}:focus {{ border: 2px solid {accent}; padding: 5px 9px; }}"
            f"QComboBox#{obj}::drop-down {{"
            f" background-color: {bg}; border: none;"
            f" border-left: 1px solid {border}; width: 22px;"
            f"}}"
            f"QComboBox#{obj} QAbstractItemView {{"
            f" background-color: {_p('bg_container')};"
            f" border: 1px solid {border}; selection-background-color: {_p('selected_bg')};"
            f" selection-color: {_p('selected_fg')};"
            f"}}"
        )
    elif isinstance(widget, QTimeEdit):
        widget.setStyleSheet(
            f"QTimeEdit#{obj} {{ {base} }}"
            f"QTimeEdit#{obj}:focus {{ border: 2px solid {accent}; padding: 5px 9px; }}"
        )
    elif isinstance(widget, (QDoubleSpinBox, QSpinBox)):
        widget.setStyleSheet(
            f"QSpinBox#{obj}, QDoubleSpinBox#{obj} {{ {base} }}"
            f"QSpinBox#{obj}:focus, QDoubleSpinBox#{obj}:focus {{"
            f" border: 2px solid {accent}; padding: 5px 9px; }}"
        )
    elif isinstance(widget, QLineEdit):
        widget.setStyleSheet(
            f"QLineEdit#{obj} {{ {base} }}"
            f"QLineEdit#{obj}:hover {{ border-color: {_p('text_muted')}; }}"
            f"QLineEdit#{obj}:focus {{ border: 2px solid {accent}; padding: 5px 9px; }}"
        )


def fd_apply_settings_nav_tree(tree: QWidget) -> None:
    """设置导航树 — 分支列与选中行同色，消除左侧露白。"""
    from PySide6.QtWidgets import QTreeWidget

    if not isinstance(tree, QTreeWidget):
        return
    bg = _p("bg_container")
    border = _p("border")
    sel = _p("selected_bg")
    sel_fg = _p("selected_fg")
    hover = _p("surface_alt")
    text = _p("text")
    gold = _p("gold_thread")
    obj = tree.objectName() or "SettingsNavTree"
    tree.setAutoFillBackground(True)
    tree.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    pal = tree.palette()
    pal.setColor(QPalette.ColorRole.Base, QColor(bg))
    pal.setColor(QPalette.ColorRole.Window, QColor(bg))
    tree.setPalette(pal)
    tree.setStyleSheet(
        f"QTreeWidget#{obj} {{"
        f" background-color: {bg}; border: none;"
        f" border-right: 1px solid {border}; outline: 0; padding: 10px 0 8px 0;"
        f"}}"
        f"QTreeWidget#{obj}::item {{ padding: 7px 10px; color: {text}; }}"
        f"QTreeWidget#{obj}::item:has-children {{ color: {_p('text_muted')}; background: transparent; }}"
        f"QTreeWidget#{obj}::item:selected,"
        f"QTreeWidget#{obj}::item:selected:active,"
        f"QTreeWidget#{obj}::item:selected:!active {{"
        f" background-color: {sel}; color: {sel_fg}; font-weight: 700; border-radius: 6px;"
        f"}}"
        f"QTreeWidget#{obj}::branch:selected {{ background-color: {sel}; }}"
        f"QTreeWidget#{obj}::branch:has-children:selected {{ background-color: {sel}; }}"
        f"QTreeWidget#{obj}::item:hover:!selected {{ background-color: {hover}; border-radius: 6px; }}"
        f"QTreeWidget#{obj}::branch {{ background: transparent; }}"
    )
    tree.setProperty("console_nav_gold", gold)


def fd_apply_console_page(root: QWidget) -> None:
    """系统设置页 — 树 + 全部表单控件实底设色。"""
    from PySide6.QtWidgets import QComboBox, QDoubleSpinBox, QLineEdit, QSpinBox, QTimeEdit, QTreeWidget

    for tree in root.findChildren(QTreeWidget, "SettingsNavTree"):
        fd_apply_settings_nav_tree(tree)
    for w in root.findChildren(QWidget):
        if isinstance(w, (QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox, QTimeEdit)):
            fd_apply_console_control(w)
    search = root.findChild(QLineEdit, "ConsoleSearchInput")
    if search is not None:
        fd_apply_console_control(search)


def fd_apply_checkin_card_fill(frame: QFrame) -> None:
    """右卡内弹性空白 — 与右卡内层同色实底。"""
    bg = _p("bg_container")
    obj = frame.objectName() or "FdCheckinCardFill"
    frame.setMinimumHeight(4)
    from PySide6.QtWidgets import QSizePolicy
    frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
    _frame_solid(frame, bg)
    frame.setStyleSheet(
        f"QFrame#{obj} {{ background-color: {bg}; border: none; margin: 0; padding: 0; }}"
    )


def fd_apply_checkin_right_card_body(body: QFrame) -> None:
    """右收款卡内层 — 同 elevated 浮卡底色，与外层卡片一致（莫兰迪空间统一）。"""
    bg = _p("elevated")      # 卡片内层 — 与 fd_apply_card_panel 同色
    well = _p("bg")          # 数据井内底 — L0
    surface = _p("surface")  # 纯白数据面
    border = _p("panel_border")
    obj = body.objectName() or "FdCheckinRightCardBody"
    body.setAutoFillBackground(True)
    body.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    from PySide6.QtWidgets import QSizePolicy
    body.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
    pal = body.palette()
    pal.setColor(QPalette.ColorRole.Window, QColor(bg))
    body.setPalette(pal)
    body.setStyleSheet(
        f"QFrame#{obj} {{"
        f" background-color: {bg}; border: none;"
        f" border-radius: 8px; margin: 0; padding: 0;"
        f"}}"
        f"QFrame#{obj} QLabel {{ background: transparent; }}"
        f"QFrame#{obj} QWidget#PaymentMethodTiles {{"
        f" background-color: {well}; border: 1px solid {border}; border-radius: 8px;"
        f"}}"
        f"QFrame#{obj} QWidget#PaymentMethodTilesV4 {{"
        f" background-color: {well}; border: 1px solid {border}; border-radius: 8px;"
        f"}}"
        f"QFrame#{obj} QFrame#FdActionBar {{"
        f" background-color: {surface}; border-top: 1px solid {border}; border-radius: 0;"
        f"}}"
        f"QFrame#{obj} QFrame#FdCheckinCardFill {{ background-color: {bg}; }}"
    )


def fd_apply_aux_bar(bar: QFrame) -> None:
    """账单区底部辅助操作条 — 收银内嵌用 bg_container 铬条，勿与 L2 父卡融色。"""
    embedded = _aux_in_card(bar)
    if embedded and _in_checkin_panel(bar):
        bg = _p("bg_container")
    elif embedded:
        bg = _checkin_card()
    else:
        bg = _p("bg_root")
    obj = bar.objectName() or "FdAuxBar"
    _frame_solid(bar, bg)
    bar.setStyleSheet(
        f"QFrame#{obj} {{"
        f" background-color: {bg};"
        f" border: none; margin: 0; padding: 0;"
        f"}}"
    )


def fd_apply_checkin_vsplit(splitter) -> None:
    """收银上下分屏 — handle 与 L0 canvas 同色。"""
    from PySide6.QtWidgets import QSplitter

    bg = _checkin_canvas()
    if not isinstance(splitter, QSplitter):
        return
    splitter.setAutoFillBackground(True)
    splitter.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    pal = splitter.palette()
    pal.setColor(QPalette.ColorRole.Window, QColor(bg))
    splitter.setPalette(pal)
    splitter.setHandleWidth(0)
    splitter.setStyleSheet(
        f"QSplitter#FdCheckinVSplit {{ background-color: {bg}; border: none; margin: 0; padding: 0; }}"
        f"QSplitter#FdCheckinVSplit::handle {{ background-color: {bg}; height: 0; max-height: 0; border: none; }}"
    )
    for child in splitter.findChildren(QWidget):
        cn = child.metaObject().className()
        if "SplitterHandle" in cn:
            child.setFixedHeight(0)
            child.setMaximumHeight(0)
            child.hide()


def fd_apply_quick_btn(btn: QPushButton) -> None:
    """横幅快捷按钮 — 由 base.qss 全权控色（@surface_alt@ 底 + @primary@ 字/边框）。"""
    btn.setAutoFillBackground(True)
    btn.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)


def fd_apply_payment_tiles(tiles: QWidget) -> None:
    """支付方式磁贴区 — 卡片内 L1 bg_container 面板底。"""
    if _in_checkin_panel(tiles):
        bg = _p("bg_container")
        border = _p("panel_border")
    else:
        bg = _p("bg_root")
        border = _p("border")
    tiles.setAutoFillBackground(True)
    tiles.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    pal = tiles.palette()
    pal.setColor(QPalette.ColorRole.Window, QColor(bg))
    tiles.setPalette(pal)
    obj = tiles.objectName() or "PaymentMethodTiles"
    tiles.setStyleSheet(
        f"QWidget#{obj} {{ background-color: {bg}; border: 1px solid {border};"
        f" border-radius: 8px; padding: 4px; }}"
    )


def fd_apply_sticky_footer(footer: QFrame) -> None:
    """右栏吸底确认区 — 收银内与 card 同色。"""
    ck = _in_checkin_panel(footer)
    bg = _checkin_card() if ck else _p("bg_root")
    border = _visible_border()
    obj = footer.objectName() or "FdStickyFooter"
    _frame_solid(footer, bg)
    footer.setStyleSheet(
        f"QFrame#{obj} {{"
        f" background-color: {bg};"
        f" border-top: 2px solid {border};"
        f" border-left: none;"
        f"}}"
    )


def fd_apply_panel_sep(sep: QFrame) -> None:
    """面板内 1px 水平分隔 — 结构缝用 text_muted，旧钱绿下可辨。"""
    sep.setObjectName("FdPanelSep")
    sep.setFixedHeight(1)
    border = _visible_border()
    _frame_solid(sep, border)
    sep.setStyleSheet(
        f"QFrame#FdPanelSep {{ background-color: {border}; border: none; "
        f"min-height: 1px; max-height: 1px; }}"
    )


_PAGE_SCROLL_SURFACE = frozenset({
    "SystemConsolePage",
    "VendorConsolePage",
    "ConsoleSettingsPage",
})


def fd_apply_scroll_area(scroll, *, bg_key: str = "bg_root") -> None:
    """嵌套/匿名 QScrollArea — viewport + 内层 widget 实底（日志 C：#000000 透明链）。"""
    from PySide6.QtWidgets import QScrollArea

    if not isinstance(scroll, QScrollArea):
        return
    inner = scroll.widget()
    effective_key = bg_key
    oname = scroll.objectName() or ""
    if oname == "SmartHeaderCtxScroll":
        effective_key = "bg_container"
    elif oname == "MiniTabScrollArea":
        effective_key = "surface"
    elif oname == "VendorConsoleScroll":
        effective_key = "surface"
    elif oname == "PageScrollWrap":
        effective_key = "bg_root"
    elif inner is not None and inner.objectName() in _PAGE_SCROLL_SURFACE:
        effective_key = "surface"
    elif inner is not None and inner.objectName() == "ContentBox":
        effective_key = "surface"
    elif _in_checkin_panel(scroll):
        bg = _checkin_bg_for(scroll)
        effective_key = None
    else:
        bg = None
    if effective_key is not None:
        bg = _p(effective_key)
    scroll.setAutoFillBackground(True)
    scroll.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    sp = scroll.palette()
    sp.setColor(QPalette.ColorRole.Window, QColor(bg))
    scroll.setPalette(sp)
    vp = scroll.viewport()
    vp.setAutoFillBackground(True)
    vp.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    vpp = vp.palette()
    vpp.setColor(QPalette.ColorRole.Window, QColor(bg))
    vp.setPalette(vpp)
    scope = f"QScrollArea#{oname}" if oname else "QScrollArea"
    scroll.setStyleSheet(
        f"{scope} {{ border: none; }}"
    )
    if inner is not None and inner is not vp:
        if inner.objectName() == "ContentBox":
            return
        inner.setAutoFillBackground(True)
        inner.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        ip = inner.palette()
        ip.setColor(QPalette.ColorRole.Window, QColor(bg))
        inner.setPalette(ip)
        iname = inner.objectName()
        if iname:
            inner.setStyleSheet(f"QWidget#{iname} {{ background-color: {bg}; border: none; }}")
        else:
            inner.setStyleSheet(f"background-color: {bg}; border: none;")
    if oname == "PageScrollWrap" and inner is not None and inner.objectName():
        fd_apply_page_tab_root(inner)


def fd_apply_workspace_splitter(splitter) -> None:
    """工作台页 QSplitter — L0 画布缝（ContentBox 之间）。"""
    from PySide6.QtWidgets import QSplitter

    if not isinstance(splitter, QSplitter):
        return
    if splitter.objectName() == "FdCheckinVSplit":
        return
    bg = _p("bg_root")
    if not splitter.objectName():
        splitter.setObjectName("WorkspaceSplit")
    splitter.setAutoFillBackground(True)
    splitter.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    pal = splitter.palette()
    pal.setColor(QPalette.ColorRole.Window, QColor(bg))
    splitter.setPalette(pal)
    sid = splitter.objectName()
    splitter.setStyleSheet(
        f"QSplitter#{sid} {{ background-color: {bg}; border: none; }}"
        f"QSplitter#{sid}::handle {{ background-color: {bg}; }}"
    )


def fd_apply_content_text_edit(edit) -> None:
    """ContentBox 内只读文本井 — L3 bg_card。"""
    from PySide6.QtWidgets import QTextEdit

    if not isinstance(edit, QTextEdit):
        return
    bg = _p("bg_card")
    border = _p("panel_border")
    text = _p("text")
    obj = edit.objectName() or "ContentTextEdit"
    edit.setAutoFillBackground(True)
    edit.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    pal = edit.palette()
    pal.setColor(QPalette.ColorRole.Base, QColor(bg))
    pal.setColor(QPalette.ColorRole.Text, QColor(text))
    edit.setPalette(pal)
    edit.setStyleSheet(
        f"QTextEdit#{obj} {{"
        f" background-color: {bg}; color: {text};"
        f" border: 1px solid {border}; border-radius: 4px;"
        f" padding: 8px; font-family: Consolas, monospace; font-size: 12px;"
        f"}}"
    )


def fd_apply_label_card(lbl: QLabel) -> None:
    """具名信息卡 QLabel — AuditOverviewCard 与 ContentBox 同色；其余用 surface 浮卡。"""
    obj = lbl.objectName() or "LabelCard"
    if obj == "AuditOverviewCard":
        bg = _p("surface_alt")
        border = _p("panel_border")
        lbl.setAutoFillBackground(True)
        lbl.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        pal = lbl.palette()
        pal.setColor(QPalette.ColorRole.Window, QColor(bg))
        lbl.setPalette(pal)
        lbl.setStyleSheet(
            f"QLabel#{obj} {{"
            f" background-color: {bg}; color: {_p('text')};"
            f" border: 1px solid {border}; border-radius: 8px;"
            f" padding: 14px 16px;"
            f"}}"
        )
        return
    bg = _p("surface")
    obj = lbl.objectName() or "LabelCard"
    lbl.setAutoFillBackground(True)
    lbl.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    pal = lbl.palette()
    pal.setColor(QPalette.ColorRole.Window, QColor(bg))
    lbl.setPalette(pal)
    lbl.setStyleSheet(
        f"QLabel#{obj} {{"
        f" background-color: {bg}; color: {_p('text')};"
        f" border: none; border-radius: 8px;"
        f" padding: 14px 16px;"
        f"}}"
    )


def _surface_host(w: QWidget) -> str:
    p = w.parentWidget()
    while p is not None:
        name = p.objectName()
        if name in _PAGE_SCROLL_SURFACE:
            return name
        p = p.parentWidget()
    return ""


def fd_refresh_surfaces(root: QWidget) -> None:
    """主题切换后重刷子树内所有 ui_surface 设色（收银台/财务表等）。"""
    if root is None:
        return
    for page in root.findChildren(QWidget):
        on = page.objectName()
        if on == "SystemConsolePage" or on == "VendorConsolePage":
            fd_apply_page_tab_root(page)
        elif on == "ConsoleSettingsPage":
            fd_apply_settings_page(page)
        elif on in ("SettingsNavTree", "ConsoleSettingsStack", "VendorConsoleSubTabs"):
            fd_apply_panel_container(page)
    from PySide6.QtWidgets import QGroupBox

    for gb in root.findChildren(QGroupBox):
        gon = gb.objectName()
        if gon in ("ConsoleSettingsGroup", "VendorConsoleGroup") or _surface_host(gb) == "ConsoleSettingsPage":
            fd_apply_panel_groupbox(gb, fallback_name=gon or "PanelGroupBox")
    for frame in root.findChildren(QFrame):
        name = frame.objectName()
        if name == "BillRail":
            fd_apply_bill_rail(frame)
        elif name == "FdCheckinPanel":
            fd_apply_checkin_panel(frame)
        elif name == "FdPanelBanner":
            fd_apply_panel_banner(frame)
        elif name == "FdCard":
            in_rail = _card_in_bill_rail(frame)
            in_right = _card_in_right_rail(frame)
            fd_apply_card_panel(
                frame,
                flush_left=in_rail,
                gold_left=not (in_rail or in_right),
            )
        elif name == "FdBillFolioShell":
            table = frame.findChild(QTableWidget)
            if table is not None:
                fd_apply_bill_folio_shell(frame, table)
        elif name == "DataTableShell":
            table = frame.findChild(QTableWidget)
            if table is not None:
                fd_apply_data_table_shell(frame, table)
        elif name == "FdTotalsStrip":
            fd_apply_totals_strip(frame)
        elif name == "FdDockDivider":
            fd_apply_dock_divider(frame)
        elif name == "FdCheckinTopPanel":
            fd_apply_checkin_top_panel(frame)
        elif name == "FdCheckinRightRail":
            fd_apply_checkin_right_rail(frame)
        elif name == "FdActionBar":
            fd_apply_card_action_bar(frame)
        elif name == "FdHDivider":
            fd_apply_h_divider(frame)
        elif name == "FdCheckinBottomDock":
            fd_apply_checkin_bottom_dock(frame)
        elif name == "FrontdeskLedgerDock":
            flush_right = bool(getattr(frame, "_dock_mode", False))
            fd_apply_ledger_dock(frame, flush_right=flush_right)
            for shell in frame.findChildren(QFrame):
                if shell.objectName() == "DataTableShell":
                    table = shell.findChild(QTableWidget)
                    if table is not None:
                        fd_apply_data_table_shell(shell, table)
        elif name == "FdShiftDock":
            fd_apply_shift_dock(frame)
        elif name == "FdAuxBar":
            fd_apply_aux_bar(frame)
        elif name == "FdBillTierRow":
            fd_apply_bill_tier_row(frame)
        elif name == "FdCheckinCardFill":
            fd_apply_checkin_card_fill(frame)
        elif name == "FdCheckinRightCardBody":
            fd_apply_checkin_right_card_body(frame)
        elif name == "FdStickyFooter":
            fd_apply_sticky_footer(frame)
        elif name == "FdPanelSep":
            fd_apply_panel_sep(frame)
        elif name == "ContentBox":
            fd_apply_content_box(frame)
        elif name == "MatrixEmptyState":
            fd_apply_empty_state(frame)
        elif name == "VendorStatRow":
            fd_apply_vendor_stat_row(frame)
        elif name == "FdLedgerFilterBar":
            fd_apply_ledger_filter_bar(frame)
        elif name == "FdBillSectionHead":
            fd_apply_bill_section_head(frame)
        elif name == "FdBillFolioBlock":
            fd_apply_bill_folio_block(frame)
        elif name == "FdGoldLine":
            fd_apply_gold_line(frame)
        elif name == "FdSectionBar":
            in_content_box = False
            p = frame.parentWidget()
            while p is not None:
                if p.objectName() == "ContentBox":
                    in_content_box = True
                    break
                p = p.parentWidget()
            for line in frame.findChildren(QFrame):
                if line.objectName() == "FdGoldLine":
                    line.setVisible(not in_content_box)
            parent = frame.parentWidget()
            if parent is not None and parent.objectName() == "FrontdeskLedgerDock":
                fd_apply_section_bar_embedded(frame, bg_key="bg_container")
            elif parent is not None and parent.objectName() == "FdShiftDock":
                fd_apply_section_bar_embedded(frame, bg_key="bg_card")
            else:
                fd_apply_section_bar_embedded(frame, bg_key="surface_alt")
    for lbl in root.findChildren(QLabel):
        if lbl.objectName() == "FdInfoBanner":
            fd_apply_info_banner(lbl)
        elif lbl.objectName() == "AuditOverviewCard":
            fd_apply_label_card(lbl)
    from PySide6.QtWidgets import QTextEdit

    for te in root.findChildren(QTextEdit):
        if te.objectName() == "NightAuditReport":
            fd_apply_content_text_edit(te)
    from PySide6.QtWidgets import QSplitter

    for split in root.findChildren(QSplitter):
        if split.objectName() == "FdCheckinVSplit":
            fd_apply_checkin_vsplit(split)
        else:
            fd_apply_workspace_splitter(split)
    for tiles in root.findChildren(QWidget, "PaymentMethodTiles"):
        fd_apply_payment_tiles(tiles)
    for tiles in root.findChildren(QWidget, "PaymentMethodTilesV4"):
        fd_apply_payment_tiles(tiles)
    for btn in root.findChildren(QPushButton, "FdQuickBtn"):
        if _in_checkin_panel(btn):
            fd_apply_quick_btn(btn)
    for btn in root.findChildren(QPushButton, "FdGhostBtn"):
        if _in_checkin_panel(btn):
            fd_apply_ghost_btn(btn)
    for btn in root.findChildren(QPushButton, "SolidPrimaryBtn"):
        fd_apply_solid_primary_btn(btn)
    from PySide6.QtWidgets import QComboBox, QDoubleSpinBox, QLineEdit, QSpinBox
    for w in root.findChildren(QWidget):
        try:
            on = w.objectName()
        except RuntimeError:
            continue
        if on in ("FdCompactCombo", "FdCompactSpin", "FdCompactInput") and isinstance(
            w, (QComboBox, QDoubleSpinBox, QLineEdit, QSpinBox)
        ):
            if _in_checkin_panel(w):
                p = w.parentWidget()
                while p is not None:
                    if p.objectName() == "FdBillTierRow":
                        fd_apply_compact_checkin_control(w, flat=True)
                        break
                    p = p.parentWidget()
            else:
                fd_apply_compact_filter_control(w)
    from PySide6.QtWidgets import QScrollArea

    for scroll in root.findChildren(QScrollArea):
        fd_apply_scroll_area(scroll)
    if root.objectName() == "SystemConsolePage":
        fd_apply_console_page(root)
    for frame in root.findChildren(QFrame, "ContentBox"):
        fd_apply_content_box(frame)
    for tbl in root.findChildren(QTableWidget):
        fd_apply_table_palette(tbl)

    # agent log removed — debug_visual_probe deleted



def fd_connect_theme_refresh(root: QWidget, extra=None) -> None:
    """挂载 theme_changed：重刷 ui_surface 设色 + 可选页面级回调。"""
    from event_bus import bus

    def _go(_theme: str = "") -> None:
        fd_refresh_surfaces(root)
        if extra is not None:
            extra(_theme)
        root.update()

    bus.theme_changed.connect(_go)
    _go()
