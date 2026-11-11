"""系统托盘程序：右键菜单控制、后台每日调度。

依赖 pystray（仅托盘需要，命令行功能不依赖它）。
"""

from __future__ import annotations

import ctypes
import os
import threading
import time
import webbrowser
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from . import icon as icon_mod
from . import paths, report, wallpaper
from .config import load_config, save_config
from .engine import Engine
from .util import ensure_std_streams, get_logger, parse_hhmm, setup_logging

TIME_PRESETS = ("06:30", "07:30", "08:30", "12:00", "18:00", "21:00", "23:00")
TICK_SECONDS = 30


MUTEX_NAME = "Local\\DailyOilPainting.Tray.Singleton"


def acquire_single_instance(name: str = MUTEX_NAME):
    """用命名互斥体保证只有一个托盘实例（自启动重复触发时不会开两个）。"""
    if not wallpaper.IS_WINDOWS:
        return True
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.CreateMutexW(None, False, name)
    if not handle:
        return True
    ERROR_ALREADY_EXISTS = 183
    if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        return False
    globals()["_MUTEX_HANDLE"] = handle  # 保持引用，防止被回收
    return True


def tray_is_running(name: str = MUTEX_NAME) -> bool:
    """探测托盘程序是否已经在运行（创建后立刻关闭，不会占用互斥体）。"""
    if not wallpaper.IS_WINDOWS:
        return False
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.CreateMutexW(None, False, name)
    if not handle:
        return False
    try:
        return kernel32.GetLastError() == 183  # ERROR_ALREADY_EXISTS
    finally:
        kernel32.CloseHandle(handle)


def start_tray_if_needed() -> bool:
    """托盘不在就把它拉起来（供 watchdog 与界面共用）。已在运行则返回 False。"""
    if tray_is_running():
        return False
    import subprocess

    get_logger("tray").info("托盘未在运行，正在启动")
    subprocess.Popen(
        paths.app_argv("tray", "--quiet"),
        cwd=str(paths.app_root()),
        creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        close_fds=True,
    )
    return True


class TrayApp:
    def __init__(self, engine: Optional[Engine] = None, quiet: bool = False) -> None:
        self.log = get_logger("tray")
        self.engine = engine or Engine()
        self.cfg = self.engine.cfg
        self.quiet = quiet
        self.icon = None
        self._stop = threading.Event()
        self._busy = threading.Lock()
        self._icon_ready = threading.Event()
        self._last_guard = 0.0
        self._last_error: Optional[str] = None

    # ------------------------------------------------------------------ 图标
    def _tray_image(self):
        current = self.engine.current()
        source = None
        if current:
            candidate = Path(str(current.get("wallpaper_file") or ""))
            if candidate.exists():
                source = candidate
        return icon_mod.tray_image(source)

    def _tooltip(self) -> str:
        current = self.engine.current()
        if not current:
            return "每日欧洲油画壁纸 · 还没有换过"
        text = f"《{current.get('title')}》"
        if current.get("artist"):
            text += f" — {current['artist']}"
        if current.get("date_display"):
            text += f" {current['date_display']}"
        paused = "（已暂停）" if (self.cfg.get("schedule") or {}).get("paused") else ""
        tooltip = f"{text}{paused}"
        return tooltip[:120]

    def _refresh_icon(self) -> None:
        if not self.icon:
            return
        try:
            self.icon.icon = self._tray_image()
            self.icon.title = self._tooltip()
        except Exception as exc:  # pragma: no cover
            self.log.debug("刷新托盘图标失败：%s", exc)

    def _notify(self, message: str, title: str = "每日名画") -> None:
        if self.quiet or not self.icon:
            return
        try:
            self.icon.notify(message, title)
        except Exception as exc:  # pragma: no cover
            self.log.debug("系统通知失败：%s", exc)

    # ------------------------------------------------------------------ 动作
    def action_open_panel(self, *_args) -> None:
        """单击托盘只打开控制面板；Tk 必须在独立进程的主线程运行。"""
        import subprocess

        try:
            subprocess.Popen(
                paths.app_argv("gui"),
                cwd=str(paths.app_root()),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                close_fds=True,
            )
        except OSError as exc:
            self.log.exception("打开控制面板失败：%s", exc)
            self._notify("控制面板打开失败，请查看日志", "每日名画")

    def _run_async(self, func, *args, **kwargs) -> None:
        if not self._busy.acquire(blocking=False):
            self.log.info("上一个任务还在进行，忽略本次操作")
            return

        def _wrapper():
            try:
                func(*args, **kwargs)
            except Exception as exc:
                self._last_error = str(exc)
                self.log.exception("任务执行失败：%s", exc)
                self._notify(f"出错了：{exc}", "每日名画")
            finally:
                self._busy.release()

        threading.Thread(target=_wrapper, daemon=True).start()

    def action_change(self, *_args) -> None:
        self._run_async(self._do_change, False)

    def action_redownload(self, *_args) -> None:
        self._run_async(self._do_change, True)

    def _do_change(self, force_new: bool) -> None:
        work = None
        if not force_new:
            self.engine.reload_config()
        record = self.engine.apply(
            work=None,
            force_new=force_new,
            progress=lambda msg: self.log.debug("进度：%s", msg),
        )
        self.cfg = self.engine.cfg
        self._refresh_icon()
        self._notify(
            f"《{record.get('title')}》\n{record.get('artist') or '佚名'} "
            f"{record.get('date_display') or ''}",
            "已换上今日名画",
        )

    def action_favorite(self, *_args) -> None:
        target = self.engine.favorite()
        if target:
            self._notify(f"已收藏到：\n{target.name}", "收藏成功")
        else:
            self._notify("还没有可收藏的壁纸", "收藏失败")

    def action_details(self, *_args) -> None:
        current = self.engine.current()
        if not current:
            self._notify("还没有换过壁纸", "暂无信息")
            return
        page = report.write_html(current)
        webbrowser.open(page.as_uri())

    def action_open_dir(self, *_args) -> None:
        os.startfile(str(paths.wallpaper_dir()))  # noqa: S606

    def action_open_cache(self, *_args) -> None:
        os.startfile(str(paths.cache_dir()))  # noqa: S606

    def action_open_page(self, *_args) -> None:
        current = self.engine.current()
        url = str((current or {}).get("page_url") or "").strip()
        if url:
            webbrowser.open(url)
        else:
            self._notify("当前作品没有馆藏页面链接", "暂无链接")

    def action_open_log(self, *_args) -> None:
        path = paths.log_path()
        if path.exists():
            os.startfile(str(path))  # noqa: S606

    def action_toggle_pause(self, *_args) -> None:
        cfg = load_config()
        cfg.setdefault("schedule", {})
        cfg["schedule"]["paused"] = not bool(cfg["schedule"].get("paused"))
        save_config(cfg)
        self.cfg = cfg
        self._refresh_icon()
        state = "已暂停自动更换" if cfg["schedule"]["paused"] else "已恢复自动更换"
        self.log.info(state)
        self._notify(state)

    def _make_time_setter(self, value: str):
        def _setter(*_args) -> None:
            cfg = load_config()
            cfg.setdefault("schedule", {})["time"] = value
            save_config(cfg)
            self.cfg = cfg
            self.log.info("每日更换时间改为 %s", value)
            self._notify(f"每天 {value} 自动更换壁纸")

        return _setter

    def _time_checked(self, value: str):
        def _check(_item) -> bool:
            current = str((load_config().get("schedule") or {}).get("time") or "")
            try:
                return parse_hhmm(current) == parse_hhmm(value)
            except Exception:  # pragma: no cover
                return current == value

        return _check

    def _pause_checked(self, _item) -> bool:
        return bool((load_config().get("schedule") or {}).get("paused"))

    def action_toggle_autostart(self, *_args) -> None:
        from . import autostart

        enabled = autostart.is_enabled()
        if enabled:
            autostart.disable()
            self._notify("已关闭开机自动启动")
        else:
            ok = autostart.enable()
            if ok:
                self._notify(
                    "已开启开机自动启动\n（登录时自动运行，并且每 10 分钟检查一次是否还活着）"
                )
            else:
                self._notify("设置失败，请查看日志")
        self._refresh_icon()

    def _autostart_checked(self, _item) -> bool:
        from . import autostart

        return autostart.is_enabled()

    def action_about(self, *_args) -> None:
        from .util import VERSION

        catalog = self.engine.get_catalog()
        text = (
            f"每日欧洲油画壁纸 v{VERSION}\n\n"
            f"每天自动把一幅 1300-1800 年的欧洲油画设为桌面壁纸。\n"
            f"图源：大都会艺术博物馆 / 芝加哥艺术博物馆 /\n"
            f"      克利夫兰艺术博物馆（均为公共领域）\n\n"
            f"当前作品库：{catalog.get('total', 0)} 幅\n"
            f"数据目录：{paths.data_root()}"
        )
        self._notify(text, "关于")

    def action_quit(self, *_args) -> None:
        self._stop.set()
        if self.icon:
            self.icon.stop()

    # ------------------------------------------------------------------ 调度
    def _guard_wallpaper(self) -> None:
        """每隔几分钟确认桌面壁纸还是我们设的那张。

        踩过的坑：桌面壁纸会被系统重置或被别的软件改掉（用户机器上就发生过，
        被还原成 Windows 默认壁纸），当时程序又没在运行，于是再没人管。
        只要托盘还活着，这里几分钟内就会把壁纸设回去。
        暂停自动更换时不干预，方便用户换回自己的壁纸。
        """
        interval = int((self.cfg.get("advanced") or {}).get("wallpaper_guard_seconds", 300) or 0)
        if interval <= 0:
            return
        if time.time() - self._last_guard < interval:
            return
        self._last_guard = time.time()
        try:
            if self.engine.wallpaper_matches():
                return
            if self.engine.restore_wallpaper():
                self.log.warning("检测到桌面壁纸被改动，已按当前作品设回去")
                self._refresh_icon()
            else:
                self.log.warning("检测到桌面壁纸被改动，但无法恢复（壁纸文件可能已被删）")
        except Exception as exc:  # noqa: BLE001
            self.log.debug("壁纸守护检查失败：%s", exc)

    def _tick(self) -> None:
        cfg = load_config()
        self.cfg = cfg
        schedule = cfg.get("schedule") or {}
        if schedule.get("paused"):
            return
        if not self._busy.acquire(blocking=False):
            return
        self._busy.release()

        self._guard_wallpaper()

        now = datetime.now()
        hour, minute = parse_hhmm(str(schedule.get("time") or "08:30"))
        today_target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        catch_up = bool(schedule.get("catch_up", True))
        if now >= today_target:
            target = today_target
        elif catch_up:
            target = today_target - timedelta(days=1)
        else:
            return

        current = self.engine.current()
        last_ts = float((current or {}).get("ts") or 0)
        if last_ts >= target.timestamp():
            return

        last_display = (current or {}).get("time_display") or "从未"
        self.log.info("到了更换时间（上次 %s，目标 %s），开始更换", last_display, target)
        self.action_change()

    def _scheduler_loop(self) -> None:
        # 启动后先等 8 秒再检查一次（错过时间就立刻补换）
        if self._stop.wait(8):
            return
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as exc:  # pragma: no cover
                self.log.exception("调度循环出错：%s", exc)
            self._stop.wait(TICK_SECONDS)

    # ------------------------------------------------------------------ 运行
    def build_menu(self):
        import pystray
        from pystray import Menu, MenuItem

        time_items = [
            MenuItem(
                f"每天 {value}",
                self._make_time_setter(value),
                checked=self._time_checked(value),
                radio=True,
            )
            for value in TIME_PRESETS
        ]
        return Menu(
            MenuItem("打开控制面板", self.action_open_panel, default=True),
            MenuItem("现在换一张", self.action_change),
            MenuItem("重新下载当前画作", self.action_redownload),
            MenuItem("收藏当前画作", self.action_favorite),
            MenuItem("查看画作详情", self.action_details),
            MenuItem("打开馆藏页面", self.action_open_page),
            Menu.SEPARATOR,
            MenuItem("打开壁纸文件夹", self.action_open_dir),
            MenuItem("打开图片缓存", self.action_open_cache),
            MenuItem("打开日志", self.action_open_log),
            Menu.SEPARATOR,
            MenuItem("暂停自动更换", self.action_toggle_pause, checked=self._pause_checked),
            MenuItem("每日更换时间", Menu(*time_items)),
            Menu.SEPARATOR,
            MenuItem("开机自动启动", self.action_toggle_autostart, checked=self._autostart_checked),
            MenuItem("关于", self.action_about),
            Menu.SEPARATOR,
            MenuItem("退出", self.action_quit),
        )

    def run(self) -> int:
        try:
            import pystray  # noqa: F401
        except ImportError:
            self.log.error("缺少 pystray，托盘无法启动。请运行：python -m pip install pystray")
            print("缺少 pystray，请先运行：python -m pip install pystray")
            return 2

        paths.ensure_dirs()
        if not acquire_single_instance():
            self.log.info("已经有一个托盘实例在运行，本次退出")
            return 0

        # 关键设计：每日调度与托盘图标解耦。
        # 托盘图标依赖 pystray 与若干 Win32 调用，在受限环境（沙箱、
        # 某些安全策略）下可能创建失败；但"每天自动换壁纸"不该因此停摆。
        # 所以调度线程先起来，图标只是"能加就加"，加不上就无图标继续后台运行。
        scheduler = threading.Thread(target=self._scheduler_loop, name="dop-scheduler", daemon=True)
        scheduler.start()

        if self._start_icon():
            self.log.info("托盘程序已启动（数据目录：%s）", paths.data_root())
        else:
            self.log.warning(
                "托盘图标创建失败，改为无图标后台运行；每天自动更换壁纸照常进行，"
                "需要操作时用桌面快捷方式打开控制面板。"
            )

        while not self._stop.is_set():
            self._stop.wait(0.5)
        return 0

    def _start_icon(self) -> bool:
        """尝试创建托盘图标；失败返回 False，调用方继续无图标运行。"""
        try:
            import pystray
        except ImportError:
            return False
        try:
            self.icon = pystray.Icon(
                "DailyOilPainting",
                icon=self._tray_image(),
                title=self._tooltip(),
                menu=self.build_menu(),
            )
            self.icon.run_detached(setup=self._on_ready)
        except Exception as exc:  # noqa: BLE001
            self.log.error("托盘图标创建失败：%s", exc)
            return False
        if not self._icon_ready.wait(20):
            self.log.error("托盘图标 20 秒内未就绪，按无图标模式继续")
            return False
        return True

    def _on_ready(self, icon) -> None:
        """图标已经出现在通知区域之后调用（pystray 在独立线程里回调）。"""
        icon.visible = True
        self._icon_ready.set()
        self._announce_first_run()
        self._refresh_icon()

    def _announce_first_run(self) -> None:
        """首次运行时提示用户图标在哪。

        Windows 11 默认把新图标收进"隐藏的图标"溢出区，不提示的话
        用户会以为程序没启动。
        """
        marker = paths.state_dir() / "tray_intro_shown"
        if marker.exists():
            return
        if not self.quiet:
            try:
                self.icon.notify(
                    "我已经在后台待命，每天会自动换一张欧洲油画。\n\n"
                    "如果任务栏上看不到我：点右下角的 ^ 展开「隐藏的图标」，"
                    "把画框图标拖到任务栏上即可常驻。\n"
                    "单击画框图标打开控制面板；右键菜单可选择「现在换一张」。\n"
                    "也可以双击桌面上的「每日欧洲油画壁纸」打开控制面板。",
                    "每日欧洲油画壁纸已启动",
                )
            except Exception as exc:  # pragma: no cover
                self.log.debug("首次运行提示失败：%s", exc)
        try:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(datetime.now().isoformat(timespec="seconds"), encoding="utf-8")
        except OSError:
            pass


def run_tray(quiet: bool = False) -> int:
    # 托盘通常由 pythonw 启动（没有控制台），必须在这里初始化日志，
    # 否则出问题时日志里什么都看不到。
    ensure_std_streams()
    paths.ensure_dirs()
    from .config import load_config

    secret = str(
        ((load_config().get("sources") or {}).get("harvard") or {}).get("api_key") or ""
    ).strip()
    setup_logging(
        paths.log_path(), console=True, secrets=[secret] if secret else None
    )
    wallpaper.set_dpi_aware()
    app = TrayApp(quiet=quiet)
    return app.run()
