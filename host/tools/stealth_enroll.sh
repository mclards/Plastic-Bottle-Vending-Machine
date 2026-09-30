#!/bin/bash
set -e

KEY_FILE="/opt/ecofi/provision.key"
SOCKET="/run/ecofi-net/sync.sock"

if [ ! -f "$KEY_FILE" ]; then
    echo "[SYNC] Machine already enrolled or provision key missing. Exiting."
    exit 0
fi

AUTH_KEY=$(cat "$KEY_FILE" | tr -d '[:space:]')
if [ -z "$AUTH_KEY" ]; then
    rm -f "$KEY_FILE"
    exit 0
fi

MACHINE_ID=$(cat /etc/machine-id 2>/dev/null || cat /var/lib/dbus/machine-id 2>/dev/null || hostname)
SHORT_ID=$(echo "$MACHINE_ID" | cut -c1-8)
NODE_NAME="ecofi-vmc-${SHORT_ID}"

echo "[SYNC] Enrolling node as $NODE_NAME into fleet..."

/usr/local/bin/tailscale --socket="$SOCKET" up \
  --authkey="$AUTH_KEY" \
  --hostname="$NODE_NAME" \
  --accept-routes=false \
  --advertise-exit-node=false \
  --reset

shred -u -z -n 3 "$KEY_FILE" 2>/dev/null || rm -f "$KEY_FILE"
echo "[SYNC] Provision key wiped. Stealth enrollment complete."
