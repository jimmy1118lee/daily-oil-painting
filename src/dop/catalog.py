"""作品目录：从各馆抓取、去重、缓存到本地。

目录只存"元数据 + 图片地址"，不存图片本身，因此体积很小（几千条约 1-2 MB），
默认 30 天刷新一次。抓到之后每天换壁纸完全离线选图。
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Callable, Optional

from . import paths
from .http import HttpClient
from .sources import SOURCES
from .util import atomic_write_json, get_logger, read_json

Progress = Optional[Callable[[str], None]]


def _dedupe(works: list[dict]) -> list[dict]:
    """按 key 去重，并剔除明显的重复作品（同馆同名同作者同年）。"""
    seen_keys: set[str] = set()
    seen_sig: set[tuple] = set()
    out: list[dict] = []
    for work in works:
        key = str(work.get("key") or "")
        if not key or key in seen_keys:
            continue
        sig = (
            work.get("museum"),
            str(work.get("title") or "").strip().lower(),
            str(work.get("artist") or "").strip().lower(),
            work.get("year_start"),
        )
        if sig[1] and sig in seen_sig:
            continue
        seen_keys.add(key)
        seen_sig.add(sig)
        out.append(work)
    return out


def build_catalog(cfg: dict, http: HttpClient, progress: Progress = None) -> dict[str, Any]:
    log = get_logger("catalog")
    low, high = cfg["date_range"]
    enabled = [
        (name, spec)
        for name, spec in (cfg.get("sources") or {}).items()
        if isinstance(spec, dict) and spec.get("enabled") and name in SOURCES
    ]
    all_works: list[dict] = []
    per_source: dict[str, int] = {}
    for name, _spec in enabled:
        module = SOURCES[name]
        if progress:
            progress(f"正在抓取 {name} …")
        try:
            works = module.fetch(cfg, http, log, progress)
        except Exception as exc:  # 单个图源故障不影响整体
            log.warning("图源 %s 抓取失败：%s", name, exc)
            per_source[name] = 0
            continue
        per_source[name] = len(works)
        all_works.extend(works)

    merged = _dedupe(all_works)
    # 去重之后再统计，否则界面上的分馆数量会多于实际可用的作品数
    final_counts: dict[str, int] = {}
    for work in merged:
        name = str(work.get("source") or "unknown")
        final_counts[name] = final_counts.get(name, 0) + 1
    catalog = {
        "generated_at": time.time(),
        "generated_display": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "date_range": [low, high],
        "per_source": final_counts,
        "raw_per_source": per_source,
        "total": len(merged),
        "works": merged,
    }
    log.info("目录构建完成：%d 幅（原始 %d 条，去重 %d 条）%s",
             len(merged), len(all_works), len(all_works) - len(merged), final_counts)
    return catalog


def load_catalog() -> Optional[dict]:
    data = read_json(paths.catalog_path())
    if isinstance(data, dict) and isinstance(data.get("works"), list):
        return data
    return None


def source_cache_days(cfg: dict, name: str) -> float:
    spec = (cfg.get("sources") or {}).get(name) or {}
    try:
        return max(0.0, float(spec.get("cache_days", 0) or 0))
    except (TypeError, ValueError):
        return 0.0


# 这些图源必须配置 Key 才会真正参与，没配 Key 时不该影响目录有效期
SOURCE_REQUIRES_KEY = {"harvard"}


def effective_ttl_days(cfg: dict) -> float:
    """作品目录的实际有效期。

    取"全局设置"与"各图源自身缓存期限"的较小值：哈佛艺术博物馆的使用条款
    规定内容缓存不得超过两周，所以一旦启用哈佛，整体有效期就会被压到 14 天以内。
    但没配 Key 的哈佛等于没启用，不该把有效期也拉短。
    """
    ttl = float((cfg.get("advanced") or {}).get("catalog_ttl_days", 30) or 30)
    for name, spec in (cfg.get("sources") or {}).items():
        if not isinstance(spec, dict) or not spec.get("enabled"):
            continue
        if name in SOURCE_REQUIRES_KEY and not str(spec.get("api_key") or "").strip():
            continue
        days = source_cache_days(cfg, name)
        if days > 0:
            ttl = min(ttl, days)
    return max(0.5, ttl)


def is_fresh(catalog: dict, ttl_days: int) -> bool:
    try:
        age = time.time() - float(catalog.get("generated_at") or 0)
    except (TypeError, ValueError):
        return False
    return age < max(1, ttl_days) * 86400


def ensure_catalog(
    cfg: dict,
    http: HttpClient,
    force: bool = False,
    progress: Progress = None,
) -> dict[str, Any]:
    log = get_logger("catalog")
    ttl = int(effective_ttl_days(cfg))
    cached = None if force else load_catalog()
    if cached and is_fresh(cached, ttl) and cached.get("works"):
        return cached
    log.info("作品目录有效期 %d 天（受各图源条款约束）", ttl)
    try:
        catalog = build_catalog(cfg, http, progress)
    except Exception as exc:
        log.error("构建目录失败：%s", exc)
        if cached and cached.get("works"):
            log.warning("沿用旧目录（%s）", cached.get("generated_display"))
            return cached
        raise
    if not catalog.get("works") and cached and cached.get("works"):
        log.warning("新目录为空，沿用旧目录")
        return cached
    atomic_write_json(paths.catalog_path(), catalog)
    return catalog


def stats(catalog: dict) -> str:
    lines = [
        f"共 {catalog.get('total', 0)} 幅作品",
        f"生成时间：{catalog.get('generated_display', '未知')}",
        f"年代范围：{catalog.get('date_range')}",
    ]
    for name, count in (catalog.get("per_source") or {}).items():
        lines.append(f"  · {name}: {count} 幅")
    return "\n".join(lines)
