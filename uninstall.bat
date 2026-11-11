@echo off
rem Uninstall Daily Oil Painting Wallpaper. Add --purge to delete cached images too.
setlocal
cd /d "%~dp0"

set "PY="
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PY (
  where py >nul 2>nul && set "PY=py -3"
)
if not defined PY (
  where python >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo [ERROR] Python not found.
  pause
  exit /b 1
)

%PY% "%~dp0src\bootstrap.py" uninstall %*
echo.
pause
