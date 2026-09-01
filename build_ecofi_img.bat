@echo off
echo ======================================================================
echo    ECO-FI OS IMAGE BUILDER & CUSTOMIZER
echo    Target: resources\EcoFi_Opi_v1.0.img
echo ======================================================================
echo.
echo Launching WSL build script to inject Eco-Fi software stack...
echo.

wsl -u root bash /mnt/d/PROJECTS_IO/Plastic-Bottle-Vending-Machine/build_ecofi_img.sh

echo.
pause
