@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\pythonw.exe" (
  start "" ".venv\Scripts\pythonw.exe" app.py
) else (
  echo Run Setup-LanDesk.cmd first, or use LanDesk.exe.
  pause
)
