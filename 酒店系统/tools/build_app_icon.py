# [DESIGNER-SPEC] 纯 Qt 绘制全套品牌位图（零外部图片依赖）
"""生成全套品牌位图 — 从 brand_assets Qt 绘制输出。

产出：
  assets/app_icon.png   — 512px 窗口/任务栏
  assets/app_icon.ico   — 16–256 多帧桌面图标
  assets/mark.png       — 192px 侧栏/闪屏/登录
  assets/mark_sm.png    — 56px 顶栏 @2x

用法：
  python tools/build_app_icon.py
"""
from __future__ import annotations

import io
import struct
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PIL import Image

ASSETS = Path(__file__).resolve().parent.parent / "assets"
BRAND = ASSETS / "brand"
PNG_OUT = ASSETS / "app_icon.png"
ICO_OUT = ASSETS / "app_icon.ico"
MARK_OUT = ASSETS / "mark.png"
MARK_SM_OUT = ASSETS / "mark_sm.png"

SIZES = [16, 24, 32, 48, 64, 96, 128, 256]
MARK_PX = 192
MARK_SM_PX = 56
APP_ICON_PX = 512


def _qt2pil(qpixmap: QPixmap) -> Image.Image:
    """将 QPixmap 转为 PIL Image (RGBA) — 通过临时 PNG 文件。"""
    import tempfile
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    tmp_path = tmp.name
    tmp.close()
    qpixmap.save(tmp_path, format="PNG")
    img = Image.open(tmp_path).convert("RGBA")
    Path(tmp_path).unlink(missing_ok=True)
    return img


def _make_ico(pil_sizes: list[Image.Image]) -> bytes:
    """生成 .ico 字节（多帧 PNG 封装）。"""
    buf = io.BytesIO()
    count = len(pil_sizes)
    # ICO header
    buf.write(struct.pack("<HHH", 0, 1, count))
    offset = 6 + count * 16
    for img in pil_sizes:
        w = img.width if img.width < 256 else 0
        h = img.height if img.height < 256 else 0
        png_data = io.BytesIO()
        img.save(png_data, format="PNG")
        png_bytes = png_data.getvalue()
        size = len(png_bytes)
        buf.write(struct.pack("<BBBBHHII", w, h, 0, 0, 1, 32, size, offset))
        offset += size
    for img in pil_sizes:
        png_data = io.BytesIO()
        img.save(png_data, format="PNG")
        buf.write(png_data.getvalue())
    return buf.getvalue()


def main():
    _ = QApplication(sys.argv)

    from brand_assets import make_brand_pixmap

    print("生成品牌位图（纯 Qt 绘制）...")

    # 1. app_icon.png (512)
    pix_512 = make_brand_pixmap(APP_ICON_PX)
    pix_512.save(str(PNG_OUT))
    print(f"  [OK] {PNG_OUT.name}  {APP_ICON_PX}px")

    # 2. mark.png (192)
    pix_192 = make_brand_pixmap(MARK_PX)
    pix_192.save(str(MARK_OUT))
    print(f"  [OK] {MARK_OUT.name}  {MARK_PX}px")

    # 3. mark_sm.png (56)
    pix_56 = make_brand_pixmap(MARK_SM_PX)
    pix_56.save(str(MARK_SM_OUT))
    print(f"  [OK] {MARK_SM_OUT.name}  {MARK_SM_PX}px")

    # 4. app_icon.ico (多帧)
    pil_images = []
    for s in SIZES:
        px = make_brand_pixmap(s)
        pil_images.append(_qt2pil(px))
    ico_bytes = _make_ico(pil_images)
    ICO_OUT.write_bytes(ico_bytes)
    print(f"  [OK] {ICO_OUT.name}  {len(SIZES)} sizes: {SIZES}")

    # 5. 同步到采集器
    collector_assets = Path(__file__).resolve().parent.parent.parent / "采集器" / "assets"
    if collector_assets.exists():
        for fname in ["app_icon.png", "mark.png", "mark_sm.png"]:
            src = ASSETS / fname
            if src.exists():
                dst = collector_assets / fname
                dst.write_bytes(src.read_bytes())
                print(f"  [OK] sync -> 采集器/{fname}")
        ico_src = ASSETS / "app_icon.ico"
        ico_dst = collector_assets / "app_icon.ico"
        if ico_src.exists():
            ico_dst.write_bytes(ico_src.read_bytes())
            print(f"  [OK] sync -> 采集器/app_icon.ico")
    print("完成！")


if __name__ == "__main__":
    main()
