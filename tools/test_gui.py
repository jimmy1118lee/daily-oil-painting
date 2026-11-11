"""深色观画界面功能回归：隔离配置、真实 Tk 控件、拦截系统写入。"""
from __future__ import annotations

import copy
import gc
import os
from pathlib import Path
import sys
import tempfile
import time
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PIL import Image
from dop import paths
from dop.config import DEFAULT_CONFIG, load_config, save_config
from dop.gui import ControlPanel
from dop.util import atomic_write_json


class GalleryTests(unittest.TestCase):
    def setUp(self):
        # 旧 Tk 对象只能在主线程释放，避免后台渲染触发跨用例的循环垃圾回收。
        gc.collect()
        folder = tempfile.TemporaryDirectory(prefix="dop-gallery-")
        self.addCleanup(folder.cleanup)
        self.directory = Path(folder.name)
        env = patch.dict(os.environ, {"DOP_DATA_DIR": folder.name, "DOP_HARVARD_API_KEY": "",
                                     "HARVARD_API_KEY": "", "HARVARD_APIKEY": ""})
        env.start()
        self.addCleanup(env.stop)
        paths.ensure_dirs()
        save_config(copy.deepcopy(DEFAULT_CONFIG))
        self.painting = self.directory / "painting.jpg"
        Image.new("RGB", (240, 160), (100, 140, 90)).save(self.painting)
        self.records = [dict(key=f"met:{i}", title=f"测试画作 {i}", artist="测试作者",
                             source="met", source_id=str(i), image_url=f"https://example.invalid/{i}.jpg",
                             source_file=str(self.painting), wallpaper_file=str(self.painting),
                             time_display="2026-11-11 08:30:00", date_display="1650", museum="测试博物馆")
                        for i in (1, 2)]
        atomic_write_json(paths.current_path(), self.records[1])
        atomic_write_json(paths.history_path(), self.records)
        for target, kwargs in (
            ("dop.autostart.is_enabled", {"return_value": False}),
            ("dop.tray.tray_is_running", {"return_value": True}),
            ("dop.wallpaper.get_monitors", {"return_value": [dict(width=3840, height=2160)]}),
        ):
            hook = patch(target, **kwargs)
            hook.start()
            self.addCleanup(hook.stop)
        self.root = tk.Tk()
        self.root.withdraw()
        self.panel = ControlPanel(self.root)
        self.addCleanup(self.close_panel)
        self.root.update()

    def close_panel(self):
        self.root.destroy()
        self.panel = None
        self.root = None
        gc.collect()

    def pump(self, until=lambda: True):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            self.root.update()
            if until():
                return
            time.sleep(.01)
        self.fail("后台界面任务未按时完成")

    def test_opening_and_browsing_preserve_current_wallpaper_and_configuration(self):
        original_config = paths.config_path().read_bytes()
        original_current = paths.current_path().read_bytes()
        with patch.object(self.panel.engine, "apply") as apply:
            self.panel.show_view("history")
            self.panel.ui.tree.selection_set("1")
            self.panel.on_collection_selected()
            self.panel.show_view("favorites")
            self.panel.show_view("today")
            self.panel.open_settings()
            self.root.update()
        apply.assert_not_called()
        self.assertEqual(paths.config_path().read_bytes(), original_config)
        self.assertEqual(paths.current_path().read_bytes(), original_current)

    def test_empty_favorites_show_guidance_and_disable_apply(self):
        self.panel.show_view("favorites")
        self.assertIn("还没有收藏", self.panel.ui.collection_title.cget("text"))
        self.assertEqual(self.panel.ui.apply_button.cget("state"), "disabled")

    def test_favorites_read_real_index_and_collapse_duplicate_records(self):
        atomic_write_json(paths.favorites_index_path(), [self.records[0], self.records[0]])
        self.panel.show_view("favorites")
        self.assertEqual(len(self.panel.ui.tree.get_children()), 1)
        self.assertEqual(self.panel._selected_record["key"], "met:1")

    def test_history_selection_only_previews(self):
        with patch.object(self.panel.engine, "apply") as apply:
            self.panel.show_view("history")
            self.panel.ui.tree.selection_set("1")
            self.panel.on_collection_selected()
        self.assertEqual(self.panel._selected_record["key"], "met:1")
        self.assertEqual(self.panel.engine.current()["key"], "met:2")
        apply.assert_not_called()

    def test_explicit_apply_uses_selected_work_and_returns_to_today(self):
        self.panel.show_view("history")
        self.panel.ui.tree.selection_set("1")
        self.panel.on_collection_selected()
        with patch.object(self.panel.engine, "apply") as apply:
            self.panel.ui.apply_button.invoke()
            self.pump(lambda: not self.panel.busy)
        self.assertEqual(apply.call_args.kwargs["work"]["key"], "met:1")
        self.assertEqual(self.panel.view_name, "today")

    def test_settings_window_is_reused_and_opening_does_not_save(self):
        with patch("dop.gui.save_config") as save:
            self.panel.open_settings()
            first = self.panel._settings.window
            self.panel.open_settings()
        self.assertIs(self.panel._settings.window, first)
        save.assert_not_called()

    def test_pause_updates_schedule_without_changing_wallpaper(self):
        original = load_config()
        with patch.object(self.panel.engine, "apply") as apply:
            self.panel.ui.pause_button.invoke()
        cfg = load_config()
        self.assertTrue(cfg["schedule"]["paused"])
        self.assertEqual(cfg["image"], original["image"])
        self.assertEqual(self.panel.ui.pause_button.cget("text"), "恢复")
        apply.assert_not_called()

    def test_settings_switch_saves_only_when_user_invokes_it(self):
        self.panel.open_settings()
        before = paths.config_path().read_bytes()
        self.panel.refresh_settings()
        self.assertEqual(paths.config_path().read_bytes(), before)
        self.panel._settings.auto_switch.invoke()
        self.assertTrue(load_config()["schedule"]["paused"])

    def test_time_and_landscape_settings_persist(self):
        self.panel.var_time.set("12:00")
        self.panel.on_time_changed()
        self.panel.var_landscape.set(False)
        self.panel.on_landscape_changed()
        cfg = load_config()
        self.assertEqual(cfg["schedule"]["time"], "12:00")
        self.assertFalse(cfg["selection"]["landscape_only"])
        self.assertFalse(cfg["schedule"]["paused"])

    def test_image_settings_persist_without_applying_wallpaper(self):
        with patch.object(self.panel.engine, "apply") as apply:
            self.panel.var_multi.set("跨屏展开")
            self.panel.var_style.set("铺满屏幕")
            self.panel.on_image_changed()
            self.panel.var_caption.set(False)
            self.panel.on_caption_changed()
        cfg = load_config()
        self.assertEqual(cfg["image"]["multi_monitor"], "span")
        self.assertEqual(cfg["image"]["style"], "fill")
        self.assertFalse(cfg["image"]["show_caption"])
        apply.assert_not_called()

    def test_repeated_change_clicks_do_not_launch_overlapping_tasks(self):
        import threading
        release = threading.Event()
        self.addCleanup(release.set)
        with patch.object(self.panel.engine, "apply", side_effect=lambda **kw: release.wait(1)) as apply:
            self.panel.on_change()
            self.panel.on_change()
            release.set()
            self.pump(lambda: not self.panel.busy)
        apply.assert_called_once()
        self.assertEqual(self.panel.ui.change_button.cget("state"), "normal")

    def test_failed_task_restores_controls(self):
        with patch.object(self.panel.engine, "apply", side_effect=RuntimeError("模拟网络错误")), \
             patch("dop.gui.messagebox.showerror") as error, \
             self.assertLogs("DailyOilPainting.gui", level="ERROR"):
            self.panel.on_change()
            self.pump(lambda: not self.panel.busy)
        error.assert_called_once()
        self.assertEqual(self.panel.ui.change_button.cget("state"), "normal")

    def test_external_tray_changes_refresh_current_work(self):
        replacement = {**self.records[0], "title": "后台换入的新画作"}
        atomic_write_json(paths.current_path(), replacement)
        self.root.after_cancel(self.panel._poll_after)
        self.panel._poll_state()
        self.assertEqual(self.panel.ui.title_label.cget("text"), "后台换入的新画作")

    def test_preview_resizes_without_distorting_artwork(self):
        preview = self.panel.ui.preview
        with patch.object(preview, "winfo_width", return_value=600), \
             patch.object(preview, "winfo_height", return_value=200):
            preview.draw()
        self.assertIsNotNone(preview._photo)
        self.assertAlmostEqual(preview._photo.width() / preview._photo.height(), 1.5, delta=.02)

    def test_missing_images_do_not_break_history_preview(self):
        self.painting.unlink()
        self.panel.show_view("history")
        self.root.update()
        self.assertIsNone(self.panel.ui.collection_preview._source)
        self.assertIn("测试画作", self.panel.ui.collection_title.cget("text"))

    def test_background_startup_query_does_not_flash_console(self):
        import subprocess
        from dop import autostart
        result = Mock(returncode=0, stdout="", stderr="")
        with patch("dop.autostart.subprocess.run", return_value=result) as run:
            autostart._run(["schtasks", "/query"])
        self.assertTrue(run.call_args.kwargs.get("creationflags", 0) & subprocess.CREATE_NO_WINDOW)

    def test_favorite_portrait_applies_offline_from_saved_original(self):
        favorite = paths.favorites_dir() / "20261020_flowers.jpg"
        original = favorite.with_name(favorite.stem + "_原图.jpg")
        Image.new("RGB", (320, 180), "green").save(favorite)
        Image.new("RGB", (80, 160), "red").save(original)
        record = {**self.records[0], "favorite_file": str(favorite),
                  "source_file": str(self.directory / "evicted.jpg")}
        atomic_write_json(paths.favorites_index_path(), [record])
        # 新作品目录没有本地收藏字段，验证 GUI 不会在补齐元数据时丢掉它们。
        canonical = {key: value for key, value in record.items()
                     if key not in ("favorite_file", "source_file", "wallpaper_file")}
        atomic_write_json(paths.catalog_path(), {"works": [canonical]})
        screen = dict(left=0, top=0, right=320, bottom=180, width=320, height=180, primary=True)
        with patch.object(self.panel.engine.http, "download", side_effect=RuntimeError("模拟网络断开")) as download, \
             patch("dop.wallpaper.get_monitors", return_value=[screen]), \
             patch("dop.wallpaper.set_wallpaper") as setter, \
             patch("dop.gui.messagebox.showerror") as error:
            self.panel.show_view("favorites")
            self.panel.ui.apply_button.invoke()
            self.pump(lambda: not self.panel.busy)
        error.assert_not_called()
        download.assert_not_called()
        setter.assert_called_once()
        self.assertEqual(self.panel.engine.current()["source_file"], str(original))
        self.assertTrue(load_config()["selection"]["landscape_only"])

    def test_favorite_can_use_saved_wallpaper_when_original_is_missing(self):
        favorite = paths.favorites_dir() / "only-wallpaper.jpg"
        Image.new("RGB", (320, 180), "blue").save(favorite)
        record = {**self.records[0], "favorite_file": str(favorite),
                  "source_file": str(self.directory / "missing.jpg")}
        atomic_write_json(paths.favorites_index_path(), [record])
        screen = dict(left=0, top=0, right=320, bottom=180, width=320, height=180, primary=True)
        with patch.object(self.panel.engine.http, "download", side_effect=RuntimeError("模拟断网")) as download, \
             patch("dop.wallpaper.get_monitors", return_value=[screen]), \
             patch("dop.wallpaper.set_wallpaper") as setter, \
             patch("dop.gui.messagebox.showerror") as error:
            self.panel.show_view("favorites")
            self.panel.ui.apply_button.invoke()
            self.pump(lambda: not self.panel.busy)
        error.assert_not_called()
        download.assert_not_called()
        setter.assert_called_once()
        self.assertEqual(self.panel.engine.current()["source_file"], str(favorite))


if __name__ == "__main__":
    unittest.main(verbosity=2)
