#!/usr/bin/env bash
# Reproduce static inspection. Requires Ubuntu WSL with root mount permissions.
set -euo pipefail
DIR=/mnt/d/PROJECTS_IO/Plastic-Bottle-Vending-Machine/pisofi_inspect
cleanup() {
    if mountpoint -q /mnt/pisofi-inspect-ro; then
        umount /mnt/pisofi-inspect-ro
    fi
}
trap cleanup EXIT
bash "$DIR/inspect_image.sh"
python3 "$DIR/extract_evidence.py"
python3 "$DIR/deep_inventory.py"
python3 "$DIR/enrich_evidence.py"
python3 "$DIR/index_sources.py"
python3 "$DIR/decode_embedded.py"
bash "$DIR/verify_image.sh"
