@echo off
title EcoVendo Fleet Manager
cd /d "%~dp0"

where python >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo Python was not found in PATH. Please ensure Python is installed.
    pause
    exit /b 1
)

python tools\fleet_manager_gui.py
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo EcoVendo Fleet Manager exited with an error.
    pause
)
