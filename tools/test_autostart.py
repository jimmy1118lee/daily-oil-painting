"""看门狗直接启动回归：不注册真实任务、不修改注册表。"""
from __future__ import annotations

import base64
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from dop import autostart


class ScheduledActionTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="dop-task-action-")
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        env = patch.dict(os.environ, {"DOP_DATA_DIR": folder.name})
        env.start()
        self.addCleanup(env.stop)
        for target in ("_set_run_value", "_ensure_startup_approved_enabled"):
            hook = patch.object(autostart, target)
            hook.start()
            self.addCleanup(hook.stop)

    @staticmethod
    def scripts(run):
        result = []
        for call in run.call_args_list:
            argv = call.args[0]
            if "-EncodedCommand" in argv:
                result.append(base64.b64decode(argv[argv.index("-EncodedCommand")+1]).decode("utf-16le"))
            else:
                result.append(" ".join(argv))
        return result

    def test_enable_uses_direct_windowless_action_without_batch_wrapper(self):
        argv = [r"C:\Program Files\Python\pythonw.exe", r"C:\Daily Painting\main.py", "watchdog"]
        with patch("dop.paths.app_argv", return_value=argv), \
             patch.object(autostart, "_run", return_value=(0, "")) as run:
            self.assertTrue(autostart.enable())
        self.assertFalse((self.folder / "watchdog.bat").exists(), "不应再生成会引起控制台闪窗的批处理入口")
        self.assertEqual(run.call_count, 2)
        for script in self.scripts(run):
            self.assertNotIn(".bat", script.lower())
            self.assertIn("pythonw.exe", script)
            self.assertIn("New-ScheduledTaskAction", script)
            self.assertIn("Set-ScheduledTask", script)

    def test_unicode_spaces_and_apostrophes_are_preserved_in_action(self):
        argv = [r"C:\Program Files\Python\pythonw.exe", r"D:\艺术家's 收藏\main.py", "watchdog"]
        with patch("dop.paths.app_argv", return_value=argv), \
             patch.object(autostart, "_run", return_value=(0, "")) as run:
            self.assertTrue(autostart.enable())
        for script in self.scripts(run):
            self.assertIn('"D:\\艺术家\'\'s 收藏\\main.py" watchdog', script)
            self.assertIn("-WorkingDirectory", script)

    def test_packaged_application_calls_executable_directly(self):
        argv = [r"D:\每日油画\DailyOilPainting.exe", "watchdog"]
        with patch("dop.paths.app_argv", return_value=argv), \
             patch.object(autostart, "_run", return_value=(0, "")) as run:
            self.assertTrue(autostart.enable())
        for script in self.scripts(run):
            self.assertIn(argv[0], script)
            self.assertNotIn("main.py", script)
            self.assertNotIn(".bat", script.lower())

    def test_task_failure_is_not_reported_as_success(self):
        with patch.object(autostart, "_run", side_effect=[(1, "模拟注册失败"), (0, "")]) as run, \
             self.assertLogs("DailyOilPainting.autostart", level="WARNING"):
            self.assertFalse(autostart.enable())
        self.assertEqual(run.call_count, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
