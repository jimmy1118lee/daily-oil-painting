"""哈佛艺术博物馆 (Harvard Art Museums) —— 官方 API，需要 API Key。

官方文档：https://github.com/harvardartmuseums/api-docs

踩过的坑（都是实测出来的，不是文档写的）：

1. **只能用查询参数 ``apikey`` 鉴权**。文档里没提请求头，实测 ``X-Api-Key``
   一律返回 401。因为 Key 会出现在 URL 里，所有进日志的文本都必须脱敏
   （见 ``dop.http.redact`` 和 ``dop.util.RedactingFilter``）。
2. **``culture`` 参数要用小写值**才能匹配：``culture=italian`` 有 12188 条，
   ``culture=Italian`` 是 0 条。所以这里改用 ``q`` 里的 elasticsearch 语法
   （``division:"..."``）来做服务端过滤，客户端再按文化白名单收口。
3. ``yearmade=起-止`` 是**"有交集"语义**，会把年代跨界的作品也带进来，
   因此客户端必须再做一次严格校验。
4. ``culture=European`` 根本不存在这个取值（返回 0）。
5. ``division:"European and American Art"`` 会把**美国绘画**一起带出来，
   必须按 culture 排掉。
6. ``primaryimageurl`` 只是 **IIIF 基址**（形如
   ``https://nrs.harvard.edu/urn-3:HUAM:79717_dynmc``），
   要自己拼 ``/full/<尺寸>/0/default.jpg`` 才是图片地址。
7. ``imagepermissionlevel``：0=可任意尺寸显示，1=最多 256px，2=禁止。
   做壁纸只能要 0。
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from ..cache import JsonCache
from ..http import HttpClient, HttpError
from ..util import get_logger
from .base import in_date_range, looks_like_painting, make_work

API = "https://api.harvardartmuseums.org/object"
FIELDS = ",".join([
    "id", "objectnumber", "title", "dated", "datebegin", "dateend", "people",
    "primaryimageurl", "imagepermissionlevel", "url", "medium", "technique",
    "classification", "culture", "division", "imagecount", "worktypes",
])
# 服务端先按"欧洲和美洲艺术"部门收窄，再在客户端排掉美洲
DIVISION_QUERY = 'division:"European and American Art"'
PAGE_SIZE = 100
# 条款规定不得缓存超过两周，这里硬性封顶
MAX_CACHE_DAYS = 14.0

# 欧洲文化白名单（按前缀匹配，能覆盖 "Italian, Tuscan, Florentine" 这类细分）
EUROPEAN_CULTURE_KEYS = (
    "italian", "italo", "tuscan", "venetian", "florentine", "lombard", "neapolitan",
    "sienese", "emilian", "roman", "sicilian", "piedmontese", "marchigian", "paduan",
    "vicentine", "ferrarese", "parmesan", "brescian", "bergamasque", "lucchese",
    "pisan", "bolognese", "umbrian",
    "dutch", "netherlandish", "flemish", "belgian", "walloon", "frisian",
    "french", "german", "austrian", "swiss", "tyrolean", "bohemian", "czech",
    "hungarian", "polish", "silesian",
    "spanish", "catalan", "hispano", "portuguese",
    "british", "english", "scottish", "irish", "welsh",
    "danish", "swedish", "norwegian", "finnish", "icelandic",
    "russian", "ukrainian", "byzantine", "greek", "dalmatian", "croatian",
    "slovak", "slovenian", "lithuanian", "latvian", "estonian",
)


def is_european(culture: str) -> bool:
    """判断文化归属是不是欧洲（用来排掉 American、Argentinian、Asian 等）。"""
    text = (culture or "").strip().lower()
    if not text:
        return False
    return any(key in text for key in EUROPEAN_CULTURE_KEYS)


def _iiif_urls(base: str) -> list[str]:
    """把 IIIF 基址拼成可下载的图片地址，从大到小依次回退。"""
    root = (base or "").strip().rstrip("/")
    if not root:
        return []
    if root.endswith((".jpg", ".jpeg", ".png")):
        return [root]
    sizes = ("2400,", "!2560,1440", "1680,", "843,", "full")
    return [f"{root}/full/{size}/0/default.jpg" for size in sizes]


def _artist(record: dict) -> str:
    people = record.get("people") or []
    for person in people:
        if "artist" in str(person.get("role") or "").lower():
            name = str(person.get("name") or "").strip()
            if name:
                return name
    if people:
        return str(people[0].get("name") or "").strip()
    return ""


def _worktypes(record: dict) -> str:
    items = record.get("worktypes") or []
    return " ".join(str(w.get("worktype") or "") for w in items if isinstance(w, dict))


def _parse(record: dict, low: int, high: int) -> Optional[dict]:
    image = str(record.get("primaryimageurl") or "").strip()
    if not image:
        return None
    # 图片权限：只有 0（可任意尺寸显示）才适合做壁纸
    if record.get("imagepermissionlevel") != 0:
        return None
    begin, end = record.get("datebegin"), record.get("dateend")
    if not isinstance(begin, int) or not isinstance(end, int):
        return None
    # yearmade 是"有交集"语义，这里做严格校验
    if not in_date_range(begin, end, low, high):
        return None
    if not is_european(str(record.get("culture") or "")):
        return None
    if not looks_like_painting(
        object_name=str(record.get("title") or ""),
        classification=str(record.get("classification") or ""),
        medium=str(record.get("medium") or ""),
        artwork_type=_worktypes(record),
    ):
        return None
    urls = _iiif_urls(image)
    if not urls:
        return None
    return make_work(
        source="harvard",
        source_id=str(record.get("id") or record.get("objectnumber") or ""),
        title=str(record.get("title") or ""),
        artist=_artist(record),
        date_display=str(record.get("dated") or ""),
        year_start=min(begin, end),
        year_end=max(begin, end),
        image_url=urls[0],
        fallback_urls=urls[1:],
        page_url=str(record.get("url") or ""),
        medium=str(record.get("medium") or ""),
        # 哈佛条款要求标注来源并回链，这里的说明会出现在水印与详情页上
        license_text="Harvard Art Museums · 开放图像(可任意尺寸)",
    )


def _cache_key(params: dict) -> str:
    """缓存键绝不能包含 API Key。"""
    parts = [f"{k}={params[k]}" for k in sorted(params)]
    return "|".join(parts)


def fetch(
    cfg: dict,
    http: HttpClient,
    log,
    progress: Optional[Callable[[str], None]] = None,
) -> list[dict[str, Any]]:
    spec = (cfg.get("sources", {}) or {}).get("harvard", {}) or {}
    api_key = str(spec.get("api_key") or "").strip()
    if not api_key:
        log.info(
            "Harvard：未配置 API Key，已跳过。"
            "可设环境变量 DOP_HARVARD_API_KEY，或写入 config.json 的 sources.harvard.api_key"
        )
        return []

    low, high = cfg["date_range"]
    target = int(spec.get("target_valid", 400))
    max_pages = max(1, int(spec.get("max_pages", 12)))
    cache_days = min(MAX_CACHE_DAYS, float(spec.get("cache_days", MAX_CACHE_DAYS)))
    cache = JsonCache("harvard", cache_days)

    base_params = {
        "q": DIVISION_QUERY,
        "classification": "Paintings",
        "yearmade": f"{low}-{high}",
        "hasimage": 1,
        "size": PAGE_SIZE,
        "fields": FIELDS,
    }

    works: list[dict] = []
    seen: set[str] = set()
    hits = 0
    calls = 0
    rejected = {"permission": 0, "date": 0, "culture": 0, "medium": 0, "other": 0}

    for page in range(1, max_pages + 1):
        params = {**base_params, "page": page}
        key = _cache_key(params)
        data = cache.get(key)
        if data is None:
            try:
                data = http.get_json(API, {**params, "apikey": api_key})
                calls += 1
                cache.set(key, data)
            except HttpError as exc:
                log.warning("Harvard 第 %d 页请求失败：%s", page, exc)
                break
        else:
            hits += 1

        records = data.get("records") or []
        if not records:
            break
        if page == 1:
            total = (data.get("info") or {}).get("totalrecords")
            log.info("Harvard：服务端命中 %s 条（欧洲和美洲艺术部门的绘画，%s-%s 年）",
                     total, low, high)

        for record in records:
            work = _parse(record, low, high)
            if work is None:
                # 统计被拒原因，便于排查过滤是否过严
                if record.get("imagepermissionlevel") != 0:
                    rejected["permission"] += 1
                elif not isinstance(record.get("datebegin"), int) or not in_date_range(
                    record.get("datebegin") or 0, record.get("dateend") or 0, low, high
                ):
                    rejected["date"] += 1
                elif not is_european(str(record.get("culture") or "")):
                    rejected["culture"] += 1
                elif not looks_like_painting(
                    object_name=str(record.get("title") or ""),
                    classification=str(record.get("classification") or ""),
                    medium=str(record.get("medium") or ""),
                    artwork_type=_worktypes(record),
                ):
                    rejected["medium"] += 1
                else:
                    rejected["other"] += 1
                continue
            if work["key"] in seen:
                continue
            seen.add(work["key"])
            works.append(work)

        if progress:
            progress(f"Harvard 已取 {len(works)} 幅（第 {page} 页）")
        info = data.get("info") or {}
        if not info.get("next") or len(works) >= target:
            break

    log.info(
        "Harvard：取得 %d 幅（接口 %d 次，缓存命中 %d 页；被过滤掉 %s）",
        len(works), calls, hits, rejected,
    )
    if works:
        log.info("Harvard：缓存 TTL %.0f 天（其使用条款上限为两周）", cache_days)
    return works
