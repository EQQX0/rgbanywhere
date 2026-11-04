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


# Windows icon (tray, title bar, shortcuts, installer)
ico_sizes = [16, 24, 32, 48, 64, 128, 256]
sized(256).save(os.path.join(ROOT, "rgb.ico"), format="ICO", sizes=[(s, s) for s in ico_sizes])

# Android launcher icons
for name, px in {"mdpi": 48, "hdpi": 72, "xhdpi": 96, "xxhdpi": 144, "xxxhdpi": 192}.items():
    out = os.path.join(ROOT, "android", "app", "res", "mipmap-" + name)
    os.makedirs(out, exist_ok=True)
    sized(px).save(os.path.join(out, "ic_launcher.png"))

# Web UI header logo and favicon / home-screen icon
sized(128).save(os.path.join(ROOT, "ui", "logo.png"), optimize=True)
sized(180).save(os.path.join(ROOT, "ui", "apple-touch-icon.png"), optimize=True)
print("icons ok")
