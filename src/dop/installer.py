"""一键安装/卸载：写配置、生成图标、设置开机自启、创建快捷方式。

源码安装（install.bat → bootstrap.py）和独立 exe 的首次运行都走这里，
保证两种分发方式的行为一致。
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from . import icon as icon_mod
from . import paths, wallpaper
from .config import load_config, save_config
from .shortcuts import create_shortcuts, remove_shortcuts
from .util import get_logger

APP_TITLE = "每日欧洲油画壁纸"
SETUP_MARKER = "setup_done"


def generate_icon() -> Path:
    """生成程序图标。

    打包成 exe 后程序目录不可写（资源在临时目录里），所以统一放到数据目录。
    """
    target = paths.data_root() / "app.ico"
    try:
        icon_mod.write_ico(target)
    except Exception as exc:  # noqa: BLE001
        get_logger("installer").warning("图标生成失败（不影响使用）：%s", exc)
    return target


def autostart_command() -> str:
    return paths.launch_command("tray", "--quiet")


def install_shortcuts(icon: Path | None = None, name: str = APP_TITLE) -> list[Path]:
    target, _prefix = paths.launch_target()
    return create_shortcuts(
        target=target,
        arguments=paths.shortcut_arguments("gui"),
        workdir=str(paths.app_root()),
        icon=str(icon) if icon else "",
        name=name,
    )


def install(with_shortcuts: bool = True, with_autostart: bool = True) -> dict[str, Any]:
    """完成安装，返回结果摘要（供界面显示）。"""
    paths.ensure_dirs()
    save_config(load_config())
    icon = generate_icon()
    result: dict[str, Any] = {
        "config": str(paths.config_path()),
        "icon": str(icon),
        "autostart": False,
        "shortcuts": [],
    }
    if with_autostart:
        from . import autostart

        result["autostart"] = autostart.enable()
        result["autostart_detail"] = autostart.describe()
    if with_shortcuts:
        result["shortcuts"] = [str(p) for p in install_shortcuts(icon)]
    mark_setup_done()
    return result


def mark_setup_done() -> None:
    try:
        marker = paths.state_dir() / SETUP_MARKER
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("ok", encoding="utf-8")
    except OSError:
        pass


def is_setup_done() -> bool:
    return (paths.state_dir() / SETUP_MARKER).exists()


def uninstall(purge: bool = False) -> None:
    from . import autostart

    autostart.disable()
    wallpaper.set_autostart(False, "")
    remove_shortcuts(APP_TITLE)
    if purge:
        shutil.rmtree(paths.data_root(), ignore_errors=True)
