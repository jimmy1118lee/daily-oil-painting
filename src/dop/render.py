"""壁纸渲染：把画作按显示器分辨率排版成一张成品图。

设计要点
--------
* 成品尺寸严格等于屏幕分辨率（多屏时为虚拟桌面尺寸），这样 Windows
  不会做二次缩放，画质最好。
* style=auto：单屏铺满（fill）；多屏时背景用画作自身的模糊+压暗版，
  画作完整居中放在主屏，留白处不会出现生硬的黑边。
* 左下角可选水印：标题 / 作者·年代 / 博物馆·公共领域。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from PIL import Image, ImageColor, ImageDraw, ImageFilter, ImageFont, ImageOps

try:  # Pillow >= 10
    RESAMPLE = Image.Resampling.LANCZOS
    BICUBIC = Image.Resampling.BICUBIC
except AttributeError:  # pragma: no cover - 兼容旧版 Pillow
    RESAMPLE = Image.LANCZOS
    BICUBIC = Image.BICUBIC

FONT_DIR = Path(r"C:\Windows\Fonts")
SERIF = ("georgia.ttf", "times.ttf", "constan.ttf")
SERIF_BOLD = ("georgiab.ttf", "timesbd.ttf", "constanb.ttf")
SANS = ("segoeui.ttf", "arial.ttf")
SANS_BOLD = ("segoeuib.ttf", "arialbd.ttf")
# 含中文的文本必须用中文字体，否则会渲染成豆腐块（□）
CJK = ("msyh.ttc", "simhei.ttf", "simsun.ttc", "segoeui.ttf")
CJK_BOLD = ("msyhbd.ttc", "simhei.ttf", "msyh.ttc", "segoeuib.ttf")


def has_cjk(text: str) -> bool:
    for ch in str(text):
        code = ord(ch)
        if 0x2E80 <= code <= 0x9FFF or 0x3000 <= code <= 0x30FF or 0xFF00 <= code <= 0xFFEF:
            return True
    return False


def _font(names: tuple[str, ...], size: int) -> ImageFont.FreeTypeFont:
    size = max(8, int(size))
    for name in names:
        candidate = FONT_DIR / name
        if candidate.exists():
            try:
                return ImageFont.truetype(str(candidate), size)
            except OSError:
                continue
    return ImageFont.load_default()


def _font_for(text: str, latin: tuple[str, ...], cjk: tuple[str, ...], size: int):
    return _font(cjk if has_cjk(text) else latin, size)


def _cover(img: Image.Image, width: int, height: int) -> Image.Image:
    """等比缩放到铺满目标尺寸后居中裁剪。"""
    width, height = max(1, int(width)), max(1, int(height))
    ratio = max(width / img.width, height / img.height)
    new_size = (max(width, int(img.width * ratio + 0.5)), max(height, int(img.height * ratio + 0.5)))
    resized = img.resize(new_size, RESAMPLE)
    left = (resized.width - width) // 2
    top = (resized.height - height) // 2
    return resized.crop((left, top, left + width, top + height))


def _contain(img: Image.Image, width: int, height: int, allow_upscale: bool = True) -> Image.Image:
    """等比缩放到完整放进目标尺寸内。"""
    width, height = max(1, int(width)), max(1, int(height))
    ratio = min(width / img.width, height / img.height)
    if ratio > 1 and not allow_upscale:
        ratio = 1.0
    new_size = (max(1, int(img.width * ratio + 0.5)), max(1, int(img.height * ratio + 0.5)))
    return img.resize(new_size, RESAMPLE)


def _color(text: str, fallback: tuple[int, int, int] = (13, 13, 18)) -> tuple[int, int, int]:
    try:
        return ImageColor.getrgb(str(text))
    except (ValueError, TypeError):
        return fallback


def _background(size: tuple[int, int], src: Image.Image, im_cfg: dict) -> Image.Image:
    """生成留白处的背景：画作模糊放大 + 压暗，或纯色。"""
    width, height = size
    mode = str(im_cfg.get("background") or "blur").lower()
    if mode == "color":
        return Image.new("RGB", size, _color(im_cfg.get("background_color") or "#0d0d12"))

    strength = float(im_cfg.get("blur_strength") or 42)
    # 在小尺寸上做模糊再放大，速度快很多，视觉上没有区别
    sw, sh = max(200, width // 3), max(150, height // 3)
    small = src.copy()
    small.thumbnail((sw, sh), RESAMPLE)
    bg = _cover(small, sw, sh).filter(ImageFilter.GaussianBlur(max(6.0, strength / 3.0)))
    bg = bg.resize((width, height), RESAMPLE)

    darken = float(im_cfg.get("darken") or 0.0)
    if darken > 0:
        darken = min(0.9, max(0.0, darken))
        bg = Image.blend(bg, Image.new("RGB", size, (0, 0, 0)), darken)
    return bg


def _fit_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: float) -> str:
    if max_width <= 0 or not text:
        return text
    try:
        if draw.textlength(text, font=font) <= max_width:
            return text
    except (AttributeError, TypeError):  # 位图默认字体
        return text[:60]
    ellipsis = "…"
    low, high = 0, len(text)
    while low < high:
        mid = (low + high + 1) // 2
        if draw.textlength(text[:mid] + ellipsis, font=font) <= max_width:
            low = mid
        else:
            high = mid - 1
    return text[:low].rstrip() + ellipsis


def _composite_region(base: Image.Image, layer: Image.Image, box: tuple[int, int, int, int]) -> None:
    region = base.crop(box)
    base.paste(Image.alpha_composite(region, layer), box)


def _add_shadow(canvas: Image.Image, box: tuple[int, int, int, int], radius: int,
                blur: float, alpha: int, offset: int) -> Image.Image:
    mask = Image.new("L", canvas.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [box[0] + offset, box[1] + offset, box[2] + offset, box[3] + offset],
        radius=radius,
        fill=alpha,
    )
    mask = mask.filter(ImageFilter.GaussianBlur(blur))
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 255))
    shadow.putalpha(mask)
    return Image.alpha_composite(canvas, shadow)


def _draw_caption(canvas: Image.Image, region: tuple[int, int, int, int],
                  work: dict, im_cfg: dict) -> Image.Image:
    """在 region 的左下角画渐变 + 作品信息。"""
    if not work or not im_cfg.get("show_caption", True):
        return canvas
    x0, y0, x1, y1 = region
    rw, rh = x1 - x0, y1 - y0
    if rw < 320 or rh < 240:
        return canvas

    title = str(work.get("title") or "").strip()
    artist = str(work.get("artist") or "").strip()
    date = str(work.get("date_display") or "").strip()
    bits = [b for b in (artist, date) if b]
    meta = "   ·   ".join(bits)
    credit_bits = []
    if im_cfg.get("show_museum", True) and work.get("museum"):
        credit_bits.append(str(work["museum"]))
    if work.get("license"):
        credit_bits.append(str(work["license"]))
    credit = "   ·   ".join(credit_bits)

    title_size = min(56, max(17, int(rh * 0.0265)))
    meta_size = min(34, max(12, int(rh * 0.0155)))
    credit_size = min(28, max(11, int(rh * 0.0132)))

    pad = int(rh * 0.038)
    max_text_w = rw - pad * 2

    scratch = ImageDraw.Draw(canvas)
    rows: list[tuple[str, Any, tuple[int, int, int, int]]] = []
    if title:
        font = _font_for(title, SERIF_BOLD, CJK_BOLD, title_size)
        rows.append((_fit_text(scratch, title, font, max_text_w), font, (255, 255, 255, 246)))
    if meta:
        font = _font_for(meta, SANS, CJK, meta_size)
        rows.append((_fit_text(scratch, meta, font, max_text_w), font, (232, 230, 225, 232)))
    if credit:
        font = _font_for(credit, SANS, CJK, credit_size)
        rows.append((_fit_text(scratch, credit, font, max_text_w), font, (206, 204, 202, 196)))

    heights = []
    for text, font, _color in rows:
        try:
            bbox = font.getbbox(text or "Ag")
            heights.append(bbox[3] - bbox[1])
        except (AttributeError, TypeError):
            heights.append(int(getattr(font, "size", 16)))
    gap = max(4, int(rh * 0.0055))
    block_h = sum(heights) + gap * max(0, len(rows) - 1)
    grad_h = min(rh, int(block_h + pad * 2.1))
    grad_top = y1 - grad_h

    # 底部渐变，保证文字在任何画面上都清晰。
    # 渐变横跨整个桌面宽度（而不是只覆盖主屏），否则多屏时会在显示器
    # 接缝处留下一条突兀的竖向明暗断层。
    grad = Image.new("L", (1, max(2, grad_h)))
    for i in range(grad.height):
        t = i / (grad.height - 1)
        grad.putpixel((0, i), int(4 + 178 * (t ** 1.5)))
    grad = grad.resize((canvas.width, max(2, grad_h)), BICUBIC)
    layer = Image.new("RGBA", (canvas.width, grad_h), (5, 5, 9, 0))
    layer.putalpha(grad)
    _composite_region(canvas, layer, (0, grad_top, canvas.width, y1))

    draw = ImageDraw.Draw(canvas)
    y = y1 - pad - block_h
    for (text, font, color), line_h in zip(rows, heights):
        if text:
            try:
                bbox = font.getbbox(text)
                draw.text((x0 + pad, y - bbox[1]), text, font=font, fill=color)
            except (AttributeError, TypeError):
                draw.text((x0 + pad, y), text, font=font, fill=color)
        y += line_h + gap
    return canvas


# --------------------------------------------------------------------------- 主流程
def compose(
    painting_path: Path,
    monitors: list[dict],
    cfg: dict,
    work: Optional[dict] = None,
) -> Image.Image:
    """把画作排版成一张适配当前显示器的成品图。"""
    im_cfg = dict(cfg.get("image") or {})
    with Image.open(painting_path) as raw:
        src = ImageOps.exif_transpose(raw).convert("RGB")

    monitors = [m for m in (monitors or []) if m and m.get("width") and m.get("height")]
    if not monitors:
        monitors = [{"left": 0, "top": 0, "right": 1920, "bottom": 1080,
                     "width": 1920, "height": 1080, "primary": True}]

    vx0 = min(m["left"] for m in monitors)
    vy0 = min(m["top"] for m in monitors)
    vx1 = max(m["right"] for m in monitors)
    vy1 = max(m["bottom"] for m in monitors)
    canvas_size = (max(1, vx1 - vx0), max(1, vy1 - vy0))

    primary = next((m for m in monitors if m.get("primary")), monitors[0])
    region = (
        primary["left"] - vx0,
        primary["top"] - vy0,
        primary["right"] - vx0,
        primary["bottom"] - vy0,
    )
    multi = len(monitors) > 1
    rw, rh = region[2] - region[0], region[3] - region[1]

    style = str(im_cfg.get("style") or "auto").lower()
    if style == "auto":
        style = "fit" if multi else "fill"

    if style in ("fill", "cover"):
        canvas = _cover(src, *canvas_size)
    else:
        canvas = _background(canvas_size, src, im_cfg)
        if style == "center":
            inner = _contain(src, rw, rh, allow_upscale=False)
        else:  # fit
            inner = _contain(src, rw, rh, allow_upscale=True)
        pos = (region[0] + (rw - inner.width) // 2, region[1] + (rh - inner.height) // 2)
        if multi:
            shadow_box = (pos[0] - 6, pos[1] - 6, pos[0] + inner.width + 6, pos[1] + inner.height + 6)
            canvas = _add_shadow(
                canvas.convert("RGBA"),
                shadow_box,
                radius=max(2, int(min(inner.size) * 0.004)),
                blur=max(6.0, min(inner.size) * 0.012),
                alpha=150,
                offset=max(2, int(rh * 0.004)),
            ).convert("RGB")
        canvas.paste(inner, pos)

    rgba = canvas.convert("RGBA")
    rgba = _draw_caption(rgba, region, work or {}, im_cfg)
    return rgba.convert("RGB")


def suffix_for(cfg: dict) -> str:
    fmt = str((cfg.get("image") or {}).get("format") or "jpeg").lower()
    return ".png" if fmt == "png" else ".jpg"


def split_by_monitor(canvas: Image.Image, monitors: list[dict]) -> list[tuple[dict, Image.Image]]:
    """把整张画布按显示器切成每屏一张。

    为什么要这么做：Windows 11 并不总是遵守注册表里的"跨屏(Span)"设置，
    实测会把整张跨屏大图分别缩放进每一块屏幕，导致画作被压扁、两块屏各显示
    一份。改成给每块屏一张尺寸完全匹配的图，再按显示器单独设置壁纸，
    就彻底不依赖系统的缩放逻辑了。
    """
    if not monitors:
        return [({"left": 0, "top": 0, "right": canvas.width, "bottom": canvas.height,
                  "width": canvas.width, "height": canvas.height, "primary": True}, canvas)]
    vx0 = min(m["left"] for m in monitors)
    vy0 = min(m["top"] for m in monitors)
    slices: list[tuple[dict, Image.Image]] = []
    for monitor in monitors:
        box = (
            monitor["left"] - vx0,
            monitor["top"] - vy0,
            monitor["right"] - vx0,
            monitor["bottom"] - vy0,
        )
        box = (
            max(0, box[0]), max(0, box[1]),
            min(canvas.width, box[2]), min(canvas.height, box[3]),
        )
        slices.append((monitor, canvas.crop(box)))
    return slices


def save(img: Image.Image, out_path: Path, cfg: dict) -> Path:
    """保存成品图。JPEG 用 4:4:4 采样、关闭渐进式，兼容性最好。"""
    im_cfg = dict(cfg.get("image") or {})
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if suffix_for(cfg) == ".png":
        img.save(out_path, "PNG", optimize=True)
    else:
        img.save(
            out_path,
            "JPEG",
            quality=int(im_cfg.get("jpeg_quality") or 93),
            subsampling=0,
            optimize=True,
            progressive=False,
        )
    return out_path
