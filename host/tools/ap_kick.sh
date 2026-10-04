#!/bin/sh
# ==============================================================================
# VMC ECO-VENDO - Access Point Client Disconnect / Deauth Hook
# Invoked when a client's time expires, session is revoked, or client is disconnected.
#
# Arguments:
#   $1 = MAC Address (e.g. "AA:BB:CC:DD:EE:FF")
#   $2 = IP Address  (e.g. "10.0.1.50") [optional]
# ==============================================================================

MAC="$1"
IP="$2"

if [ -z "$MAC" ] || [ "$MAC" = "00:00:00:00:00:00" ]; then
    exit 0
fi

# 1. Immediately drop the ARP neighbor entry on LAN interface
if [ -n "$IP" ]; then
    ip neigh del "$IP" dev eth1 2>/dev/null || true
fi

# 2. Managed AP Kick Implementations (Optional / Configurable)
# If your external Access Point runs OpenWrt, MikroTik RouterOS, or hostapd,
# configure the corresponding command below:

# --- Option A: OpenWrt AP (via SSH key) ---
# OPENWRT_IP="10.0.0.2"
# ssh -i /root/.ssh/id_rsa -o ConnectTimeout=2 -o StrictHostKeyChecking=no root@${OPENWRT_IP} \
#     "ubus call hostapd.wlan0 del_client '{\"addr\":\"$MAC\"}' 2>/dev/null; ubus call hostapd.wlan1 del_client '{\"addr\":\"$MAC\"}' 2>/dev/null" &

# --- Option B: MikroTik RouterOS (via SSH key) ---
# MIKROTIK_IP="10.0.0.2"
# ssh -i /root/.ssh/id_rsa -o ConnectTimeout=2 -o StrictHostKeyChecking=no admin@${MIKROTIK_IP} \
#     "/interface wireless registration-table remove [find mac-address=$MAC]" 2>/dev/null &

# --- Option C: Local hostapd (if running on Orange Pi with USB Wi-Fi) ---
# if which hostapd_cli >/dev/null 2>&1; then
#     hostapd_cli deauthenticate "$MAC" 2>/dev/null || true
#     hostapd_cli disassociate "$MAC" 2>/dev/null || true
# fi

exit 0

