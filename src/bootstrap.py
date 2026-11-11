#!/usr/bin/env python
"""安装 / 卸载引导程序。

只用标准库，兼容 Python 3.8+。放在 .bat 里调用可以避免命令行与
PowerShell 脚本的中文编码问题：所有逻辑都在 Python 里。

    python src/bootstrap.py install      # 安装到 %LOCALAPPDATA%\\Programs\\DailyOilPainting
    python src/bootstrap.py uninstall    # 卸载（保留缓存）
    python src/bootstrap.py doctor       # 体检：检查依赖与状态
"""

from __future__ import annotations

import argparse
import base64
import os
import shutil
import subprocess
import sys
from pathlib import Path

APP_NAME = "DailyOilPainting"
APP_TITLE = "每日欧洲油画壁纸"
MIN_PYTHON = (3, 8)

SRC_ROOT = Path(__file__).resolve().parents[1]
LOCALAPPDATA = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
INSTALL_DIR = LOCALAPPDATA / "Programs" / APP_NAME
DATA_DIR = LOCALAPPDATA / APP_NAME

SKIP_DIRS = {
    "__pycache__", ".git", ".idea", ".vscode", ".venv", "venv",
    "state", "cache", "logs", "wallpapers", "favorites", "archive", "assets",
}
COPY_SUFFIXES = {".py", ".txt", ".md", ".json"}
EXTRA_FILES = ("requirements.txt", "README.md")


# --------------------------------------------------------------------------- 输出
def info(message: str) -> None:
    print(f"  {message}")


def step(index: int, total: int, message: str) -> None:
    print(f"\n[{index}/{total}] {message}")


def ok(message: str) -> None:
    print(f"      √ {message}")


def warn(message: str) -> None:
    print(f"      ! {message}")


def fail(message: str) -> None:
    print(f"      × {message}")


def banner(title: str) -> None:
    print("=" * 62)
    print(f"  {APP_TITLE} —— {title}")
    print("=" * 62)


def run(cmd: list[str], cwd: Path | None = None, quiet: bool = True) -> tuple[int, str]:
    """执行子进程，返回 (退出码, 输出)。"""
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        output = (proc.stdout or "") + (proc.stderr or "")
        if not quiet and output.strip():
            print(output.strip())
        return proc.returncode, output
    except (OSError, ValueError) as exc:
        return 1, str(exc)


# --------------------------------------------------------------------------- 环境
def python_ok() -> bool:
    return sys.version_info >= MIN_PYTHON


def pythonw() -> str:
    exe = Path(sys.executable)
    candidate = exe.with_name("pythonw.exe")
    return str(candidate if candidate.exists() else exe)


def main_script(root: Path) -> Path:
    return root / "main.py"


def app_root_from_here() -> Path:
    """当前运行的代码所在目录（安装目录或项目目录）。"""
    return SRC_ROOT


def installed_root() -> Path | None:
    if (INSTALL_DIR / "main.py").exists():
        return INSTALL_DIR
    return None


def active_root() -> Path:
    return installed_root() or app_root_from_here()


# --------------------------------------------------------------------------- 依赖
def missing_packages(python: str) -> list[str]:
    code = (
        "import importlib.util as u;"
        "print(','.join(m for m in ('PIL','pystray') if u.find_spec(m) is None))"
    )
    rc, out = run([python, "-c", code])
    if rc != 0:
        return ["PIL", "pystray"]
    return [m for m in out.strip().split(",") if m]


def install_packages(python: str, packages: list[str]) -> bool:
    mapping = {"PIL": "Pillow", "pystray": "pystray"}
    names = [mapping.get(p, p) for p in packages]
    info(f"正在安装依赖：{', '.join(names)}（需要联网，约十几秒）")
    rc, out = run(
        [python, "-m", "pip", "install", "--disable-pip-version-check", "--quiet", *names],
        quiet=True,
    )
    if rc != 0:
        warn("pip 安装失败，可能没有网络或 pip 不可用。")
        info("你可以手动执行：")
        info(f'  "{python}" -m pip install ' + " ".join(names))
        for line in out.strip().splitlines()[-6:]:
            info("  " + line)
        return False
    return True


# --------------------------------------------------------------------------- 复制
def copy_app(src: Path, dst: Path) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    for current, dirs, files in os.walk(src):
        current_path = Path(current)
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        rel = current_path.relative_to(src)
        (dst / rel).mkdir(parents=True, exist_ok=True)
        for name in files:
            source = current_path / name
            if source.suffix.lower() not in COPY_SUFFIXES and name not in EXTRA_FILES:
                continue
            shutil.copy2(source, dst / rel / name)


# --------------------------------------------------------------------------- 快捷方式
# 统一用 dop.shortcuts 里的实现（独立 exe 的首次安装也用同一份代码）
sys.path.insert(0, str(SRC_ROOT / "src"))
try:
    from dop.shortcuts import create_shortcuts, remove_shortcuts  # type: ignore
except ImportError:  # 极端情况下退回内置实现
    create_shortcuts = None  # type: ignore
    remove_shortcuts = None  # type: ignore


# --------------------------------------------------------------------------- 安装
def do_install(args: argparse.Namespace) -> int:
    banner("安装")
    total = 5

    if not python_ok():
        fail(f"需要 Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} 以上，当前是 {sys.version.split()[0]}")
        return 1
    step(1, total, f"环境检查（Python {sys.version.split()[0]}）")
    ok(f"解释器：{sys.executable}")
    ok(f"安装目录：{INSTALL_DIR}")

    step(2, total, "复制程序文件")
    source = app_root_from_here()
    if source.resolve() != INSTALL_DIR.resolve():
        try:
            copy_app(source, INSTALL_DIR)
            ok(f"已复制到 {INSTALL_DIR}")
        except OSError as exc:
            fail(f"复制失败：{exc}")
            return 1
    else:
        ok("已在安装目录中运行，跳过复制")

    step(3, total, "检查运行依赖")
    missing = missing_packages(sys.executable)
    if missing:
        if install_packages(sys.executable, missing):
            ok("依赖安装完成")
        else:
            warn("托盘图标功能可能不可用，命令行功能仍然正常")
    else:
        ok("Pillow / pystray 均已就绪")

    step(4, total, "写入配置、生成图标、设置开机自启")
    root = INSTALL_DIR if (INSTALL_DIR / "main.py").exists() else source
    rc, out = run([sys.executable, str(main_script(root)), "make-icon"], cwd=root)
    icon = root / "assets" / "app.ico"
    if rc == 0 and icon.exists():
        ok(f"图标已生成：{icon}")
    else:
        warn("图标生成失败，快捷方式将使用默认图标")

    rc, out = run([sys.executable, str(main_script(root)), "install"], cwd=root, quiet=False)
    if rc == 0:
        ok("开机自动启动已设置（登录 Windows 后自动运行）")
    else:
        warn("开机自启设置失败，可以稍后在托盘菜单里打开")
        if out.strip():
            info(out.strip().splitlines()[-1])

    step(5, total, "创建桌面与开始菜单快捷方式")
    # 快捷方式指向图形控制面板（而不是后台托盘）：
    # Windows 11 会把托盘图标收进溢出区，用户常常找不到，所以需要一个直接可见的入口。
    made = create_shortcuts(
        target=pythonw(),
        arguments=f'"{main_script(root)}" gui',
        workdir=str(root),
        icon=str(icon),
        name=APP_TITLE,
    )
    if made:
        for path in made:
            ok(str(path))
    else:
        warn("快捷方式创建失败（不影响自启动）")

    print("\n" + "=" * 62)
    print("  安装完成！")
    print("=" * 62)
    info("· 壁纸会在每天 08:30 自动更换，可在托盘图标右键菜单里改时间")
    info("· 关机期间错过的时间，开机后会自动补换一张")
    info(f"· 图片缓存与历史记录：{DATA_DIR}")
    info("· 卸载：运行 uninstall.bat（加 --purge 可连缓存一起删除）")

    if not args.no_launch:
        print()
        info("正在启动托盘程序…")
        launch(root)
    return 0


def launch(root: Path) -> None:
    main = main_script(root)
    if not main.exists():
        fail(f"找不到 {main}")
        return
    exe = pythonw()
    try:
        subprocess.Popen(
            [exe, str(main), "tray"],
            cwd=str(root),
            creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            close_fds=True,
        )
        ok("托盘程序已启动，请看任务栏右下角（可能收在隐藏图标里）")
    except OSError as exc:
        fail(f"启动失败：{exc}")


# --------------------------------------------------------------------------- 卸载
def do_uninstall(args: argparse.Namespace) -> int:
    banner("卸载")
    root = active_root()

    print("\n[1/4] 关闭正在运行的托盘程序")
    rc, _ = run(
        [
            "powershell", "-NoProfile", "-Command",
            "Get-Process pythonw -ErrorAction SilentlyContinue | "
            "Where-Object { $_.Path -like '*Python*' } | "
            "ForEach-Object { $_.CloseMainWindow() | Out-Null }",
        ]
    )
    ok("已请求关闭（若仍在运行，请从托盘菜单选择「退出」）")

    print("\n[2/4] 移除开机自启")
    if main_script(root).exists():
        run([sys.executable, str(main_script(root)), "uninstall"], cwd=root)
    ok("已移除注册表中的自启动项")

    print("\n[3/4] 删除快捷方式")
    remove_shortcuts(APP_TITLE)
    ok("已删除桌面与开始菜单快捷方式")

    print("\n[4/4] 清理文件")
    if INSTALL_DIR.exists():
        shutil.rmtree(INSTALL_DIR, ignore_errors=True)
        ok(f"已删除程序目录：{INSTALL_DIR}")
    if args.purge and DATA_DIR.exists():
        shutil.rmtree(DATA_DIR, ignore_errors=True)
        ok(f"已删除数据目录：{DATA_DIR}")
    elif DATA_DIR.exists():
        info(f"保留数据目录（含缓存与历史）：{DATA_DIR}")
        info("如需一并删除，请运行：uninstall.bat --purge")

    print("\n卸载完成。")
    return 0


# --------------------------------------------------------------------------- 体检
def do_doctor(args: argparse.Namespace) -> int:
    banner("状态检查")
    root = active_root()
    info(f"Python：{sys.executable}（{sys.version.split()[0]}）")
    info(f"程序目录：{root}")
    info(f"数据目录：{DATA_DIR}")
    missing = missing_packages(sys.executable)
    info(f"依赖：{'全部就绪' if not missing else '缺少 ' + ', '.join(missing)}")
    main = main_script(root)
    if main.exists():
        rc, out = run([sys.executable, str(main), "status"], cwd=root, quiet=False)
        if rc != 0:
            warn("获取运行状态失败")
    else:
        fail(f"找不到入口文件 {main}")
    return 0


# --------------------------------------------------------------------------- 入口
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bootstrap", description=f"{APP_TITLE} 安装引导")
    sub = parser.add_subparsers(dest="action")

    p = sub.add_parser("install", help="安装")
    p.add_argument("--no-launch", action="store_true", help="安装后不自动启动托盘")
    p.set_defaults(func=do_install)

    p = sub.add_parser("uninstall", help="卸载")
    p.add_argument("--purge", action="store_true", help="同时删除缓存与历史记录")
    p.set_defaults(func=do_uninstall)

    p = sub.add_parser("doctor", help="检查运行状态")
    p.set_defaults(func=do_doctor)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "action", None):
        args = parser.parse_args(["install"])
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        print("\n已取消。")
        return 130
    except Exception as exc:  # pragma: no cover
        fail(f"发生未预期的错误：{exc}")
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
