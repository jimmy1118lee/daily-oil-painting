"""路径解析：程序目录（可执行文件所在）与用户数据目录分离。

程序目录  = 代码所在目录（安装后位于 %LOCALAPPDATA%\\Programs\\DailyOilPainting），
            打包成独立 exe 时就是 exe 自己所在目录。
数据目录  = %LOCALAPPDATA%\\DailyOilPainting（缓存、历史、日志、壁纸成品）
可用环境变量 DOP_DATA_DIR 覆盖数据目录（便于测试）。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from .util import APP_NAME


def is_frozen() -> bool:
    """是不是被 PyInstaller 之类的工具打包成了独立可执行文件。"""
    return bool(getattr(sys, "frozen", False))


def app_root() -> Path:
    """程序所在目录（含 main.py 的那一层，或 exe 所在目录）。"""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def resource_path(relative: str) -> Path:
    """读取打包进程序的资源（例如图标）。

    打包后资源被解到临时目录 _MEIPASS，不能再按源码目录去找。
    """
    if is_frozen():
        base = Path(getattr(sys, "_MEIPASS", app_root()))
        return base / relative
    return app_root() / relative


def data_root() -> Path:
    override = os.environ.get("DOP_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return Path(base) / APP_NAME


def state_dir() -> Path:
    return data_root() / "state"


def cache_dir() -> Path:
    return data_root() / "cache"


def log_dir() -> Path:
    return data_root() / "logs"


def wallpaper_dir() -> Path:
    return data_root() / "wallpapers"


def favorites_dir() -> Path:
    return data_root() / "favorites"


def archive_dir() -> Path:
    return data_root() / "archive"


def config_path() -> Path:
    return data_root() / "config.json"


def catalog_path() -> Path:
    return state_dir() / "catalog.json"


def history_path() -> Path:
    return state_dir() / "history.json"


def current_path() -> Path:
    return state_dir() / "current.json"


def favorites_index_path() -> Path:
    return state_dir() / "favorites.json"


def skip_path() -> Path:
    """记录取图失败的作品，避免每天重复去试同一张。"""
    return state_dir() / "skipped.json"


def health_path() -> Path:
    """记录各图源最近的取图成败，用于给故障图源降权。"""
    return state_dir() / "source_health.json"


def log_path() -> Path:
    return log_dir() / "dop.log"


def ensure_dirs() -> None:
    for d in (
        data_root(),
        state_dir(),
        cache_dir(),
        log_dir(),
        wallpaper_dir(),
        favorites_dir(),
    ):
        d.mkdir(parents=True, exist_ok=True)


def pythonw() -> str:
    """返回 pythonw.exe 路径（无控制台窗口）；打包后返回 exe 自己。"""
    if is_frozen():
        return str(Path(sys.executable).resolve())
    exe = Path(sys.executable)
    cand = exe.with_name("pythonw.exe")
    if cand.exists():
        return str(cand)
    return str(exe)


def entry_script() -> Path:
    return app_root() / "main.py"


def launch_command(*args: str) -> str:
    """拼出"启动本程序并带上参数"的命令行，用于开机自启与快捷方式。"""
    if is_frozen():
        parts = [f'"{Path(sys.executable).resolve()}"', *args]
    else:
        parts = [f'"{pythonw()}"', f'"{entry_script()}"', *args]
    return " ".join(parts)


def app_argv(*args: str) -> list[str]:
    """启动本程序所需的 argv。打包后就是 exe 自己，未打包时是 pythonw + main.py。"""
    if is_frozen():
        return [str(Path(sys.executable).resolve()), *args]
    return [pythonw(), str(entry_script()), *args]


def launch_target() -> tuple[str, str]:
    """快捷方式要用到的 (目标程序, 参数前缀)。"""
    if is_frozen():
        return str(Path(sys.executable).resolve()), ""
    return pythonw(), f'"{entry_script()}"'


def shortcut_arguments(*args: str) -> str:
    """快捷方式的参数（已带上前缀）。"""
    _target, prefix = launch_target()
    return (prefix + " " + " ".join(args)).strip()
