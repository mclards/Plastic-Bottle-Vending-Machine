@echo off
echo ======================================================================
echo    VMC ECO-VENDO OS IMAGE BUILDER ^& CUSTOMIZER
echo    Eco-Vendo: An Empty Bottle-Initiated Internet Access Vending System
set /p ECOFI_VERSION=<"%~dp0VERSION"
echo    Target: resources\EcoFi_Opi_v%ECOFI_VERSION%.img
echo ======================================================================
echo.
echo Launching WSL build script to inject VMC ECO-VENDO software stack...
echo.

wsl -d Ubuntu -u root -- bash /mnt/d/PROJECTS_IO/Plastic-Bottle-Vending-Machine/build_ecofi_img.sh

if errorlevel 1 exit /b %errorlevel%
echo.
pause
