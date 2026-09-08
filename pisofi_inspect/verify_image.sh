#!/usr/bin/env bash
set -euo pipefail
PROJECT=/mnt/d/PROJECTS_IO/Plastic-Bottle-Vending-Machine
IMAGE="$PROJECT/resources/PisoFi_Opi1&PC_v5.3.0-05-10-26_EXT.img"
OUT="$PROJECT/pisofi_inspect/analysis"
if mountpoint -q /mnt/pisofi-inspect-ro; then
    umount /mnt/pisofi-inspect-ro
fi
DEVICE=$(losetup --find --show --read-only --offset 4194304 "$IMAGE")
trap 'losetup --detach "$DEVICE"' EXIT
set +e
e2fsck -fn "$DEVICE" > "$OUT/fsck_readonly.txt" 2>&1
CHECK_STATUS=$?
set -e
echo "$CHECK_STATUS" > "$OUT/fsck_exit_status.txt"
losetup --detach "$DEVICE"
trap - EXIT
sha256sum "$IMAGE" > "$OUT/image_sha256_after.txt"
cmp "$OUT/image_sha256_before.txt" "$OUT/image_sha256_after.txt"
echo 'Before/after SHA-256 matches; image unmounted.'
cat "$OUT/fsck_readonly.txt"
exit "$CHECK_STATUS"
