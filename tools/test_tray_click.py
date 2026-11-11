"""托盘点击回归：python tools/test_tray_click.py（不启动真实界面或更换壁纸）。"""

from __future__ import annotations

import copy
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dop.config import DEFAULT_CONFIG
from dop.tray import TrayApp


class TrayClickTests(unittest.TestCase):
    def setUp(self):
        self.cfg = copy.deepcopy(DEFAULT_CONFIG)
        self.engine = Mock()
        self.engine.cfg = self.cfg
        self.engine.apply.return_value = {"key": "met:1", "title": "测试画作"}
        self.app = TrayApp(engine=self.engine, quiet=True)

    def test_single_left_click_opens_panel_without_changing_wallpaper(self):
        with patch("subprocess.Popen") as launch, \
             patch.object(self.app, "_run_async", side_effect=lambda fn, *args: fn(*args)):
            # pystray 的 WM_LBUTTONUP 调用图标，再由真实 Menu 执行默认菜单项。
            self.app.build_menu()(object())
        self.engine.apply.assert_not_called()
        launch.assert_called_once()
        self.assertEqual(launch.call_args.args[0][-1], "gui")

    def test_explicit_change_menu_still_changes_wallpaper(self):
        with patch("subprocess.Popen") as launch, \
             patch.object(self.app, "_run_async", side_effect=lambda fn, *args: fn(*args)):
            item = next(item for item in self.app.build_menu().items if item.text == "现在换一张")
            item(object())
        self.engine.apply.assert_called_once()
        launch.assert_not_called()

    def test_panel_launch_does_not_create_console_window(self):
        with patch("subprocess.Popen") as launch, \
             patch.object(self.app, "_run_async", side_effect=lambda fn, *args: fn(*args)):
            self.app.build_menu()(object())
        launch.assert_called_once()
        self.assertTrue(launch.call_args.kwargs["creationflags"] & subprocess.CREATE_NO_WINDOW)

    def test_panel_launch_error_does_not_trigger_wallpaper_change(self):
        with patch("subprocess.Popen", side_effect=OSError("模拟无法启动界面")), \
             self.assertLogs("DailyOilPainting.tray", level="ERROR"):
            self.app.build_menu()(object())
        self.engine.apply.assert_not_called()

    def test_control_panel_startup_keeps_wallpaper_and_stays_running(self):
        import tkinter as tk
        from dop import gui

        with tempfile.TemporaryDirectory(prefix="dop-tray-ui-") as folder, \
             patch.dict(os.environ, {"DOP_DATA_DIR": folder}):
            root = tk.Tk()
            root.withdraw()
            try:
                # 使用真实 Tk 控件与事件循环；窗口隐藏，避免打扰当前桌面。
                with patch.object(gui.tk, "Tk", return_value=root), \
                     patch.object(gui, "setup_logging"), \
                     patch("dop.installer.is_setup_done", return_value=True), \
                     patch("dop.engine.Engine.apply") as apply:
                    alive = []
                    root.after(800, lambda: (alive.append(bool(root.winfo_exists())), root.quit()))
                    self.assertEqual(gui.run_gui(), 0)
                    self.assertEqual(alive, [True])
                    apply.assert_not_called()
            finally:
                root.destroy()


if __name__ == "__main__":
    unittest.main(verbosity=2)
