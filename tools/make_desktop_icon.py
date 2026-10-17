from pathlib import Path

from PIL import Image

root = Path(__file__).resolve().parent.parent
source = root / "web" / "icon-512.png"
target = root / "web" / "icon.ico"
with Image.open(source) as image:
    image.save(target, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print(target)
