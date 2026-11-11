"""B「深色观画」控制面板：界面状态、设置与后台任务；布局位于 gui_view。"""
from __future__ import annotations

import ctypes
import os
import queue
import threading
import traceback
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

import tkinter as tk
from tkinter import messagebox

from . import catalog as catalog_mod
from . import paths, report, wallpaper
from .config import load_config, save_config
from .engine import Engine
from .gui_view import MUTED, STAGE, ArtworkPreview, GalleryView, SettingsView, configure_styles
from .util import ensure_std_streams, get_logger, next_target, parse_hhmm, read_json, setup_logging

TIME_PRESETS = ("06:30", "07:30", "08:30", "12:00", "18:00", "21:00", "23:00")


class ControlPanel:
    multi_modes = {"每屏完整显示": "each", "画作放主屏": "primary", "跨屏展开": "span"}
    image_styles = {"完整显示": "fit", "铺满屏幕": "fill", "原尺寸居中": "center", "自动选择": "auto"}
    time_presets = TIME_PRESETS

    def __init__(self, root: tk.Tk):
        self.root = root
        self.log = get_logger("gui")
        self.log_path = paths.log_path()
        self.engine = Engine()
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.busy = False
        self.view_name = "today"
        self._closed = False
        self._settings: Optional[SettingsView] = None
        self._collection_records: list[dict] = []
        self._selected_record: Optional[dict] = None
        self._poll_after = self._event_after = None
        self._poll_ticks = 0
        self._signature = ()
        self.scale = max(1.0, root.winfo_fpixels("1i") / 96.0)
        self.var_time = tk.StringVar(master=root)
        self.var_auto = tk.BooleanVar(master=root)
        self.var_catch_up = tk.BooleanVar(master=root)
        self.var_autostart = tk.BooleanVar(master=root)
        self.var_landscape = tk.BooleanVar(master=root)
        self.var_caption = tk.BooleanVar(master=root)
        self.var_multi = tk.StringVar(master=root)
        self.var_style = tk.StringVar(master=root)
        root.title("每日欧洲油画壁纸")
        p = lambda n: round(n * self.scale)
        width = min(p(1260), root.winfo_screenwidth() - p(80))
        height = min(p(820), root.winfo_screenheight() - p(110))
        root.geometry(f"{width}x{height}+{max(0, (root.winfo_screenwidth()-width)//2)}+{max(0, (root.winfo_screenheight()-height)//2)}")
        root.minsize(min(p(900), width), min(p(640), height))
        root.report_callback_exception = self._on_callback_error
        root.protocol("WM_DELETE_WINDOW", root.destroy)
        root.bind("<Destroy>", self._on_destroy, add=True)
        self._set_window_icon()
        configure_styles(root, self.scale)
        self.ui = GalleryView(root, self, self.scale)
        self.ui.show("today")
        self.refresh()
        self._event_after = root.after(150, self._drain_events)
        self._poll_after = root.after(2500, self._poll_state)
        root.after_idle(self._dark_titlebar)

    def _set_window_icon(self):
        icon = paths.resource_path("assets/app.ico")
        if not icon.is_file():
            icon = paths.data_root() / "app.ico"
        if icon.is_file():
            try:
                self.root.iconbitmap(default=str(icon))
            except tk.TclError:
                pass

    def _dark_titlebar(self, window=None):
        if self._closed:
            return
        target = window or self.root
        if not target.winfo_exists():
            return
        try:
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            user32.GetParent.argtypes = [wintypes.HWND]
            user32.GetParent.restype = wintypes.HWND
            handle = user32.GetParent(target.winfo_id()) or target.winfo_id()
            value = ctypes.c_int(1)
            setter = ctypes.windll.dwmapi.DwmSetWindowAttribute
            setter.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
            setter(handle, 20, ctypes.byref(value), ctypes.sizeof(value))
        except (OSError, AttributeError):
            pass

    def _on_destroy(self, event):
        if event.widget is not self.root:
            return
        self._closed = True
        for token in (self._poll_after, self._event_after):
            if token:
                self.root.after_cancel(token)

    def _on_callback_error(self, exc_type, exc_value, exc_tb):
        text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        self.log.error("界面回调出错：\n%s", text)
        self.set_status(f"操作未完成：{exc_value}")
        messagebox.showerror("操作未完成", str(exc_value), parent=self.root)

    @staticmethod
    def _open(path: Path):
        if path.exists():
            os.startfile(str(path))  # noqa: S606

    def set_status(self, text):
        if not self._closed:
            self.ui.status_label.configure(text=str(text)[:150])

    def _set_busy(self, busy, text=""):
        self.busy = busy
        state = "disabled" if busy else "normal"
        self.ui.change_button.configure(state=state, text="正在处理…" if busy else "↻  换一幅画")
        self.ui.favorite_button.configure(state=state)
        self.ui.apply_button.configure(state="disabled" if busy or not self._selected_record else "normal")
        if self._settings and self._settings.window.winfo_exists():
            self._settings.rebuild_button.configure(state=state)
        if text:
            self.set_status(text)

    def run_task(self, label: str, func: Callable[[], Any], on_done: Optional[Callable] = None):
        if self.busy:
            self.set_status("上一个操作还在进行，请稍候")
            return
        self._set_busy(True, label)

        def worker():
            try:
                func()
            except Exception as exc:  # noqa: BLE001
                self.log.exception("操作失败：%s", exc)
                self.events.put(("error", f"{label}未完成：{exc}"))
            else:
                self.events.put(("done", (label, on_done)))

        threading.Thread(target=worker, name="dop-ui-task", daemon=True).start()

    def _progress(self, text):
        self.events.put(("progress", text))

    def _drain_events(self):
        if self._closed:
            return
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "progress":
                    self.set_status(payload)
                elif kind == "error":
                    self._set_busy(False, payload)
                    messagebox.showerror("操作未完成", payload, parent=self.root)
                else:
                    label, on_done = payload
                    self._set_busy(False, f"{label}完成")
                    self.refresh()
                    if on_done:
                        on_done()
        except queue.Empty:
            pass
        self._event_after = self.root.after(150, self._drain_events)

    def _state_signature(self):
        values = []
        for path in (paths.current_path(), paths.config_path(), paths.favorites_index_path(),
                     paths.history_path(), paths.catalog_path()):
            try:
                values.append(path.stat().st_mtime_ns)
            except OSError:
                values.append(0)
        return tuple(values)

    def _poll_state(self):
        if self._closed:
            return
        self._poll_ticks += 1
        if not self.busy and (self._state_signature() != self._signature or self._poll_ticks % 12 == 0):
            self.refresh()
        self._poll_after = self.root.after(2500, self._poll_state)

    # ------------------------------------------------------------------ 当前展出
    def refresh(self):
        self.refresh_current()
        self.refresh_settings()
        self.refresh_catalog()
        if self.view_name != "today":
            self._refresh_collection()
        self._signature = self._state_signature()

    def refresh_current(self):
        current = self.engine.current()
        self.ui.preview.set_record(current)
        if not current:
            self.ui.title_label.configure(text="还没有展出的画作")
            self.ui.artist_label.configure(text="从博物馆的欧洲古典绘画中，遇见第一幅。")
            self.ui.favorite_button.configure(text="♡ 收藏")
            return
        self.ui.title_label.configure(text=current.get("title") or "无题")
        parts = [current.get("artist") or "佚名", current.get("date_display"), current.get("museum")]
        self.ui.artist_label.configure(text="   ·   ".join(str(part) for part in parts if part))
        saved = any(item.get("key") == current.get("key") for item in self._favorite_records())
        self.ui.favorite_button.configure(text="♥ 已收藏" if saved else "♡ 收藏")

    def refresh_settings(self):
        from . import autostart
        from .tray import tray_is_running
        cfg = load_config()
        schedule, image = cfg.get("schedule") or {}, cfg.get("image") or {}
        hour, minute = parse_hhmm(str(schedule.get("time") or "08:30"))
        value = f"{hour:02d}:{minute:02d}"
        self.var_time.set(value)
        self.var_auto.set(not bool(schedule.get("paused")))
        self.var_catch_up.set(bool(schedule.get("catch_up", True)))
        self.var_autostart.set(autostart.is_enabled())
        self.var_landscape.set(bool((cfg.get("selection") or {}).get("landscape_only", True)))
        self.var_caption.set(bool(image.get("show_caption", True)))
        self.var_multi.set(next((name for name, key in self.multi_modes.items() if key == image.get("multi_monitor")), "每屏完整显示"))
        self.var_style.set(next((name for name, key in self.image_styles.items() if key == image.get("style")), "完整显示"))
        if self.var_auto.get():
            now = datetime.now()
            nxt = next_target(now, value)
            when = "明天" if nxt.date() != now.date() else "今天"
            text = f"◷  每天 {value} 自动更换   ·   下次，{when} {value}"
        else:
            text = "◷  自动更换已暂停   ·   仍可手动换画"
        self.ui.schedule_label.configure(text=text)
        self.ui.pause_button.configure(text="暂停" if self.var_auto.get() else "恢复")
        self.ui.landscape_label.configure(text="✓ 竖图已过滤" if self.var_landscape.get() else "允许所有画幅")
        running = tray_is_running()
        self.ui.tray_label.configure(text="●  后台运行中" if running else "○  后台未运行",
                                     fg=MUTED if running else "#d5b193")
        try:
            monitors = wallpaper.get_monitors()
            count = len(monitors)
            all_4k = count and all(m.get("width", 0) >= 3840 and m.get("height", 0) >= 2160 for m in monitors)
            self.ui.display_label.configure(text=f"{count} 块{' 4K ' if all_4k else ''}屏幕 · {self.var_multi.get()}")
        except Exception:
            self.ui.display_label.configure(text=self.var_multi.get())

    def refresh_catalog(self):
        catalog = catalog_mod.load_catalog()
        if not catalog:
            self.ui.catalog_label.configure(text="作品库尚未建立 · 首次换画时自动准备")
            return
        dates = catalog.get("date_range") or [1300, 1800]
        self.ui.catalog_label.configure(text=f"{catalog.get('total', 0):,} 幅馆藏作品 · {dates[0]}–{dates[1]} 年")

    def on_change(self):
        self.run_task("更换壁纸", lambda: self.engine.apply(progress=self._progress))

    def on_favorite(self):
        current = self.engine.current()
        if not current:
            self.set_status("还没有可收藏的画作，请先换一幅画")
            return
        if any(item.get("key") == current.get("key") for item in self._favorite_records()):
            self.set_status("这幅画已在「我的收藏」中")
            return

        def favorite():
            if not self.engine.favorite(current):
                raise RuntimeError("画作文件已不存在，请重新下载后再收藏")

        self.run_task("收藏画作", favorite)

    def on_details(self):
        current = self.engine.current()
        if not current:
            self.set_status("还没有画作详情，请先换一幅画")
            return
        page = report.write_html(current)
        if not webbrowser.open(page.as_uri()):
            messagebox.showinfo("详情页已生成", f"请手动打开：\n{page}", parent=self.root)

    def _open_record_page(self, record):
        url = str((record or {}).get("page_url") or "").strip()
        if not url:
            self.set_status("这幅画作没有馆藏页面链接")
        elif not webbrowser.open(url):
            messagebox.showinfo("馆藏页面", f"请手动打开：\n{url}", parent=self.root)

    def on_open_page(self):
        self._open_record_page(self.engine.current())

    def open_preview(self):
        record = self.engine.current() if self.view_name == "today" else self._selected_record
        if not record:
            self.set_status("请先选择一幅画作")
            return
        window = tk.Toplevel(self.root)
        window.title(str(record.get("title") or "画作预览"))
        window.configure(bg=STAGE)
        window.geometry(f"{round(1080*self.scale)}x{round(720*self.scale)}")
        window.transient(self.root)
        preview = ArtworkPreview(window, self.scale)
        preview.pack(fill="both", expand=True)
        preview.set_record(record)
        window.after_idle(lambda: self._dark_titlebar(window))
        window.bind("<Escape>", lambda _event: window.destroy())

    # ------------------------------------------------------------------ 收藏与更换记录
    def _favorite_records(self):
        data = read_json(paths.favorites_index_path(), [])
        return [item for item in data if isinstance(item, dict) and item.get("key")] if isinstance(data, list) else []

    def show_view(self, name):
        if name not in ("today", "favorites", "history"):
            return
        self.view_name = name
        self.ui.show(name)
        if name == "today":
            self.refresh_current()
        else:
            self._refresh_collection()

    def _refresh_collection(self):
        previous = (self._selected_record or {}).get("key")
        favorites = self.view_name == "favorites"
        raw = self._favorite_records() if favorites else self.engine.get_history()
        valid = [item for item in raw if isinstance(item, dict) and item.get("key")]
        records = list(reversed(valid))
        if favorites:
            unique = {}
            for item in records:
                unique.setdefault(item["key"], item)
            records = list(unique.values())
        self._collection_records = records[:200]
        self.ui.tree.delete(*self.ui.tree.get_children())
        self.ui.collection_heading.configure(text="我的收藏" if favorites else "更换记录")
        for index, item in enumerate(self._collection_records):
            date = item.get("time_display") or ""
            if favorites and item.get("favorited_at"):
                try:
                    date = datetime.fromtimestamp(float(item["favorited_at"])).strftime("%Y-%m-%d %H:%M")
                except (TypeError, ValueError, OSError):
                    pass
            self.ui.tree.insert("", "end", iid=str(index), values=(
                f"{item.get('title') or '无题'}  /  {item.get('artist') or '佚名'}",
                str(date)[5:16] if len(str(date)) >= 16 else str(date)))
        self.ui.collection_count.configure(text=f"显示最近 {len(self._collection_records)} 幅 · 共 {len(records)} 幅；选中只预览，不会换壁纸")
        self.ui.collection_empty.place_forget()
        if not self._collection_records:
            self._selected_record = None
            self.ui.collection_preview.set_record(None)
            self.ui.collection_title.configure(text="还没有收藏" if favorites else "还没有更换记录")
            self.ui.collection_meta.configure(text="在「正在展出」中收藏喜欢的画作。" if favorites else "换过的画作会出现在这里。")
            self.ui.collection_empty.configure(text="收藏一幅喜欢的画作，\n以后可以从这里找到它。" if favorites else "换过的画作会出现在这里。")
            self.ui.collection_empty.place(relx=.5, rely=.5, anchor="center")
            self.ui.apply_button.configure(state="disabled")
            return
        selected = next((i for i, item in enumerate(self._collection_records) if item.get("key") == previous), 0)
        self.ui.tree.selection_set(str(selected))
        self.ui.tree.focus(str(selected))
        self.on_collection_selected()

    def on_collection_selected(self, _event=None):
        selection = self.ui.tree.selection()
        if not selection:
            return
        index = int(selection[0])
        if index >= len(self._collection_records):
            return
        record = self._collection_records[index]
        self._selected_record = record
        self.ui.collection_preview.set_record(record)
        self.ui.collection_title.configure(text=record.get("title") or "无题")
        parts = [record.get("artist") or "佚名", record.get("date_display"), record.get("museum")]
        self.ui.collection_meta.configure(text=" · ".join(str(part) for part in parts if part))
        self.ui.apply_button.configure(state="disabled" if self.busy else "normal")

    def on_apply_selected(self):
        if not self._selected_record:
            return
        record = dict(self._selected_record)
        # 旧历史不含备用图片地址，优先从现有目录补齐，不强制联网重建。
        catalog = catalog_mod.load_catalog() or {}
        work = next((item for item in catalog.get("works", []) if item.get("key") == record.get("key")), record)
        self.run_task("应用画作", lambda: self.engine.apply(work=work, progress=self._progress),
                      on_done=lambda: self.show_view("today"))

    def on_selected_page(self):
        self._open_record_page(self._selected_record)

    # ------------------------------------------------------------------ 设置
    def open_settings(self):
        if self._settings and self._settings.window.winfo_exists():
            self._settings.window.deiconify()
            self._settings.window.lift()
            self._settings.window.focus_set()
            return
        self.refresh_settings()
        self._settings = SettingsView(self.root, self, self.scale)
        self._dark_titlebar(self._settings.window)
        self._set_busy(self.busy)

    def _save_preference(self, section, key, value, text):
        cfg = load_config()
        cfg.setdefault(section, {})[key] = value
        save_config(cfg)
        self.set_status(text)
        self.refresh_settings()

    def on_time_changed(self):
        self._save_preference("schedule", "time", self.var_time.get(), f"每日更换时间已设为 {self.var_time.get()}")

    def on_auto_changed(self):
        self._save_preference("schedule", "paused", not self.var_auto.get(),
                              "已恢复自动更换" if self.var_auto.get() else "已暂停自动更换与壁纸守护")

    def toggle_auto(self):
        self.var_auto.set(not self.var_auto.get())
        self.on_auto_changed()

    def on_catch_up_changed(self):
        self._save_preference("schedule", "catch_up", self.var_catch_up.get(), "已更新开机补换设置")

    def on_landscape_changed(self):
        self._save_preference("selection", "landscape_only", self.var_landscape.get(), "已更新画作方向筛选")

    def on_caption_changed(self):
        self._save_preference("image", "show_caption", self.var_caption.get(), "署名设置将在下次换画时应用")

    def on_image_changed(self):
        cfg = load_config()
        cfg.setdefault("image", {}).update(
            multi_monitor=self.multi_modes[self.var_multi.get()], style=self.image_styles[self.var_style.get()])
        save_config(cfg)
        self.set_status("画面设置将在下次换画时应用")
        self.refresh_settings()

    def on_toggle_autostart(self):
        from . import autostart
        want = self.var_autostart.get()

        def change():
            if want:
                if not autostart.enable():
                    raise RuntimeError("未能开启开机自动启动，请查看日志")
            else:
                autostart.disable()

        self.run_task("设置开机启动", change)

    def on_open_dir(self):
        self._open(paths.wallpaper_dir())

    def on_start_tray(self):
        from .tray import start_tray_if_needed
        started = start_tray_if_needed()
        self.set_status("后台已启动" if started else "后台已经在运行")
        self.root.after(1500, self.refresh_settings)

    def on_pin_tray(self):
        if wallpaper.promote_tray_icon():
            self.set_status("已把托盘图标设为常显")
        else:
            messagebox.showinfo("显示托盘图标", "展开任务栏右下角的 ^，把画框图标拖到任务栏上即可。", parent=self.root)

    def on_rebuild_catalog(self):
        if messagebox.askyesno("重建作品库", "会重新联网抓取馆藏，通常需要约 10 分钟。\n\n重建期间保留当前壁纸，是否继续？", parent=self.root):
            self.run_task("重建作品库", lambda: self.engine.get_catalog(force=True, progress=self._progress))

    def maybe_first_run_setup(self):
        from . import autostart
        from .installer import install, is_setup_done, mark_setup_done
        if is_setup_done() or autostart.is_enabled():
            return
        if not messagebox.askyesno("完成安装", "是否创建快捷方式并开启开机自动启动？\n\n每天会在设定时间自动换画。", parent=self.root):
            mark_setup_done()
            return
        install()
        self.set_status("已创建快捷方式并开启自动启动")
        self.refresh_settings()


def run_gui() -> int:
    ensure_std_streams()
    paths.ensure_dirs()
    secret = str(((load_config().get("sources") or {}).get("harvard") or {}).get("api_key") or "").strip()
    setup_logging(paths.log_path(), console=True, secrets=[secret] if secret else None)
    wallpaper.set_dpi_aware()
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        get_logger("gui").error("无法创建窗口：%s", exc)
        return 1
    panel = ControlPanel(root)
    root.after(500, panel.maybe_first_run_setup)
    root.mainloop()
    return 0
