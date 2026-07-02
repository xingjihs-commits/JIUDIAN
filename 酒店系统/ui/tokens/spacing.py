"""ui/tokens/spacing.py — 间距系统"""


class SpacingDesktop:
    XS = 4
    SM = 8
    MD = 12
    LG = 16
    XL = 20
    XXL = 24
    XXXL = 32
    XXXXL = 40


class SpacingTablet:
    XS = 3
    SM = 6
    MD = 10
    LG = 14
    XL = 18
    XXL = 20
    XXXL = 28
    XXXXL = 36


class SpacingMobile:
    XS = 2
    SM = 4
    MD = 8
    LG = 12
    XL = 16
    XXL = 20
    XXXL = 24
    XXXXL = 32


def get_spacing(level: str, screen_width: int) -> int:
    if screen_width >= 1080:
        return getattr(SpacingDesktop, level.upper(), 0)
    elif screen_width >= 768:
        return getattr(SpacingTablet, level.upper(), 0)
    else:
        return getattr(SpacingMobile, level.upper(), 0)
