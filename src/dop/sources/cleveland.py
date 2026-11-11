"""克利夫兰艺术博物馆 (Cleveland Museum of Art) —— 开放访问 API，无需 Key。

很多藏品是 CC0 且提供多档图片：web（约 900px，偏小）、
print（约 2000px，适合壁纸）、full（巨型 TIF，不用）。
这里优先 print，回退 web。
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from ..http import HttpClient, HttpError
from .base import looks_like_painting, make_work

API = "https://openaccess-api.clevelandart.org/api/artworks/"
DEPARTMENT = "European Painting and Sculpture"
# 这个部门里雕塑/器物占了近三成，必须用 type=Painting 在服务端就筛掉
ARTWORK_TYPE = "Painting"
PAGE_LIMIT = 100


def _image_urls(images: dict) -> list[str]:
    urls: list[str] = []
    for key in ("print", "web", "full"):
        node = (images or {}).get(key) or {}
        url = (node.get("url") or "").strip()
        if url and not url.lower().endswith(".tif") and url not in urls:
            urls.append(url)
    return urls


def _parse(art: dict, low: int, high: int) -> Optional[dict]:
    # 双保险：即使服务端过滤失效，客户端也要再确认一次类型与媒材
    if str(art.get("type") or "").strip().lower() not in ("painting", ""):
        return None
    if not looks_like_painting(
        object_name=art.get("title") or "",
        medium=art.get("technique") or "",
        artwork_type=art.get("type") or "",
    ):
        return None
    earliest = art.get("creation_date_earliest")
    latest = art.get("creation_date_latest")
    if not isinstance(earliest, int) or not isinstance(latest, int):
        return None
    if earliest < low or latest > high:
        return None
    urls = _image_urls(art.get("images") or {})
    if not urls:
        return None
    title = art.get("title") or ""
    creators = art.get("creators") or []
    artist = ""
    if creators:
        artist = (creators[0].get("description") or creators[0].get("name") or "").split("(")[0].strip()
    return make_work(
        source="cleveland",
        source_id=str(art.get("id")),
        title=title,
        artist=artist,
        date_display=art.get("creation_date") or "",
        year_start=min(earliest, latest),
        year_end=max(earliest, latest),
        image_url=urls[0],
        fallback_urls=urls[1:],
        page_url=(art.get("url") or f"https://www.clevelandart.org/art/{art.get('accession_number','')}"),
        medium=art.get("technique") or "",
        license_text="CC0 / Public Domain",
    )


def fetch(
    cfg: dict,
    http: HttpClient,
    log,
    progress: Optional[Callable[[str], None]] = None,
) -> list[dict[str, Any]]:
    low, high = cfg["date_range"]
    works: list[dict] = []
    skip = 0
    while True:
        try:
            data = http.get_json(
                API,
                {
                    "department": DEPARTMENT,
                    "type": ARTWORK_TYPE,
                    "has_image": 1,
                    "cc0": 1,
                    "created_after": max(low - 10, 0),
                    "created_before": high + 5,
                    "limit": PAGE_LIMIT,
                    "skip": skip,
                },
            )
        except HttpError as exc:
            log.warning("CMA 第 %d 条起请求失败：%s", skip, exc)
            break
        info = data.get("info") or {}
        if skip == 0:
            log.info("CMA：服务端命中 %s 幅", info.get("total"))
        batch = data.get("data") or []
        if not batch:
            break
        for art in batch:
            work = _parse(art, low, high)
            if work:
                works.append(work)
        if progress:
            progress(f"CMA 已取 {len(works)} 幅")
        skip += PAGE_LIMIT
        total = int(info.get("total") or 0)
        if skip >= total or skip >= 3000:
            break
    log.info("CMA：共取得 %d 幅", len(works))
    return works
