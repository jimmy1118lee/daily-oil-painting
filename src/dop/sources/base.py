"""图源公共逻辑：作品结构、年代校验、去重键。"""

from __future__ import annotations

from typing import Any, Optional

# 各馆显示名（中文，用于壁纸水印与界面）
MUSEUM_NAMES = {
    "met": "大都会艺术博物馆",
    "artic": "芝加哥艺术博物馆",
    "cleveland": "克利夫兰艺术博物馆",
    "harvard": "哈佛艺术博物馆",
}


def make_work(
    *,
    source: str,
    source_id: str,
    title: str,
    artist: str = "",
    date_display: str = "",
    year_start: Optional[int] = None,
    year_end: Optional[int] = None,
    image_url: str = "",
    fallback_urls: Optional[list[str]] = None,
    page_url: str = "",
    medium: str = "",
    license_text: str = "Public Domain",
) -> dict[str, Any]:
    """构造统一的"作品"字典。"""
    title = (title or "无题").strip() or "无题"
    artist = (artist or "").strip()
    return {
        "key": f"{source}:{source_id}",
        "source": source,
        "source_id": str(source_id),
        "title": title,
        "artist": artist,
        "date_display": (date_display or "").strip(),
        "year_start": year_start,
        "year_end": year_end,
        "image_url": image_url,
        "fallback_urls": list(fallback_urls or []),
        "page_url": page_url,
        "medium": (medium or "").strip(),
        "museum": MUSEUM_NAMES.get(source, source),
        "license": license_text,
    }


def in_date_range(
    year_start: Optional[int], year_end: Optional[int], low: int, high: int
) -> bool:
    """年代必须完整落在 [low, high] 区间内。"""
    if not isinstance(year_start, int) or not isinstance(year_end, int):
        return False
    if year_start > year_end:
        year_start, year_end = year_end, year_start
    return year_start >= low and year_end <= high


def deduce_year(text: str) -> Optional[int]:
    """从 'ca. 1520'、'1520-1530' 之类的文本里猜一个年份。"""
    import re

    if not text:
        return None
    years = [int(y) for y in re.findall(r"\b(1[2-8]\d{2})\b", str(text))]
    return years[0] if years else None


# 真正的绘画媒材（distemper 是蛋彩/胶彩的一类，也算绘画）
PAINT_MEDIA = ("oil", "tempera", "fresco", "encaustic", "distemper")
# 出现这些词就说明不是（或不该作为壁纸的）绘画作品：
# 象牙、大理石、铜、陶、铅是雕塑/器物；vellum/paper/plumbago 是素描；
# 水彩/水粉/色粉不是油画；微缩画尺寸太小不适合做壁纸。
NON_PAINT_MEDIA = (
    "ivory", "marble", "bronze", "terracotta", "lead", "vellum", "parchment",
    "paper", "graphite", "plumbago", "chalk", "ink", "watercolor", "watercolour",
    "gouache", "pastel", "silver", "enamel", "plaster", "alabaster", "boxwood",
    "porcelain", "glass", "tapestry", "mosaic", "stained glass",
)


def looks_like_painting(
    *,
    object_name: str = "",
    classification: str = "",
    medium: str = "",
    department: str = "",
    artwork_type: str = "",
) -> bool:
    """判断一条记录到底是不是绘画。

    踩过的坑：大都会的"欧洲绘画"部门里混着象牙器和素描，
    克利夫兰的"European Painting and Sculpture"部门里雕塑占了近三成，
    所以不能只看部门，必须看媒材。
    """
    med = (medium or "").strip().lower()
    if any(bad in med for bad in NON_PAINT_MEDIA):
        return False
    if any(good in med for good in PAINT_MEDIA):
        return True
    if med:
        # 有媒材信息却看不出是绘画（例如只有 "Card"）→ 不收
        return False
    # 媒材缺失时才退一步看类型/分类/名称里有没有 painting
    kind = f"{artwork_type} {classification} {object_name}".lower()
    return "painting" in kind
