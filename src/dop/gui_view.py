"""深色观画界面：Tk 视图与自适应画作预览，不负责修改配置或壁纸。"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from pathlib import Path
from typing import Callable, Optional

from PIL import Image, ImageOps, ImageTk

from .util import get_logger

BG = "#202825"
STAGE = "#18201c"
PANEL = "#242e27"
FG = "#e3e9df"
MUTED = "#a3b39a"
FAINT = "#7e9177"
LINE = "#3c4937"
ACCENT = "#d5dfc4"
INK = "#31452d"
FONT = "Microsoft YaHei UI"
SERIF = "Palatino Linotype"
RESAMPLE = Image.Resampling.LANCZOS


def frame(parent, color=BG, **kwargs):
    return tk.Frame(parent, bg=color, **kwargs)


def label(parent, text="", size=10, color=FG, bg=BG, **kwargs):
    return tk.Label(parent, text=text, bg=bg, fg=color, font=(FONT, size), **kwargs)


def button(parent, text, command, *, primary=False, quiet=False, scale=1.0):
    return tk.Button(
        parent, text=text, command=command, font=(FONT, 10),
        bg=ACCENT if primary else parent.cget("bg"), fg=INK if primary else MUTED,
        activebackground="#e1e9d5" if primary else "#303e2d",
        activeforeground=INK if primary else FG, relief="flat", borderwidth=0,
        highlightthickness=0 if primary or quiet else 1, highlightbackground=LINE,
        highlightcolor=ACCENT, padx=round((13 if not quiet else 7) * scale),
        pady=round(8 * scale), cursor="hand2", takefocus=True,
        disabledforeground="#75836d",
    )


def configure_styles(root, scale):
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure("Gallery.TCombobox", fieldbackground=PANEL, background=LINE,
                    foreground=FG, arrowcolor=MUTED, bordercolor=LINE, padding=round(5 * scale))
    style.map("Gallery.TCombobox", fieldbackground=[("readonly", PANEL)],
              foreground=[("readonly", FG)], selectbackground=[("readonly", PANEL)],
              selectforeground=[("readonly", FG)])
    style.configure("Gallery.TCheckbutton", background=PANEL, foreground=FG,
                    font=(FONT, 10), padding=(0, round(5 * scale)))
    style.map("Gallery.TCheckbutton", background=[("active", PANEL)],
              foreground=[("disabled", FAINT), ("active", FG)])
    style.configure("Gallery.Treeview", background=STAGE, fieldbackground=STAGE,
                    foreground=FG, borderwidth=0, rowheight=round(46 * scale), font=(FONT, 10))
    style.configure("Gallery.Treeview.Heading", background=PANEL, foreground=MUTED,
                    relief="flat", font=(FONT, 9), padding=round(9 * scale))
    style.map("Gallery.Treeview", background=[("selected", "#354630")],
              foreground=[("selected", FG)])
    style.map("Gallery.Treeview.Heading", background=[("active", PANEL)])
    style.configure("Gallery.Vertical.TScrollbar", background=LINE, troughcolor=STAGE,
                    bordercolor=STAGE, arrowcolor=MUTED)
    root.option_add("*TCombobox*Listbox.background", PANEL)
    root.option_add("*TCombobox*Listbox.foreground", FG)
    root.option_add("*TCombobox*Listbox.selectBackground", LINE)


class ToggleSwitch(tk.Canvas):
    """支持鼠标、空格和回车的原生开关；程序同步变量时不触发保存动作。"""

    def __init__(self, parent, variable, command, scale):
        self.variable, self.command, self.scale = variable, command, scale
        super().__init__(parent, width=round(34*scale), height=round(23*scale), bg=PANEL,
                         highlightthickness=0, takefocus=True, cursor="hand2")
        self._trace = variable.trace_add("write", lambda *_args: self.draw())
        self.bind("<Button-1>", lambda _event: self.invoke())
        self.bind("<space>", self._key)
        self.bind("<Return>", self._key)
        self.bind("<FocusIn>", lambda _event: self.draw())
        self.bind("<FocusOut>", lambda _event: self.draw())
        self.bind("<Destroy>", self._destroyed)
        self.draw()

    def invoke(self):
        if self.cget("state") != "disabled":
            self.focus_set()
            self.variable.set(not self.variable.get())
            self.command()

    def _key(self, _event):
        self.invoke()
        return "break"

    def draw(self):
        self.delete("all")
        s = self.scale
        on = self.variable.get()
        self.create_line(10*s, 11.5*s, 24*s, 11.5*s, width=16*s,
                         fill="#769568" if on else "#52614b", capstyle=tk.ROUND)
        x = (24 if on else 10)*s
        self.create_oval(x-5.5*s, 6*s, x+5.5*s, 17*s, fill="#f3f5eb", outline="")
        if self.focus_get() is self:
            self.create_rectangle(s, s, 33*s, 22*s, outline=ACCENT)

    def _destroyed(self, event):
        if event.widget is self:
            self.variable.trace_remove("write", self._trace)


class ArtworkPreview(tk.Canvas):
    """只显示原作，等比缩放；窗口变大或缩小时不裁切，也不反复解码原图。"""

    def __init__(self, parent, scale=1.0, on_expand: Optional[Callable] = None):
        super().__init__(parent, bg=STAGE, highlightthickness=0, width=1, height=1)
        self.scale = scale
        self._source: Optional[Image.Image] = None
        self._photo = None
        self._identity = None
        self._after = None
        self._closed = False
        self.bind("<Configure>", self._schedule_draw)
        self.bind("<Destroy>", self._destroyed)
        if on_expand:
            self.bind("<Double-Button-1>", lambda _event: on_expand())

    def set_record(self, record: Optional[dict]):
        record = record or {}
        candidates = []
        for key in ("favorite_source_file", "source_file", "favorite_file", "wallpaper_file", "canvas_file"):
            value = record.get(key)
            if value:
                path = Path(str(value))
                try:
                    if path.is_file():
                        candidates.append((path, path.stat().st_mtime_ns))
                except OSError:
                    continue
        identity = tuple((str(path), stamp) for path, stamp in candidates)
        if identity != self._identity:
            self._identity = identity
            self._source = None
            for path, _stamp in candidates:
                try:
                    with Image.open(path) as raw:
                        raw.draft("RGB", (2400, 1800))
                        image = ImageOps.exif_transpose(raw).convert("RGB")
                    image.thumbnail((2400, 1800), RESAMPLE)
                    self._source = image
                    break
                except (OSError, ValueError, Image.DecompressionBombError) as exc:
                    get_logger("gui.preview").debug("画作预览不可用：%s", exc)
        self.draw()

    def _schedule_draw(self, _event=None):
        if self._closed:
            return
        if self._after:
            self.after_cancel(self._after)
        self._after = self.after(70, self.draw)

    def draw(self):
        self._after = None
        if self._closed:
            return
        self.delete("art")
        width, height = self.winfo_width(), self.winfo_height()
        if width < 4 or height < 4:
            return
        pad = round(16 * self.scale)
        if self._source is None:
            self._photo = None
            self.create_text(width / 2, height / 2 - 16 * self.scale,
                             text="画作将在这里展出", fill=MUTED, font=(FONT, 16), tags="art")
            self.create_text(width / 2, height / 2 + 18 * self.scale,
                             text="点击「换一幅画」开始，或从更换记录中选择作品",
                             fill=FAINT, font=(FONT, 10), tags="art")
            return
        image = self._source.copy()
        image.thumbnail((max(1, width - 2 * pad), max(1, height - 2 * pad)), RESAMPLE)
        self._photo = ImageTk.PhotoImage(image, master=self)
        self.create_image(width // 2, height // 2, image=self._photo, tags="art")

    def _destroyed(self, event):
        if event.widget is self:
            self._closed = True
            if self._after:
                self.after_cancel(self._after)
                self._after = None


class GalleryView:
    """B 方案：顶部轻导航、中央原作、下方信息与操作、底部每日计划。"""

    def __init__(self, root, controller, scale):
        self.root, self.controller, self.scale = root, controller, scale
        self.px = lambda value: round(value * scale)
        p = self.px
        root.configure(bg=BG)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(1, weight=1)

        header = frame(root)
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(1, weight=1)
        brand = frame(header)
        brand.grid(row=0, column=0, padx=(p(32), p(25)), pady=p(19), sticky="w")
        mark = tk.Canvas(brand, width=p(27), height=p(31), bg=BG, highlightthickness=0)
        mark.pack(side="left", padx=(0, p(12)))
        for inset in (1, 5):
            mark.create_rectangle(p(inset), p(inset), p(26-inset), p(30-inset), outline=MUTED)
        mark.create_line(p(8), p(21), p(12), p(14), p(16), p(18), p(20), p(12), fill=MUTED)
        titles = frame(brand)
        titles.pack(side="left")
        label(titles, "每日油画", 14).pack(anchor="w")
        label(titles, "DAILY OIL PAINTING", 7, FAINT).pack(anchor="w", pady=(p(3), 0))

        nav = frame(header)
        nav.grid(row=0, column=1)
        self.nav_buttons = {}
        for key, text in (("today", "正在展出"), ("favorites", "我的收藏"), ("history", "更换记录")):
            item = button(nav, text, lambda key=key: controller.show_view(key), quiet=True, scale=scale)
            item.pack(side="left", padx=p(11))
            self.nav_buttons[key] = item
        button(nav, "设置", controller.open_settings, quiet=True, scale=scale).pack(side="left", padx=p(11))
        self.tray_label = label(header, "●  检查后台…", 9, MUTED)
        self.tray_label.grid(row=0, column=2, padx=(p(20), p(32)))
        frame(root, LINE, height=1).grid(row=0, column=0, sticky="sew")

        body = frame(root, STAGE)
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        self.today = frame(body, STAGE)
        self.today.grid(row=0, column=0, sticky="nsew")
        self.today.columnconfigure(0, weight=1)
        self.today.rowconfigure(0, weight=1)
        stage = frame(self.today, STAGE)
        stage.grid(row=0, column=0, sticky="nsew", padx=p(32), pady=(p(13), 0))
        self.preview = ArtworkPreview(stage, scale, controller.open_preview)
        self.preview.pack(fill="both", expand=True)
        label(stage, "原作预览 · 完整显示", 8, MUTED, bg="#2b3529", padx=p(9), pady=p(5)).place(x=p(5), y=p(7))
        button(stage, "放大  ⤢", controller.open_preview, quiet=True, scale=scale).place(relx=1, x=-p(5), y=p(4), anchor="ne")

        info = frame(self.today, STAGE)
        info.grid(row=1, column=0, sticky="ew", padx=p(38), pady=(p(18), p(22)))
        info.columnconfigure(0, weight=1)
        self.title_label = tk.Label(info, text="", bg=STAGE, fg=FG, font=(SERIF, 24),
                                    anchor="w", justify="left")
        self.title_label.grid(row=0, column=0, sticky="ew")
        self.artist_label = label(info, "", 9, MUTED, STAGE, anchor="w")
        self.artist_label.grid(row=1, column=0, sticky="ew", pady=(p(8), 0))
        actions = frame(info, STAGE)
        actions.grid(row=0, column=1, rowspan=2, sticky="e", padx=(p(18), 0))
        self.favorite_button = button(actions, "♡ 收藏", controller.on_favorite, scale=scale)
        self.favorite_button.pack(side="left", padx=(0, p(9)))
        button(actions, "作品详情 ↗", controller.on_details, scale=scale).pack(side="left", padx=(0, p(9)))
        self.change_button = button(actions, "↻  换一幅画", controller.on_change, primary=True, scale=scale)
        self.change_button.pack(side="left")
        info.bind("<Configure>", lambda event: self.title_label.configure(
            wraplength=max(p(260), event.width - actions.winfo_reqwidth() - p(25))))

        self.collection = frame(body, STAGE)
        self.collection.grid(row=0, column=0, sticky="nsew", padx=p(30), pady=p(22))
        self.collection.rowconfigure(1, weight=1)
        self.collection.columnconfigure(0, weight=1)
        self.collection.columnconfigure(1, weight=1)
        self.collection_heading = label(self.collection, "", 14, FG, STAGE, anchor="w")
        self.collection_heading.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, p(16)))
        list_frame = frame(self.collection, STAGE)
        list_frame.grid(row=1, column=0, sticky="nsew", padx=(0, p(22)))
        list_frame.rowconfigure(0, weight=1)
        list_frame.columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(list_frame, columns=("title", "date"), show="headings",
                                 selectmode="browse", style="Gallery.Treeview")
        self.tree.heading("title", text="画作 / 艺术家", anchor="w")
        self.tree.heading("date", text="时间", anchor="w")
        self.tree.column("title", width=p(250), minwidth=p(130))
        self.tree.column("date", width=p(100), minwidth=p(80), stretch=False)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree.yview,
                               style="Gallery.Vertical.TScrollbar")
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind("<<TreeviewSelect>>", controller.on_collection_selected)
        self.collection_empty = label(list_frame, "", 10, MUTED, STAGE, justify="center", wraplength=p(300))
        self.collection_count = label(self.collection, "", 9, FAINT, STAGE, anchor="w")
        self.collection_count.grid(row=2, column=0, sticky="w", pady=(p(12), 0))
        detail = frame(self.collection, STAGE)
        detail.grid(row=1, column=1, sticky="nsew")
        detail.rowconfigure(0, weight=1)
        detail.columnconfigure(0, weight=1)
        self.collection_preview = ArtworkPreview(detail, scale)
        self.collection_preview.grid(row=0, column=0, sticky="nsew")
        self.collection_title = label(detail, "", 13, FG, STAGE, anchor="w", justify="left", wraplength=p(450))
        self.collection_title.grid(row=1, column=0, sticky="ew", pady=(p(13), p(7)))
        self.collection_meta = label(detail, "", 9, MUTED, STAGE, anchor="w", justify="left", wraplength=p(450))
        self.collection_meta.grid(row=2, column=0, sticky="ew", pady=(0, p(14)))
        detail_actions = frame(detail, STAGE)
        detail_actions.grid(row=3, column=0, sticky="w")
        self.apply_button = button(detail_actions, "设为壁纸", controller.on_apply_selected, primary=True, scale=scale)
        self.apply_button.pack(side="left")
        button(detail_actions, "馆藏页面 ↗", controller.on_selected_page, quiet=True, scale=scale).pack(side="left", padx=p(12))

        bottom = frame(root)
        bottom.grid(row=2, column=0, sticky="ew")
        bottom.columnconfigure(0, weight=1)
        frame(bottom, LINE, height=1).grid(row=0, column=0, columnspan=3, sticky="ew")
        self.schedule_label = label(bottom, "", 9, MUTED, anchor="w")
        self.schedule_label.grid(row=1, column=0, sticky="w", padx=(p(37), p(10)), pady=p(17))
        self.pause_button = button(bottom, "暂停", controller.toggle_auto, quiet=True, scale=scale)
        self.pause_button.grid(row=1, column=1, padx=p(10))
        settings_actions = frame(bottom)
        settings_actions.grid(row=1, column=2, padx=(p(12), p(31)))
        self.landscape_label = label(settings_actions, "竖图已过滤", 8, MUTED)
        self.landscape_label.pack(side="left", padx=(0, p(16)))
        self.display_label = label(settings_actions, "", 8, MUTED)
        self.display_label.pack(side="left", padx=(0, p(10)))
        button(settings_actions, "⚙ 更换设置", controller.open_settings, quiet=True, scale=scale).pack(side="left")

        status = frame(root)
        status.grid(row=3, column=0, sticky="ew")
        status.columnconfigure(0, weight=1)
        frame(status, LINE, height=1).grid(row=0, column=0, columnspan=2, sticky="ew")
        self.status_label = label(status, "就绪", 8, FAINT, anchor="w")
        self.status_label.grid(row=1, column=0, sticky="ew", padx=p(31), pady=p(9))
        self.catalog_label = label(status, "", 8, FAINT)
        self.catalog_label.grid(row=1, column=1, padx=p(31))
        self.today.tkraise()

    def show(self, name):
        for key, item in self.nav_buttons.items():
            item.configure(fg=FG if key == name else FAINT)
        (self.today if name == "today" else self.collection).tkraise()


class SettingsView:
    """独立设置面板，控件绑定控制器变量，构造时不执行任何写操作。"""

    def __init__(self, root, controller, scale):
        self.window = window = tk.Toplevel(root)
        self.window.title("更换设置 · 每日油画")
        self.window.configure(bg=PANEL)
        self.window.transient(root)
        self.window.resizable(False, False)
        p = lambda value: round(value * scale)
        outer = frame(window, PANEL)
        outer.pack(fill="both", expand=True, padx=p(27), pady=p(22))
        label(outer, "更换设置", 17, FG, PANEL).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, p(7)))
        label(outer, "按自己的节奏，遇见下一幅画。", 9, MUTED, PANEL).grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(0, p(18)))

        def check(text, variable, command, row):
            line = frame(outer, PANEL)
            line.grid(row=row, column=0, columnspan=2, sticky="ew", pady=p(8))
            label(line, text, 10, FG, PANEL).pack(side="left")
            widget = ToggleSwitch(line, variable, command, scale)
            widget.pack(side="right", padx=(p(15), 0))
            return widget

        def choice(text, variable, values, command, row):
            label(outer, text, 10, MUTED, PANEL).grid(row=row, column=0, sticky="w", pady=p(9))
            widget = ttk.Combobox(outer, textvariable=variable, values=values, width=20,
                                  state="readonly", style="Gallery.TCombobox", font=(FONT, 10))
            widget.grid(row=row, column=1, sticky="e", padx=(p(20), 0), pady=p(6))
            widget.bind("<<ComboboxSelected>>", lambda _event: command())
            return widget

        self.auto_switch = check("每天自动换一幅", controller.var_auto, controller.on_auto_changed, 2)
        self.time_combo = choice("每日更换时间", controller.var_time, controller.time_presets,
                                 controller.on_time_changed, 3)
        check("错过时间，开机后补换", controller.var_catch_up, controller.on_catch_up_changed, 4)
        check("登录 Windows 后自动启动", controller.var_autostart, controller.on_toggle_autostart, 5)
        frame(outer, LINE, height=1).grid(row=6, column=0, columnspan=2, sticky="ew", pady=p(13))
        check("不使用竖图", controller.var_landscape, controller.on_landscape_changed, 7)
        choice("多屏排版", controller.var_multi, list(controller.multi_modes), controller.on_image_changed, 8)
        choice("画面适配", controller.var_style, list(controller.image_styles), controller.on_image_changed, 9)
        check("显示作品信息与博物馆署名", controller.var_caption, controller.on_caption_changed, 10)
        label(outer, "画面设置将在下次换画时应用。", 8, FAINT, PANEL).grid(
            row=11, column=0, columnspan=2, sticky="w", pady=(p(5), p(15)))
        frame(outer, LINE, height=1).grid(row=12, column=0, columnspan=2, sticky="ew", pady=(0, p(12)))
        tools = frame(outer, PANEL)
        tools.grid(row=13, column=0, columnspan=2, sticky="ew")
        for index, (text, command) in enumerate((
            ("壁纸文件夹", controller.on_open_dir),
            ("打开日志", lambda: controller._open(controller.log_path)),
            ("固定托盘图标", controller.on_pin_tray),
            ("启动后台", controller.on_start_tray),
        )):
            button(tools, text, command, quiet=True, scale=scale).grid(row=index//2, column=index%2, sticky="w", padx=(0, p(18)))
        self.rebuild_button = button(outer, "重建作品库…", controller.on_rebuild_catalog, scale=scale)
        self.rebuild_button.grid(row=14, column=0, sticky="w", pady=(p(12), 0))
        button(outer, "完成", self.close, primary=True, scale=scale).grid(row=14, column=1, sticky="e", pady=(p(12), 0))
        window.bind("<Escape>", lambda _event: self.close())
        window.update_idletasks()
        x = root.winfo_rootx() + (root.winfo_width() - window.winfo_reqwidth())//2
        y = max(20, root.winfo_rooty() + (root.winfo_height() - window.winfo_reqheight())//2)
        window.geometry(f"+{max(0, x)}+{y}")

    def close(self):
        self.window.destroy()
