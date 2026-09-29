"""Prepare transparent PNG and multi-size ICO assets for Shadowing Trainer."""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageChops, ImageFilter


def remove_flat_black_background(image: Image.Image) -> Image.Image:
    """Recover a smooth alpha edge from the legacy icon's black matte."""
    rgba = image.convert("RGBA")
    alpha = rgba.getchannel("A")
    if alpha.getextrema()[0] < 255:
        return rgba

    red, green, blue, _ = rgba.split()
    brightness = ImageChops.lighter(ImageChops.lighter(red, green), blue)

    # The icon itself is vivid purple/cyan/white.  A high-confidence interior
    # mask lets us discard isolated dark pixels in the old black corners while
    # retaining just the antialiased pixels immediately around the silhouette.
    interior = brightness.point(lambda value: 255 if value >= 180 else 0)
    near_icon = interior.filter(ImageFilter.MaxFilter(7))
    recovered_alpha = Image.new("L", rgba.size, 0)
    recovered = recovered_alpha.load()
    values = brightness.load()
    nearby = near_icon.load()
    pixels = rgba.load()

    for y in range(rgba.height):
        for x in range(rgba.width):
            level = values[x, y]
            if level >= 180:
                recovered[x, y] = 255
                continue
            if nearby[x, y] == 0 or level <= 3:
                pixels[x, y] = (0, 0, 0, 0)
                continue

            edge_alpha = min(254, round(level * 255 / 235))
            recovered[x, y] = edge_alpha
            if edge_alpha:
                factor = 255 / edge_alpha
                r, g, b, _ = pixels[x, y]
                pixels[x, y] = (
                    min(255, round(r * factor)),
                    min(255, round(g * factor)),
                    min(255, round(b * factor)),
                    edge_alpha,
                )

    rgba.putalpha(recovered_alpha)
    return rgba


def write_checkerboard_preview(image: Image.Image, path: Path) -> None:
    tile = 48
    background = Image.new("RGBA", image.size, (235, 235, 235, 255))
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = overlay.load()
    for y in range(image.height):
        for x in range(image.width):
            if (x // tile + y // tile) % 2:
                draw[x, y] = (185, 185, 185, 255)
    background.alpha_composite(overlay)
    background.alpha_composite(image)
    background.convert("RGB").save(path, quality=95)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("png", type=Path)
    parser.add_argument("ico", type=Path)
    parser.add_argument("--preview", type=Path)
    args = parser.parse_args()

    icon = remove_flat_black_background(Image.open(args.png))
    icon.save(args.png, "PNG", optimize=True)
    icon.save(
        args.ico,
        "ICO",
        sizes=[(16, 16), (20, 20), (24, 24), (32, 32), (40, 40),
               (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    if args.preview:
        write_checkerboard_preview(icon, args.preview)


if __name__ == "__main__":
    main()
