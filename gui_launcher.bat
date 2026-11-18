@echo off
REM SPDX-License-Identifier: GPL-3.0-or-later
REM Launches the PyQt5 GUI, so it follows the GUI's license — see THIRD_PARTY_NOTICES.md.
cd /d "%~dp0"
python standalone\gui.py %*
if errorlevel 1 (
    echo.
    echo GUI exited with error. Make sure PyQt5 is installed:
    echo   pip install PyQt5
    echo.
    pause
)
