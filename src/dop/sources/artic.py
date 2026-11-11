"""芝加哥艺术博物馆 (Art Institute of Chicago) —— 开放 API，无需 Key。

搜索接口支持 Elasticsearch 风格的服务端过滤，可以直接筛出
"欧洲绘画部门 + 绘画类 + 公共领域 + 年代落在区间内 + 有图" 的作品。
图片走 IIIF，可以按需要请求尺寸；CDN 对请求头敏感，HttpClient 已带上。
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from ..http import HttpClient, HttpError
from .base import looks_like_painting, make_work

SEARCH_URL = "https://api.artic.edu/api/v1/artworks/search"
IIIF = "https://www.artic.edu/iiif/2/{image_id}/full/{size}/0/default.jpg"
FIELDS = [
    "id",
    "title",
    "artist_display",
    "artist_title",
    "date_display",
    "date_start",
    "date_end",
    "image_id",
    "medium_display",
    "department_title",
    "is_public_domain",
    "artwork_type_title",
    "classification_title",
]
# 从大到小尝试：部分藏品的 IIIF 尺寸上限较低，会返回 403，需要逐级回退
SIZES = ("2400,", "1680,", "843,", "600,")


def _image_urls(image_id: str) -> list[str]:
    return [IIIF.format(image_id=image_id, size=size) for size in SIZES]


def build_query(low: int, high: int, page: int, limit: int = 100) -> dict[str, Any]:
    return {
        "query": {
            "bool": {
                "filter": [
                    {"term": {"is_public_domain": True}},
                    {"term": {"artwork_type_title.keyword": "Painting"}},
                    {"term": {"department_title.keyword": "Painting and Sculpture of Europe"}},
                    {"exists": {"field": "image_id"}},
                    {"range": {"date_start": {"gte": low}}},
                    {"range": {"date_end": {"lte": high}}},
                ]
            }
        },
        "fields": FIELDS,
        "limit": limit,
        "page": page,
    }


def _parse(item: dict, low: int, high: int) -> Optional[dict]:
    image_id = (item.get("image_id") or "").strip()
    if not image_id:
        return None
    # 服务端已经按 artwork_type_title=Painting 过滤过，这里再按媒材兜一次底
    if not looks_like_painting(
        object_name=item.get("title") or "",
        classification=item.get("classification_title") or "",
        medium=item.get("medium_display") or "",
        artwork_type=item.get("artwork_type_title") or "",
    ):
        return None
    start = item.get("date_start")
    end = item.get("date_end")
    if not isinstance(start, int) or not isinstance(end, int):
        return None
    if start < low or end > high:
        return None
    artist = (item.get("artist_title") or "").strip()
    if not artist:
        display = (item.get("artist_display") or "").split("\n")[0].strip()
        artist = display
    urls = _image_urls(image_id)
    return make_work(
        source="artic",
        source_id=str(item.get("id")),
        title=item.get("title") or "",
        artist=artist,
        date_display=item.get("date_display") or "",
        year_start=min(start, end),
        year_end=max(start, end),
        image_url=urls[0],
        fallback_urls=urls[1:],
        page_url=f"https://www.artic.edu/artworks/{item.get('id')}",
        medium=item.get("medium_display") or "",
        license_text="Public Domain (CC0)",
    )


def fetch(
    cfg: dict,
    http: HttpClient,
    log,
    progress: Optional[Callable[[str], None]] = None,
) -> list[dict[str, Any]]:
    low, high = cfg["date_range"]
    works: list[dict] = []
    page = 1
    total = None
    while True:
        try:
            data = http.post_json(SEARCH_URL, build_query(low, high, page))
        except HttpError as exc:
            log.warning("AIC 第 %d 页失败：%s", page, exc)
            break
        pagination = data.get("pagination") or {}
        if total is None:
            total = int(pagination.get("total") or 0)
            log.info("AIC：服务端命中 %d 幅（%d-%d 年，公共领域）", total, low, high)
        items = data.get("data") or []
        if not items:
            break
        for item in items:
            work = _parse(item, low, high)
            if work:
                works.append(work)
        if progress:
            progress(f"AIC 已取 {len(works)} 幅")
        if page * 100 >= total or page >= 40:
            break
        page += 1
    log.info("AIC：共取得 %d 幅", len(works))
    return works
