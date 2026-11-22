from pathlib import Path
from PIL import Image, ImageDraw

OUT_ICO = Path(__file__).resolve().parent / "wattwatch.ico"


def render_icon(size: int) -> Image.Image:
    # Draw at 4x resolution for crisp anti-aliased scaling
    s = size * 4
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    pad = int(s * 0.06)
    rad = int(s * 0.22)
    draw.rounded_rectangle(
        (pad, pad, s - pad, s - pad),
        radius=rad,
        fill=(13, 25, 48, 255),
        outline=(56, 189, 248, 255),
        width=max(2, int(s * 0.05)),
    )

    bolt_pts = [
        (int(s * 0.56), int(s * 0.16)),
        (int(s * 0.28), int(s * 0.54)),
        (int(s * 0.47), int(s * 0.54)),
        (int(s * 0.42), int(s * 0.84)),
        (int(s * 0.72), int(s * 0.44)),
        (int(s * 0.53), int(s * 0.44)),
    ]
    draw.polygon(bolt_pts, fill=(34, 211, 238, 255))
    return img.resize((size, size), Image.Resampling.LANCZOS)


def main() -> None:
    sizes = [16, 24, 32, 48, 64, 128, 256]
    imgs = [render_icon(sz) for sz in sizes]
    imgs[-1].save(
        str(OUT_ICO),
        format="ICO",
        sizes=[(sz, sz) for sz in sizes],
        append_images=imgs[:-1],
    )
    print(f"Generated icon: {OUT_ICO} ({OUT_ICO.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
