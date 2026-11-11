@echo off
rem ============================================================
rem  Daily Oil Painting Wallpaper - installer (ASCII only)
rem  All Chinese/logic lives in src\bootstrap.py so that the
rem  console codepage can never mangle this script.
rem ============================================================
setlocal
cd /d "%~dp0"

set "PY="
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if not defined PY if exist "%ProgramFiles%\Python312\python.exe" set "PY=%ProgramFiles%\Python312\python.exe"
if not defined PY (
  where py >nul 2>nul && set "PY=py -3"
)
if not defined PY (
  where python >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo.
  echo  [ERROR] Python not found.
  echo  Please install Python 3.8+ from https://www.python.org/downloads/
  echo  and check "Add python.exe to PATH" during setup.
  echo.
  pause
  exit /b 1
)

%PY% "%~dp0src\bootstrap.py" install %*
if errorlevel 1 (
  echo.
  echo  Installation failed - see the messages above.
  pause
  exit /b 1
)
echo.
pause
