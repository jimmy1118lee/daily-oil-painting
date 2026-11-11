"""创建/删除桌面与开始菜单快捷方式。

统一走 PowerShell 的 WScript.Shell，并用 -EncodedCommand 传脚本，
这样可以完全绕开命令行与脚本编码带来的中文乱码问题。
"""

from __future__ import annotations

import base64
import subprocess
from pathlib import Path

from .util import get_logger

DEFAULT_DESCRIPTION = "每天自动更换一幅 1300-1800 年的欧洲古典油画壁纸"


def _ps_quote(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _run_powershell(script: str) -> tuple[int, str]:
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    try:
        proc = subprocess.run(
            [
                "powershell", "-NoProfile", "-NonInteractive",
                "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except (OSError, ValueError) as exc:
        get_logger("shortcuts").warning("调用 PowerShell 失败：%s", exc)
        return 1, str(exc)


def create_shortcuts(
    target: str,
    arguments: str,
    workdir: str,
    icon: str,
    name: str,
    description: str = DEFAULT_DESCRIPTION,
) -> list[Path]:
    """在桌面和开始菜单创建快捷方式，返回成功的 .lnk 路径。"""
    script = f"""
$ErrorActionPreference = 'Stop'
$ws = New-Object -ComObject WScript.Shell
$dirs = @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))
$made = @()
foreach ($d in $dirs) {{
    if ([string]::IsNullOrEmpty($d)) {{ continue }}
    $lnk = Join-Path $d {_ps_quote(name + '.lnk')}
    $sc = $ws.CreateShortcut($lnk)
    $sc.TargetPath = {_ps_quote(target)}
    $sc.Arguments = {_ps_quote(arguments)}
    $sc.WorkingDirectory = {_ps_quote(workdir)}
    if (Test-Path {_ps_quote(icon)}) {{ $sc.IconLocation = {_ps_quote(icon)} }}
    $sc.Description = {_ps_quote(description)}
    $sc.Save()
    $made += $lnk
}}
$made | ForEach-Object {{ Write-Output $_ }}
"""
    code, output = _run_powershell(script)
    made: list[Path] = []
    if code == 0:
        for line in output.splitlines():
            line = line.strip()
            if line.lower().endswith(".lnk"):
                made.append(Path(line))
    if not made:
        get_logger("shortcuts").warning("创建快捷方式失败：%s", output.strip()[:200])
    return made


def remove_shortcuts(name: str) -> None:
    """删除桌面和开始菜单里同名快捷方式。"""
    script = f"""
$ws = New-Object -ComObject WScript.Shell
foreach ($d in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {{
    if ([string]::IsNullOrEmpty($d)) {{ continue }}
    $lnk = Join-Path $d {_ps_quote(name + '.lnk')}
    if (Test-Path $lnk) {{ Remove-Item $lnk -Force; Write-Output $lnk }}
}}
"""
    _run_powershell(script)
