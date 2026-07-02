"""
room_standee_renderer.py — A6 立牌渲染器（内置极简模板）

A6 横版 148×105mm @300DPI = 1748×1240px
纯代码渲染，不依赖外部底板 PNG。
支持单张 A6 和 A4 竖版拼版（一页 2 张 A6 上下排列，含裁切线）。

设计理念：大面积留白，信息极简，字体加粗。
"""

from __future__ import annotations

import logging
import os
from io import BytesIO
from pathlib import Path
from typing import List, Optional, Tuple

from database import db
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

# ─── 纸张常量（mm） ──────────────────────────────────────────────────────────
A6_MM_W = 148   # 横版宽
A6_MM_H = 105   # 横版高
A4_MM_W = 210   # 竖版宽
A4_MM_H = 297   # 竖版高
DPI = 300
MM_TO_PX = DPI / 25.4  # 1mm ≈ 11.811px

A6_PX_W = int(A6_MM_W * MM_TO_PX)   # 1748
A6_PX_H = int(A6_MM_H * MM_TO_PX)   # 1240
A4_PX_W = int(A4_MM_W * MM_TO_PX)   # 2480
A4_PX_H = int(A4_MM_H * MM_TO_PX)   # 3508

# A6 在 A4 竖版中的偏移（居中，左右留白）
A6_X_IN_A4 = (A4_PX_W - A6_PX_W) // 2   # ~366px ≈ 31mm 留白
A6_TOP_IN_A4 = int(8 * MM_TO_PX)         # 上边距 8mm
A6_GAP_IN_A4 = int(14 * MM_TO_PX)        # 两张 A6 间距 14mm（含裁切线）

QR_SIZE_MM = 55
QR_SIZE_PX = int(QR_SIZE_MM * MM_TO_PX)  # ~650px

# ─── 排版参数（mm 基准，统一换算 px） ──────────────────────────────────────

def _px(mm: float) -> int:
    return int(mm * MM_TO_PX)


# 各元素在 A6 画布上的 Y 坐标（从顶部算）
Y_WELCOME = _px(18)      # 「欢迎入住 · 酒店名」
Y_ROOM = _px(28)          # 「308 房」
Y_QR = _px(40)            # 二维码顶部
Y_USAGE = _px(98)         # 用途说明（从底部往上）
Y_SAFETY = _px(98)        # 安全提示（底部）—— 实际会自适应

# 字体大小
FONT_WELCOME = 26   # pt
FONT_ROOM = 22      # pt
FONT_USAGE = 17     # pt
FONT_SAFETY = 11    # pt

# 颜色
COLOR_TITLE = "#222222"
COLOR_BODY = "#444444"
COLOR_MUTED = "#999999"
COLOR_BG = "#FFFFFF"
COLOR_CUT = "#CCCCCC"  # 裁切线


# ─── 字体加载 ────────────────────────────────────────────────────────────────

def _load_font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    """加载中文字体，优先微软雅黑加粗。"""
    import deploy_paths
    fonts_dir = deploy_paths.fonts_dir()
    candidates = []
    if bold:
        candidates += [
            os.path.join(fonts_dir, "msyhbd.ttc"),
            os.path.join(fonts_dir, "msyh.ttc"),
            os.path.join(fonts_dir, "simhei.ttf"),
        ]
    else:
        candidates += [
            os.path.join(fonts_dir, "msyh.ttc"),
            os.path.join(fonts_dir, "msyhl.ttc"),
            os.path.join(fonts_dir, "simhei.ttf"),
        ]
    candidates += [
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


# ─── 内置 A6 单张渲染 ───────────────────────────────────────────────────────

def render_a6_standee(room_id: str, qr_url: str) -> Image.Image:
    """
    渲染单张 A6 立牌（148×105mm 横版 @300DPI）。
    纯代码底板，不需要外部 PNG。
    返回 PIL Image RGBA。
    """
    img = Image.new("RGBA", (A6_PX_W, A6_PX_H), COLOR_BG)
    draw = ImageDraw.Draw(img)

    hotel = (db.get_config("hotel_name") or "").strip() or "酒店"
    cx = A6_PX_W // 2  # 水平居中

    # ── 欢迎语 + 酒店名 ──
    welcome_text = f"欢迎入住 · {hotel}"
    font_welcome = _load_font(FONT_WELCOME, bold=True)
    bbox = draw.textbbox((0, 0), welcome_text, font=font_welcome)
    tw = bbox[2] - bbox[0]
    draw.text((cx - tw // 2, Y_WELCOME), welcome_text, fill=COLOR_TITLE, font=font_welcome)

    # ── 房号 ──
    room_text = f"{room_id} 房"
    font_room = _load_font(FONT_ROOM, bold=True)
    bbox = draw.textbbox((0, 0), room_text, font=font_room)
    tw = bbox[2] - bbox[0]
    draw.text((cx - tw // 2, Y_ROOM), room_text, fill=COLOR_TITLE, font=font_room)

    # ── 二维码 ──
    qr_img = _make_qr_image(qr_url, QR_SIZE_PX)
    qx = cx - QR_SIZE_PX // 2
    qy = Y_QR
    # 二维码区域白底（防止透明通道问题）
    draw.rounded_rectangle(
        [qx - 6, qy - 6, qx + QR_SIZE_PX + 6, qy + QR_SIZE_PX + 6],
        radius=8, fill=COLOR_BG, outline="#E0E0E0", width=2,
    )
    img.paste(qr_img, (qx, qy), qr_img)

    # ── 用途说明（二维码下方） ──
    usage_y = qy + QR_SIZE_PX + _px(6)
    usage_lines = [
        "扫码连 WiFi · 叫保洁 · 前台服务 · 逛超市",
    ]
    font_usage = _load_font(FONT_USAGE, bold=True)
    for i, line in enumerate(usage_lines):
        bbox = draw.textbbox((0, 0), line, font=font_usage)
        tw = bbox[2] - bbox[0]
        draw.text((cx - tw // 2, usage_y + i * _px(7)), line, fill=COLOR_BODY, font=font_usage)

    # ── 安全提示（底部） ──
    safety_text = "退房后扫码自动失效"
    font_safety = _load_font(FONT_SAFETY, bold=True)
    bbox = draw.textbbox((0, 0), safety_text, font=font_safety)
    tw = bbox[2] - bbox[0]
    safety_y = A6_PX_H - _px(10)
    draw.text((cx - tw // 2, safety_y), safety_text, fill=COLOR_MUTED, font=font_safety)

    return img


# ─── A4 竖版拼版（2 张 A6 上下排列） ────────────────────────────────────────

def render_a4_sheet(rooms: List[Tuple[str, str]]) -> Image.Image:
    """
    A4 竖版拼版：上下各一张 A6，中间裁切线。
    rooms: [(room_id, qr_url), ...] 最多 2 个，最少 1 个。
    返回 PIL Image RGBA (A4 竖版 @300DPI)。
    """
    sheet = Image.new("RGBA", (A4_PX_W, A4_PX_H), COLOR_BG)
    draw = ImageDraw.Draw(sheet)

    for i, (room_id, qr_url) in enumerate(rooms[:2]):
        a6 = render_a6_standee(room_id, qr_url)
        # 缩放 A6 到精确像素（render_a6_standee 已经是精确值，保险起见）
        if a6.size != (A6_PX_W, A6_PX_H):
            a6 = a6.resize((A6_PX_W, A6_PX_H), Image.Resampling.LANCZOS)
        top_y = A6_TOP_IN_A4 + i * (A6_PX_H + A6_GAP_IN_A4)
        sheet.paste(a6, (A6_X_IN_A4, top_y), a6)

    # ── 裁切线（两张 A6 之间） ──
    if len(rooms) >= 2:
        cut_y = A6_TOP_IN_A4 + A6_PX_H + A6_GAP_IN_A4 // 2
        dash_len = _px(3)
        gap_len = _px(3)
        x_start = A6_X_IN_A4
        x_end = A6_X_IN_A4 + A6_PX_W
        x = x_start
        while x < x_end:
            draw.line([(x, cut_y), (min(x + dash_len, x_end), cut_y)], fill=COLOR_CUT, width=2)
            x += dash_len + gap_len

        # 裁切标记（两端小剪刀符号用竖线代替）
        marker_len = _px(6)
        draw.line([(x_start, cut_y - marker_len), (x_start, cut_y + marker_len)], fill=COLOR_CUT, width=2)
        draw.line([(x_end, cut_y - marker_len), (x_end, cut_y + marker_len)], fill=COLOR_CUT, width=2)

    return sheet


def render_a4_batch(rooms: List[Tuple[str, str]]) -> List[Image.Image]:
    """
    批量渲染：每 2 个房间一张 A4 竖版。
    返回 Image 列表。
    """
    sheets = []
    for i in range(0, len(rooms), 2):
        chunk = rooms[i:i + 2]
        sheets.append(render_a4_sheet(chunk))
    return sheets


# ─── 二维码生成 ──────────────────────────────────────────────────────────────

def _make_qr_image(url: str, size: int) -> Image.Image:
    import qrcode
    qr = qrcode.QRCode(version=1, box_size=10, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGBA")
    return img.resize((size, size), Image.Resampling.LANCZOS)


# ─── 保存接口 ────────────────────────────────────────────────────────────────

def save_a6_png(room_id: str, qr_url: str, save_path: str) -> bool:
    """保存单张 A6 立牌 PNG。"""
    try:
        img = render_a6_standee(room_id, qr_url)
        img.convert("RGB").save(save_path, "PNG", dpi=(DPI, DPI))
        return True
    except Exception as exc:
        logger.warning("[STANDEE] A6 save failed %s: %s", room_id, exc)
        return False


def save_a4_sheet_png(rooms: List[Tuple[str, str]], save_path: str) -> bool:
    """保存单张 A4 拼版 PNG（含裁切线）。"""
    try:
        sheet = render_a4_sheet(rooms)
        sheet.convert("RGB").save(save_path, "PNG", dpi=(DPI, DPI))
        return True
    except Exception as exc:
        logger.warning("[STANDEE] A4 sheet save failed: %s", exc)
        return False


def export_all_a6(
    output_dir: str,
    rooms: Optional[List[Tuple[str, str]]] = None,
) -> Tuple[int, int]:
    """
    批量导出单张 A6 PNG。
    rooms: [(room_id, ...), ...] 或 None=全部房间。
    返回 (成功数, 总数)。
    """
    from qr_code_service import LiveQrNotReadyError, QRTokenService

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    if rooms is None:
        rows = db.execute("SELECT room_id FROM rooms ORDER BY room_id").fetchall()
        room_ids = [r[0] for r in rows]
    else:
        room_ids = [r[0] if isinstance(r, (list, tuple)) else r for r in rooms]

    ok = 0
    for rid in room_ids:
        try:
            url = QRTokenService.build_qr_url(str(rid))
        except LiveQrNotReadyError:
            continue
        path = out / f"room_{rid}_standee.png"
        if save_a6_png(str(rid), url, str(path)):
            ok += 1
    return ok, len(room_ids)


def export_all_a4(
    output_dir: str,
    rooms: Optional[List[Tuple[str, str]]] = None,
) -> Tuple[int, int]:
    """
    批量导出 A4 拼版 PNG（每张 2 个房间，含裁切线）。
    返回 (成功张数, 总房间数)。
    """
    from qr_code_service import LiveQrNotReadyError, QRTokenService

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    if rooms is None:
        rows = db.execute("SELECT room_id FROM rooms ORDER BY room_id").fetchall()
        room_ids = [r[0] for r in rows]
    else:
        room_ids = [r[0] if isinstance(r, (list, tuple)) else r for r in rooms]

    # 收集有效的 (room_id, qr_url)
    valid: List[Tuple[str, str]] = []
    for rid in room_ids:
        try:
            url = QRTokenService.build_qr_url(str(rid))
            valid.append((str(rid), url))
        except LiveQrNotReadyError:
            continue

    sheets = render_a4_batch(valid)
    for i, sheet in enumerate(sheets):
        path = out / f"standee_a4_{i + 1:03d}.png"
        sheet.convert("RGB").save(str(path), "PNG", dpi=(DPI, DPI))

    return len(sheets), len(valid)


# ─── 兼容旧接口 ──────────────────────────────────────────────────────────────

def render_standee_image(room_id: str, qr_url: str) -> Image.Image:
    """兼容旧调用，现在直接走内置 A6 模板。"""
    return render_a6_standee(room_id, qr_url)


def save_standee_png(room_id: str, qr_url: str, save_path: str) -> bool:
    """兼容旧调用。"""
    return save_a6_png(room_id, qr_url, save_path)


def export_all_standees(
    output_dir: str,
    rooms: Optional[list] = None,
) -> Tuple[int, int]:
    """兼容旧调用，默认导出 A6 单张。"""
    return export_all_a6(output_dir, rooms)


def template_ready() -> Tuple[bool, str]:
    """内置模板始终可用。"""
    return True, "内置 A6 极简模板（148×105mm @300DPI）"


def template_image_path() -> Path:
    """兼容旧接口，但内置模板无外部文件。"""
    return Path("builtin://a6_standee")


def layout_path() -> Path:
    """兼容旧接口。"""
    return Path("builtin://a6_layout")


def load_layout():
    """兼容旧接口，返回空（新版不使用 JSON 布局）。"""
    return {"slots": {}}


def standee_assets_dir() -> Path:
    from deploy_paths import bundled_path
    d = bundled_path("assets", "room_standee")
    d.mkdir(parents=True, exist_ok=True)
    return d


def standee_folder_instructions() -> str:
    folder = standee_assets_dir().resolve()
    return (
        "【A6 立牌模板 — 内置极简设计】\n"
        f"导出目录：{folder}\n\n"
        "立牌规格：148×105mm 横版 @300DPI\n"
        "单张 A6：直接打印或送打印店\n"
        "A4 拼版：办公室打印机打出来，沿裁切线裁开即可\n\n"
        "设计：大面积留白 · 二维码 55mm · 字体加粗"
    )


def copy_standee_folder_path() -> str:
    path = str(standee_assets_dir().resolve())
    try:
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(path)
    except Exception:
        pass
    return path


def open_standee_folder():
    import subprocess
    import sys

    d = standee_assets_dir()
    d.mkdir(parents=True, exist_ok=True)
    path = str(d.resolve())
    if sys.platform == "win32":
        os.startfile(path)
    elif sys.platform == "darwin":
        subprocess.run(["open", path], check=False)
    else:
        subprocess.run(["xdg-open", path], check=False)
    return path


def build_standee_hint_widget(parent=None):
    """可嵌入对话框的立牌说明条（新版 A6 内置模板）。"""
    from design_tokens import _p
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

    frame = QFrame(parent)
    frame.setStyleSheet(
        f"QFrame {{ background:{_p('surface_alt')}; border:2px solid {_p('primary')}; border-radius:10px; }}"
    )
    root = QVBoxLayout(frame)
    root.setContentsMargins(12, 10, 12, 10)
    root.setSpacing(8)

    title = QLabel("🪧 A6 立牌（148×105mm 横版 · 极简留白）— 内置模板")
    title.setStyleSheet(f"font-weight:700; color:{_p('primary')}; font-size:13px;")
    root.addWidget(title)

    sub = QLabel(
        "内置版式：欢迎语+酒店名 · 房号 · 55mm 二维码 · 扫码用途说明 · 安全提示\n"
        "大面积留白，字体加粗，300DPI 高清输出。"
    )
    sub.setWordWrap(True)
    sub.setStyleSheet(f"color:{_p('text_muted')}; font-size:11px;")
    root.addWidget(sub)

    folder = standee_assets_dir().resolve()
    path_lbl = QLabel(
        f"📁 导出目录：\n{folder}\n\n"
        f"单张 A6：直接送打印店或热敏打印机\n"
        f"A4 拼版：办公室普通打印机，打完沿裁切线裁开"
    )
    path_lbl.setWordWrap(True)
    path_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    path_lbl.setStyleSheet(
        f"font-family:Consolas,'Microsoft YaHei'; font-size:11px; color:{_p('text')}; "
        f"background:{_p('surface')}; border:1px solid {_p('primary')}; border-radius:6px; padding:8px;"
    )
    root.addWidget(path_lbl)

    status = QLabel("✅ 内置模板已就绪，无需额外准备底板图")
    status.setWordWrap(True)
    status.setStyleSheet(f"font-size:12px; font-weight:600; color:{_p('amount_positive')};")
    root.addWidget(status)

    row = QHBoxLayout()
    btn_open = QPushButton("📂 打开导出目录")
    btn_open.setObjectName("FdGhostBtn")
    btn_open.setStyleSheet(
        f"background:{_p('primary')}; color:white; font-weight:bold; "
        "border-radius:8px; padding:8px 14px;"
    )
    btn_copy = QPushButton("📋 复制目录路径")
    btn_copy.setObjectName("FdGhostBtn")
    btn_copy.setStyleSheet(
        f"background:{_p('surface_alt')}; color:{_p('text_muted')}; border-radius:8px; padding:8px 14px;"
    )

    def _open():
        open_standee_folder()

    def _copy():
        p = copy_standee_folder_path()
        status.setText(f"✅ 已复制路径到剪贴板：\n{p}")

    btn_open.clicked.connect(_open)
    btn_copy.clicked.connect(_copy)
    row.addWidget(btn_open)
    row.addWidget(btn_copy)
    row.addStretch()
    root.addLayout(row)

    return frame
