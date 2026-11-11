"""外部 API 响应的本地缓存，用来减少重复请求。

哈佛的条款要求"不得缓存超过两周"，所以这里带 TTL，
调用方（如哈佛图源）把 TTL 设为 14 天即可自动合规。
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any, Optional

from . import paths
from .util import atomic_write_json, get_logger, read_json


class JsonCache:
    """按命名空间存放的 JSON 缓存。

    注意：调用方传入的 key 绝不能包含 API Key —— 文件名是它的哈希，
    但为了可排查性这里也不保存原始 key。
    """

    def __init__(self, namespace: str, ttl_days: float = 14.0) -> None:
        self.namespace = namespace
        self.ttl_seconds = max(0.0, float(ttl_days)) * 86400
        self.directory = paths.cache_dir() / "api" / namespace
        self.log = get_logger(f"cache.{namespace}")

    def _path(self, key: str) -> Path:
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:20]
        return self.directory / f"{digest}.json"

    def get(self, key: str) -> Optional[Any]:
        data = read_json(self._path(key))
        if not isinstance(data, dict) or "_cached_at" not in data:
            return None
        try:
            age = time.time() - float(data["_cached_at"])
        except (TypeError, ValueError):
            return None
        if self.ttl_seconds and age > self.ttl_seconds:
            return None
        return data.get("value")

    def set(self, key: str, value: Any) -> None:
        try:
            atomic_write_json(
                self._path(key),
                {"_cached_at": time.time(), "value": value},
            )
        except OSError as exc:
            self.log.debug("写缓存失败：%s", exc)

    def prune(self, max_age_days: float) -> int:
        """删掉超过指定天数的缓存文件，返回删除数量。"""
        if not self.directory.exists():
            return 0
        cutoff = time.time() - max(0.0, float(max_age_days)) * 86400
        removed = 0
        for path in self.directory.glob("*.json"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
            except OSError:
                continue
        return removed
