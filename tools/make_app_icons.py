"""Render the existing geometric paper-plane mark for app manifests (development only)."""
from pathlib import Path
from PIL import Image, ImageDraw

root = Path(__file__).resolve().parents[1] / "web"
for size in (192, 512):
    scale = size * 3 / 40
    image = Image.new("RGB", (size * 3, size * 3), "#245cf2")
    draw = ImageDraw.Draw(image)
    points = [(12, 19), (29, 12), (23, 29), (19, 22), (12, 19)]
    draw.line([(round(x * scale), round(y * scale)) for x, y in points], fill="white", width=round(1.6 * scale), joint="curve")
    draw.line([(round(19 * scale), round(22 * scale)), (round(29 * scale), round(12 * scale))], fill="white", width=round(1.6 * scale))
    image.resize((size, size), Image.Resampling.LANCZOS).save(root / f"icon-{size}.png")
