@echo off
title EcoVendo Fleet Command & Manager
cd /d "%~dp0"

if exist "dist\EcoVendoFleetManager\EcoVendoFleetManager.exe" (
    start "" "dist\EcoVendoFleetManager\EcoVendoFleetManager.exe"
    exit /b 0
)

python tools\fleet_manager_gui.py
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo An error occurred running Fleet Manager.
    pause
)
