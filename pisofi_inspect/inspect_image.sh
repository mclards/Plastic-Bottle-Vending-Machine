#!/usr/bin/env bash
# Inspect the original image without replaying its filesystem journal.
set -euo pipefail
PROJECT=/mnt/d/PROJECTS_IO/Plastic-Bottle-Vending-Machine
IMAGE="$PROJECT/resources/PisoFi_Opi1&PC_v5.3.0-05-10-26_EXT.img"
OUT="$PROJECT/pisofi_inspect/analysis"
MNT=/mnt/pisofi-inspect-ro
mkdir -p "$OUT" "$MNT"
sha256sum "$IMAGE" > "$OUT/image_sha256_before.txt"
fdisk -l "$IMAGE" > "$OUT/partition_table.txt"
if ! mountpoint -q "$MNT"; then
    mount -t ext4 -o loop,ro,noload,offset=4194304 "$IMAGE" "$MNT"
fi
findmnt "$MNT" > "$OUT/mount.txt"
python3 "$PROJECT/pisofi_inspect/inventory_image.py" "$MNT" "$OUT"
