"""开机自启与自我修复（用计划任务，不只靠注册表 Run 键）。

踩过的坑：Windows 的"启动项优化"（或某些清理/管家类工具）会把
``HKCU\\...\\Run`` 里的项在 ``StartupApproved\\Run`` 里标记成禁用
（值 ``03 00 ...``）。注册表项还在，看起来一切正常，但开机**根本不会执行**。
用户机器上就这样静默失效过一次：中间重启了几次，托盘再也没起来，
桌面壁纸被改回系统默认后也没人管。

计划任务不受那个开关影响，而且可以加"每 N 分钟检查一次"的触发器，
进程意外退出也能自己回来。所以这里用两个计划任务：

* ``DailyOilPainting``          —— 登录时触发
* ``DailyOilPainting-Watchdog`` —— 每 10 分钟触发，发现托盘不在就跑起来

两个任务都执行同一个短命的 watchdog 脚本（几十毫秒内退出），
托盘本身由它"派生"出去，这样不会有"任务运行超过 72 小时被结束"的问题。
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from . import paths
from .util import get_logger

LOGON_TASK = "DailyOilPainting"
WATCHDOG_TASK = "DailyOilPainting-Watchdog"
WATCHDOG_MINUTES = 10
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "DailyOilPainting"
STARTUP_APPROVED_KEY = (
    r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"
)
# 02 开头 = 启用；03 开头 = 被"启动项优化"禁用
_ENABLED_VALUE = bytes([0x02] + [0] * 11)


def _run(args: list[str], timeout: int = 60) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except (OSError, ValueError) as exc:
        return 1, str(exc)


# --------------------------------------------------------------------------- 看门狗脚本
def shim_path() -> Path:
    return paths.data_root() / "watchdog.bat"


def _write_shim() -> Path:
    """写一个最小的 .bat 来执行 watchdog。

    这样计划任务的动作只是一个普通文件路径，不含任何内部引号，
    彻底避免 schtasks 解析参数时的各种意外。
    """
    argv = paths.app_argv("watchdog")
    quoted = " ".join(f'"{a}"' if " " in a else a for a in argv)
    content = "@echo off\r\n" + quoted + "\r\n"
    target = shim_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding="ascii", newline="") as fh:
        fh.write(content)
    return target


# --------------------------------------------------------------------------- 计划任务
def _create_task(name: str, shim: Path, schedule: list[str]) -> bool:
    code, out = _run(
        ["schtasks", "/create", "/tn", name, "/tr", str(shim), *schedule, "/f"]
    )
    if code != 0:
        get_logger("autostart").warning(
            "创建计划任务 %s 失败：%s", name, out.strip()[:200]
        )
        return False
    return True


def _delete_task(name: str) -> None:
    _run(["schtasks", "/delete", "/tn", name, "/f"])


def _task_exists(name: str) -> bool:
    code, _out = _run(["schtasks", "/query", "/tn", name])
    return code == 0


def tasks_installed() -> bool:
    return _task_exists(LOGON_TASK) and _task_exists(WATCHDOG_TASK)


# --------------------------------------------------------------------------- Run 键（备用）
def _reg():
    import winreg

    return winreg


def _set_run_value(command: str | None) -> None:
    winreg = _reg()
    try:
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            if command:
                winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, command)
            else:
                try:
                    winreg.DeleteValue(key, RUN_VALUE)
                except FileNotFoundError:
                    pass
    except OSError as exc:
        get_logger("autostart").debug("写 Run 键失败：%s", exc)


def _ensure_startup_approved_enabled() -> None:
    """把"启动项优化"留下的禁用标记改回启用。"""
    winreg = _reg()
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, STARTUP_APPROVED_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_BINARY, _ENABLED_VALUE)
    except FileNotFoundError:
        pass
    except OSError as exc:
        get_logger("autostart").debug("修正启动项开关失败：%s", exc)


# --------------------------------------------------------------------------- 对外接口
def enable() -> bool:
    """开启开机自启（计划任务为主，Run 键为辅）。"""
    shim = _write_shim()
    ok_logon = _create_task(LOGON_TASK, shim, ["/sc", "onlogon"])
    ok_watch = _create_task(
        WATCHDOG_TASK, shim, ["/sc", "minute", "/mo", str(WATCHDOG_MINUTES)]
    )
    _set_run_value(paths.launch_command("tray", "--quiet"))
    _ensure_startup_approved_enabled()
    ok = ok_logon and ok_watch
    if ok:
        get_logger("autostart").info(
            "已设置开机自启：登录时启动，并且每 %d 分钟检查一次", WATCHDOG_MINUTES
        )
    return ok


def disable() -> None:
    _delete_task(LOGON_TASK)
    _delete_task(WATCHDOG_TASK)
    _set_run_value(None)
    try:
        shim_path().unlink(missing_ok=True)
    except OSError:
        pass
    get_logger("autostart").info("已关闭开机自启")


def is_enabled() -> bool:
    """计划任务或（启用状态的）Run 键任一存在即视为已开启。"""
    if tasks_installed():
        return True
    winreg = _reg()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, RUN_VALUE)
            if not str(value).strip():
                return False
    except OSError:
        return False
    # Run 键存在但可能被启动项优化禁用
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_APPROVED_KEY) as key:
            raw, _ = winreg.QueryValueEx(key, RUN_VALUE)
            if isinstance(raw, (bytes, bytearray)) and raw and raw[0] in (0x03, 0x07):
                return False
    except OSError:
        pass
    return True


def describe() -> str:
    parts = []
    parts.append(f"登录任务：{'有' if _task_exists(LOGON_TASK) else '无'}")
    parts.append(f"定时看护：{'有' if _task_exists(WATCHDOG_TASK) else '无'}")
    parts.append(f"注册表项：{'有' if _run_value() else '无'}")
    return "  ".join(parts)


def _run_value() -> str:
    winreg = _reg()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, RUN_VALUE)
            return str(value)
    except OSError:
        return ""
