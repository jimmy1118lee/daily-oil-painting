"""Windows 集成：显示器枚举（含高 DPI）、设置壁纸、开机自启。

壁纸设置走 SystemParametersInfoW(SPI_SETDESKWALLPAPER)，
先在注册表里写好拉伸方式（填充/适应/居中/跨屏），再调用系统 API。
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from pathlib import Path
from typing import Any, Optional

from .util import get_logger

SPI_SETDESKWALLPAPER = 20
SPIF_UPDATEINIFILE = 0x01
SPIF_SENDCHANGE = 0x02
SPI_FLAGS = SPIF_UPDATEINIFILE | SPIF_SENDCHANGE
MONITORINFOF_PRIMARY = 0x1
MONITOR_DEFAULTTONEAREST = 0x2

DESKTOP_KEY = r"Control Panel\Desktop"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "DailyOilPainting"

# 注册表 WallpaperStyle 取值
WALLPAPER_STYLES = {
    "center": "0",
    "tile": "0",
    "stretch": "2",
    "fit": "6",
    "fill": "10",
    "span": "22",
}

IS_WINDOWS = sys.platform.startswith("win")


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", RECT),
        ("rcWork", RECT),
        ("dwFlags", wintypes.DWORD),
        ("szDevice", wintypes.WCHAR * 32),
    ]


MONITORENUMPROC = ctypes.WINFUNCTYPE(
    ctypes.c_int,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.POINTER(RECT),
    wintypes.LPARAM,
)


# --------------------------------------------------------------------------- DPI / 显示器
_dpi_ready = False


def set_dpi_aware() -> None:
    """必须在查询显示器之前调用，否则高 DPI 屏会拿到缩放后的逻辑分辨率。

    幂等：同一进程里重复调用只生效一次（系统也只允许设置一次）。
    """
    global _dpi_ready
    if _dpi_ready or not IS_WINDOWS:
        _dpi_ready = True
        return
    _dpi_ready = True
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_DPI_AWARE
        return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass


def get_monitors() -> list[dict[str, Any]]:
    """返回所有显示器的像素矩形。

    这里主动确保 DPI 感知已开启：任何入口（命令行、托盘、界面、脚本）
    只要走到这里，拿到的一定是物理分辨率。否则 4K 屏会得到 2560×1440
    这类逻辑尺寸，渲染出来的壁纸会被系统放大、明显变糊。
    """
    set_dpi_aware()
    if not IS_WINDOWS:
        return [_fallback_monitor()]
    user32 = ctypes.windll.user32
    monitors: list[dict[str, Any]] = []

    def _callback(hmon, _hdc, _rect, _data):
        info = MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(MONITORINFOEXW)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
            r = info.rcMonitor
            monitors.append(
                {
                    "left": int(r.left),
                    "top": int(r.top),
                    "right": int(r.right),
                    "bottom": int(r.bottom),
                    "width": int(r.right - r.left),
                    "height": int(r.bottom - r.top),
                    "primary": bool(info.dwFlags & MONITORINFOF_PRIMARY),
                    "device": str(info.szDevice),
                }
            )
        return 1

    try:
        user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(_callback), 0)
    except (AttributeError, OSError) as exc:
        get_logger("wallpaper").warning("枚举显示器失败：%s", exc)
    if not monitors:
        monitors = [_fallback_monitor()]
    monitors.sort(key=lambda m: (not m["primary"], m["left"], m["top"]))
    return monitors


def _fallback_monitor() -> dict[str, Any]:
    width, height = 1920, 1080
    if IS_WINDOWS:
        try:
            user32 = ctypes.windll.user32
            width = user32.GetSystemMetrics(0) or width
            height = user32.GetSystemMetrics(1) or height
        except (AttributeError, OSError):
            pass
    return {
        "left": 0, "top": 0, "right": width, "bottom": height,
        "width": width, "height": height, "primary": True, "device": "DISPLAY",
    }


def describe_monitors(monitors: list[dict]) -> str:
    parts = []
    for m in monitors:
        tag = "主屏" if m.get("primary") else "副屏"
        parts.append(f"{tag} {m['width']}×{m['height']}" + ("" if m.get("primary") else f"@{m['left']},{m['top']}"))
    return " + ".join(parts)


# --------------------------------------------------------------------------- 设置壁纸
def _winreg():
    import winreg

    return winreg


def _set_desktop_value(name: str, value: str) -> None:
    winreg = _winreg()
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, DESKTOP_KEY, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)


def set_wallpaper(image_path: Path, style: str = "fill") -> None:
    """把 image_path 设为桌面壁纸。style: fill/fit/center/stretch/span。"""
    if not IS_WINDOWS:
        raise RuntimeError("设置壁纸仅支持 Windows")
    path = Path(image_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"壁纸文件不存在：{path}")
    style_key = (style or "fill").lower()
    try:
        _set_desktop_value("WallpaperStyle", WALLPAPER_STYLES.get(style_key, "10"))
        _set_desktop_value("TileWallpaper", "1" if style_key == "tile" else "0")
    except OSError as exc:
        get_logger("wallpaper").warning("写入壁纸样式注册表失败：%s", exc)

    user32 = ctypes.windll.user32
    user32.SystemParametersInfoW.argtypes = [
        wintypes.UINT, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT
    ]
    user32.SystemParametersInfoW.restype = wintypes.BOOL
    if not user32.SystemParametersInfoW(SPI_SETDESKWALLPAPER, 0, str(path), SPI_FLAGS):
        raise ctypes.WinError(ctypes.get_last_error())
    get_logger("wallpaper").info("壁纸已设置：%s（样式 %s）", path.name, style_key)


def current_wallpaper() -> Optional[str]:
    if not IS_WINDOWS:
        return None
    try:
        winreg = _winreg()
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, DESKTOP_KEY) as key:
            value, _ = winreg.QueryValueEx(key, "Wallpaper")
            return str(value)
    except OSError:
        return None


# --------------------------------------------------------------------------- 开机自启
def set_autostart(enabled: bool, command: str) -> bool:
    """通过 HKCU\\...\\Run 注册开机自启动（无需管理员权限）。"""
    if not IS_WINDOWS:
        return False
    winreg = _winreg()
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, command)
            else:
                try:
                    winreg.DeleteValue(key, RUN_VALUE)
                except FileNotFoundError:
                    pass
        return True
    except OSError as exc:
        get_logger("wallpaper").warning("设置开机自启失败：%s", exc)
        return False


def is_autostart_enabled() -> bool:
    if not IS_WINDOWS:
        return False
    winreg = _winreg()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, RUN_VALUE)
            return bool(str(value).strip())
    except OSError:
        return False


# --------------------------------------------------------------------------- 按显示器设置壁纸
# Windows 11 并不总是遵守注册表里的 WallpaperStyle=22（跨屏）。实测它会把整张
# 跨屏大图分别缩放进每一块屏幕，所以多屏场景改用 IDesktopWallpaper COM 接口，
# 给每块屏单独指定一张尺寸完全匹配的图。
class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def of(cls, text: str) -> "_GUID":
        raw = text.strip().strip("{}")
        a, b, c, d, e = raw.split("-")
        return cls(int(a, 16), int(b, 16), int(c, 16), (ctypes.c_ubyte * 8)(*bytes.fromhex(d + e)))


CLSID_DESKTOP_WALLPAPER = "{C2CF3110-460E-4FC1-B9D0-8A1C0C9CC4BD}"
IID_IDESKTOP_WALLPAPER = "{B92B56A9-8B55-4E14-9A89-0199BBB6F93B}"
# 这个类是外壳提供的本地服务器，用 CLSCTX_INPROC_SERVER 会报 REGDB_E_CLASSNOTREG
CLSCTX_ALL = 0x17
# DESKTOP_WALLPAPER_POSITION 枚举（注意与注册表里的 WallpaperStyle 取值不同）
COM_POSITIONS = {
    "center": 0, "tile": 1, "stretch": 2, "fit": 3, "fill": 4, "span": 5,
}
# IDesktopWallpaper 的虚表下标：前 3 个是 IUnknown，之后按
# SetWallpaper, GetWallpaper, GetMonitorDevicePathAt, GetMonitorDevicePathCount,
# GetMonitorRECT, SetBackgroundColor, GetBackgroundColor, SetPosition, ...
_VT_RELEASE = 2
_VT_SET_WALLPAPER = 3
_VT_GET_WALLPAPER = 4
_VT_DEVICE_AT = 5
_VT_COUNT = 6
_VT_MONITOR_RECT = 7
_VT_SET_POSITION = 10


class _ComObject:
    """最小可用的 COM 包装：够调用 IDesktopWallpaper 就行。"""

    def __init__(self, pointer: ctypes.c_void_p) -> None:
        self.pointer = pointer
        self.vtable = ctypes.cast(
            pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))
        ).contents

    def method(self, index: int, restype, *argtypes):
        prototype = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
        return prototype(self.vtable[index])

    def release(self) -> None:
        try:
            self.method(_VT_RELEASE, ctypes.c_ulong)(self.pointer)
        except (OSError, ValueError):
            pass


def _create_desktop_wallpaper() -> Optional[_ComObject]:
    if not IS_WINDOWS:
        return None
    ole32 = ctypes.windll.ole32
    ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    ole32.CoInitializeEx.restype = ctypes.c_long
    ole32.CoCreateInstance.argtypes = [
        ctypes.POINTER(_GUID), ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p),
    ]
    ole32.CoCreateInstance.restype = ctypes.c_long
    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    ole32.CoTaskMemFree.restype = None

    # 工作线程里调用需要先初始化 COM；已初始化会返回 RPC_E_CHANGED_MODE，可忽略
    try:
        ole32.CoInitializeEx(None, 0x2)  # COINIT_APARTMENTTHREADED
    except OSError:
        pass

    clsid = _GUID.of(CLSID_DESKTOP_WALLPAPER)
    iid = _GUID.of(IID_IDESKTOP_WALLPAPER)
    pointer = ctypes.c_void_p()
    hr = ole32.CoCreateInstance(
        ctypes.byref(clsid), None, CLSCTX_ALL, ctypes.byref(iid), ctypes.byref(pointer)
    )
    if hr != 0 or not pointer:
        get_logger("wallpaper").debug("IDesktopWallpaper 不可用（hr=0x%08X）", hr & 0xFFFFFFFF)
        return None
    return _ComObject(pointer)


def _com_monitors(shell: _ComObject) -> list[tuple[str, tuple[int, int, int, int]]]:
    count = wintypes.UINT()
    if shell.method(_VT_COUNT, ctypes.c_long, ctypes.POINTER(wintypes.UINT))(
        shell.pointer, ctypes.byref(count)
    ) != 0:
        return []
    ole32 = ctypes.windll.ole32
    found: list[tuple[str, tuple[int, int, int, int]]] = []
    for index in range(count.value):
        device = wintypes.LPWSTR()
        hr = shell.method(
            _VT_DEVICE_AT, ctypes.c_long, wintypes.UINT, ctypes.POINTER(wintypes.LPWSTR)
        )(shell.pointer, index, ctypes.byref(device))
        if hr != 0 or not device:
            continue
        rect = RECT()
        shell.method(_VT_MONITOR_RECT, ctypes.c_long, wintypes.LPCWSTR, ctypes.POINTER(RECT))(
            shell.pointer, device, ctypes.byref(rect)
        )
        found.append((str(device.value), (rect.left, rect.top, rect.right, rect.bottom)))
        ole32.CoTaskMemFree(device)
    return found


def describe_wallpapers() -> list[dict[str, Any]]:
    """读回系统为每块显示器记录的壁纸路径（自检用）。"""
    if not IS_WINDOWS:
        return []
    shell = _create_desktop_wallpaper()
    if shell is None:
        return []
    try:
        ole32 = ctypes.windll.ole32
        getter = shell.method(
            _VT_GET_WALLPAPER, ctypes.c_long, wintypes.LPCWSTR, ctypes.POINTER(wintypes.LPWSTR)
        )
        result: list[dict[str, Any]] = []
        for device, rect in _com_monitors(shell):
            path = wintypes.LPWSTR()
            hr = getter(shell.pointer, device, ctypes.byref(path))
            result.append(
                {
                    "device": device,
                    "rect": rect,
                    "wallpaper": str(path.value) if hr == 0 and path else None,
                }
            )
            if path:
                ole32.CoTaskMemFree(path)
        return result
    except (OSError, ValueError) as exc:
        get_logger("wallpaper").debug("读取逐屏壁纸失败：%s", exc)
        return []
    finally:
        shell.release()


def set_wallpapers_per_monitor(
    assignments: list[tuple[dict, Path]], position: str = "fill"
) -> bool:
    """给每块显示器单独设置一张壁纸。

    assignments 形如 [(显示器字典, 图片路径)]，按显示器矩形与系统枚举结果匹配。
    成功返回 True；若接口不可用返回 False，调用方应回退到传统方式。
    """
    if not IS_WINDOWS or not assignments:
        return False
    shell = _create_desktop_wallpaper()
    if shell is None:
        return False
    try:
        com_monitors = _com_monitors(shell)
        if not com_monitors:
            return False
        by_rect: dict[tuple[int, int, int, int], str] = {}
        for monitor, image in assignments:
            rect = (monitor["left"], monitor["top"], monitor["right"], monitor["bottom"])
            by_rect[rect] = str(Path(image).resolve())
        fallback = next(iter(by_rect.values()))
        setter = shell.method(_VT_SET_WALLPAPER, ctypes.c_long, wintypes.LPCWSTR, wintypes.LPCWSTR)
        shell.method(_VT_SET_POSITION, ctypes.c_long, ctypes.c_int)(
            shell.pointer, COM_POSITIONS.get((position or "fill").lower(), 4)
        )
        applied = 0
        unmatched: list[tuple[int, int, int, int]] = []
        for device, rect in com_monitors:
            target = by_rect.get(rect)
            if target is None:
                unmatched.append(rect)
                target = fallback
            if setter(shell.pointer, device, target) == 0:
                applied += 1
        if unmatched:
            get_logger("wallpaper").debug("有 %d 块显示器未匹配到图片，已用主屏图填充", len(unmatched))
        get_logger("wallpaper").info("已按显示器分别设置壁纸：%d/%d 块", applied, len(com_monitors))
        return applied > 0
    except (OSError, ValueError) as exc:
        get_logger("wallpaper").warning("按显示器设置壁纸失败：%s", exc)
        return False
    finally:
        shell.release()


def autostart_command(pythonw: str, script: str) -> str:
    return f'"{pythonw}" "{script}" tray --quiet'


# --------------------------------------------------------------------------- 托盘图标常显
def promote_tray_icon(exe_name: str = "pythonw.exe") -> list[str]:
    """把通知区域里的图标设为"常显"。

    Windows 11 默认把新出现的托盘图标收进"隐藏的图标"溢出区。
    系统会在 ``HKCU\\Control Panel\\NotifyIconSettings`` 下为每个图标建一个子项，
    把里面的 IsPromoted 设为 1 就等于在设置里打开了"显示该图标"。

    注意：只有系统已经登记过这个图标（用户至少展开过一次 ^ 菜单）才会有子项，
    所以这里按 ExecutablePath 反查，查不到就返回空列表，由界面提示用户先展开一次。
    """
    if not IS_WINDOWS:
        return []
    winreg = _winreg()
    promoted: list[str] = []
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\NotifyIconSettings") as root:
            count = winreg.QueryInfoKey(root)[0]
            for index in range(count):
                sub = winreg.EnumKey(root, index)
                try:
                    with winreg.OpenKey(root, sub) as key:
                        try:
                            exe, _ = winreg.QueryValueEx(key, "ExecutablePath")
                        except FileNotFoundError:
                            continue
                except OSError:
                    continue
                if str(exe).lower().endswith(exe_name.lower()):
                    try:
                        with winreg.OpenKey(
                            root, sub, 0, winreg.KEY_SET_VALUE
                        ) as key:
                            winreg.SetValueEx(key, "IsPromoted", 0, winreg.REG_DWORD, 1)
                        promoted.append(sub)
                    except OSError as exc:
                        get_logger("wallpaper").warning("写入图标常显设置失败：%s", exc)
    except FileNotFoundError:
        return []
    except OSError as exc:
        get_logger("wallpaper").warning("读取通知区域设置失败：%s", exc)
        return []
    if promoted:
        get_logger("wallpaper").info("已把 %d 个托盘图标设为常显", len(promoted))
    return promoted
