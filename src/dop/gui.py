"""图形控制面板（tkinter，只用标准库）。

托盘图标在 Windows 11 上默认会被收进"隐藏的图标"溢出区，甚至被系统更新重置，
所以要有一个不依赖托盘的入口：双击桌面快捷方式打开这个窗口就能操作一切。
"""

from __future__ import annotations

import queue
import threading
import traceback
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

import tkinter as tk
from tkinter import messagebox, ttk

from PIL import Image, ImageTk

from . import paths, report, wallpaper
from .config import load_config, save_config
from .engine import Engine
from .util import (
    ensure_std_streams,
    get_logger,
    human_size,
    next_target,
    parse_hhmm,
    setup_logging,
)

try:
    RESAMPLE = Image.Resampling.LANCZOS
except AttributeError:  # pragma: no cover
    RESAMPLE = Image.LANCZOS

TIME_PRESETS = ("06:30", "07:30", "08:30", "12:00", "18:00", "21:00", "23:00")
BG = "#14141a"
CARD = "#1d1d25"
FG = "#e8e6e1"
MUTED = "#9a968e"
ACCENT = "#d9bd76"


class ControlPanel:
    def __init__(self, root: tk.Tk) -> None:
        self.log = get_logger("gui")
        self.root = root
        self.engine = Engine()
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.busy = False
        self._photo: Optional[ImageTk.PhotoImage] = None

        root.title("每日欧洲油画壁纸")
        root.configure(bg=BG)
        root.minsize(660, 470)
        # pythonw 启动时没有 stderr，Tk 默认会静默吞掉回调里的异常，
        # 表现就是"点了按钮没反应"。这里改成写进日志文件。
        root.report_callback_exception = self._on_callback_error
        self._set_window_icon()
        self._build_styles()
        self._build_layout()
        self.refresh()
        root.after(150, self._drain_events)

    # ------------------------------------------------------------------ 外观
    def _set_window_icon(self) -> None:
        icon = paths.resource_path("assets/app.ico")
        if not icon.exists():
            icon = paths.data_root() / "app.ico"
        if icon.exists():
            try:
                self.root.iconbitmap(default=str(icon))
            except tk.TclError:
                pass

    def _build_styles(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(".", background=BG, foreground=FG, font=("Microsoft YaHei UI", 10))
        style.configure("TFrame", background=BG)
        style.configure("Card.TFrame", background=CARD)
        style.configure("TLabel", background=BG, foreground=FG)
        style.configure("Card.TLabel", background=CARD, foreground=FG)
        style.configure("Title.TLabel", background=CARD, foreground="#ffffff",
                        font=("Microsoft YaHei UI", 15, "bold"))
        style.configure("Meta.TLabel", background=CARD, foreground=MUTED)
        style.configure("Accent.TLabel", background=BG, foreground=ACCENT)
        style.configure("TButton", padding=(12, 7), font=("Microsoft YaHei UI", 10))
        style.configure("Accent.TButton", padding=(14, 8), font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("TCheckbutton", background=BG, foreground=FG)
        style.map("TCheckbutton", background=[("active", BG)])
        style.configure("TCombobox", padding=4)

    def _build_layout(self) -> None:
        outer = ttk.Frame(self.root, padding=18)
        outer.pack(fill="both", expand=True)

        # ---------------- 当前作品卡片 ----------------
        card = ttk.Frame(outer, style="Card.TFrame", padding=14)
        card.pack(fill="x")
        card.columnconfigure(1, weight=1)

        self.thumb = tk.Label(card, background="#000000", borderwidth=0)
        self.thumb.grid(row=0, column=0, rowspan=4, sticky="nw", padx=(0, 16))

        self.lbl_title = ttk.Label(card, text="还没有换过壁纸", style="Title.TLabel",
                                   wraplength=380, justify="left")
        self.lbl_title.grid(row=0, column=1, sticky="w")
        self.lbl_artist = ttk.Label(card, text="", style="Card.TLabel",
                                    font=("Microsoft YaHei UI", 12))
        self.lbl_artist.grid(row=1, column=1, sticky="w", pady=(6, 0))
        self.lbl_meta = ttk.Label(card, text="", style="Meta.TLabel",
                                  wraplength=380, justify="left")
        self.lbl_meta.grid(row=2, column=1, sticky="w", pady=(4, 0))
        self.lbl_time = ttk.Label(card, text="", style="Meta.TLabel",
                                  wraplength=380, justify="left")
        self.lbl_time.grid(row=3, column=1, sticky="w", pady=(4, 0))

        # ---------------- 主要操作 ----------------
        actions = ttk.Frame(outer)
        actions.pack(fill="x", pady=(16, 0))
        self.btn_change = ttk.Button(actions, text="立刻换一张", style="Accent.TButton",
                                     command=self.on_change)
        self.btn_change.pack(side="left")
        ttk.Button(actions, text="收藏", command=self.on_favorite).pack(side="left", padx=8)
        ttk.Button(actions, text="查看详情", command=self.on_details).pack(side="left")
        ttk.Button(actions, text="打开馆藏页面", command=self.on_open_page).pack(side="left", padx=8)

        # ---------------- 自动更换设置 ----------------
        opts = ttk.LabelFrame(outer, text=" 自动更换 ", padding=12)
        opts.pack(fill="x", pady=(16, 0))

        row1 = ttk.Frame(opts)
        row1.pack(fill="x")
        ttk.Label(row1, text="每天").pack(side="left")
        self.var_time = tk.StringVar()
        self.cmb_time = ttk.Combobox(row1, textvariable=self.var_time, values=list(TIME_PRESETS),
                                     width=8, state="readonly")
        self.cmb_time.pack(side="left", padx=6)
        self.cmb_time.bind("<<ComboboxSelected>>", lambda _e: self.on_time_changed())
        ttk.Label(row1, text="自动换一张").pack(side="left")

        self.var_pause = tk.BooleanVar()
        ttk.Checkbutton(row1, text="暂停自动更换", variable=self.var_pause,
                        command=self.on_toggle_pause).pack(side="left", padx=24)

        self.var_autostart = tk.BooleanVar()
        ttk.Checkbutton(row1, text="开机自动启动", variable=self.var_autostart,
                        command=self.on_toggle_autostart).pack(side="left")

        row2 = ttk.Frame(opts)
        row2.pack(fill="x", pady=(10, 0))
        self.lbl_schedule = ttk.Label(row2, text="", style="Meta.TLabel")
        self.lbl_schedule.pack(side="left")

        row3 = ttk.Frame(opts)
        row3.pack(fill="x", pady=(10, 0))
        self.lbl_tray = ttk.Label(row3, text="", style="Meta.TLabel")
        self.lbl_tray.pack(side="left")
        ttk.Button(row3, text="启动后台托盘", command=self.on_start_tray).pack(side="left", padx=8)
        ttk.Button(row3, text="把图标固定到任务栏", command=self.on_pin_tray).pack(side="left")

        # ---------------- 底部状态 ----------------
        bottom = ttk.Frame(outer)
        bottom.pack(fill="both", expand=True, pady=(14, 0))
        self.lbl_status = ttk.Label(bottom, text="就绪", style="Accent.TLabel")
        self.lbl_status.pack(anchor="w")
        self.lbl_catalog = ttk.Label(bottom, text="", style="Meta.TLabel", justify="left")
        self.lbl_catalog.pack(anchor="w", pady=(4, 0))

        links = ttk.Frame(bottom)
        links.pack(fill="x", pady=(10, 0))
        ttk.Button(links, text="打开壁纸文件夹", command=self.on_open_dir).pack(side="left")
        ttk.Button(links, text="打开日志", command=lambda: self._open(paths.log_path())).pack(side="left", padx=8)
        ttk.Button(links, text="重建作品库", command=self.on_rebuild_catalog).pack(side="left")
        ttk.Button(links, text="退出", command=self.root.destroy).pack(side="right")

    # ------------------------------------------------------------------ 工具
    def _on_callback_error(self, exc_type, exc_value, exc_tb) -> None:
        """任何界面回调抛异常都要留下痕迹，否则用户只会看到"没反应"。"""
        text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        self.log.error("界面回调出错：\n%s", text)
        self.set_status(f"出错了：{exc_value}（详情见日志）")
        try:
            messagebox.showerror(
                "界面出错",
                f"{exc_type.__name__}: {exc_value}\n\n完整信息已写入日志，"
                "可通过「打开日志」查看。",
                parent=self.root,
            )
        except tk.TclError:
            pass

    @staticmethod
    def _open(path: Path) -> None:
        target = Path(path)
        if target.exists():
            import os

            os.startfile(str(target))  # noqa: S606

    def set_status(self, text: str) -> None:
        self.lbl_status.configure(text=text)

    def _set_busy(self, busy: bool, text: str = "") -> None:
        self.busy = busy
        state = "disabled" if busy else "normal"
        self.btn_change.configure(state=state)
        if text:
            self.set_status(text)

    def run_task(self, label: str, func: Callable[[], Any]) -> None:
        """在后台线程里跑耗时操作，完成后回到主线程刷新界面。"""
        if self.busy:
            return
        self._set_busy(True, label)

        def worker() -> None:
            try:
                func()
            except Exception as exc:  # noqa: BLE001
                self.log.exception("操作失败：%s", exc)
                self.events.put(("error", f"{label}失败：{exc}"))
            else:
                self.events.put(("done", label))

        threading.Thread(target=worker, daemon=True).start()

    def _drain_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "error":
                    self._set_busy(False, str(payload))
                    messagebox.showerror("出错了", str(payload), parent=self.root)
                else:
                    self._set_busy(False, f"{payload}完成")
                    self.refresh()
        except queue.Empty:
            pass
        self.root.after(150, self._drain_events)

    # ------------------------------------------------------------------ 刷新
    def refresh(self) -> None:
        self.refresh_current()
        self.refresh_settings()
        self.refresh_catalog()

    def refresh_current(self) -> None:
        current = self.engine.current()
        if not current:
            self.lbl_title.configure(text="还没有换过壁纸")
            self.lbl_artist.configure(text="点「立刻换一张」开始")
            self.lbl_meta.configure(text="")
            self.lbl_time.configure(text="")
            # 没有 image 时 Label 的宽高单位是字符/行，不能用像素尺寸。
            # 保留真正的占位图片，也保持引用，避免首次运行把窗口撑成黑屏。
            self._photo = ImageTk.PhotoImage(
                Image.new("RGB", (260, 150), (14, 14, 18)), master=self.root
            )
            self.thumb.configure(image=self._photo, width=260, height=150)
            return

        self.lbl_title.configure(text=current.get("title") or "无题")
        artist = current.get("artist") or "佚名"
        self.lbl_artist.configure(text=f"{artist}    {current.get('date_display') or ''}".strip())
        self.lbl_meta.configure(
            text=f"{current.get('museum') or ''}    {current.get('license') or ''}"
        )
        try:
            size = human_size(Path(str(current.get("source_file") or "")).stat().st_size)
        except OSError:
            size = "—"
        self.lbl_time.configure(
            text=f"更换于 {current.get('time_display')}    分辨率 {current.get('resolution')}"
                 f"    原图 {size}"
        )

        # 缩略图：用 draft() 让 JPEG 解码器直接降采样，避免解码整张 7K 大图。
        # 优先用整张跨屏图，这样多屏时也能看到完整构图。
        wallpaper_file = Path(str(current.get("canvas_file") or current.get("wallpaper_file") or ""))
        if wallpaper_file.exists():
            try:
                with Image.open(wallpaper_file) as raw:
                    raw.draft("RGB", (520, 520))
                    img = raw.convert("RGB")
                img.thumbnail((260, 150), RESAMPLE)
                canvas = Image.new("RGB", (260, 150), (14, 14, 18))
                canvas.paste(img, ((260 - img.width) // 2, (150 - img.height) // 2))
                self._photo = ImageTk.PhotoImage(canvas)
                self.thumb.configure(image=self._photo, width=260, height=150)
            except (OSError, ValueError) as exc:
                self.log.debug("缩略图加载失败：%s", exc)

    def refresh_settings(self) -> None:
        cfg = load_config()
        schedule = cfg.get("schedule") or {}
        value = str(schedule.get("time") or "08:30")
        hour, minute = parse_hhmm(value)
        value = f"{hour:02d}:{minute:02d}"
        self.var_time.set(value)
        self.var_pause.set(bool(schedule.get("paused")))
        from . import autostart

        self.var_autostart.set(autostart.is_enabled())

        now = datetime.now()
        nxt = next_target(now, value)
        current = self.engine.current()
        last = (current or {}).get("time_display") or "从未"
        if schedule.get("paused"):
            self.lbl_schedule.configure(text=f"已暂停。上次更换：{last}")
        else:
            delta = nxt - now
            hours = int(delta.total_seconds() // 3600)
            minutes = int((delta.total_seconds() % 3600) // 60)
            self.lbl_schedule.configure(
                text=f"下次自动更换：{nxt.strftime('%m-%d %H:%M')}"
                     f"（{hours} 小时 {minutes} 分后）    上次更换：{last}"
            )

        from .tray import tray_is_running

        if tray_is_running():
            self.lbl_tray.configure(text="后台托盘：运行中（在任务栏右下角，可能收在 ^ 里）")
        else:
            self.lbl_tray.configure(text="后台托盘：未运行（每日自动更换需要它）")

    def refresh_catalog(self) -> None:
        from . import catalog as catalog_mod

        catalog = catalog_mod.load_catalog()
        if not catalog:
            self.lbl_catalog.configure(text="作品库：尚未建立（第一次换壁纸时会自动抓取，约 2-4 分钟）")
            return
        parts = " / ".join(
            f"{name} {count}" for name, count in (catalog.get("per_source") or {}).items()
        )
        self.lbl_catalog.configure(
            text=f"作品库：{catalog.get('total', 0)} 幅（{parts}）    年代 "
                 f"{catalog.get('date_range', ['', ''])[0]}-{catalog.get('date_range', ['', ''])[1]}"
                 f"    更新于 {catalog.get('generated_display')}"
        )

    # ------------------------------------------------------------------ 动作
    def on_change(self) -> None:
        self.run_task("更换壁纸", lambda: self.engine.apply())

    def on_favorite(self) -> None:
        self.set_status("正在收藏当前作品…")
        target = self.engine.favorite()
        if target:
            self.set_status(f"已收藏：{target.name}")
            messagebox.showinfo("收藏成功", f"已保存到：\n{target}", parent=self.root)
        else:
            self.set_status("收藏失败：还没有可收藏的壁纸")
            messagebox.showwarning("收藏失败", "还没有可收藏的壁纸。", parent=self.root)

    def on_details(self) -> None:
        current = self.engine.current()
        if not current:
            messagebox.showwarning("暂无信息", "还没有换过壁纸。", parent=self.root)
            return
        page = report.write_html(current)
        self.set_status(f"详情页已生成：{page}")
        if not webbrowser.open(page.as_uri()):
            self.set_status("详情页已生成，但没能自动打开浏览器")
            messagebox.showinfo(
                "详情页已生成",
                f"没能自动打开浏览器，请手动打开：\n{page}",
                parent=self.root,
            )

    def on_open_page(self) -> None:
        url = str((self.engine.current() or {}).get("page_url") or "").strip()
        if not url:
            messagebox.showwarning("暂无链接", "当前作品没有馆藏页面链接。", parent=self.root)
            return
        self.set_status(f"正在打开：{url}")
        if not webbrowser.open(url):
            self.set_status("没能自动打开浏览器")
            messagebox.showinfo("请手动打开", f"没能自动打开浏览器，链接是：\n{url}", parent=self.root)

    def on_open_dir(self) -> None:
        self._open(paths.wallpaper_dir())

    def on_time_changed(self) -> None:
        value = self.var_time.get()
        cfg = load_config()
        cfg.setdefault("schedule", {})["time"] = value
        save_config(cfg)
        self.set_status(f"每日更换时间已设为 {value}")
        self.refresh_settings()

    def on_toggle_pause(self) -> None:
        cfg = load_config()
        cfg.setdefault("schedule", {})["paused"] = bool(self.var_pause.get())
        save_config(cfg)
        self.set_status("已暂停自动更换" if self.var_pause.get() else "已恢复自动更换")
        self.refresh_settings()

    def on_toggle_autostart(self) -> None:
        from . import autostart

        want = bool(self.var_autostart.get())
        if want:
            ok = autostart.enable()
            self.set_status(
                "已开启开机自动启动（登录时运行，每 10 分钟检查一次是否还活着）"
                if ok
                else "设置失败，请查看日志"
            )
        else:
            autostart.disable()
            self.set_status("已关闭开机自动启动")
        self.refresh_settings()

    def on_start_tray(self) -> None:
        from .tray import tray_is_running

        if tray_is_running():
            self.set_status("后台托盘已经在运行了")
            self.refresh_settings()
            return
        import subprocess

        app = paths.app_root()
        try:
            subprocess.Popen(
                paths.app_argv("tray"),
                cwd=str(app),
                creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
                | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
                close_fds=True,
            )
            self.set_status("已启动后台托盘")
        except OSError as exc:
            self.set_status(f"启动失败：{exc}")
        self.root.after(1500, self.refresh_settings)

    def on_pin_tray(self) -> None:
        found = wallpaper.promote_tray_icon()
        if found:
            messagebox.showinfo(
                "已设为常显",
                "已把通知区域图标设为常显。\n\n"
                "如果任务栏上还是看不到，请注销后重新登录一次即可生效。",
                parent=self.root,
            )
            self.set_status("已把托盘图标设为常显")
        else:
            messagebox.showinfo(
                "需要先让系统登记这个图标",
                "Windows 还没有登记本程序的托盘图标，所以无法直接固定。\n\n"
                "请这样做：\n"
                "1. 点击任务栏右下角的 ^（向上箭头）展开「隐藏的图标」\n"
                "2. 找到画框图标后，再用鼠标把它拖到任务栏上\n\n"
                "拖过去之后，Windows 会永久记住这个位置。\n"
                "（也可以点开一次 ^ 之后，再回来按一次本按钮）",
                parent=self.root,
            )

    def on_rebuild_catalog(self) -> None:
        if not messagebox.askyesno(
            "重建作品库",
            "会重新联网抓取三家博物馆的作品列表，大约需要 2-4 分钟。\n\n确定继续吗？",
            parent=self.root,
        ):
            return
        self.run_task(
            "重建作品库",
            lambda: self.engine.get_catalog(force=True),
        )

    # ------------------------------------------------------------------ 首次运行
    def maybe_first_run_setup(self) -> None:
        """独立 exe 首次运行时，询问是否建立快捷方式与开机自启。"""
        from . import autostart
        from .installer import install, is_setup_done, mark_setup_done

        if is_setup_done() or autostart.is_enabled():
            return
        answer = messagebox.askyesno(
            "完成安装",
            "要不要把它装得更省心一点？\n\n"
            "· 在桌面和开始菜单创建快捷方式\n"
            "· 设置开机自动启动（这样每天才会自动换壁纸）\n\n"
            "选「否」也能正常使用，只是需要手动打开、也不会自动换。",
            parent=self.root,
        )
        if not answer:
            mark_setup_done()
            self.set_status("已跳过安装，可随时在界面里开启开机自启")
            return
        result = install()
        lines = []
        if result["shortcuts"]:
            lines.append("已创建快捷方式：")
            lines += [f"    {Path(p).name}" for p in result["shortcuts"]]
        else:
            lines.append("快捷方式创建失败（不影响使用）")
        lines.append("开机自动启动：" + ("已开启" if result["autostart"] else "设置失败"))
        lines.append("")
        lines.append("不需要时可以在界面上关掉开机自启，或运行 uninstall 卸载。")
        messagebox.showinfo("安装完成", "\n".join(lines), parent=self.root)
        self.set_status("已完成安装")
        self.refresh_settings()


def run_gui() -> int:
    ensure_std_streams()
    paths.ensure_dirs()
    secret = str(
        ((load_config().get("sources") or {}).get("harvard") or {}).get("api_key") or ""
    ).strip()
    setup_logging(
        paths.log_path(), console=True, secrets=[secret] if secret else None
    )
    wallpaper.set_dpi_aware()
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        get_logger("gui").error("无法创建窗口：%s", exc)
        print(f"无法创建图形界面：{exc}")
        return 1
    panel = ControlPanel(root)
    root.after(500, panel.maybe_first_run_setup)
    root.mainloop()
    return 0
