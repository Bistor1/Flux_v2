#!/usr/bin/env python3
"""Icon generator for Flux v2 — used by build_deb.sh.

Creates a single radial-gradient disc representing color temperature:
- Center: warm red
- Mid:    orange
- Rim:    cool blue
No sun, no moon, no purple. Recognizable as "temperature" at a glance.
"""
import sys


def create_icon(size=256):
    from PIL import Image, ImageDraw, ImageFilter
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    cx, cy = size // 2, size // 2
    r = size // 2 - 4

    warm = (239, 68, 68)    # red-500
    mid = (251, 146, 60)   # orange-400
    cool = (37, 99, 235)   # blue-600

    for px in range(r, 0, -1):
        t = 1 - (px / r)        # 0 rim, 1 center
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

    # Subtle glossy highlight
    try:
        hi = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        hdraw = ImageDraw.Draw(hi)
        hi_r = int(r * 0.55)
        hdraw.ellipse(
            [cx - hi_r, int(cy - r * 0.85), cx + hi_r, int(cy - r * 0.05)],
            fill=(255, 255, 255, 35),
        )
        hi = hi.filter(ImageFilter.GaussianBlur(radius=8))
        img.alpha_composite(hi)
    except Exception:
        pass

    return img


def main():
    if len(sys.argv) < 2:
        print("usage: gen_icon.py <output.png>", file=sys.stderr)
        sys.exit(2)
    out = sys.argv[1]
    try:
        img = create_icon(256)
        img.save(out, "PNG")
        print("Icon created: " + out)
    except Exception as e:
        print("WARNING: Icon generation failed: " + str(e), file=sys.stderr)
        try:
            from PIL import Image
            Image.new("RGBA", (256, 256), (245, 158, 11, 255)).save(out, "PNG")
        except Exception:
            pass


if __name__ == "__main__":
    main()
