import json, sys
from pathlib import Path
from database import db

def _get_resource_dir() -> Path:
    """获取资源目录（兼容打包程序和直接运行）"""
    if getattr(sys, 'frozen', False):
        # PyInstaller 打包后，资源在 _MEIPASS 临时目录
        meipass = Path(sys._MEIPASS)
        # 优先用 _MEIPASS，但如果 translations 不存在，尝试 EXE 同目录
        if (meipass / "translations").exists():
            return meipass
        exe_dir = Path(sys.executable).parent
        if (exe_dir / "translations").exists():
            return exe_dir
        return meipass
    else:
        return Path(__file__).parent

# 支持的语言列表（key → 显示名称）
SUPPORTED_LANGUAGES = {
    "zh": "中文（简体）",
    "en": "英语",
    "th": "ภาษาไทย（泰语）",
    "vi": "Tiếng Việt（越南语）",
    "km": "ភាសាខ្មែរ（高棉语）",
}

class I18nEngine:
    def __init__(self):
        self.lang = db.get_config("language") or "zh"
        self.data = {}
        self._fallback = {}  # 中文兜底
        self.load(self.lang)

    def load(self, lang: str):
        """加载指定语言，找不到时自动降级到中文"""
        self.lang = lang
        path = _get_resource_dir() / "translations" / f"{lang}.json"
        if path.exists():
            with open(str(path), "r", encoding="utf-8") as f:
                self.data = json.load(f)
        else:
            self.data = {}
        # 加载中文兜底（非中文时使用）
        if lang != "zh":
            zh_path = _get_resource_dir() / "translations" / "zh.json"
            if zh_path.exists():
                with open(str(zh_path), "r", encoding="utf-8") as f:
                    self._fallback = json.load(f)
            else:
                self._fallback = {}
        else:
            self._fallback = {}

    def switch(self, lang: str):
        """切换语言并持久化到数据库，发出 language_changed 事件通知 UI 即时刷新。

        UI 组件监听 event_bus.language_changed 信号（str），收到后调用各自的
        retranslateUi() / refresh_labels() 方法即可无需重启切换语言。
        """
        self.load(lang)
        try:
            db.set_config("language", lang)
        except Exception:
            pass
        # 通知 UI 组件刷新
        try:
            from event_bus import bus
            bus.language_changed.emit(lang)
        except Exception:
            pass

    @staticmethod
    def _nested_get(data: dict, key: str):
        """用点分 key（如 'cardlock_frontdesk.title'）逐层访问嵌套字典"""
        parts = key.split(".")
        cur = data
        for p in parts:
            if isinstance(cur, dict):
                cur = cur.get(p)
            else:
                return None
        return cur

    def t(self, key: str, default: str | None = None) -> str:
        """翻译 key，支持点分嵌套查找（如 'cardlock_frontdesk.title'）。
        找不到时依次降级：当前语言 → 中文兜底 → default 参数 → key 本身"""
        val = self._nested_get(self.data, key)
        if val is not None:
            return val
        # 中文兜底
        val = self._nested_get(self._fallback, key)
        if val is not None:
            return val
        return default if default is not None else key

    def update_label(self, key: str, value: str):
        """动态更新翻译并写回文件"""
        self.data[key] = value
        path = _get_resource_dir() / "translations" / f"{self.lang}.json"
        with open(str(path), "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=4)

    @staticmethod
    def available_languages() -> dict:
        """返回已安装的语言列表"""
        result = {}
        res_dir = _get_resource_dir() / "translations"
        for key, name in SUPPORTED_LANGUAGES.items():
            if (res_dir / f"{key}.json").exists():
                result[key] = name
        return result

i18n = I18nEngine()
