#!/usr/bin/env bash
set -euo pipefail
PROJECT=/mnt/d/PROJECTS_IO/Plastic-Bottle-Vending-Machine
SOURCE="$PROJECT/resources/PisoFi_Opi1&PC_v5.3.0-05-10-26_EXT.img"
WORK=/var/tmp/pisofi-custom-test-v1
test ! -e "$WORK/root.img"
mkdir -p "$WORK" "$WORK/root" "$PROJECT/pisofi_inspect/rebuild/private"
chmod 700 "$WORK"
echo 'Copying original into isolated build workspace...'
cp --sparse=always "$SOURCE" "$WORK/root.img"
sha256sum "$WORK/root.img" > "$WORK/source.sha256"
grep -q '^d72e56563aca1af9d5e6298fa9bea5fec716a3d4d3565f6d8eabe0398f52f1dd ' "$WORK/source.sha256"
mount -t ext4 -o loop,offset=4194304 "$WORK/root.img" "$WORK/root"
cp /usr/bin/qemu-arm-static "$WORK/root/tmp/ecofi-qemu-arm-static"
chroot "$WORK/root" /tmp/ecofi-qemu-arm-static /usr/bin/php7.0 -n -v
echo 'Isolated image ready.'
