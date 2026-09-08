#!/bin/bash
set -euo pipefail
WORK=/var/tmp/ecofi-brand-v1
SOURCE=/mnt/d/PROJECTS_IO/Plastic-Bottle-Vending-Machine/resources/PisoFi_Custom_Test_v1.img
test ! -e "$WORK"
test "$(sha256sum "$SOURCE" | cut -d' ' -f1)" = 2254997707f5978e29b8e3e7a83ae15afc2e549f8c8a0cafef71fa389ca74d5b
mkdir -p "$WORK/root"
cp --sparse=always "$SOURCE" "$WORK/root.img"
mount -o loop,offset=4194304 "$WORK/root.img" "$WORK/root"
cp /usr/bin/qemu-arm-static "$WORK/root/tmp/ecofi-qemu-arm-static"
echo 'Branding staging image prepared.'
