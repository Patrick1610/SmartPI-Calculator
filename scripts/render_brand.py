"""Rebuild the original, code-drawn SmartPI icon (Pillow, development only)."""

from pathlib import Path

from PIL import Image, ImageDraw

size = 256
image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
draw = ImageDraw.Draw(image)
draw.rounded_rectangle((8, 8, 248, 248), radius=52, fill="#087f8c")
# A pi symbol above a rising response curve; no third-party branding.
draw.line([(67, 72), (191, 72)], fill="white", width=15)
draw.line([(100, 72), (100, 139)], fill="white", width=15)
draw.line([(161, 72), (161, 132), (178, 139)], fill="white", width=15)
draw.line(
    [(48, 198), (78, 198), (100, 185), (120, 164), (144, 153), (208, 153)],
    fill="#ffc857",
    width=10,
)
destination = (
    Path(__file__).resolve().parents[1] / "custom_components/smartpi_calculator/brand"
)
destination.mkdir(exist_ok=True)
image.save(destination / "icon.png")
