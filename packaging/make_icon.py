"""生成 EthanDesk 图标：一个圆角方块 + 白色「E」，同时产出 png 和 ico。

图标是二进制产物，但生成脚本要留在仓库里，改样式时能重跑。
"""

from __future__ import annotations

import os

from PIL import Image, ImageDraw

ACCENT = (37, 99, 235)
ACCENT_DARK = (29, 78, 216)


def build(size: int = 512) -> Image.Image:
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    radius = int(size * 0.22)
    draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=ACCENT)

    # 右上角一块更深的天窗，视觉上像一块屏幕
    draw.rounded_rectangle(
        (int(size * 0.52), int(size * 0.12), int(size * 0.88), int(size * 0.40)),
        radius=int(size * 0.06),
        fill=ACCENT_DARK,
    )

    bar_h = int(size * 0.11)
    bar_w = int(size * 0.44)
    left = int(size * 0.18)
    for index, top in enumerate((0.30, 0.475, 0.64)):
        width = bar_w if index < 2 else int(bar_w * 0.68)
        draw.rounded_rectangle(
            (left, int(size * top), left + width, int(size * top) + bar_h),
            radius=int(bar_h * 0.28),
            fill=(255, 255, 255, 255),
        )
    return image


def main() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    assets = os.path.join(os.path.dirname(here), "assets")
    os.makedirs(assets, exist_ok=True)

    image = build(512)
    png = os.path.join(assets, "ethandesk.png")
    image.resize((256, 256), Image.LANCZOS).save(png, "PNG")
    image.save(
        os.path.join(assets, "ethandesk.ico"),
        "ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    print(f"png {png}")
    print(f"ico {os.path.join(assets, 'ethandesk.ico')}")


if __name__ == "__main__":
    main()
