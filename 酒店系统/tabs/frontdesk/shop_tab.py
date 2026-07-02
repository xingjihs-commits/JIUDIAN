"""超市收银页面

该页面由主窗口构造并传入前台工作台枢纽，
非本模块直接定义。此处保留为占位模块以保持导入兼容。

[sub-a] 库存闭环：销售/退货的库存自动扣减/回补实际逻辑落在
``shop_frontdesk.ShopTab`` 中（调用 ``db.reserve_shop_stock`` 原子扣减），
本模块仅做兼容 re-export，方便外部 ``from tabs.frontdesk.shop_tab import ShopTab``。
"""
from __future__ import annotations

# 延迟导入避免循环依赖：shop_frontdesk 导入大量 frontdesk_ui / ui_surface
def __getattr__(name: str):  # PEP 562
    if name == "ShopTab":
        from shop_frontdesk import ShopTab
        return ShopTab
    raise AttributeError(f"module 'tabs.frontdesk.shop_tab' has no attribute {name!r}")
