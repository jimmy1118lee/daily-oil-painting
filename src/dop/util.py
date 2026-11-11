"""通用工具：日志、JSON 原子读写、时间计算。"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import threading
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Optional

APP_NAME = "DailyOilPainting"
VERSION = "1.0.0"

_log_lock = threading.RLock()  # 可重入：setup_logging 里会调用 register_secrets
_configured = False


# --------------------------------------------------------------------------- 日志
_SECRET_PATTERN = None


class RedactingFilter(logging.Filter):
    """把密钥从日志里抹掉。

    哈佛的 API 只能通过 URL 查询参数鉴权，万一异常信息里带上了完整 URL，
    密钥就会顺着日志泄出去。这里做最后一道防线。
    """

    def __init__(self, secrets: Optional[list[str]] = None) -> None:
        super().__init__()
        self.secrets = [s for s in (secrets or []) if s and len(str(s)) >= 6]

    def filter(self, record: logging.LogRecord) -> bool:
        global _SECRET_PATTERN
        try:
            message = record.getMessage()
        except (TypeError, ValueError):
            return True
        redacted = message
        for secret in self.secrets:
            if secret in redacted:
                redacted = redacted.replace(secret, "***")
        if _SECRET_PATTERN is None:
            import re

            # 兜底：无论哪个参数名，只要形如 key=很长的串就抹掉
            _SECRET_PATTERN = re.compile(
                r"((?:api[-_]?key|apikey|token|secret|password)=)[^&\s\"'\)]+",
                re.IGNORECASE,
            )
        redacted = _SECRET_PATTERN.sub(r"\1***", redacted)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


_secret_filter: Optional[RedactingFilter] = None
_log_setup_error: Optional[str] = None


def logging_setup_error() -> Optional[str]:
    """文件日志初始化失败的原因（None 表示正常）。

    以前这里静默吞掉异常，结果是"程序在跑但日志一行没有"，非常难排查，
    所以现在把原因记下来，供 `diagnose` 命令显示。
    """
    return _log_setup_error


def register_secrets(secrets: list[str]) -> None:
    """把密钥登记到日志过滤器（可重复调用，逐步补充）。

    注意：filter 必须同时挂到 **handler** 上。logging 只会对"直接由该 logger
    产生"的记录应用 logger 级 filter，子 logger（如 DailyOilPainting.engine）
    传播上来的记录不会经过父 logger 的 filter，只有 handler 级 filter 才会生效。
    """
    global _secret_filter
    values = [str(s).strip() for s in (secrets or []) if s and str(s).strip()]
    logger = logging.getLogger(APP_NAME)
    with _log_lock:
        if _secret_filter is None:
            _secret_filter = RedactingFilter(values)
        else:
            _secret_filter.secrets.extend(
                v for v in values if v not in _secret_filter.secrets
            )
        if _secret_filter not in logger.filters:
            logger.addFilter(_secret_filter)
        for handler in list(logger.handlers):
            if _secret_filter not in handler.filters:
                handler.addFilter(_secret_filter)


def setup_logging(
    log_file: Path,
    verbose: bool = False,
    console: bool = True,
    secrets: Optional[list[str]] = None,
) -> logging.Logger:
    """初始化全局日志（幂等）。托盘模式用 pythonw 启动时 console=False。"""
    global _configured
    logger = logging.getLogger(APP_NAME)
    # 注意：register_secrets 自己会加锁，所以 _log_lock 必须是可重入锁，
    # 否则这里会自己把自己锁死（启动即挂起）。
    with _log_lock:
        if _configured:
            already = True
        else:
            already = False
            logger.setLevel(logging.DEBUG if verbose else logging.INFO)
            logger.propagate = False
            fmt = logging.Formatter(
                "%(asctime)s [%(levelname)-7s] %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S"
            )
            try:
                log_file.parent.mkdir(parents=True, exist_ok=True)
                fh = RotatingFileHandler(
                    str(log_file), maxBytes=2_000_000, backupCount=3, encoding="utf-8"
                )
                fh.setFormatter(fmt)
                logger.addHandler(fh)
            except OSError as exc:
                global _log_setup_error
                _log_setup_error = f"{type(exc).__name__}: {exc}"
                try:
                    sys.stderr.write(f"[logging] 无法写入日志文件 {log_file}：{exc}\n")
                except (AttributeError, ValueError, OSError):
                    pass
            if console and sys.stderr is not None:
                sh = logging.StreamHandler(sys.stderr)
                sh.setFormatter(fmt)
                logger.addHandler(sh)
            if not logger.handlers:
                logger.addHandler(logging.NullHandler())
            _configured = True
    # 放在锁外调用：它会把过滤器挂到上面刚建好的 handler 上
    register_secrets(secrets or [])
    return logger


def get_logger(name: str = "core") -> logging.Logger:
    return logging.getLogger(APP_NAME).getChild(name)


class _NullStream:
    """给 pythonw 用的空输出流。

    pythonw.exe 启动的进程里 sys.stdout / sys.stderr 是 None，
    任何 print() 或 sys.stdout.write() 都会抛 AttributeError。
    这类异常在没有控制台时会被静默吞掉，表现为"命令跑了但什么都没发生"，
    所以必须在一开始就把它们换成安全的空流。
    """

    encoding = "utf-8"
    errors = "replace"

    def write(self, text) -> int:  # noqa: ANN001
        return len(text) if text else 0

    def writelines(self, lines) -> None:  # noqa: ANN001
        return None

    def flush(self) -> None:
        return None

    def isatty(self) -> bool:
        return False

    def reconfigure(self, **_kwargs) -> None:
        return None

    def close(self) -> None:
        return None


def ensure_std_streams() -> None:
    """确保 sys.stdout / sys.stderr 可用（pythonw 下它们是 None）。"""
    for name in ("stdout", "stderr"):
        if getattr(sys, name, None) is None:
            setattr(sys, name, _NullStream())


# --------------------------------------------------------------------------- JSON
def atomic_write_json(path: Path, data: Any) -> None:
    """先写临时文件再原子替换，避免程序中断导致 JSON 损坏。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_json(path: Path, default: Any = None) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding=encoding) as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# --------------------------------------------------------------------------- 时间
def parse_hhmm(value: str, fallback: str = "08:30") -> tuple[int, int]:
    for candidate in (value, fallback):
        try:
            hh, mm = str(candidate).strip().replace("：", ":").split(":")
            h, m = int(hh), int(mm)
            if 0 <= h <= 23 and 0 <= m <= 59:
                return h, m
        except (ValueError, AttributeError):
            continue
    return 8, 30


def most_recent_target(now: datetime, hhmm: str) -> datetime:
    """返回不晚于 now 的最近一个"每天 hhmm"时刻。

    用它来判断"今天该换的壁纸是否已经换过"：
    若 last_change < most_recent_target(now) 就说明错过了，需要补换。
    """
    h, m = parse_hhmm(hhmm)
    today = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if now >= today:
        return today
    return today - timedelta(days=1)


def next_target(now: datetime, hhmm: str) -> datetime:
    h, m = parse_hhmm(hhmm)
    today = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if now < today:
        return today
    return today + timedelta(days=1)


def ts_to_dt(ts: Optional[float]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(float(ts))
    except (ValueError, OSError, OverflowError):
        return None


def human_size(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(num) < 1024.0:
            return f"{num:.0f} {unit}" if unit == "B" else f"{num:.1f} {unit}"
        num /= 1024.0
    return f"{num:.1f} TB"


def safe_filename(name: str, limit: int = 80) -> str:
    keep = []
    for ch in str(name):
        if ch.isalnum() or ch in "-_.":
            keep.append(ch)
        elif ch in " /\\:，,":
            keep.append("_")
    out = "".join(keep).strip("._") or "artwork"
    return out[:limit]
