"""Windows 单实例锁：防止用户连点打开多个 Solid。"""
from __future__ import annotations

import sys


def ensure_single_instance(app_name: str = "SolidHotelPMS") -> bool:
    """
    若已有实例在运行则提示并返回 False。
    必须在创建 QApplication 之前调用（仅用 Win32 API）。
    """
    if sys.platform != "win32":
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        user32 = ctypes.windll.user32
        mutex_name = f"Global\\{app_name}_SingleInstance_v1"
        handle = kernel32.CreateMutexW(None, True, mutex_name)
        last_error = kernel32.GetLastError()
        # ERROR_ALREADY_EXISTS = 183
        if last_error == 183:
            user32.MessageBoxW(
                None,
                "Solid 酒店管理系统已在运行中。\n\n请切换到任务栏中的窗口，不要重复打开。",
                "Solid",
                0x00000040,
            )
            if handle:
                kernel32.CloseHandle(handle)
            return False
        return True
    except Exception:
        return True
