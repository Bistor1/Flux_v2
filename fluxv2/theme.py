"""Colors, temperature range, and the label gradient."""

MIN_TEMP = 1000
MAX_TEMP = 6500
# 6500K is the identity ramp. The slider starts here because nothing has
# been applied yet; Evening (3400K) is a preset, not a hidden default.
DEFAULT_TEMP = 6500

PRESETS = [
    (6500, "Daylight"),
    (5500, "Neutral"),
    (4200, "Cool White"),
    (3400, "Evening"),
    (3000, "Cozy"),
    (2600, "Warm"),
    (2200, "Night"),
    (1800, "Deep Red"),
]

BG_COLOR = "#0f0f0f"
CARD_BG = "#1a1a1a"
# Warm orange-to-amber accent. The cool blue is the other end of the label.
ACCENT = "#f59e0b"
ACCENT_HOVER = "#fbbf24"
COOL = "#3b82f6"
COOL_HOVER = "#60a5fa"
TEXT_SECONDARY = "#888888"
SUCCESS = "#22c55e"
WARNING = "#f59e0b"
ERROR_COLOR = "#ff5555"

# Straight RGB from amber to blue browns out in the middle. These stops
# stay on the warm→white→cool path, so the label never goes purple or mud.
_TEMP_COLOR_STOPS = (
    (0.00, (239, 90, 20)),
    (0.40, (255, 176, 48)),
    (0.72, (255, 236, 210)),
    (1.00, (147, 197, 253)),
)


def temp_accent_hex(temp):
    """Label color for a temperature in MIN_TEMP..MAX_TEMP."""
    span = MAX_TEMP - MIN_TEMP
    ratio = (temp - MIN_TEMP) / span if span else 0.0
    if ratio <= _TEMP_COLOR_STOPS[0][0]:
        r, g, b = _TEMP_COLOR_STOPS[0][1]
    elif ratio >= _TEMP_COLOR_STOPS[-1][0]:
        r, g, b = _TEMP_COLOR_STOPS[-1][1]
    else:
        r, g, b = _TEMP_COLOR_STOPS[-1][1]
        for (left, c0), (right, c1) in zip(_TEMP_COLOR_STOPS, _TEMP_COLOR_STOPS[1:]):
            if ratio <= right:
                u = (ratio - left) / (right - left)
                r = int(c0[0] + (c1[0] - c0[0]) * u)
                g = int(c0[1] + (c1[1] - c0[1]) * u)
                b = int(c0[2] + (c1[2] - c0[2]) * u)
                break
    return "#%02x%02x%02x" % (r, g, b)
