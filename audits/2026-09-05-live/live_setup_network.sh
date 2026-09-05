#!/bin/bash
sysctl -w net.ipv4.ip_forward=1 &>/dev/null

LAN_IFACE=""
for iface in eth1 $(ls -1 /sys/class/net 2>/dev/null | grep -E '^(usb[0-9]|enx)'); do
    if ip link show "$iface" &>/dev/null && [[ "$iface" != "eth0" && "$iface" != "lo" ]]; then
        LAN_IFACE="$iface"
        break
    fi
done

if [[ -n "$LAN_IFACE" ]]; then
    # Remove link-local / spurious routes on LAN interface
    ip addr show dev "$LAN_IFACE" | grep -o '169\.254\.[0-9.]*' | while read -r ip; do ip addr del "$ip/16" dev "$LAN_IFACE" 2>/dev/null || true; done
    ip route del default dev "$LAN_IFACE" 2>/dev/null || true

    # Assign static 10.0.0.1/19 if not present
    if ! ip addr show dev "$LAN_IFACE" | grep -q '10\.0\.0\.1/19'; then
        ip addr add 10.0.0.1/19 dev "$LAN_IFACE" 2>/dev/null || true
    fi
    ip link set "$LAN_IFACE" up

    # Bind dnsmasq to USB-LAN adapter only
    sed -i "s/^interface=.*/interface=$LAN_IFACE/" /etc/dnsmasq.conf 2>/dev/null || true

    # Prepare eth0 for WAN (ISP Router via DHCP)
    ip link set eth0 up
    if ! pgrep -f "dhclient.*eth0" >/dev/null; then
        dhclient -4 -nw -pf /run/dhclient.eth0.pid eth0 2>/dev/null || true
    fi

    # Explicitly enforce NAT Masquerade out eth0 to ISP
    iptables -t nat -C POSTROUTING -o eth0 -j MASQUERADE 2>/dev/null || iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE
else
    killall -9 dhclient 2>/dev/null || true
    ip addr flush dev eth0 2>/dev/null || true
    ip addr add 10.0.0.1/19 dev eth0 2>/dev/null || true
    ip link set eth0 up
    sed -i "s/^interface=.*/interface=eth0/" /etc/dnsmasq.conf 2>/dev/null || true
    WAN=$(ip route | grep default | awk '{print $5}' | head -n 1)
    if [[ -n "$WAN" && "$WAN" != "eth0" ]]; then
        iptables -t nat -C POSTROUTING -o "$WAN" -j MASQUERADE 2>/dev/null || iptables -t nat -A POSTROUTING -o "$WAN" -j MASQUERADE
    fi
fi

systemctl restart dnsmasq 2>/dev/null || true
