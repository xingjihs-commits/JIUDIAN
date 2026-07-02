"""ui/components/ — 原子组件"""

from ui.components.toast import ToastManager, ToastType, toast

# ButtonSystem/OptimizedButton 延迟导入，避免 components.button_optimized → design_tokens 循环
_btn_lazy = None


def _get_btn():
    global _btn_lazy
    if _btn_lazy is None:
        from components.button_optimized import ButtonSystem, OptimizedButton
        _btn_lazy = (ButtonSystem, OptimizedButton)
    return _btn_lazy


def __getattr__(name: str):
    if name in ("ButtonSystem", "OptimizedButton"):
        vals = _get_btn()
        return vals[0] if name == "ButtonSystem" else vals[1]
    raise AttributeError(f"module 'ui.components' has no attribute '{name}'")


__all__ = ["ToastManager", "ToastType", "toast", "ButtonSystem", "OptimizedButton"]
