@echo off
cd /d "%~dp0"
py -3 -m venv .venv
if errorlevel 1 goto fail
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto fail
echo Ready. Open Start-LanDesk.cmd.
pause
exit /b 0
:fail
echo Setup failed. Install Python 3.12 or newer with Tcl/Tk and check package access.
pause
exit /b 1
