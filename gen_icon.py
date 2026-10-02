#!/usr/bin/env python3
"""Write the Flux v2 icon. Used by the package build scripts."""

import sys

from fluxv2.icon import create_icon


def main():
    if len(sys.argv) < 2:
        print("usage: gen_icon.py <output.png>", file=sys.stderr)
        sys.exit(2)
    out = sys.argv[1]
    try:
        create_icon(256).save(out, "PNG")
        print("Icon created: " + out)
    except Exception as exc:
        print("WARNING: Icon generation failed: " + str(exc), file=sys.stderr)
        try:
            from PIL import Image
            Image.new("RGBA", (256, 256), (245, 158, 11, 255)).save(out, "PNG")
        except Exception:
            pass


if __name__ == "__main__":
    main()
