"""ui/tokens/typography.py — 排版系统 + 字体栈"""


class Typography:
    class H1:
        SIZE = 28; WEIGHT = 700; LINE_HEIGHT = 1.2; LETTER_SPACING = -0.5

    class H2:
        SIZE = 24; WEIGHT = 700; LINE_HEIGHT = 1.25; LETTER_SPACING = -0.3

    class H3:
        SIZE = 18; WEIGHT = 600; LINE_HEIGHT = 1.3; LETTER_SPACING = 0

    class Body:
        SIZE = 13; WEIGHT = 400; LINE_HEIGHT = 1.5; LETTER_SPACING = 0

    class Caption:
        SIZE = 11; WEIGHT = 400; LINE_HEIGHT = 1.4; LETTER_SPACING = 0.3

    class Label:
        SIZE = 12; WEIGHT = 500; LINE_HEIGHT = 1.4; LETTER_SPACING = 0


class Fonts:
    DEFAULT = ["Segoe UI", "Roboto", "-apple-system", "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC"]
    MONOSPACE = ["JetBrains Mono", "Fira Code", "Courier New"]


class BorderRadius:
    NONE = 0; SM = 4; MD = 6; LG = 8; XL = 12


class Shadow:
    NONE = "none"
    SM = "0 1px 3px rgba(0, 0, 0, 0.08)"
    MD = "0 2px 8px rgba(0, 0, 0, 0.1)"
    LG = "0 4px 12px rgba(0, 0, 0, 0.12)"
    XL = "0 8px 24px rgba(0, 0, 0, 0.15)"
    BUTTON = "0 2px 8px rgba(0, 0, 0, 0.15)"
    CARD = "0 2px 8px rgba(0, 0, 0, 0.08)"
    MODAL = "0 8px 24px rgba(0, 0, 0, 0.2)"


class Animation:
    TIMING = {"fast": "0.1s", "normal": "0.2s", "slow": "0.3s", "slower": "0.5s"}
    EASING = {"ease_in_out": "cubic-bezier(0.4, 0, 0.2, 1)", "ease_out": "cubic-bezier(0.0, 0, 0.2, 1)", "ease_in": "cubic-bezier(0.4, 0, 1, 1)", "ease_linear": "linear"}

    @staticmethod
    def transition(properties: list, timing: str = "normal", easing: str = "ease_in_out") -> str:
        return f"{', '.join(properties)} {Animation.TIMING[timing]} {Animation.EASING[easing]}"
