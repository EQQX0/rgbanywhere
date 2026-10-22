"""Generate the launcher icons (same four-tile logo as the desktop app)."""
import os

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
SIZES = {"mdpi": 48, "hdpi": 72, "xhdpi": 96, "xxhdpi": 144, "xxxhdpi": 192}
TILES = [(255, 60, 0), (166, 39, 0), (255, 176, 0), (59, 130, 246)]

for name, px in SIZES.items():
    s = px * 4  # draw large, then downsample for smooth edges
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, s - 1, s - 1], radius=int(s * 0.22), fill=(13, 13, 17, 255))
    pad, gap = int(s * 0.2), int(s * 0.07)
    tile = (s - 2 * pad - gap) // 2
    for i, c in enumerate(TILES):
        x = pad + (i % 2) * (tile + gap)
        y = pad + (i // 2) * (tile + gap)
        d.rounded_rectangle([x, y, x + tile, y + tile], radius=int(tile * 0.28), fill=c + (255,))
    out = os.path.join(HERE, "app", "res", "mipmap-" + name)
    os.makedirs(out, exist_ok=True)
    img.resize((px, px), Image.LANCZOS).save(os.path.join(out, "ic_launcher.png"))
print("icons ok")
