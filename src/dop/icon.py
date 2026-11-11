"""应用图标生成：程序图标（带金框的小油画）与托盘小图标。"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw

try:
    RESAMPLE = Image.Resampling.LANCZOS
except AttributeError:  # pragma: no cover
    RESAMPLE = Image.LANCZOS

GOLD_DARK = (120, 86, 26)
GOLD = (196, 152, 58)
GOLD_LIGHT = (238, 206, 130)
FRAME_BG = (32, 26, 20)


def make_app_icon(size: int = 256) -> Image.Image:
    """生成一个"镀金画框里的小风景"图标，纯代码绘制，不依赖外部素材。"""
    scale = size / 256.0
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    def s(v: float) -> float:
        return v * scale

    # 外框
    draw.rounded_rectangle([s(6), s(6), s(250), s(250)], radius=s(18), fill=FRAME_BG)
    draw.rounded_rectangle([s(12), s(12), s(244), s(244)], radius=s(14), fill=GOLD_DARK)
    draw.rounded_rectangle([s(17), s(17), s(239), s(239)], radius=s(11), fill=GOLD)
    draw.rounded_rectangle([s(21), s(21), s(235), s(235)], radius=s(9), fill=GOLD_LIGHT)
    draw.rounded_rectangle([s(27), s(27), s(229), s(229)], radius=s(6), fill=GOLD)

    # 画布区域
    left, top, right, bottom = s(36), s(36), s(220), s(220)
    canvas = Image.new("RGB", (int(right - left), int(bottom - top)))
    cw, ch = canvas.size
    cd = ImageDraw.Draw(canvas)
    # 天空渐变
    for y in range(ch):
        t = y / max(1, ch - 1)
        cd.line(
            [(0, y), (cw, y)],
            fill=(
                int(246 + (204 - 246) * t),
                int(206 + (150 - 206) * t),
                int(150 + (104 - 150) * t),
            ),
        )
    # 太阳
    sun_r = cw * 0.10
    sx, sy = cw * 0.70, ch * 0.28
    cd.ellipse([sx - sun_r, sy - sun_r, sx + sun_r, sy + sun_r], fill=(252, 236, 190))
    # 远山近山
    cd.polygon(
        [(0, ch * 0.66), (cw * 0.28, ch * 0.40), (cw * 0.52, ch * 0.66)],
        fill=(96, 112, 108),
    )
    cd.polygon(
        [(cw * 0.34, ch * 0.70), (cw * 0.66, ch * 0.42), (cw, ch * 0.70)],
        fill=(70, 88, 88),
    )
    # 地面
    cd.rectangle([0, ch * 0.68, cw, ch], fill=(52, 62, 48))
    cd.polygon(
        [(0, ch * 0.78), (cw * 0.5, ch * 0.70), (cw, ch * 0.80), (cw, ch), (0, ch)],
        fill=(38, 48, 36),
    )
    # 水面高光
    cd.rectangle([0, ch * 0.80, cw, ch * 0.86], fill=(64, 78, 66))

    img.paste(canvas, (int(left), int(top)))
    ImageDraw.Draw(img).rectangle(
        [left, top, right - 1, bottom - 1], outline=(90, 66, 24), width=max(1, int(s(2)))
    )
    return img


def write_ico(path: Path, sizes=(16, 24, 32, 48, 64, 128, 256)) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    base = make_app_icon(256)
    base.save(path, format="ICO", sizes=[(s, s) for s in sizes])
    return path


def tray_image(source: Optional[Path] = None, size: int = 64) -> Image.Image:
    """托盘图标：有当前壁纸就用它的中心正方形，否则用程序图标。"""
    if source:
        try:
            with Image.open(source) as raw:
                # draft() 让 JPEG 解码器直接按 1/2、1/4、1/8 降采样输出，
                # 否则光是解析一张 7680×2160 的成品图就要占几十 MB 内存
                raw.draft("RGB", (size * 4, size * 4))
                img = raw.convert("RGB")
            side = min(img.size)
            left = (img.width - side) // 2
            top = (img.height - side) // 2
            icon = img.crop((left, top, left + side, top + side)).resize((size, size), RESAMPLE)
            framed = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            framed.paste(icon, (0, 0))
            ImageDraw.Draw(framed).rectangle(
                [0, 0, size - 1, size - 1], outline=GOLD_LIGHT, width=max(1, size // 28)
            )
            return framed
        except (OSError, ValueError):
            pass
    return make_app_icon(size)
