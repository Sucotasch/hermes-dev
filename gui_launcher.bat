@echo off
cd /d "%~dp0"
python standalone\gui.py %*
if errorlevel 1 (
    echo.
    echo GUI exited with error. Make sure PyQt5 is installed:
    echo   pip install PyQt5
    echo.
    pause
)
