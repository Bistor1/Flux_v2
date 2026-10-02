"""Temperature-gradient icon shared by the window, tray, and package builds."""

try:
    from PIL import Image, ImageDraw, ImageFilter
    PIL_AVAILABLE = True
except ImportError:
    Image = ImageDraw = ImageFilter = None
    PIL_AVAILABLE = False


def create_icon(size=256):
    """Radial disc: warm red center, orange middle, cool blue rim.

    No sun, no moon, no purple. Returns None when Pillow is missing.
    """
    if not PIL_AVAILABLE:
        return None
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    cx, cy = size // 2, size // 2
    radius = size // 2 - 4

    warm = (239, 68, 68)
    mid = (251, 146, 60)
    cool = (37, 99, 235)
    for px in range(radius, 0, -1):
        t = 1 - (px / radius)  # 0 at rim, 1 at center
        if t > 0.5:
            u = (t - 0.5) / 0.5
            col = (
                int(mid[0] * (1 - u) + warm[0] * u),
                int(mid[1] * (1 - u) + warm[1] * u),
                int(mid[2] * (1 - u) + warm[2] * u),
                255,
            )
        else:
            u = t / 0.5
            col = (
                int(cool[0] * (1 - u) + mid[0] * u),
                int(cool[1] * (1 - u) + mid[1] * u),
                int(cool[2] * (1 - u) + mid[2] * u),
                255,
            )
        draw.ellipse([cx - px, cy - px, cx + px, cy + px], fill=col)

    try:
        highlight = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        hdraw = ImageDraw.Draw(highlight)
        hi_r = int(radius * 0.55)
        hdraw.ellipse(
            [cx - hi_r, int(cy - radius * 0.85), cx + hi_r, int(cy - radius * 0.05)],
            fill=(255, 255, 255, 35),
        )
        highlight = highlight.filter(ImageFilter.GaussianBlur(radius=8))
        img.alpha_composite(highlight)
    except Exception:
        pass
    return img
