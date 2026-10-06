"""Rasterize vendored Lucide icons; no network access is needed during a build."""
import io
from pathlib import Path
import xml.etree.ElementTree as ET

from PIL import Image, ImageDraw
import resvg_py

ROOT = Path(__file__).resolve().parent


def render(name, color, size):
    tree = ET.parse(ROOT / "assets" / (name + ".svg"))
    tree.getroot().set("stroke", color)
    return resvg_py.svg_to_bytes(svg_string=ET.tostring(tree.getroot(), encoding="unicode"),
                                 width=size, height=size, skip_system_fonts=True)


def main():
    for svg in (ROOT / "assets").glob("*.svg"):
        svg.with_suffix(".png").write_bytes(render(svg.stem, "#45555e", 18))
    (ROOT / "assets" / "send-white.png").write_bytes(render("send", "#ffffff", 18))
    icon = Image.new("RGBA", (256, 256))
    ImageDraw.Draw(icon).rounded_rectangle((0, 0, 255, 255), radius=42, fill="#14765a")
    paper = Image.open(io.BytesIO(render("file-up", "#ffffff", 164)))
    icon.alpha_composite(paper, (46, 46))
    icon.resize((36, 36), Image.Resampling.LANCZOS).save(ROOT / "assets" / "app.png")
    icon.save(ROOT / "assets" / "app.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    icon.save(ROOT / "examples" / "sample.png")


if __name__ == "__main__":
    main()
