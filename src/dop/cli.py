"""命令行入口。"""

from __future__ import annotations

import argparse
import os
import sys
import webbrowser
from pathlib import Path

from . import icon as icon_mod
from . import paths, report, wallpaper
from .config import (
    coerce,
    get_value,
    is_secret_path,
    load_config,
    mask,
    mask_secrets,
    save_config,
    set_value,
)
from .engine import Engine
from .util import VERSION, ensure_std_streams, setup_logging


def _setup(verbose: bool = False) -> Engine:
    paths.ensure_dirs()
    wallpaper.set_dpi_aware()
    # pythonw 下没有控制台，先把输出流换成安全的空流，
    # 否则任何 print/进度输出都会抛 AttributeError 并被静默吞掉
    ensure_std_streams()
    cfg = load_config()
    # 先把密钥登记进日志过滤器，后面任何日志都不可能把 Key 打出去
    secret = str(((cfg.get("sources") or {}).get("harvard") or {}).get("api_key") or "").strip()
    console = os.environ.get("DOP_NO_CONSOLE") != "1"
    setup_logging(
        paths.log_path(),
        verbose=verbose,
        console=console,
        secrets=[secret] if secret else None,
    )
    # 保留控制台原有编码（中文 Windows 是 cp936），只把无法编码的字符降级处理，
    # 这样无论 cmd / PowerShell / 重定向都不会抛 UnicodeEncodeError。
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    return Engine(cfg)


def _print_catalog_progress(message: str) -> None:
    sys.stdout.write(f"\r  {message[:78]:<78}")
    sys.stdout.flush()


def cmd_change(args: argparse.Namespace) -> int:
    engine = _setup(args.verbose)
    record = engine.apply(
        force_new=args.redownload,
        rebuild_catalog=args.rebuild_catalog,
        progress=(None if args.quiet else _print_catalog_progress),
    )
    if not args.quiet:
        sys.stdout.write("\r" + " " * 80 + "\r")
        print(f"已更换壁纸：《{record['title']}》")
        print(f"  作者：{record.get('artist') or '佚名'}   {record.get('date_display') or ''}")
        print(f"  馆藏：{record.get('museum')}  ·  {record.get('license')}")
        print(f"  分辨率：{record.get('resolution')}   显示器：{record.get('monitors')}")
        print(f"  文件：{record.get('wallpaper_file')}")
    return 0


def cmd_info(args: argparse.Namespace) -> int:
    engine = _setup(args.verbose)
    current = engine.current()
    if not current:
        print("还没有换过壁纸。运行 `python main.py change` 立刻换一张。")
        return 1
    print(f"《{current.get('title')}》")
    print(f"  作者：{current.get('artist') or '佚名'}")
    print(f"  年代：{current.get('date_display') or '—'}")
    print(f"  馆藏：{current.get('museum')}")
    print(f"  材质：{current.get('medium') or '—'}")
    print(f"  版权：{current.get('license')}")
    print(f"  更换时间：{current.get('time_display')}")
    print(f"  成品文件：{current.get('wallpaper_file')}")
    print(f"  原始图片：{current.get('source_file')}")
    print(f"  馆藏页面：{current.get('page_url') or '—'}")
    if args.html or args.open:
        page = report.write_html(current)
        print(f"  详情页：{page}")
        if args.open:
            webbrowser.open(page.as_uri())
    return 0


def cmd_catalog(args: argparse.Namespace) -> int:
    engine = _setup(args.verbose)
    catalog = engine.get_catalog(force=args.force, progress=None if args.quiet else _print_catalog_progress)
    if not args.quiet:
        sys.stdout.write("\r" + " " * 80 + "\r")
    print(f"作品目录：{catalog.get('total', 0)} 幅")
    print(f"  生成时间：{catalog.get('generated_display')}")
    print(f"  年代范围：{catalog.get('date_range')}")
    for name, count in (catalog.get("per_source") or {}).items():
        print(f"    · {name}: {count} 幅")
    print(f"  缓存文件：{paths.catalog_path()}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    engine = _setup(args.verbose)
    print(engine.status_text())
    return 0


def cmd_favorite(args: argparse.Namespace) -> int:
    engine = _setup(args.verbose)
    target = engine.favorite()
    if target:
        print(f"已收藏：{target}")
        return 0
    print("收藏失败（还没有壁纸，或文件已不存在）")
    return 1


def cmd_config(args: argparse.Namespace) -> int:
    paths.ensure_dirs()
    cfg = load_config()
    action = args.action or "show"
    if action == "show":
        import json

        # 密钥打码后再显示，避免截图/贴日志时泄露
        print(json.dumps(mask_secrets(cfg), ensure_ascii=False, indent=2))
        return 0
    if action == "path":
        print(paths.config_path())
        return 0
    if action == "get":
        if not args.key:
            print("用法：config get <键>")
            return 2
        value = get_value(cfg, args.key, "<未设置>")
        if is_secret_path(args.key) and value != "<未设置>":
            value = mask(value)
        print(value)
        return 0
    if action == "set":
        if not args.key or args.value is None:
            print("用法：config set <键> <值>")
            return 2
        value = coerce(args.value)
        set_value(cfg, args.key, value)
        save_config(cfg)
        print(f"{args.key} = {value!r}（已保存到 {paths.config_path()}）")
        return 0
    print(f"未知操作：{action}")
    return 2


def cmd_pause(args: argparse.Namespace) -> int:
    cfg = load_config()
    cfg.setdefault("schedule", {})["paused"] = args.resume is False
    save_config(cfg)
    print("已暂停自动更换" if not args.resume else "已恢复自动更换")
    return 0


def cmd_set_time(args: argparse.Namespace) -> int:
    from .util import parse_hhmm

    hour, minute = parse_hhmm(args.time)
    cfg = load_config()
    cfg.setdefault("schedule", {})["time"] = f"{hour:02d}:{minute:02d}"
    save_config(cfg)
    print(f"每日更换时间已设为 {hour:02d}:{minute:02d}")
    return 0


def cmd_install(args: argparse.Namespace) -> int:
    _setup(args.verbose)
    from .installer import install

    result = install(
        with_shortcuts=not getattr(args, "no_shortcuts", False),
        with_autostart=not args.no_autostart,
    )
    print(f"配置文件：{result['config']}")
    print(f"程序图标：{result['icon']}")
    print("开机自启：" + ("已设置" if result["autostart"] else "未设置"))
    if result["shortcuts"]:
        for path in result["shortcuts"]:
            print(f"快捷方式：{path}")
    else:
        print("快捷方式：未创建")
    print(f"程序目录：{paths.app_root()}")
    print(f"数据目录：{paths.data_root()}")
    print("安装完成。")

    if getattr(args, "launch", False):
        import subprocess

        try:
            subprocess.Popen(
                paths.app_argv("tray"),
                cwd=str(paths.app_root()),
                creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
                | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
                close_fds=True,
            )
            print("后台托盘已启动。")
        except OSError as exc:
            print(f"启动托盘失败：{exc}")
    return 0


def cmd_uninstall(args: argparse.Namespace) -> int:
    from .installer import uninstall

    uninstall(purge=args.purge)
    print("已移除开机自启与快捷方式")
    if args.purge:
        print(f"已删除数据目录：{paths.data_root()}")
    else:
        print(f"保留数据目录（如需删除请加 --purge）：{paths.data_root()}")
    return 0


def cmd_watchdog(args: argparse.Namespace) -> int:
    """确保后台托盘在运行（供计划任务调用）。

    已经有一个实例时立刻退出，所以可以放心地每 10 分钟跑一次。
    """
    from .tray import start_tray_if_needed

    started = start_tray_if_needed()
    if started and not getattr(args, "quiet", False):
        print("后台托盘未运行，已启动")
    return 0


def cmd_scrub_logs(args: argparse.Namespace) -> int:
    """把日志里可能残留的密钥抹掉（例如旧版本写入的历史记录）。"""
    from .http import redact

    cfg = load_config()
    secret = str(((cfg.get("sources") or {}).get("harvard") or {}).get("api_key") or "").strip()
    if not secret:
        print("配置里没有 API Key，无需清理。")
        return 0
    changed = 0
    for path in sorted(paths.log_dir().glob("dop.log*")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        new = redact(text.replace(secret, "***"))
        if new != text:
            try:
                path.write_text(new, encoding="utf-8")
                changed += 1
                print(f"已清理：{path}")
            except OSError as exc:
                print(f"清理失败 {path}：{exc}")
    print(f"完成，共处理 {changed} 个日志文件。" if changed else "日志里没有发现残留密钥。")
    return 0


def _probe_tray_window() -> tuple[bool, str]:
    """复现 pystray 创建托盘窗口的两个关键调用，确认系统是否允许。

    pystray 在 `_create_window` 里先 CreateWindowEx 再
    ChangeWindowMessageFilterEx；后者在受限令牌下会返回"拒绝访问"，
    导致托盘图标起不来。这里把它单独测出来，比看堆栈直观得多。
    """
    if not sys.platform.startswith("win"):
        return True, "非 Windows，跳过"
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    WS_POPUP = 0x80000000
    WM_TASKBARCREATED = 0x031B
    MSGFLT_ALLOW = 1

    hwnd = user32.CreateWindowExW(0, "STATIC", None, WS_POPUP, 0, 0, 0, 0, 0, None, None, None)
    if not hwnd:
        return False, f"CreateWindowEx 失败：{ctypes.WinError(ctypes.get_last_error())}"
    try:
        user32.ChangeWindowMessageFilterEx.argtypes = [
            wintypes.HWND, wintypes.UINT, wintypes.DWORD, ctypes.c_void_p
        ]
        user32.ChangeWindowMessageFilterEx.restype = wintypes.BOOL
        if not user32.ChangeWindowMessageFilterEx(hwnd, WM_TASKBARCREATED, MSGFLT_ALLOW, None):
            return False, (
                "ChangeWindowMessageFilterEx 被系统拒绝："
                f"{ctypes.WinError(ctypes.get_last_error())}"
                "（通常是因为进程运行在受限/沙箱环境下；正常双击运行不会这样）"
            )
        return True, "CreateWindowEx 与 ChangeWindowMessageFilterEx 均成功"
    finally:
        user32.DestroyWindow(hwnd)


def cmd_diagnose(args: argparse.Namespace) -> int:
    """一次性体检：环境、路径、日志、密钥、作品库、显示器、托盘。"""
    import platform

    engine = _setup(args.verbose)
    cfg = engine.cfg
    from .catalog import effective_ttl_days, load_catalog
    from .config import env_api_key, mask
    from .tray import tray_is_running
    from .util import VERSION, logging_setup_error

    print(f"每日欧洲油画壁纸 v{VERSION} 诊断")
    print("=== 运行方式 ===")
    print(f"  打包模式：{'是（独立 exe）' if paths.is_frozen() else '否（源码运行）'}")
    print(f"  Python  ：{platform.python_version()}  {platform.machine()}")
    print(f"  程序目录：{paths.app_root()}")
    print(f"  数据目录：{paths.data_root()}")
    print(f"  配置文件：{paths.config_path()}")

    print("=== 日志 ===")
    log_file = paths.log_path()
    print(f"  日志文件：{log_file}")
    try:
        paths.log_dir().mkdir(parents=True, exist_ok=True)
        with open(log_file, "a", encoding="utf-8"):
            pass
        print("  可写入：是")
    except OSError as exc:
        print(f"  可写入：否 -> {type(exc).__name__}: {exc}")
    print(f"  初始化异常：{logging_setup_error() or '无'}")

    print("=== 密钥与图源 ===")
    from .sources import SOURCES

    for name, spec in (cfg.get("sources") or {}).items():
        if name not in SOURCES or not isinstance(spec, dict):
            continue
        enabled = "启用" if spec.get("enabled") else "停用"
        key = str(spec.get("api_key") or "").strip()
        if name == "harvard":
            if key:
                where = "环境变量" if env_api_key() else "配置文件"
                key_text = f"Key {mask(key)}（来自{where}）"
            else:
                key_text = "未配置 Key（可设环境变量 DOP_HARVARD_API_KEY）"
        else:
            key_text = "无需 Key"
        print(f"  {name:<10} {enabled}  {key_text}")

    print("=== 作品库 ===")
    catalog = load_catalog()
    if catalog:
        print(f"  共 {catalog.get('total', 0)} 幅，生成于 {catalog.get('generated_display')}")
        for name, count in (catalog.get("per_source") or {}).items():
            print(f"    · {name}: {count} 幅")
    else:
        print("  尚未建立（首次换壁纸时会自动抓取）")
    print(f"  有效期：{effective_ttl_days(cfg):.0f} 天")

    print("=== 显示器 ===")
    print(f"  {wallpaper.describe_monitors(wallpaper.get_monitors())}")

    print("=== 后台与自启 ===")
    from . import autostart

    print(f"  后台托盘：{'运行中' if tray_is_running() else '未运行'}")
    print(f"  开机自启：{'已开启' if autostart.is_enabled() else '未开启'}")
    print(f"  自启明细：{autostart.describe()}")
    print(f"  自启命令：{paths.launch_command('tray', '--quiet')}")
    guard = int((cfg.get("advanced") or {}).get("wallpaper_guard_seconds", 300) or 0)
    print(f"  壁纸守护：{'每 %d 秒检查一次' % guard if guard > 0 else '已关闭'}")
    if engine.current():
        print(f"  壁纸是否还是本程序的：{'是' if engine.wallpaper_matches() else '否（已被改动）'}")

    print("=== 系统托盘窗口自检 ===")
    ok, message = _probe_tray_window()
    print(f"  {'通过' if ok else '不通过'}：{message}")
    if not ok:
        print("  说明：托盘图标依赖这个系统调用；不通过时仍能正常换壁纸，")
        print("        只是没有托盘图标（可用桌面快捷方式打开控制面板）。")
    return 0


def cmd_make_icon(args: argparse.Namespace) -> int:
    if args.path:
        out = Path(args.path)
    elif paths.is_frozen():
        # 打包后程序目录不可写，图标放数据目录
        out = paths.data_root() / "app.ico"
    else:
        out = paths.app_root() / "assets" / "app.ico"
    icon_mod.write_ico(out)
    print(f"图标已生成：{out}")
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    from . import render

    engine = _setup(args.verbose)
    source = Path(args.image)
    if not source.exists():
        print(f"文件不存在：{source}")
        return 2
    monitors = wallpaper.get_monitors()
    work = {
        "title": args.title or source.stem,
        "artist": args.artist or "",
        "date_display": "",
        "museum": "本地文件",
        "license": "预览",
    }
    image = render.compose(source, monitors, engine.cfg, work)
    out = Path(args.out) if args.out else (paths.wallpaper_dir() / f"preview{render.suffix_for(engine.cfg)}")
    render.save(image, out, engine.cfg)
    print(f"预览已生成：{out}（{image.width}×{image.height}，显示器：{wallpaper.describe_monitors(monitors)}）")
    return 0


def cmd_tray(args: argparse.Namespace) -> int:
    from .tray import run_tray

    return run_tray(quiet=args.quiet)


def cmd_gui(args: argparse.Namespace) -> int:
    from .gui import run_gui

    return run_gui()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dop",
        description="每日欧洲油画壁纸 —— 每天自动换一张 1300-1800 年的欧洲油画。",
    )
    parser.add_argument("--version", action="version", version=f"每日欧洲油画壁纸 {VERSION}")
    parser.add_argument("-v", "--verbose", action="store_true", help="输出调试日志")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("gui", help="打开图形控制面板（默认命令，双击快捷方式用这个）")
    p.set_defaults(func=cmd_gui)

    p = sub.add_parser("tray", help="启动系统托盘程序（后台常驻，开机自启用这个）")
    p.add_argument("--quiet", action="store_true", help="不弹系统通知（自启动用）")
    p.set_defaults(func=cmd_tray)

    p = sub.add_parser("change", help="立刻换一张壁纸")
    p.add_argument("--redownload", action="store_true", help="重新下载当前作品")
    p.add_argument("--rebuild-catalog", action="store_true", help="先重建作品目录")
    p.add_argument("--quiet", action="store_true", help="不输出进度")
    p.set_defaults(func=cmd_change)

    p = sub.add_parser("info", help="显示当前壁纸信息")
    p.add_argument("--html", action="store_true", help="同时生成详情页")
    p.add_argument("--open", action="store_true", help="用浏览器打开详情页")
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("catalog", help="查看/重建作品目录")
    p.add_argument("--force", action="store_true", help="强制重新抓取")
    p.add_argument("--quiet", action="store_true")
    p.set_defaults(func=cmd_catalog)

    p = sub.add_parser("status", help="显示运行状态")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("favorite", help="收藏当前壁纸")
    p.set_defaults(func=cmd_favorite)

    p = sub.add_parser("config", help="查看或修改配置")
    p.add_argument("action", nargs="?", default="show", choices=["show", "get", "set", "path"])
    p.add_argument("key", nargs="?")
    p.add_argument("value", nargs="?")
    p.set_defaults(func=cmd_config)

    p = sub.add_parser("pause", help="暂停自动更换")
    p.add_argument("--resume", action="store_true", help="恢复自动更换")
    p.set_defaults(func=cmd_pause)

    p = sub.add_parser("set-time", help="设置每日更换时间，如 07:30")
    p.add_argument("time")
    p.set_defaults(func=cmd_set_time)

    p = sub.add_parser("install", help="写配置、生成图标、设置开机自启、创建快捷方式")
    p.add_argument("--no-autostart", action="store_true")
    p.add_argument("--no-shortcuts", action="store_true")
    p.add_argument("--launch", action="store_true", help="安装完顺便启动后台托盘")
    p.set_defaults(func=cmd_install)

    p = sub.add_parser("uninstall", help="移除开机自启")
    p.add_argument("--purge", action="store_true", help="同时删除缓存与历史")
    p.set_defaults(func=cmd_uninstall)

    p = sub.add_parser("make-icon", help="生成程序图标")
    p.add_argument("path", nargs="?")
    p.set_defaults(func=cmd_make_icon)

    p = sub.add_parser("scrub-logs", help="清除日志里可能残留的 API Key")
    p.set_defaults(func=cmd_scrub_logs)

    p = sub.add_parser("watchdog", help="确保后台托盘在运行（计划任务调用）")
    p.add_argument("--quiet", action="store_true")
    p.set_defaults(func=cmd_watchdog)

    p = sub.add_parser("diagnose", help="体检：环境、日志、密钥、作品库、托盘是否正常")
    p.set_defaults(func=cmd_diagnose)

    p = sub.add_parser("render", help="用本地图片试渲染一张壁纸（预览排版效果）")
    p.add_argument("image")
    p.add_argument("--out")
    p.add_argument("--title")
    p.add_argument("--artist")
    p.set_defaults(func=cmd_render)

    return parser


def main(argv: list[str] | None = None) -> int:
    # 必须在任何显示器查询之前调用，否则高 DPI 屏只能拿到缩放后的逻辑分辨率
    ensure_std_streams()
    try:
        wallpaper.set_dpi_aware()
    except Exception:  # pragma: no cover
        pass
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        # 不带参数时打开控制面板：双击快捷方式/直接跑 main.py 都是这个行为。
        # 后台常驻由 `main.py tray`（开机自启）负责。
        args = parser.parse_args(["gui"])
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        # 关键：pythonw 下 stderr 是空流，只 print 的话异常就彻底消失了。
        # 用户机器上出过一次"程序在跑但日志什么都没有"，就是吃了这个亏。
        try:
            from .util import get_logger

            get_logger("cli").exception("未捕获的异常：%s", exc)
        except Exception:  # pragma: no cover
            pass
        print(f"错误：{exc}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        return 1
