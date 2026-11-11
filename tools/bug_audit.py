"""离线行为审计：用隔离数据复现缺陷，不联网、不设置真实壁纸或自启。

在项目根目录运行：python tools/bug_audit.py
失败项表示已复现的行为缺陷；只替换外部边界，执行实际业务方法。
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import io
import logging
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PIL import Image

from dop import catalog, cli, paths, render, tray, util
from dop.config import DEFAULT_CONFIG
from dop.engine import Engine
from dop.http import HttpClient, HttpError
from dop.sources.base import in_date_range, looks_like_painting, make_work


def artwork(number: int, source: str = "met", year: int = 1600) -> dict:
    return make_work(
        source=source, source_id=str(number), title=f"测试画作 {number}",
        artist="测试作者", year_start=year, year_end=year,
        image_url=f"https://example.invalid/{source}/{number}.jpg", medium="Oil on canvas",
    )


def monitor(left: int = 0, top: int = 0, width: int = 320, height: int = 180,
            primary: bool = True) -> dict:
    return dict(left=left, top=top, right=left + width, bottom=top + height,
                width=width, height=height, primary=primary)


def fixture_catalog(works: list[dict], age_days: float = 0) -> dict:
    counts: dict[str, int] = {}
    for work in works:
        counts[work["source"]] = counts.get(work["source"], 0) + 1
    return dict(generated_at=time.time() - age_days * 86400,
                date_range=[1300, 1800], total=len(works), works=works, per_source=counts)


class IsolatedTest(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory(prefix="dop-audit-")
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        env = patch.dict(os.environ, {"DOP_DATA_DIR": str(self.root)})
        env.start()
        self.addCleanup(env.stop)
        self.cfg = copy.deepcopy(DEFAULT_CONFIG)
        self.cfg["image"].update(show_caption=False, background="color")
        self.cfg["sources"] = {"met": {"enabled": True, "weight": 1}}
        self.engine = Engine(self.cfg, http=Mock())
        # 避免任何测试通过 reload_config 读取用户的配置或凭据。
        reload_patch = patch.object(self.engine, "reload_config", return_value=self.cfg)
        reload_patch.start()
        self.addCleanup(reload_patch.stop)


class CatalogTests(IsolatedTest):
    def test_unchanged_fresh_catalog_reuses_cache(self):
        old = fixture_catalog([artwork(1)])
        util.atomic_write_json(paths.catalog_path(), old)
        with patch.object(catalog, "build_catalog") as rebuild:
            self.assertEqual(catalog.ensure_catalog(self.cfg, Mock()), old)
        rebuild.assert_not_called()

    def test_expired_catalog_rebuilds(self):
        util.atomic_write_json(paths.catalog_path(), fixture_catalog([artwork(1)], 31))
        replacement = fixture_catalog([artwork(2)])
        with patch.object(catalog, "build_catalog", return_value=replacement) as rebuild:
            self.assertEqual(catalog.ensure_catalog(self.cfg, Mock()), replacement)
        rebuild.assert_called_once()

    def test_changed_date_range_does_not_reuse_incompatible_cache(self):
        util.atomic_write_json(paths.catalog_path(), fixture_catalog([artwork(1, year=1400)]))
        self.cfg["date_range"] = [1700, 1800]
        replacement = fixture_catalog([artwork(2, year=1750)])
        replacement["date_range"] = [1700, 1800]
        with patch.object(catalog, "build_catalog", return_value=replacement):
            result = catalog.ensure_catalog(self.cfg, Mock())
        self.assertTrue(all(1700 <= w["year_start"] <= 1800 for w in result["works"]),
                        "修改年代后仍返回 1400 年的旧作品")

    def test_disabled_source_is_not_selected_from_fresh_cache(self):
        util.atomic_write_json(paths.catalog_path(), fixture_catalog([artwork(1), artwork(2, "artic")]))
        self.cfg["sources"] = {"met": {"enabled": False}, "artic": {"enabled": True}}
        with patch.object(catalog, "build_catalog", return_value=fixture_catalog([artwork(2, "artic")])):
            result = catalog.ensure_catalog(self.cfg, Mock())
        with patch("dop.engine.random.choices", side_effect=lambda names, **kw: [names[0]]), \
             patch("dop.engine.random.choice", side_effect=lambda pool: pool[0]):
            chosen = self.engine.choose(result)
        self.assertEqual(chosen["source"], "artic", "已禁用 met，仍实际选中 met")

    def test_failed_forced_rebuild_preserves_usable_catalog(self):
        old = fixture_catalog([artwork(1)])
        util.atomic_write_json(paths.catalog_path(), old)
        fake_source = Mock()
        fake_source.fetch.side_effect = HttpError("模拟网络中断")
        with patch.dict(catalog.SOURCES, {"met": fake_source}, clear=True):
            try:
                catalog.ensure_catalog(self.cfg, Mock(), force=True)
            except Exception:
                pass  # 允许明确报错，但必须保留原有可用目录。
        self.assertEqual(catalog.load_catalog()["works"], old["works"],
                         "强制重建失败，把原有作品库覆盖为空")

    def test_failed_automatic_rebuild_keeps_old_catalog(self):
        old = fixture_catalog([artwork(1)], 31)
        util.atomic_write_json(paths.catalog_path(), old)
        fake_source = Mock()
        fake_source.fetch.side_effect = HttpError("模拟网络中断")
        with patch.dict(catalog.SOURCES, {"met": fake_source}, clear=True):
            self.assertEqual(catalog.ensure_catalog(self.cfg, Mock()), old)

    def test_dedupe_count_and_identity(self):
        first = artwork(1)
        self.assertEqual(catalog._dedupe([first, dict(first), artwork(2)]), [first, artwork(2)])


class SelectionTests(IsolatedTest):
    def test_last_work_is_not_selected_again(self):
        works = [artwork(1), artwork(2)]
        util.atomic_write_json(paths.history_path(), [{"key": works[0]["key"], "ts": time.time()}])
        self.assertEqual(self.engine.choose(fixture_catalog(works))["key"], works[1]["key"])

    def test_skipped_work_is_avoided_in_large_pool(self):
        works = [artwork(i) for i in range(21)]
        with patch("dop.engine.random.choice", side_effect=lambda pool: pool[0]):
            self.assertNotEqual(self.engine.choose(fixture_catalog(works), {"met:0"})["key"], "met:0")

    def test_skipped_work_is_avoided_when_healthy_alternative_exists(self):
        works = [artwork(0), artwork(1)]
        with patch("dop.engine.random.choice", side_effect=lambda pool: pool[0]):
            chosen = self.engine.choose(fixture_catalog(works), {"met:0"})
        self.assertEqual(chosen["key"], "met:1", "仍有正常画作，但重复选择已失败的画作")

    def test_apply_download_failure_retries_different_work(self):
        works = [artwork(0), artwork(1)]
        attempts = []

        def download(work, force=False):
            attempts.append(work["key"])
            if work["key"] == "met:0":
                raise HttpError("模拟该画作无法下载")
            return self.painting

        self.painting = self.root / "painting.png"
        Image.new("RGB", (160, 120), (200, 100, 30)).save(self.painting)
        with patch.object(self.engine, "get_catalog", return_value=fixture_catalog(works)), \
             patch.object(self.engine, "download", side_effect=download), \
             patch("dop.engine.random.choice", side_effect=lambda pool: pool[0]), \
             patch("dop.wallpaper.get_monitors", return_value=[monitor()]), \
             patch("dop.wallpaper.set_wallpaper"):
            try:
                record = self.engine.apply()
            except RuntimeError as exc:
                self.fail(f"{exc}；实际尝试 {attempts}")
        self.assertEqual(record["key"], "met:1")


class RedownloadTests(IsolatedTest):
    @contextlib.contextmanager
    def redownload_scenario(self):
        current = artwork(1)
        util.atomic_write_json(paths.current_path(), current)
        util.atomic_write_json(paths.history_path(), [{**current, "ts": time.time()}])
        painting = self.root / "painting.png"
        Image.new("RGB", (160, 120), (200, 100, 30)).save(painting)
        with patch.object(self.engine, "get_catalog", return_value=fixture_catalog([current, artwork(2)])), \
             patch.object(self.engine, "download", return_value=painting), \
             patch("dop.wallpaper.get_monitors", return_value=[monitor()]), \
             patch("dop.wallpaper.set_wallpaper"):
            yield current

    def test_tray_redownload_keeps_current_artwork(self):
        app = tray.TrayApp(engine=self.engine, quiet=True)
        with self.redownload_scenario() as current:
            app._do_change(True)
        self.assertEqual(self.engine.current()["key"], current["key"],
                         "托盘重新下载实际把当前 met:1 换成了 met:2")

    def test_cli_redownload_keeps_current_artwork(self):
        args = argparse.Namespace(verbose=False, redownload=True, rebuild_catalog=False, quiet=True)
        with patch.object(cli, "_setup", return_value=self.engine), \
             self.redownload_scenario() as current:
            cli.cmd_change(args)
        self.assertEqual(self.engine.current()["key"], current["key"],
                         "命令行重新下载实际把当前 met:1 换成了 met:2")


class ScheduleTests(IsolatedTest):
    def tick(self, now, current_ts, paused=False, catch_up=True):
        self.cfg["schedule"].update(time="08:30", paused=paused, catch_up=catch_up)
        util.atomic_write_json(paths.current_path(), {"key": "met:1", "ts": current_ts})
        app = tray.TrayApp(engine=self.engine, quiet=True)
        with patch.object(tray, "load_config", return_value=self.cfg), \
             patch.object(tray, "datetime") as clock, \
             patch.object(app, "_guard_wallpaper") as guard, \
             patch.object(app, "action_change") as change:
            clock.now.return_value = now
            app._tick()
        return change.call_count, guard.call_count

    def test_paused_neither_changes_nor_restores_wallpaper(self):
        self.assertEqual(self.tick(datetime(2026, 11, 11, 9), 0, paused=True), (0, 0))

    def test_due_change_runs_once(self):
        now = datetime(2026, 11, 11, 9)
        self.assertEqual(self.tick(now, 0)[0], 1)
        self.assertEqual(self.tick(now, now.timestamp())[0], 0)

    def test_before_schedule_with_catchup_disabled_does_not_change(self):
        self.assertEqual(self.tick(datetime(2026, 11, 11, 7), 0, catch_up=False)[0], 0)

    def test_before_schedule_catches_up_missed_yesterday(self):
        self.assertEqual(self.tick(datetime(2026, 11, 11, 7), 0)[0], 1)


class RenderTests(IsolatedTest):
    def test_negative_and_vertical_monitor_coordinates_split_correctly(self):
        screens = [monitor(), monitor(-160, -100, 160, 100, False)]
        canvas = Image.new("RGB", (480, 280), "blue")
        canvas.paste("red", (0, 0, 160, 100))
        slices = render.split_by_monitor(canvas, screens)
        self.assertEqual(slices[0][1].size, (320, 180))
        self.assertEqual(slices[0][1].getpixel((0, 0)), (0, 0, 255))
        self.assertEqual(slices[1][1].size, (160, 100))
        self.assertEqual(slices[1][1].getpixel((0, 0)), (255, 0, 0))

    def test_all_render_styles_produce_correct_dimensions(self):
        source = self.root / "painting.png"
        Image.new("RGB", (100, 200), (200, 100, 30)).save(source)
        for style in ("fit", "fill", "center", "auto"):
            with self.subTest(style=style):
                self.cfg["image"]["style"] = style
                image = render.compose(source, [monitor()], self.cfg, artwork(1))
                self.assertEqual(image.size, (320, 180))

    def test_guard_detects_wallpapers_swapped_between_monitors(self):
        screens = [monitor(), monitor(320, 0, 160, 240, False)]
        self.cfg["image"].update(multi_monitor="span", style="fill")
        painting = self.root / "painting.png"
        image = Image.new("RGB", (480, 240), "red")
        image.paste("blue", (320, 0, 480, 240))
        image.save(painting)
        with patch.object(self.engine, "download", return_value=painting), \
             patch("dop.wallpaper.get_monitors", return_value=screens), \
             patch("dop.wallpaper.set_wallpapers_per_monitor", return_value=True), \
             patch("dop.wallpaper.set_wallpaper"):
            record = self.engine.apply(work=artwork(1))
        first, second = record["wallpaper_files"]
        normal = [
            {"device": "screen-0", "rect": (0, 0, 320, 180), "wallpaper": first},
            {"device": "screen-1", "rect": (320, 0, 480, 240), "wallpaper": second},
        ]
        swapped = [{**normal[0], "wallpaper": second}, {**normal[1], "wallpaper": first}]
        with patch("dop.wallpaper.describe_wallpapers", return_value=normal):
            self.assertTrue(self.engine.wallpaper_matches())
        with patch("dop.wallpaper.describe_wallpapers", return_value=swapped):
            self.assertFalse(self.engine.wallpaper_matches(), "逐屏文件被对调，守护仍判断匹配")


class HttpTests(IsolatedTest):
    def test_download_size_limit_keeps_existing_file_and_removes_partial(self):
        client = HttpClient(retries=1)
        target = self.root / "download.jpg"
        target.write_bytes(b"old-file")
        response = io.BytesIO(b"x" * 2048)
        response.headers = {"Content-Length": "2048"}
        with patch.object(client, "_open", return_value=response):
            with self.assertRaises(HttpError):
                client.download("https://example.invalid/a.jpg", target, max_bytes=1024)
        self.assertEqual(target.read_bytes(), b"old-file")
        self.assertFalse(target.with_suffix(".jpg.part").exists())

    def test_truncated_download_is_not_accepted_as_complete(self):
        class TruncatedResponse(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", "4096")
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(b"x" * 2048)
                self.close_connection = True

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), TruncatedResponse)
        worker = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        worker.start()
        try:
            client = HttpClient(retries=1, timeout=2)
            url = f"http://127.0.0.1:{server.server_port}/a.jpg"
            with self.assertRaises(HttpError, msg="声明 4096 字节、实际 2048 字节却被当成成功"):
                client.download(url, self.root / "download.jpg")
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)


class SecretTests(IsolatedTest):
    def test_exception_traceback_is_also_redacted(self):
        # 只使用专门构造的测试字符串，不加载真实凭据。
        fake_secret = "AUDIT_FAKE_SECRET_123456"
        log_path = self.root / "audit.log"
        logger = logging.getLogger("DailyOilPaintingAudit")
        with patch.object(util, "APP_NAME", "DailyOilPaintingAudit"), \
             patch.object(util, "_configured", False), patch.object(util, "_secret_filter", None):
            util.setup_logging(log_path, console=False, secrets=[fake_secret])
            try:
                raise RuntimeError(f"https://example.invalid/?apikey={fake_secret}")
            except RuntimeError:
                util.get_logger("worker").exception("模拟下载异常")
            finally:
                for handler in list(logger.handlers):
                    handler.close()
                    logger.removeHandler(handler)
                logger.filters.clear()
        self.assertNotIn(fake_secret, log_path.read_text(encoding="utf-8"),
                         "真实 setup_logging 的异常堆栈仍含未脱敏的测试密钥")

    def test_secret_config_set_does_not_echo_plaintext(self):
        fake_secret = "AUDIT_FAKE_SECRET_123456"
        args = argparse.Namespace(action="set", key="sources.harvard.api_key", value=fake_secret)
        output = io.StringIO()
        with patch.object(cli, "load_config", return_value=copy.deepcopy(DEFAULT_CONFIG)), \
             patch.object(cli, "save_config"), contextlib.redirect_stdout(output):
            cli.cmd_config(args)
        self.assertNotIn(fake_secret, output.getvalue(), "config set 将测试密钥原样打印")


class MediaTests(unittest.TestCase):
    def test_painting_medium_filter(self):
        for medium in ("Oil on canvas", "Tempera on wood", "Fresco"):
            with self.subTest(medium=medium):
                self.assertTrue(looks_like_painting(medium=medium))
        for medium in ("Bronze", "Ink on paper", "Marble", "Oil on paper"):
            with self.subTest(medium=medium):
                self.assertFalse(looks_like_painting(medium=medium))

    def test_date_boundaries_and_reversed_years(self):
        self.assertTrue(in_date_range(1300, 1800, 1300, 1800))
        self.assertTrue(in_date_range(1700, 1600, 1300, 1800))
        self.assertFalse(in_date_range(1299, 1400, 1300, 1800))
        self.assertFalse(in_date_range(1700, 1801, 1300, 1800))
        self.assertFalse(in_date_range(None, 1600, 1300, 1800))


if __name__ == "__main__":
    unittest.main(verbosity=2)
