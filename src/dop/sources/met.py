"""大都会艺术博物馆 (The Met) —— 开放访问 API，无需 Key。

三个关键点（都是踩过坑之后总结的）：

1. **必须用 v1.1 的搜索接口**。老接口 ``/public/collection/v1/search``
   已于 2026-10-01 正式下线，之后调用一律返回 ``410 Gone``
   （结果就是 10 月起 Met 一直贡献 0 幅，而日志里只有一条不起眼的 warning）。
   新接口 ``/public/collection/v1.1/search`` 支持同样的过滤参数，
   但**改成分页**：``offset`` / ``limit``（每页最多 500，且 offset+limit ≤ 10000）。
2. v1.1 不要求 ``q``，只给过滤条件也能查，所以可以直接用
   ``departmentId + dateBegin/dateEnd + hasImages`` 精确圈定目标。
3. Met 对高频请求敏感，实测 8 线程猛发 560 次会被限流（大面积 403）。
   所以要靠 HttpClient 的全局限速 + 长退避重试。
"""

from __future__ import annotations

import random
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Optional

from ..http import HttpClient, HttpError
from .base import in_date_range, looks_like_painting, make_work

# v1.1：分页搜索（v1/search 已于 2026-10-01 下线，返回 410）
SEARCH_URL = "https://collectionapi.metmuseum.org/public/collection/v1.1/search"
OBJECT_URL = "https://collectionapi.metmuseum.org/public/collection/v1/objects/{oid}"
PAGE_LIMIT = 500          # v1.1 单页上限
MAX_RESULTS = 10000       # offset + limit 不得超过这个值
# 11 = 欧洲绘画（主库）；15 = 罗伯特·雷曼收藏（含大量欧洲油画）
# 17 = 中世纪艺术，油画占比很低，会让命中率明显下降，默认不收
DEFAULT_DEPARTMENTS = (11, 15)


def _parse(obj: dict, low: int, high: int) -> Optional[dict]:
    if not obj or not obj.get("isPublicDomain"):
        return None
    image = (obj.get("primaryImage") or "").strip()
    if not image:
        return None
    begin = obj.get("objectBeginDate")
    end = obj.get("objectEndDate")
    if not isinstance(begin, int) or not isinstance(end, int):
        return None
    if not in_date_range(begin, end, low, high):
        return None
    if not looks_like_painting(
        object_name=obj.get("objectName") or "",
        classification=obj.get("classification") or "",
        medium=obj.get("medium") or "",
        department=obj.get("department") or "",
    ):
        return None
    small = (obj.get("primaryImageSmall") or "").strip()
    return make_work(
        source="met",
        source_id=str(obj.get("objectID")),
        title=obj.get("title") or "",
        artist=obj.get("artistDisplayName") or "",
        date_display=obj.get("objectDate") or "",
        year_start=min(begin, end),
        year_end=max(begin, end),
        image_url=image,
        fallback_urls=[small] if small else [],
        page_url=obj.get("objectURL") or "",
        medium=obj.get("medium") or "",
        license_text="Public Domain (CC0)",
    )


def collect_ids(
    http: HttpClient, departments, log, low: int, high: int
) -> list[int]:
    """用 v1.1 的分页搜索按部门 + 年代收集 objectID。

    v1.1 不要求 ``q``，所以直接给过滤条件即可：部门 + 年代区间 + 有图。
    实测部门 11（欧洲绘画）在这个区间内有 1500+ 条，命中率很高。
    """
    ids: list[int] = []
    seen: set[int] = set()
    for dept in departments:
        offset = 0
        dept_count = 0
        while True:
            try:
                data = http.get_json(
                    SEARCH_URL,
                    {
                        "departmentId": dept,
                        "hasImages": "true",
                        "dateBegin": low,
                        "dateEnd": high,
                        "limit": PAGE_LIMIT,
                        "offset": offset,
                    },
                )
            except HttpError as exc:
                log.warning("Met 部门 %s offset=%d 检索失败：%s", dept, offset, exc)
                break
            batch = data.get("objectIDs") or []
            total = int(data.get("total") or 0)
            if offset == 0:
                log.debug("Met 部门 %s 命中 %d 条", dept, total)
            for oid in batch:
                if oid not in seen:
                    seen.add(oid)
                    ids.append(oid)
            dept_count += len(batch)
            if len(batch) < PAGE_LIMIT:
                break
            offset += PAGE_LIMIT
            if offset + PAGE_LIMIT > MAX_RESULTS:
                break
            if total and offset >= total:
                break
        log.debug("Met 部门 %s 共取到 %d 个 ID", dept, dept_count)
    return ids


def fetch(
    cfg: dict,
    http: HttpClient,
    log,
    progress: Optional[Callable[[str], None]] = None,
) -> list[dict[str, Any]]:
    low, high = cfg["date_range"]
    sc = (cfg.get("sources", {}) or {}).get("met", {}) or {}
    departments = sc.get("departments") or list(DEFAULT_DEPARTMENTS)
    target_valid = int(sc.get("target_valid", 260))
    max_attempts = int(sc.get("max_attempts", 700))
    workers = max(1, int(cfg.get("advanced", {}).get("concurrency", 3)))

    ids = collect_ids(http, departments, log, low, high)
    if not ids:
        log.warning(
            "Met：没有取到任何 objectID（若日志里出现 HTTP 410，说明搜索接口又变了，"
            "请检查 metmuseum.github.io 的接口说明）"
        )
        return []
    random.shuffle(ids)
    log.info(
        "Met：候选 %d 条（部门 %s，%d-%d 年，服务端已按年代+有图过滤），"
        "目标 %d 幅，最多查 %d 条",
        len(ids), list(departments), low, high, target_valid, max_attempts,
    )

    results: list[dict] = []
    attempts = 0
    failures = 0
    chunk = max(workers * 4, 12)
    index = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        while index < len(ids) and attempts < max_attempts and len(results) < target_valid:
            take = min(chunk, max_attempts - attempts, len(ids) - index)
            if take <= 0:
                break
            batch = ids[index:index + take]
            index += len(batch)
            attempts += len(batch)

            def _one(oid: int):
                try:
                    obj = http.get_json(OBJECT_URL.format(oid=oid))
                except HttpError:
                    return None, False
                return _parse(obj, low, high), True

            for work, fetched in pool.map(_one, batch):
                if not fetched:
                    failures += 1
                elif work:
                    results.append(work)
            if progress:
                progress(f"Met 抽查 {attempts} 条，命中 {len(results)} 幅")

    log.info(
        "Met：抽查 %d 条，命中 %d 幅（%d-%d 年），接口失败 %d 次",
        attempts, len(results), low, high, failures,
    )
    if failures > attempts * 0.25:
        log.warning("Met 接口失败率偏高（%d/%d），可能被限流，稍后重试可以拿到更多", failures, attempts)
    return results
