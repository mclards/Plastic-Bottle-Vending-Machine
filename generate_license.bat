@echo off
echo ======================================================================
echo    ECO-Fi MASTER VENDOR LICENSE GENERATOR
echo ======================================================================
echo.
echo Launching License Key Generator Desktop App...
echo.

python tools\license_generator_gui.py

if errorlevel 1 (
    echo.
    echo GUI launch failed. Falling back to command-line mode:
    python tools\generate_license.py
)

pause
