"""Build every app icon from assets/logo-2048.png (the rasterised assets/logo.svg).

Re-render the PNG after changing the SVG:
  chrome --headless=new --default-background-color=00000000 --window-size=2048,2048 --screenshot=assets/logo-2048.png <page showing logo.svg at 2048px>
Then run:  python assets/make_icons.py
"""
import os

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = Image.open(os.path.join(HERE, "logo-2048.png")).convert("RGBA")  # logo.svg rendered at 2048 px
K = SRC.width / 1024  # the coordinates below are for a 1024 px logo


def sized(px):
    return SRC.resize((px, px), Image.LANCZOS)


def bold():
    """Variant for small sizes: the ring with a much larger "Eq" on a plain dark disc, so it still reads at 16-48 px."""
    from PIL import ImageDraw
    im = SRC.copy()
    d = ImageDraw.Draw(im)
    d.ellipse([88 * K, 88 * K, 936 * K, 936 * K], fill=(16, 12, 8, 255))
    glyph = SRC.crop((int(265 * K), int(245 * K), int(750 * K), int(600 * K)))
    w = int(700 * K)
    glyph = glyph.resize((w, int(glyph.height * w / glyph.width)), Image.LANCZOS)
    im.alpha_composite(glyph, ((SRC.width - w) // 2, (SRC.height - glyph.height) // 2 - int(10 * K)))
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

# Android adaptive icon: the launcher masks it (circle, squircle...) over our dark background, so the logo is drawn
# at 66% of the 108 dp canvas, inside the safe zone. Without this, Android puts the old-style icon on a white plate.
for name, px in {"mdpi": 108, "hdpi": 162, "xhdpi": 216, "xxhdpi": 324, "xxxhdpi": 432}.items():
    canvas = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    logo = sized(round(px * 0.69))
    canvas.alpha_composite(logo, ((px - logo.width) // 2, (px - logo.height) // 2))
    canvas.save(os.path.join(ROOT, "android", "app", "res", "mipmap-" + name, "ic_launcher_foreground.png"))

# Web UI header logo and favicon / home-screen icon
sized(512).save(os.path.join(ROOT, "ui", "logo.png"), optimize=True)  # crisp on high-DPI screens
sized(512).save(os.path.join(ROOT, "ui", "apple-touch-icon.png"), optimize=True)
print("icons ok")
