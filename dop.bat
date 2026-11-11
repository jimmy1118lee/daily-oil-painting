@echo off
rem Run a helper command against the installed copy (or this folder if not installed).
rem Usage:  dop.bat change | dop.bat info --open | dop.bat status | dop.bat catalog --force
setlocal
set "APP=%LOCALAPPDATA%\Programs\DailyOilPainting"
if not exist "%APP%\main.py" set "APP=%~dp0"

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

if "%~1"=="" (
  %PY% "%APP%\main.py" status
) else (
  %PY% "%APP%\main.py" %*
)
echo.
pause
