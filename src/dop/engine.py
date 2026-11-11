"""核心引擎：选图 → 下载 → 渲染 → 设为壁纸 → 记录历史。"""

from __future__ import annotations

import os
import random
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlparse

from PIL import Image

from . import catalog as catalog_mod
from . import paths, render, wallpaper
from .cache import JsonCache
from .config import load_config
from .http import HttpClient, HttpError
from .util import (
    atomic_write_json,
    get_logger,
    human_size,
    read_json,
    safe_filename,
)

Progress = Optional[Callable[[str], None]]
MAX_HISTORY = 3000
# 多屏时每次更换会产生「每屏一张 + 整张跨屏图」共 3 个文件，所以保留 12 个
KEEP_WALLPAPERS = 12
# 取图失败的作品在这么多天内不再尝试（例如某个图源的图片服务器暂时不可用）
SKIP_DAYS = 7
# 哈佛条款：内容缓存不得超过两周
HARVARD_IMAGE_MAX_AGE_DAYS = 14
# 图源健康度的统计窗口
SOURCE_HEALTH_DAYS = 7
# 未知方向的旧作品会在下载后判定；单次检查有上限，避免全是竖图时持续下载。
MAX_ORIENTATION_CHECKS = 30


class PortraitImageError(ValueError):
    """竖版画作不参与横屏壁纸轮换。"""


def _image_cache_path(work: dict) -> Path:
    suffix = Path(urlparse(str(work.get("image_url") or "")).path).suffix.lower()
    if suffix not in (".jpg", ".jpeg", ".png", ".webp"):
        suffix = ".jpg"
    return paths.cache_dir() / str(work.get("source") or "misc") / (
        safe_filename(str(work.get("source_id")), 60) + suffix
    )


def _display_size(path: Path) -> tuple[int, int]:
    """按 EXIF 方向读宽高，与实际渲染方向保持一致，不解码整张大图。"""
    with Image.open(path) as image:
        width, height = image.size
        if image.getexif().get(274) in (5, 6, 7, 8):
            width, height = height, width
        return width, height


def _noop(_msg: str) -> None:
    return None


class Engine:
    def __init__(self, cfg: Optional[dict] = None, http: Optional[HttpClient] = None) -> None:
        paths.ensure_dirs()
        self.cfg = cfg or load_config()
        advanced = self.cfg.get("advanced") or {}
        timeout = int(advanced.get("request_timeout", 60))
        rps = float(advanced.get("requests_per_second", 5) or 0)
        self.http = http or HttpClient(
            timeout=timeout,
            min_interval=(1.0 / rps) if rps > 0 else 0.0,
        )
        self.log = get_logger("engine")
        self._dimension_cache = JsonCache("image_dimensions", HARVARD_IMAGE_MAX_AGE_DAYS)

    def reload_config(self) -> dict:
        self.cfg = load_config()
        return self.cfg

    # ------------------------------------------------------------------ 目录
    def get_catalog(self, force: bool = False, progress: Progress = None) -> dict:
        return catalog_mod.ensure_catalog(self.cfg, self.http, force=force, progress=progress)

    # ------------------------------------------------------------------ 选图
    def _landscape_only(self) -> bool:
        return bool((self.cfg.get("selection") or {}).get("landscape_only", True))

    def _dimension_key(self, work: dict) -> str:
        # JsonCache 仅把键的哈希用作文件名，不保存原始 URL。
        return f"{work.get('key')}|{work.get('image_url')}"

    def _known_portrait(self, work: dict) -> bool:
        if not self._landscape_only():
            return False
        cached_image = _image_cache_path(work)
        if cached_image.is_file():
            try:
                width, height = _display_size(cached_image)
                return height > width
            except (OSError, ValueError):
                # 损坏的缓存交给下载/渲染流程处理，方向未知时不误判为竖图。
                pass
        size = self._dimension_cache.get(self._dimension_key(work))
        if isinstance(size, list) and len(size) == 2:
            try:
                width, height = map(int, size)
                return width > 0 and height > width
            except (TypeError, ValueError):
                pass
        return False

    def _check_orientation(self, work: dict, source_file: Path) -> None:
        if not self._landscape_only():
            return
        width, height = _display_size(source_file)
        self._dimension_cache.set(self._dimension_key(work), [width, height])
        if height > width:
            raise PortraitImageError(f"《{work.get('title')}》为竖图（{width}×{height}），已跳过")

    def choose(self, catalog: dict, exclude: Optional[set[str]] = None) -> dict:
        works = [w for w in (catalog.get("works") or []) if w.get("image_url")]
        if not works:
            raise RuntimeError("作品目录为空，请先运行：python main.py catalog --force")
        works = [w for w in works if not self._known_portrait(w)]
        if not works:
            raise PortraitImageError("作品目录中暂时没有可用的横图，已保留当前壁纸")

        # 优先避开最近取图失败的作品；但别把池子排空
        if exclude:
            filtered = [w for w in works if w.get("key") not in exclude]
            if filtered:
                works = filtered

        history = self.get_history()
        recent: dict[str, float] = {}
        for item in history:
            key = str(item.get("key") or "")
            if key:
                recent[key] = max(recent.get(key, 0.0), float(item.get("ts") or 0))
        last_key = str(history[-1].get("key")) if history and history[-1].get("key") else None

        selection = self.cfg.get("selection") or {}
        recency_days = int(selection.get("recency_days", 540))
        cutoff = time.time() - max(0, recency_days) * 86400

        pool = [w for w in works if recent.get(w["key"], 0.0) < cutoff]
        if len(pool) < 20:  # 池子太小说明快轮完了，整体重新开始
            self.log.info("可轮换作品不足（%d 幅），重置轮换池", len(pool))
            pool = list(works)
        if last_key and len(pool) > 1:
            without_last = [w for w in pool if w["key"] != last_key]
            if without_last:
                pool = without_last

        if not selection.get("weighted_by_source", True):
            return random.choice(pool)

        groups: dict[str, list[dict]] = {}
        for work in pool:
            groups.setdefault(str(work.get("source") or "unknown"), []).append(work)
        names = list(groups.keys())
        health = self.get_source_health()
        weights = []
        for name in names:
            spec = (self.cfg.get("sources") or {}).get(name) or {}
            weight = max(0.05, float(spec.get("weight") or 1.0))
            # 连续取图失败的图源自动降权，但仍保留极小概率去试探是否恢复
            if health.get(name, 0) >= 3:
                weight = 0.03
                self.log.debug("图源 %s 近期连续失败 %d 次，已降权", name, health[name])
            weights.append(weight)
        picked_source = random.choices(names, weights=weights, k=1)[0]
        return random.choice(groups[picked_source])

    # ------------------------------------------------------------------ 下载
    def download(self, work: dict, force: bool = False) -> Path:
        urls = [u for u in ([work.get("image_url")] + list(work.get("fallback_urls") or [])) if u]
        if not urls:
            raise RuntimeError(f"《{work.get('title')}》没有可用的图片地址")

        dest = _image_cache_path({**work, "image_url": urls[0]})
        if dest.exists() and dest.stat().st_size > 30_000 and not force:
            self.log.debug("使用已缓存图片：%s", dest)
            return dest

        max_mb = float((self.cfg.get("image") or {}).get("max_download_mb", 80))
        max_bytes = int(max_mb * 1024 * 1024) if max_mb > 0 else None
        last_error: Optional[BaseException] = None
        blocked_hosts: set[str] = set()
        for url in urls:
            host = urlparse(url).netloc.lower()
            if host in blocked_hosts:
                # 同一台服务器刚刚明确拒绝过（限流/封锁），换尺寸也没用
                self.log.debug("跳过同一主机的备用地址：%s", host)
                continue
            try:
                size = self.http.download(url, dest, max_bytes=max_bytes)
                self.log.info("已下载《%s》 %s（%s）", work.get("title"), human_size(size), url.split("/")[-1][:48])
                return dest
            except HttpError as exc:
                last_error = exc
                self.log.warning("图片地址失败：%s", exc)
                status = getattr(exc, "status", None)
                # 服务器在拒绝我们（限流/封锁/源站故障）时，同一主机的其它尺寸也没意义
                if status in (403, 429) or (isinstance(status, int) and status >= 500):
                    blocked_hosts.add(host)
                continue
        raise RuntimeError(f"《{work.get('title')}》所有图片地址均下载失败：{last_error}")

    # ------------------------------------------------------------------ 应用
    def apply(
        self,
        work: Optional[dict] = None,
        *,
        force_new: bool = False,
        rebuild_catalog: bool = False,
        progress: Progress = None,
    ) -> dict[str, Any]:
        say = progress or _noop
        self.reload_config()

        source_file: Optional[Path] = None
        if work is None:
            say("正在准备作品目录…")
            catalog = self.get_catalog(force=rebuild_catalog, progress=progress)
            skipped = self.get_skipped()
            attempts = max(1, int((self.cfg.get("advanced") or {}).get("max_download_attempts", 4)))
            last_error: Optional[BaseException] = None
            remaining = list(catalog.get("works") or [])
            failures = 0
            for _index in range(max(attempts, MAX_ORIENTATION_CHECKS)):
                if not remaining:
                    break
                try:
                    candidate = self.choose({**catalog, "works": remaining}, exclude=skipped)
                except PortraitImageError as exc:
                    last_error = exc
                    break
                remaining = [w for w in remaining if w.get("key") != candidate.get("key")]
                say(f"正在下载《{candidate.get('title')}》…")
                try:
                    source_file = self.download(candidate, force=force_new)
                    self._check_orientation(candidate, source_file)
                    work = candidate
                    self.record_source_result(str(candidate.get("source") or ""), True)
                    break
                except PortraitImageError as exc:
                    # 方向不适合不是图源故障，也不应消耗取图失败的重试次数。
                    last_error = exc
                    source_file = None
                    self.log.info("%s", exc)
                    say(str(exc))
                    continue
                except Exception as exc:  # 单个作品取图失败就换下一幅，别让整次更换失败
                    last_error = exc
                    failures += 1
                    self.log.warning(
                        "第 %d/%d 次取图失败，换下一幅：《%s》%s",
                        failures, attempts, candidate.get("title"), exc,
                    )
                    self.mark_skipped(str(candidate.get("key")), str(exc))
                    self.record_source_result(str(candidate.get("source") or ""), False)
                    skipped.add(str(candidate.get("key")))
                    if failures >= attempts:
                        break
            if source_file is None or work is None:
                if isinstance(last_error, PortraitImageError):
                    raise PortraitImageError("本次未找到可用横图，已保留当前壁纸，请稍后重试") from last_error
                raise RuntimeError(
                    f"尝试 {failures} 幅仍取不到可用图片，最后一次错误：{last_error}"
                )
        else:
            say(f"正在下载《{work.get('title')}》…")
            source_file = self.download(work, force=force_new)
            self._check_orientation(work, source_file)

        say("正在渲染壁纸…")
        monitors = wallpaper.get_monitors()
        multi = len(monitors) > 1
        # 多屏布局策略：
        #   each（默认）—— 每块显示器各渲染一份完整版式。不依赖系统怎么认主副屏，
        #                   每块屏都能看到完整的画作，不会出现"某块屏只有模糊背景"。
        #   primary     —— 画作放主屏，其它屏用模糊背景延伸（依赖主副屏识别）。
        #   span        —— 整张跨屏图切分。
        mode = str((self.cfg.get("image") or {}).get("multi_monitor") or "each").lower()
        if multi and mode in ("primary", "span"):
            canvas = render.compose(source_file, monitors, self.cfg, work)
            slices = render.split_by_monitor(canvas, monitors)
        else:
            slices = []
            for monitor in monitors:
                local = dict(monitor)
                local.update(
                    left=0, top=0, right=monitor["width"], bottom=monitor["height"], primary=True
                )
                slices.append((monitor, render.compose(source_file, [local], self.cfg, work)))
            # 单屏时切片本身就是整张成品，不必再渲染一遍
            canvas = (
                slices[0][1]
                if len(slices) == 1
                else render.compose(source_file, monitors, self.cfg, work)
            )

        suffix = render.suffix_for(self.cfg)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        wall_dir = paths.wallpaper_dir()

        say("正在设置桌面壁纸…")
        per_monitor_files: list[tuple[dict, Path]] = []
        for index, (monitor, image) in enumerate(slices):
            name = f"dop-{stamp}-{index}{suffix}" if multi else f"dop-{stamp}{suffix}"
            per_monitor_files.append((monitor, render.save(image, wall_dir / name, self.cfg)))

        # 整张跨屏图留一份：详情页/缩略图用它能看到完整设计
        canvas_file = render.save(canvas, wall_dir / f"dop-{stamp}-full{suffix}", self.cfg)

        primary_file = next(
            (path for monitor, path in per_monitor_files if monitor.get("primary")),
            per_monitor_files[0][1],
        )
        applied = False
        if multi:
            # 多屏优先按显示器分别设置：Windows 11 常常不遵守注册表的跨屏设置，
            # 会把整张大图压进每一块屏，导致画作被压扁并重复出现。
            applied = wallpaper.set_wallpapers_per_monitor(per_monitor_files, position="fill")
        if not applied:
            if multi:
                self.log.info(
                    "逐屏设置不可用，回退为「每块屏显示同一张」"
                    "（图片尺寸与单块屏一致，不会被压扁）"
                )
            wallpaper.set_wallpaper(primary_file, "fill")
            per_monitor_files = [(monitors[0], primary_file)]

        if (self.cfg.get("advanced") or {}).get("keep_archive", False):
            archive = paths.archive_dir() / canvas_file.name
            archive.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(canvas_file, archive)

        record = {
            "key": work.get("key"),
            "ts": time.time(),
            "time_display": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "title": work.get("title"),
            "artist": work.get("artist"),
            "date_display": work.get("date_display"),
            "year_start": work.get("year_start"),
            "year_end": work.get("year_end"),
            "museum": work.get("museum"),
            "source": work.get("source"),
            "source_id": work.get("source_id"),
            "medium": work.get("medium"),
            "license": work.get("license"),
            "page_url": work.get("page_url"),
            "image_url": work.get("image_url"),
            "source_file": str(source_file),
            "wallpaper_file": str(primary_file),
            "canvas_file": str(canvas_file),
            "wallpaper_files": [str(path) for _monitor, path in per_monitor_files],
            "per_monitor": bool(multi and applied),
            "resolution": f"{canvas.width}×{canvas.height}",
            "monitors": wallpaper.describe_monitors(monitors),
        }
        self._record(record)
        self._prune_wallpapers()
        say("完成")
        self.log.info(
            "壁纸已更新：《%s》%s %s → %s",
            record["title"], record["artist"] or "佚名", record["date_display"] or "",
            record["resolution"],
        )
        return record

    # ------------------------------------------------------------------ 历史
    def get_history(self) -> list[dict]:
        data = read_json(paths.history_path(), [])
        return data if isinstance(data, list) else []

    def current(self) -> Optional[dict]:
        data = read_json(paths.current_path())
        return data if isinstance(data, dict) and data.get("key") else None

    # ------------------------------------------------------------------ 壁纸守护
    def _recorded_files(self, record: Optional[dict] = None) -> list[Path]:
        """取出当前作品对应的成品图（逐屏优先）。"""
        record = record or self.current() or {}
        files = [Path(str(f)) for f in (record.get("wallpaper_files") or []) if f]
        if not files:
            single = record.get("wallpaper_file")
            if single:
                files = [Path(str(single))]
        return files

    def wallpaper_matches(self) -> bool:
        """桌面当前壁纸是否还是本程序设的那一张。"""
        files = self._recorded_files()
        if not files:
            return True  # 还没换过壁纸，不用管
        try:
            current = wallpaper.describe_wallpapers()
        except Exception as exc:  # noqa: BLE001
            self.log.debug("读取当前壁纸失败：%s", exc)
            return True
        if not current:
            return True  # 读不到就别乱设，免得跟用户打架

        def norm(value: object) -> str:
            text = str(value or "")
            if not text:
                return ""
            try:
                return os.path.normcase(str(Path(text).resolve()))
            except OSError:
                return os.path.normcase(text)

        expected = {norm(f) for f in files if f.exists()}
        if not expected:
            return False  # 我们的成品图都没了，需要重新来一张
        actual = {norm(item.get("wallpaper")) for item in current if item.get("wallpaper")}
        return bool(actual) and actual == expected

    def restore_wallpaper(self) -> bool:
        """不重新下载，直接把本程序已有的成品图重新设回桌面。"""
        files = [f for f in self._recorded_files() if f.exists()]
        if not files:
            return False
        monitors = wallpaper.get_monitors()
        if len(monitors) > 1 and len(files) >= len(monitors):
            assignments = [(monitors[i], files[i]) for i in range(len(monitors))]
            if wallpaper.set_wallpapers_per_monitor(assignments, position="fill"):
                return True
        wallpaper.set_wallpaper(files[0], "span" if len(monitors) > 1 else "fill")
        return True

    def _record(self, record: dict) -> None:
        atomic_write_json(paths.current_path(), record)
        history = self.get_history()
        history.append(record)
        if len(history) > MAX_HISTORY:
            history = history[-MAX_HISTORY:]
        atomic_write_json(paths.history_path(), history)

    # ------------------------------------------------------------------ 取图失败记忆
    def get_skipped(self) -> set[str]:
        """最近取图失败的作品，短时间内不再尝试。"""
        data = read_json(paths.skip_path(), {}) or {}
        if not isinstance(data, dict):
            return set()
        cutoff = time.time() - SKIP_DAYS * 86400
        keys: set[str] = set()
        for key, ts in data.items():
            try:
                if float(ts or 0) > cutoff:
                    keys.add(str(key))
            except (TypeError, ValueError):
                continue
        return keys

    def mark_skipped(self, key: str, reason: str = "") -> None:
        if not key:
            return
        data = read_json(paths.skip_path(), {}) or {}
        if not isinstance(data, dict):
            data = {}
        data[str(key)] = time.time()
        if len(data) > 2000:
            data = dict(sorted(data.items(), key=lambda kv: kv[1], reverse=True)[:2000])
        atomic_write_json(paths.skip_path(), data)
        self.log.debug("已记录取图失败：%s（%s）", key, str(reason)[:120])

    # ------------------------------------------------------------------ 图源健康度
    def get_source_health(self) -> dict[str, int]:
        """返回各图源最近连续取图失败的次数。"""
        data = read_json(paths.health_path(), {}) or {}
        if not isinstance(data, dict):
            return {}
        cutoff = time.time() - SOURCE_HEALTH_DAYS * 86400
        health: dict[str, int] = {}
        for name, item in data.items():
            if not isinstance(item, dict):
                continue
            try:
                if float(item.get("ts") or 0) < cutoff:
                    continue
                health[str(name)] = int(item.get("fail_streak") or 0)
            except (TypeError, ValueError):
                continue
        return health

    def record_source_result(self, source: str, ok: bool) -> None:
        if not source:
            return
        data = read_json(paths.health_path(), {}) or {}
        if not isinstance(data, dict):
            data = {}
        item = data.get(source) if isinstance(data.get(source), dict) else {}
        streak = 0 if ok else int(item.get("fail_streak") or 0) + 1
        data[source] = {
            "fail_streak": streak,
            "ts": time.time(),
            "last_ok" if ok else "last_fail": time.time(),
        }
        atomic_write_json(paths.health_path(), data)
        if streak == 3:
            self.log.warning(
                "图源 %s 连续 3 次取图失败，已自动降权（等它恢复后会自动回来）", source
            )

    def _prune_source_images(self) -> None:
        """哈佛条款要求内容不得缓存超过两周，超期的原图要清掉。"""
        folder = paths.cache_dir() / "harvard"
        if folder.exists():
            cutoff = time.time() - HARVARD_IMAGE_MAX_AGE_DAYS * 86400
            removed = 0
            for path in folder.glob("*"):
                try:
                    if path.stat().st_mtime < cutoff:
                        path.unlink()
                        removed += 1
                except OSError:
                    continue
            if removed:
                self.log.info("按哈佛条款清理了 %d 张超过两周的缓存原图", removed)
        JsonCache("harvard", HARVARD_IMAGE_MAX_AGE_DAYS).prune(HARVARD_IMAGE_MAX_AGE_DAYS)

    def _prune_wallpapers(self) -> None:
        self._prune_source_images()
        if (self.cfg.get("advanced") or {}).get("keep_archive", False):
            return
        try:
            files = sorted(
                paths.wallpaper_dir().glob("dop-*"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            return
        for stale in files[KEEP_WALLPAPERS:]:
            try:
                stale.unlink()
            except OSError:
                pass

    # ------------------------------------------------------------------ 收藏
    def favorite(self, record: Optional[dict] = None) -> Optional[Path]:
        record = record or self.current()
        if not record:
            self.log.warning("还没有换过壁纸，无法收藏")
            return None
        stamp = datetime.now().strftime("%Y%m%d")
        name = f"{stamp}_{safe_filename(record.get('title') or 'artwork', 60)}"
        wall = Path(str(record.get("wallpaper_file") or ""))
        if not wall.exists():
            self.log.warning("壁纸文件已不存在：%s", wall)
            return None
        target = paths.favorites_dir() / f"{name}{wall.suffix}"
        shutil.copy2(wall, target)

        source = Path(str(record.get("source_file") or ""))
        if source.exists():
            shutil.copy2(source, paths.favorites_dir() / f"{name}_原图{source.suffix}")

        index = read_json(paths.favorites_index_path(), [])
        if not isinstance(index, list):
            index = []
        index.append({**record, "favorite_file": str(target), "favorited_at": time.time()})
        atomic_write_json(paths.favorites_index_path(), index)
        self.log.info("已收藏到 %s", target)
        return target

    # ------------------------------------------------------------------ 状态
    def status_text(self) -> str:
        lines: list[str] = []
        cfg = self.reload_config()
        sched = cfg.get("schedule") or {}
        state = "已暂停" if sched.get("paused") else "运行中"
        lines.append(f"自动更换：{state}，每天 {sched.get('time')}（补换：{'开' if sched.get('catch_up') else '关'}）")
        lines.append(f"年代范围：{cfg.get('date_range')}")
        from . import autostart

        lines.append(f"开机自启：{'开' if autostart.is_enabled() else '关'}（{autostart.describe()}）")
        from .tray import tray_is_running

        lines.append(f"后台托盘：{'运行中' if tray_is_running() else '未运行'}")
        guard = int((cfg.get("advanced") or {}).get("wallpaper_guard_seconds", 300) or 0)
        lines.append(
            f"壁纸守护：{'每 %d 秒检查一次' % guard if guard > 0 else '已关闭'}"
        )

        catalog = catalog_mod.load_catalog()
        if catalog:
            freshness = "有效" if catalog_mod.is_fresh(
                catalog, int(catalog_mod.effective_ttl_days(cfg))
            ) else "已过期"
            lines.append(
                f"作品目录：{catalog.get('total', 0)} 幅（{freshness}，生成于 {catalog.get('generated_display')}）"
            )
            counts = catalog.get("per_source") or {}
            for name, count in counts.items():
                lines.append(f"    · {name}: {count} 幅")
            # 启用了却一幅都没抓到的图源要显眼地提示，否则接口挂掉也看不出来
            from .sources import SOURCES

            for name, spec in (cfg.get("sources") or {}).items():
                if not isinstance(spec, dict) or not spec.get("enabled") or name not in SOURCES:
                    continue
                if counts.get(name):
                    continue
                if name in catalog_mod.SOURCE_REQUIRES_KEY and not str(
                    spec.get("api_key") or ""
                ).strip():
                    continue  # 没配 Key 属于正常跳过
                lines.append(
                    f"    ! {name}: 0 幅 —— 抓取可能失败了，看日志里的 {name} 警告"
                )
        else:
            lines.append("作品目录：尚未建立（首次换壁纸时会自动抓取）")
        lines.append(f"目录有效期：{catalog_mod.effective_ttl_days(cfg):.0f} 天（受各图源条款约束）")

        # 哈佛的 Key 只显示打码结果，绝不输出明文
        from .config import env_api_key, mask as mask_key

        harvard = (cfg.get("sources") or {}).get("harvard") or {}
        if harvard.get("enabled"):
            key = str(harvard.get("api_key") or "").strip()
            if key:
                where = "环境变量" if env_api_key() else "配置文件"
                lines.append(f"Harvard：已配置 Key {mask_key(key)}（来自{where}）")
            else:
                lines.append(
                    "Harvard：未配置 Key（设置环境变量 DOP_HARVARD_API_KEY 即可启用）"
                )

        try:
            monitors = wallpaper.get_monitors()
            lines.append(f"显示器：{wallpaper.describe_monitors(monitors)}")
        except Exception as exc:  # pragma: no cover
            lines.append(f"显示器：读取失败 {exc}")

        current = self.current()
        if current:
            lines.append(
                f"当前壁纸：《{current.get('title')}》{current.get('artist') or ''} "
                f"{current.get('date_display') or ''} — {current.get('museum') or ''}"
            )
            lines.append(f"    更换时间：{current.get('time_display')}  分辨率：{current.get('resolution')}")
            lines.append(f"    成品文件：{current.get('wallpaper_file')}")
        else:
            lines.append("当前壁纸：尚未更换过")
        lines.append(f"数据目录：{paths.data_root()}")
        return "\n".join(lines)
