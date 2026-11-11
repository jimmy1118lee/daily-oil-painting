"""极简 HTTP 客户端（只依赖标准库）：重试、退避、流式下载。

注意：芝加哥艺术博物馆 (AIC) 的图片 CDN 会拒绝没有浏览器特征的请求，
因此默认带上浏览器 User-Agent 以及 AIC 官方要求的 AIC-User-Agent 头。
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Optional

from .util import get_logger

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
APP_UA = "DailyOilPainting/1.0 (personal non-commercial wallpaper rotator)"

# 有些博物馆（例如哈佛）只能通过查询参数传 API Key，一旦异常信息里带上完整 URL，
# 密钥就会顺着日志泄出去。所有进入日志/异常的文本都要先过这个函数。
_SECRET_QUERY = re.compile(
    r"((?:api[-_]?key|apikey|token|secret|password)=)[^&\s\"'\)]+", re.IGNORECASE
)


def redact(value: object) -> str:
    """把文本里的密钥抹掉。"""
    return _SECRET_QUERY.sub(r"\1***", str(value))


class HttpError(RuntimeError):
    """带上 HTTP 状态码，方便调用方区分"内容不存在"和"被限流/被封锁"。"""

    def __init__(self, message: str, status: Optional[int] = None) -> None:
        super().__init__(message)
        self.status = status


class HttpClient:
    """带全局限速的 HTTP 客户端。

    min_interval 控制"两次请求发起之间至少间隔多少秒"，用来避免触发
    博物馆 API 的限流（大都会在实际使用中比文档写的更容易拒绝高频请求）。
    """

    # 403/429 属于"被限流"，要退避后重试；4xx 其它错误直接放弃
    RETRYABLE_STATUS = (403, 408, 429, 500, 502, 503, 504)

    def __init__(
        self,
        timeout: int = 60,
        retries: int = 3,
        backoff: float = 1.7,
        min_interval: float = 0.0,
    ) -> None:
        self.timeout = timeout
        self.retries = max(1, retries)
        self.backoff = backoff
        self.min_interval = max(0.0, float(min_interval))
        self.log = get_logger("http")
        self._pace_lock = threading.Lock()
        self._next_slot = 0.0
        self._base_headers = {
            "User-Agent": BROWSER_UA,
            "AIC-User-Agent": APP_UA,
            "Accept-Language": "en-US,en;q=0.9",
            "Connection": "close",
        }

    # ------------------------------------------------------------------ 内部
    def _pace(self) -> None:
        """全局节流：保证多个线程合计不超过设定的请求频率。"""
        if self.min_interval <= 0:
            return
        with self._pace_lock:
            now = time.monotonic()
            if self._next_slot > now:
                time.sleep(self._next_slot - now)
                now = time.monotonic()
            self._next_slot = now + self.min_interval

    def _headers(self, extra: Optional[dict] = None) -> dict:
        h = dict(self._base_headers)
        if extra:
            h.update({k: v for k, v in extra.items() if v is not None})
        return h

    def _open(self, url: str, data: Optional[bytes] = None, headers: Optional[dict] = None):
        req = urllib.request.Request(url, data=data, headers=self._headers(headers))
        return urllib.request.urlopen(req, timeout=self.timeout)

    @staticmethod
    def _retry_after(exc: urllib.error.HTTPError) -> float:
        try:
            value = exc.headers.get("Retry-After") if exc.headers else None
            return min(60.0, max(0.0, float(value)))
        except (TypeError, ValueError, AttributeError):
            return 0.0

    def _delay_for(self, exc: BaseException, attempt: int) -> float:
        base = self.backoff ** (attempt + 1)
        if isinstance(exc, urllib.error.HTTPError) and exc.code in (403, 429):
            # 被限流时退避要更长，避免越试越糟
            return max(base, 3.0 * (attempt + 1)) + self._retry_after(exc)
        if isinstance(exc, urllib.error.HTTPError) and exc.code >= 500:
            return max(base, 2.0 * (attempt + 1))
        return base

    def _run_with_retry(self, url: str, data: Optional[bytes], headers: Optional[dict], what: str):
        last: Optional[BaseException] = None
        for attempt in range(self.retries):
            self._pace()
            try:
                return self._open(url, data=data, headers=headers)
            except urllib.error.HTTPError as exc:
                last = exc
                if exc.code not in self.RETRYABLE_STATUS:
                    raise HttpError(
                        f"{what} 失败：HTTP {exc.code} {exc.reason} ({redact(url)})",
                        status=exc.code,
                    ) from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last = exc
            if attempt < self.retries - 1:
                delay = self._delay_for(last, attempt)
                self.log.debug(
                    "%s 第 %d 次失败(%s)，%.1fs 后重试",
                    what, attempt + 1, redact(last), delay,
                )
                time.sleep(delay)
        status = getattr(last, "code", None)
        raise HttpError(
            f"{what} 失败（已重试 {self.retries} 次）：{redact(last)} ({redact(url)})",
            status=status if isinstance(status, int) else None,
        )

    # ------------------------------------------------------------------ 公开
    def get_json(self, url: str, params: Optional[dict] = None, headers: Optional[dict] = None) -> Any:
        if params:
            clean = {k: v for k, v in params.items() if v is not None}
            url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(clean)
        resp = self._run_with_retry(url, None, headers, "获取 JSON")
        with resp:
            body = resp.read()
        try:
            return json.loads(body.decode("utf-8", "replace"))
        except ValueError as exc:
            raise HttpError(f"返回内容不是合法 JSON：{redact(url)}") from exc

    def post_json(self, url: str, payload: Any, headers: Optional[dict] = None) -> Any:
        data = json.dumps(payload).encode("utf-8")
        hdrs = {"Content-Type": "application/json"}
        if headers:
            hdrs.update(headers)
        resp = self._run_with_retry(url, data, hdrs, "POST JSON")
        with resp:
            body = resp.read()
        try:
            return json.loads(body.decode("utf-8", "replace"))
        except ValueError as exc:
            raise HttpError(f"返回内容不是合法 JSON：{redact(url)}") from exc

    def download(
        self,
        url: str,
        dest: Path,
        max_bytes: Optional[int] = None,
        headers: Optional[dict] = None,
    ) -> int:
        """流式下载到 dest，成功返回字节数。先写 .part 再改名。"""
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".part")
        last: Optional[BaseException] = None
        for attempt in range(self.retries):
            try:
                self._pace()
                resp = self._open(url, headers=headers)
                with resp, open(tmp, "wb") as fh:
                    declared = int(resp.headers.get("Content-Length") or 0)
                    if max_bytes and declared and declared > max_bytes:
                        raise HttpError(
                            f"图片过大：{declared / 1048576:.1f} MB 超过上限 "
                            f"{max_bytes / 1048576:.0f} MB"
                        )
                    got = 0
                    while True:
                        chunk = resp.read(262144)
                        if not chunk:
                            break
                        got += len(chunk)
                        if max_bytes and got > max_bytes:
                            raise HttpError("图片超过大小上限，已中断下载")
                        fh.write(chunk)
                if got < 1024:
                    raise HttpError(f"图片内容异常（仅 {got} 字节）")
                os.replace(tmp, dest)
                return got
            except HttpError:
                self._cleanup(tmp)
                raise
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
                last = exc
                self._cleanup(tmp)
                if attempt < self.retries - 1:
                    time.sleep(self.backoff ** (attempt + 1))
        raise HttpError(
            f"下载失败（已重试 {self.retries} 次）：{redact(last)} ({redact(url)})",
            status=getattr(last, "code", None),
        )

    @staticmethod
    def _cleanup(tmp: Path) -> None:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
