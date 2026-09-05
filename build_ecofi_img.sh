#!/bin/bash
# ==============================================================================
# ECO-Fi OS Image Rebuilder & Customizer
# Deep Cleaning, Hardening & ECO-Fi Integration for Orange Pi One
# Base: resources/PisoFi_Opi1&PC_v5.3.0-05-10-26_EXT.img
# Target: resources/EcoFi_Opi_v1.0.img
# ==============================================================================

set -e

BASE_IMG="/mnt/d/PROJECTS_IO/Plastic-Bottle-Vending-Machine/resources/PisoFi_Opi1&PC_v5.3.0-05-10-26_EXT.img"
TARGET_IMG="/mnt/d/PROJECTS_IO/Plastic-Bottle-Vending-Machine/resources/EcoFi_Opi_v1.0.img"
MOUNT_DIR="/tmp/ecofi_mount"
SOURCE_HOST="/mnt/d/PROJECTS_IO/Plastic-Bottle-Vending-Machine/host"

echo "======================================================================"
echo " Starting ECO-Fi OS Image Deep Cleaning & Rebuild"
echo " Base Image:   $BASE_IMG"
echo " Target Image: $TARGET_IMG"
echo "======================================================================"

# Step 1: Check target image
if [ ! -f "$TARGET_IMG" ]; then
    echo "[1/6] Copying base image to $TARGET_IMG (this takes ~30s)..."
    cp "$BASE_IMG" "$TARGET_IMG"
else
    echo "[1/6] Target image $TARGET_IMG already exists. Updating contents..."
fi

# Step 2: Create mount point and mount ext4 partition (offset 4,194,304 = 8192 * 512)
echo "[2/6] Mounting ext4 rootfs..."
mkdir -p "$MOUNT_DIR"
umount "$MOUNT_DIR" 2>/dev/null || true
mount -o loop,offset=4194304 "$TARGET_IMG" "$MOUNT_DIR"

# Step 3: PURGE all legacy PisoFi services, scripts, and phone-home daemons
echo "[3/6] Purging legacy PisoFi services and phone-home daemons..."
rm -f "$MOUNT_DIR/etc/systemd/system/pisofi_"* 2>/dev/null || true
rm -f "$MOUNT_DIR/etc/systemd/system/multi-user.target.wants/pisofi_"* 2>/dev/null || true
rm -f "$MOUNT_DIR/etc/systemd/system/multi-user.target.wants/zerotier-one.service" 2>/dev/null || true
rm -f "$MOUNT_DIR/etc/systemd/system/multi-user.target.wants/php7.0-fpm.service" 2>/dev/null || true
rm -f "$MOUNT_DIR/etc/systemd/system/multi-user.target.wants/mariadb.service" 2>/dev/null || true
rm -f "$MOUNT_DIR/etc/systemd/system/multi-user.target.wants/mysql.service" 2>/dev/null || true
rm -rf "$MOUNT_DIR/var/www/html/pisofi" 2>/dev/null || true
rm -rf "$MOUNT_DIR/var/www/html/"* 2>/dev/null || true
rm -rf "$MOUNT_DIR/.cache" 2>/dev/null || true

# BUILD-01: Complete Purge of legacy binaries and backdoors
rm -rf "$MOUNT_DIR/home/pi/.dat" 2>/dev/null || true
rm -f "$MOUNT_DIR/usr/local/bin/pisofier" 2>/dev/null || true
rm -f "$MOUNT_DIR/usr/local/bin/pisofi_resetconnections" 2>/dev/null || true
rm -f "$MOUNT_DIR/usr/local/bin/site_control" 2>/dev/null || true
rm -f "$MOUNT_DIR/usr/local/bin/mac_control" 2>/dev/null || true
rm -f "$MOUNT_DIR/usr/local/bin/reset_pins" 2>/dev/null || true
rm -f "$MOUNT_DIR/usr/local/bin/ngrok" 2>/dev/null || true
rm -f "$MOUNT_DIR/usr/local/bin/composer" 2>/dev/null || true
rm -f "$MOUNT_DIR/usr/local/bin/cmd-runner.py" 2>/dev/null || true
rm -rf "$MOUNT_DIR/usr/src/pfi" 2>/dev/null || true
rm -rf "$MOUNT_DIR/home/pi/.ngrok2" 2>/dev/null || true
rm -f "$MOUNT_DIR/home/pi/.git-credentials" 2>/dev/null || true
rm -f "$MOUNT_DIR/home/pi/.mysql_history" 2>/dev/null || true
rm -rf "$MOUNT_DIR/usr/local/bin/zerotier-one" "$MOUNT_DIR/var/lib/zerotier-one" 2>/dev/null || true
rm -rf "$MOUNT_DIR/etc/pisofi" 2>/dev/null || true
rm -rf "$MOUNT_DIR/var/lib/mysql" 2>/dev/null || true

# Step 4: Configure Nginx as an ultra-fast Reverse Proxy to ECO-Fi Portal (port 5000)
echo "[4/6] Configuring Nginx reverse proxy for ECO-Fi..."
mkdir -p "$MOUNT_DIR/etc/nginx/sites-available"
mkdir -p "$MOUNT_DIR/etc/nginx/sites-enabled"
rm -f "$MOUNT_DIR/etc/nginx/sites-enabled/"* 2>/dev/null || true

cat << 'EOF' > "$MOUNT_DIR/etc/nginx/sites-available/ecofi"
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name _;

    # Static Assets Cache
    location /static/ {
        alias /opt/ecofi/static/;
        expires 7d;
        add_header Cache-Control "public, no-transform";
    }

    # Proxy all traffic to ECO-Fi Python Web Engine
    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_connect_timeout 5s;
        proxy_read_timeout 60s;
    }
}
EOF

ln -sf ../sites-available/ecofi "$MOUNT_DIR/etc/nginx/sites-enabled/ecofi"


# Configure networking: eth0 = WAN (ISP via DHCP), eth1 = LAN (Access Point static 10.0.0.1/19)
echo "[4/6.5] Enforcing eth0 as WAN (DHCP) and eth1 as LAN (10.0.0.1/19) with authoritative dnsmasq DHCP..."

# Permanent IPv4 Forwarding in sysctl
mkdir -p "$MOUNT_DIR/etc/sysctl.d"
echo "net.ipv4.ip_forward=1" > "$MOUNT_DIR/etc/sysctl.d/99-ecofi.conf"
sed -i 's/#net.ipv4.ip_forward=1/net.ipv4.ip_forward=1/' "$MOUNT_DIR/etc/sysctl.conf" 2>/dev/null || true

# System Hostname Branding
echo "ecofi-vendo" > "$MOUNT_DIR/etc/hostname"
sed -i 's/pisofi/ecofi-vendo/g' "$MOUNT_DIR/etc/hosts" 2>/dev/null || true

# Set root and pi console login passwords to "root" for HDMI/serial console
ROOT_HASH='$6$JpJzc5Fnsdll3j83$D9xx8MwvyG9KoulpMUVrD8JfSWwfOV5QkcxAdI0z4GeT5FpbC6HyeKeUNYoc1tBMtz2SjNVRFtrGd6TQ7v0UA0'
sed -i "s|^root:[^:]*:|root:${ROOT_HASH}:|" "$MOUNT_DIR/etc/shadow"
sed -i "s|^pi:[^:]*:|pi:${ROOT_HASH}:|" "$MOUNT_DIR/etc/shadow"

# Configure /etc/network/interfaces: eth0 = WAN (ISP via DHCP), eth1 = LAN (AP static 10.0.0.1/19)
cat << 'EOF' > "$MOUNT_DIR/etc/network/interfaces"
auto lo
iface lo inet loopback

auto eth0
allow-hotplug eth0
iface eth0 inet dhcp

auto eth1
allow-hotplug eth1
iface eth1 inet static
    address 10.0.0.1
    netmask 255.255.224.0
    broadcast 10.0.31.255
EOF

rm -rf "$MOUNT_DIR/etc/network/interfaces.d/"* 2>/dev/null || true

# Prevent dhcpcd from assigning link-local or default routes to LAN AP adapter
if [ -f "$MOUNT_DIR/etc/dhcpcd.conf" ]; then
    if ! grep -q "denyinterfaces eth1" "$MOUNT_DIR/etc/dhcpcd.conf"; then
        echo -e "\ndenyinterfaces eth1 usb0 enx*" >> "$MOUNT_DIR/etc/dhcpcd.conf"
    fi
fi

# Configure /etc/dnsmasq.conf with full DHCP pool and captive portal wildcard
cat << 'EOF' > "$MOUNT_DIR/etc/dnsmasq.conf"
bogus-priv
dhcp-lease-max=20000
no-negcache
no-resolv
dns-forward-max=1024
domain-needed
bind-dynamic

domain=ecofi.local
local=/ecofi.local/
listen-address=10.0.0.1,127.0.0.1

# Auto-configured interface: eth0 in Bench Mode (default), eth1 in Production Mode
interface=eth0
dhcp-range=10.0.0.100,10.0.31.254,255.255.224.0,72h
dhcp-option=3,10.0.0.1
dhcp-option=6,10.0.0.1
dhcp-option=114,http://10.0.0.1/
dhcp-option=160,http://10.0.0.1/

# Explicit Captive Portal Probes (Windows NCSI, Android, Apple iOS/macOS, Firefox)
address=/msftconnecttest.com/10.0.0.1
address=/www.msftconnecttest.com/10.0.0.1
address=/ipv6.msftconnecttest.com/10.0.0.1
address=/msftncsi.com/10.0.0.1
address=/www.msftncsi.com/10.0.0.1
address=/connectivitycheck.gstatic.com/10.0.0.1
address=/connectivitycheck.android.com/10.0.0.1
address=/clients3.google.com/10.0.0.1
address=/apple.com/10.0.0.1
address=/captive.apple.com/10.0.0.1
address=/detectportal.firefox.com/10.0.0.1

address=/localhost/127.0.0.1
address=/ecofi-vendo/10.0.0.1

server=1.1.1.1
server=1.0.0.1
EOF
rm -rf "$MOUNT_DIR/etc/dnsmasq.d/"* 2>/dev/null || true

# Add udev hotplug rule for USB-to-Ethernet Adapter
mkdir -p "$MOUNT_DIR/etc/udev/rules.d"
cat << 'EOF' > "$MOUNT_DIR/etc/udev/rules.d/99-ecofi-usbnet.rules"
ACTION=="add", SUBSYSTEM=="net", KERNEL=="eth1|usb*|enx*", RUN+="/opt/ecofi/setup_network.sh"
EOF

mkdir -p "$MOUNT_DIR/opt/ecofi"
cat << 'EOF' > "$MOUNT_DIR/opt/ecofi/setup_network.sh"
#!/bin/bash
# Enable Kernel IPv4 Packet Forwarding
sysctl -w net.ipv4.ip_forward=1 &>/dev/null

# 1. Identify LAN interface (USB-to-Ethernet adapter for Access Point)
LAN_IFACE=""
for iface in eth1 $(ls -1 /sys/class/net 2>/dev/null | grep -E '^(usb[0-9]|enx)'); do
    if ip link show "$iface" &>/dev/null && [[ "$iface" != "eth0" && "$iface" != "lo" ]]; then
        LAN_IFACE="$iface"
        break
    fi
done

if [[ -n "$LAN_IFACE" ]]; then
    # =========================================================================
    # DUAL-PORT PRODUCTION MODE:
    # USB Adapter ($LAN_IFACE) = LAN for Access Point (Static 10.0.0.1/19 + DHCP)
    # Onboard Port (eth0)      = WAN for ISP Router (Dynamic DHCP Client)
    # =========================================================================
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
    # =========================================================================
    # SINGLE-PORT BENCH TEST MODE:
    # No USB adapter detected. Use onboard port (eth0) as LAN (10.0.0.1/19 + DHCP).
    # Allows direct connection to PC or single Access Point with instant DHCP.
    # =========================================================================
    killall -9 dhclient 2>/dev/null || true
    ip addr flush dev eth0 2>/dev/null || true
    ip addr add 10.0.0.1/19 dev eth0 2>/dev/null || true
    ip link set eth0 up

    # Bind dnsmasq to eth0 so connected PC receives DHCP IP automatically
    sed -i "s/^interface=.*/interface=eth0/" /etc/dnsmasq.conf 2>/dev/null || true

    WAN=$(ip route | grep default | awk '{print $5}' | head -n 1)
    if [[ -n "$WAN" && "$WAN" != "eth0" ]]; then
        iptables -t nat -C POSTROUTING -o "$WAN" -j MASQUERADE 2>/dev/null || iptables -t nat -A POSTROUTING -o "$WAN" -j MASQUERADE
    fi
fi

# Restart dnsmasq cleanly so DHCP is 100% active on the designated LAN interface
systemctl restart dnsmasq 2>/dev/null || true
EOF
chmod +x "$MOUNT_DIR/opt/ecofi/setup_network.sh"

# Step 5: Inject Offline Python 3.5 Packages and ECO-Fi Software Stack
echo "[5/6] Injecting offline Python 3.5 dependencies into rootfs..."
mkdir -p "$MOUNT_DIR/usr/local/lib/python3.5/dist-packages"
if [ -d "/var/cache/ecofi_wheels_py35" ]; then
    cp -r /var/cache/ecofi_wheels_py35/* "$MOUNT_DIR/usr/local/lib/python3.5/dist-packages/"
fi

echo "[5/6.5] Injecting ECO-Fi software stack into /opt/ecofi..."
mkdir -p "$MOUNT_DIR/opt/ecofi"
rm -f "$MOUNT_DIR/opt/ecofi/vendo_sessions.db" 2>/dev/null || true
cp "$SOURCE_HOST/portal.py" "$MOUNT_DIR/opt/ecofi/"
cp "$SOURCE_HOST/license_manager.py" "$MOUNT_DIR/opt/ecofi/" 2>/dev/null || true
cp "$SOURCE_HOST/esp32_simulator.py" "$MOUNT_DIR/opt/ecofi/" 2>/dev/null || true
if [ -d "$SOURCE_HOST/templates" ]; then cp -r "$SOURCE_HOST/templates" "$MOUNT_DIR/opt/ecofi/"; fi
if [ -d "$SOURCE_HOST/static" ]; then cp -r "$SOURCE_HOST/static" "$MOUNT_DIR/opt/ecofi/"; fi

chmod 755 "$MOUNT_DIR/opt/ecofi"
chmod 644 "$MOUNT_DIR/opt/ecofi/"*.py 2>/dev/null || true
chmod +x "$MOUNT_DIR/opt/ecofi/portal.py"

# Step 6: Install ECO-Fi systemd service units
echo "[6/6] Installing ECO-Fi systemd service units..."

# BUILD-08: Firewall Initialization Service
cat << 'EOF' > "$MOUNT_DIR/etc/systemd/system/ecofi_firewall.service"
[Unit]
Description=ECO-Fi Firewall Initialization
Before=ecofi_portal.service
After=network.target

[Service]
Type=oneshot
ExecStart=/opt/ecofi/setup_network.sh
ExecStartPost=/bin/bash -c "sysctl -w net.ipv4.ip_forward=1"
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF

# GAP-06 & NET-05: Update DNS Hijacking
sed -i 's/portal.pisofiapp.com/10.0.0.1/g' "$MOUNT_DIR/etc/dnsmasq.conf" 2>/dev/null || true
rm -f "$MOUNT_DIR/etc/dnsmasq.d/ecofi_captive.conf" 2>/dev/null || true

# BUILD-06: Log Rotation
cat << 'EOF' > "$MOUNT_DIR/etc/logrotate.d/ecofi"
/opt/ecofi/*.log {
    daily
    rotate 3
    compress
    missingok
    notifempty
    maxsize 10M
}
EOF

# Main Portal Service (Starts immediately on boot, 100% offline ready)
cat << 'EOF' > "$MOUNT_DIR/etc/systemd/system/ecofi_portal.service"
[Unit]
Description=ECO-Fi Captive Portal & Web Engine
After=network.target nginx.service ecofi_firewall.service

[Service]
Type=simple
User=root
WorkingDirectory=/opt/ecofi
ExecStart=/bin/bash -c "exec /usr/bin/python3 /opt/ecofi/portal.py >> /opt/ecofi/portal.log 2>&1"
StandardOutput=journal
StandardError=journal
Restart=always
RestartSec=3
Environment=PORT=5000

[Install]
WantedBy=multi-user.target
EOF

# Enable services in multi-user.target
mkdir -p "$MOUNT_DIR/etc/systemd/system/multi-user.target.wants"
rm -f "$MOUNT_DIR/etc/systemd/system/multi-user.target.wants/ecofi_firstboot.service" 2>/dev/null || true
rm -f "$MOUNT_DIR/etc/systemd/system/ecofi_firstboot.service" 2>/dev/null || true
rm -f "$MOUNT_DIR/etc/systemd/system/multi-user.target.wants/ecofi_daemon.service" 2>/dev/null || true
rm -f "$MOUNT_DIR/etc/systemd/system/ecofi_daemon.service" 2>/dev/null || true
rm -f "$MOUNT_DIR/opt/ecofi/daemon.py" 2>/dev/null || true
ln -sf /etc/systemd/system/ecofi_portal.service "$MOUNT_DIR/etc/systemd/system/multi-user.target.wants/ecofi_portal.service"
ln -sf /etc/systemd/system/ecofi_firewall.service "$MOUNT_DIR/etc/systemd/system/multi-user.target.wants/ecofi_firewall.service"


# Finalize and unmount
echo "Syncing filesystem buffers..."
sync
umount "$MOUNT_DIR"

echo "======================================================================"
echo " SUCCESS: Cleaned, Hardened ECO-Fi OS Image Ready at:"
echo " $TARGET_IMG"
echo " All legacy PisoFi services purged. Pure ECO-Fi stack running!"
echo "======================================================================"
