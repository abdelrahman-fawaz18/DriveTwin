@echo off
cd /d "%~dp0"
where pythonw.exe >nul 2>nul
if errorlevel 1 (
  echo Python for Windows is required. See docs\setup\windows-wsl.md.
  pause
  exit /b 1
)
start "" pythonw.exe scripts\launch_drivetwin.py
