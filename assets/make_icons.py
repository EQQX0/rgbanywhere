"""Build every app icon from assets/logo-1024.png (the rasterised assets/logo.svg).

Re-render the PNG after changing the SVG:
  chrome --headless=new --default-background-color=00000000 --window-size=1024,1024 --screenshot=assets/logo-1024.png <page showing logo.svg at 1024px>
Then run:  python assets/make_icons.py
"""
import os

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = Image.open(os.path.join(HERE, "logo-1024.png")).convert("RGBA")


def sized(px):
    return SRC.resize((px, px), Image.LANCZOS)


def bold():
    """Variant for small sizes: the ring with a much larger "Eq" on a plain dark disc, so it still reads at 16-48 px."""
    from PIL import ImageDraw
    im = SRC.copy()
    d = ImageDraw.Draw(im)
    d.ellipse([88, 88, 936, 936], fill=(16, 12, 8, 255))
    glyph = SRC.crop((265, 245, 750, 600))
    w = 700
    glyph = glyph.resize((w, int(glyph.height * w / glyph.width)), Image.LANCZOS)
    im.alpha_composite(glyph, ((1024 - w) // 2, (1024 - glyph.height) // 2 - 10))
    return im


def small(px):
    return bold().resize((px, px), Image.LANCZOS)


# Windows icon (tray, title bar, shortcuts, installer)
import io
import struct

frames = []
for px in (16, 24, 32, 48, 64, 128, 256):
    img = small(px) if px <= 48 else sized(px)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    frames.append((px, buf.getvalue()))
out = struct.pack("<HHH", 0, 1, len(frames))
offset = 6 + 16 * len(frames)
for px, data in frames:
    out += struct.pack("<BBBBHHII", px % 256, px % 256, 0, 0, 1, 32, len(data), offset)
    offset += len(data)
with open(os.path.join(ROOT, "rgb.ico"), "wb") as f:
    f.write(out + b"".join(d for _, d in frames))

# Android launcher icons
for name, px in {"mdpi": 48, "hdpi": 72, "xhdpi": 96, "xxhdpi": 144, "xxxhdpi": 192}.items():
    out = os.path.join(ROOT, "android", "app", "res", "mipmap-" + name)
    os.makedirs(out, exist_ok=True)
    sized(px).save(os.path.join(out, "ic_launcher.png"))

# Web UI header logo and favicon / home-screen icon
sized(160).save(os.path.join(ROOT, "ui", "logo.png"), optimize=True)
sized(180).save(os.path.join(ROOT, "ui", "apple-touch-icon.png"), optimize=True)
print("icons ok")
