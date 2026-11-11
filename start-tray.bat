@echo off
rem Start the tray program (no console window). Safe to double-click repeatedly:
rem a named mutex guarantees only one instance runs.
setlocal
set "APP=%LOCALAPPDATA%\Programs\DailyOilPainting"
if not exist "%APP%\main.py" set "APP=%~dp0"

set "PYW=%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe"
if not exist "%PYW%" set "PYW=pythonw"
where %PYW% >nul 2>nul
if errorlevel 1 set "PYW=python"

start "" "%PYW%" "%APP%\main.py" tray
exit /b 0
