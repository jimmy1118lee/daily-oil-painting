"""横屏选图回归测试：python tools/test_landscape.py（离线，不改真实桌面）。"""

from __future__ import annotations

import copy
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PIL import Image

from dop import paths
from dop.config import DEFAULT_CONFIG
from dop.engine import MAX_ORIENTATION_CHECKS, Engine, PortraitImageError, _image_cache_path
from dop.util import atomic_write_json, read_json


def work(number):
    return dict(key=f"met:{number}", source="met", source_id=str(number),
                image_url=f"https://example.invalid/{number}.jpg", title=f"测试画作 {number}")


class LandscapeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="dop-landscape-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        env = patch.dict(os.environ, {"DOP_DATA_DIR": str(self.root)})
        env.start()
        self.addCleanup(env.stop)
        self.cfg = copy.deepcopy(DEFAULT_CONFIG)
        self.cfg["selection"]["weighted_by_source"] = False
        self.cfg["image"].update(show_caption=False, background="color")
        self.engine = Engine(self.cfg, http=Mock())
        for target, kwargs in (
            (patch.object(self.engine, "reload_config", return_value=self.cfg), {}),
            (patch("dop.engine.random.choice", side_effect=lambda pool: pool[0]), {}),
            (patch("dop.wallpaper.get_monitors", return_value=[dict(
                left=0, top=0, right=320, bottom=180, width=320, height=180, primary=True)]), {}),
        ):
            target.start(**kwargs)
            self.addCleanup(target.stop)
        setter = patch("dop.wallpaper.set_wallpaper")
        self.setter = setter.start()
        self.addCleanup(setter.stop)
        multi_setter = patch("dop.wallpaper.set_wallpapers_per_monitor", return_value=True)
        self.multi_setter = multi_setter.start()
        self.addCleanup(multi_setter.stop)
        self.portrait = self.image("portrait.jpg", (80, 160))
        self.landscape = self.image("landscape.jpg", (160, 80))

    def image(self, name, size, orientation=None):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        image = Image.new("RGB", size, (180, 120, 70))
        exif = Image.Exif()
        if orientation is not None:
            exif[274] = orientation
        image.save(path, exif=exif)
        return path

    def run_apply(self, works, download):
        with patch.object(self.engine, "get_catalog", return_value={"works": works}), \
             patch.object(self.engine, "download", side_effect=download):
            return self.engine.apply()

    def test_default_excludes_portraits_for_existing_config(self):
        self.cfg["selection"].pop("landscape_only")
        self.assertTrue(self.engine._landscape_only())
        with self.assertRaises(PortraitImageError):
            self.engine._check_orientation(work(1), self.portrait)

    def test_cached_portrait_is_excluded_before_download(self):
        cached = _image_cache_path(work(1))
        cached.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (80, 160)).save(cached)
        chosen = self.engine.choose({"works": [work(1), work(2)]})
        self.assertEqual(chosen["key"], "met:2")

    def test_new_portraits_do_not_consume_download_failure_budget(self):
        self.cfg["advanced"]["max_download_attempts"] = 1
        works = [work(i) for i in range(9)]
        downloads = []

        def download(candidate, **kwargs):
            downloads.append(candidate["key"])
            return self.landscape if candidate["key"] == "met:8" else self.portrait

        with patch.object(self.engine, "record_source_result") as health:
            record = self.run_apply(works, download)
        self.assertEqual(record["key"], "met:8")
        self.assertEqual(len(downloads), 9)
        health.assert_called_once_with("met", True)
        self.assertEqual(len(self.engine.get_history()), 1)
        self.setter.assert_called_once()

    def test_all_portraits_leave_current_wallpaper_and_history_untouched(self):
        original = {"key": "met:previous", "wallpaper_file": "existing-wallpaper.jpg"}
        atomic_write_json(paths.current_path(), original)
        atomic_write_json(paths.history_path(), [original])
        with self.assertRaisesRegex(PortraitImageError, "保留当前壁纸"):
            self.run_apply([work(1), work(2)], lambda *args, **kwargs: self.portrait)
        self.assertEqual(self.engine.current(), original)
        self.assertEqual(self.engine.get_history(), [original])
        self.setter.assert_not_called()
        self.multi_setter.assert_not_called()
        self.assertEqual(list(paths.wallpaper_dir().iterdir()), [])

    def test_explicit_portrait_is_allowed_without_disabling_automatic_filter(self):
        with patch.object(self.engine, "download", return_value=self.portrait):
            record = self.engine.apply(work=work(1))
        self.assertEqual(record["key"], "met:1")
        self.setter.assert_called_once()
        self.assertTrue(self.cfg["selection"]["landscape_only"])

    def test_explicit_landscape_is_applied(self):
        with patch.object(self.engine, "download", return_value=self.landscape):
            record = self.engine.apply(work=work(1))
        self.assertEqual(record["key"], "met:1")
        self.setter.assert_called_once()

    def test_manual_history_work_uses_local_image_offline(self):
        selected = {**work(1), "source_file": str(self.portrait)}
        with patch.object(self.engine, "download") as download:
            record = self.engine.apply(work=selected)
        download.assert_not_called()
        self.assertEqual(record["source_file"], str(self.portrait))
        self.assertTrue(self.cfg["selection"]["landscape_only"])

    def test_favorite_records_saved_original_and_can_be_reused(self):
        with patch.object(self.engine, "download", return_value=self.landscape):
            self.engine.apply(work=work(1))
        self.engine.favorite()
        saved = read_json(paths.favorites_index_path())[-1]
        original = Path(saved["favorite_source_file"])
        self.assertTrue(original.is_file())
        self.assertEqual(original.read_bytes(), self.landscape.read_bytes())
        self.engine.apply(work=saved)
        self.assertEqual(self.engine.current()["source_file"], str(original))
        # 当天再次收藏已保存的原图，不应发生文件复制到自身的异常。
        self.assertIsNotNone(self.engine.favorite())

    def test_square_is_not_misclassified_as_portrait(self):
        self.engine._check_orientation(work(1), self.image("square.jpg", (100, 100)))

    def test_exif_rotated_to_portrait_is_excluded(self):
        for orientation in (5, 6, 7, 8):
            with self.subTest(orientation=orientation):
                image = self.image(f"rotated-{orientation}.jpg", (160, 80), orientation)
                with self.assertRaises(PortraitImageError):
                    self.engine._check_orientation(work(orientation), image)

    def test_exif_rotated_to_landscape_is_allowed(self):
        image = self.image("rotated-landscape.jpg", (80, 160), 6)
        self.engine._check_orientation(work(1), image)

    def test_remembered_portrait_is_skipped_after_engine_restart(self):
        with self.assertRaises(PortraitImageError):
            self.engine._check_orientation(work(1), self.portrait)
        fresh = Engine(self.cfg, http=Mock())
        self.assertEqual(fresh.choose({"works": [work(1), work(2)]})["key"], "met:2")

    def test_changed_image_url_does_not_reuse_old_orientation(self):
        with self.assertRaises(PortraitImageError):
            self.engine._check_orientation(work(1), self.portrait)
        changed = {**work(1), "image_url": "https://example.invalid/replacement.jpg"}
        self.assertFalse(self.engine._known_portrait(changed))

    def test_orientation_check_count_is_bounded(self):
        calls = []

        def download(candidate, **kwargs):
            calls.append(candidate["key"])
            return self.portrait

        with self.assertRaises(PortraitImageError):
            self.run_apply([work(i) for i in range(MAX_ORIENTATION_CHECKS + 1)], download)
        self.assertEqual(len(calls), MAX_ORIENTATION_CHECKS)
        self.setter.assert_not_called()

    def test_no_portraits_are_selected_when_entire_cache_is_portrait(self):
        with self.assertRaises(PortraitImageError):
            self.engine._check_orientation(work(1), self.portrait)
        with self.assertRaises(PortraitImageError):
            self.engine.choose({"works": [work(1)]})

    def test_setting_can_restore_original_selection_behavior(self):
        self.cfg["selection"]["landscape_only"] = False
        record = self.run_apply([work(1)], lambda *args, **kwargs: self.portrait)
        self.assertEqual(record["key"], "met:1")


if __name__ == "__main__":
    unittest.main(verbosity=2)
