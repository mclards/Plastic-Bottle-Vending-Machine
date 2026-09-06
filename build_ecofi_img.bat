@echo off
echo ======================================================================
echo    ECO-FI OS IMAGE BUILDER ^& CUSTOMIZER
echo    Target: resources\EcoFi_Opi_v2.0.img
echo ======================================================================
echo.
echo Launching WSL build script to inject Eco-Fi software stack...
echo.

wsl -d Ubuntu -u root -- bash /mnt/d/PROJECTS_IO/Plastic-Bottle-Vending-Machine/build_ecofi_img.sh

if errorlevel 1 exit /b %errorlevel%
echo.
pause
