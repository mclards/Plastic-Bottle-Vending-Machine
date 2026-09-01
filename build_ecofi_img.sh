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

    # Captive Portal Trigger Intercepts (Android, iOS, Windows, ChromeOS)
    location /generate_204 { return 302 http://10.0.0.1/; }
    location /gen_204 { return 302 http://10.0.0.1/; }
    location /ncsi.txt { return 302 http://10.0.0.1/; }
    location /connecttest.txt { return 302 http://10.0.0.1/; }
    location /hotspot-detect.html { return 302 http://10.0.0.1/; }
    location /canonical.html { return 302 http://10.0.0.1/; }
    location /connectivitycheck.gstatic.com { return 302 http://10.0.0.1/; }
    location /connectivitycheck.android.com { return 302 http://10.0.0.1/; }
    location /msftconnecttest.com { return 302 http://10.0.0.1/; }

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


# Force dynamic LAN interface and static IP 10.0.0.1
echo "[4/6.5] Enforcing 10.0.0.1 static IP with /19 subnet mask and IP routing..."

# Permanent IPv4 Forwarding in sysctl
mkdir -p "$MOUNT_DIR/etc/sysctl.d"
echo "net.ipv4.ip_forward=1" > "$MOUNT_DIR/etc/sysctl.d/99-ecofi.conf"
sed -i 's/#net.ipv4.ip_forward=1/net.ipv4.ip_forward=1/' "$MOUNT_DIR/etc/sysctl.conf" 2>/dev/null || true

# System Hostname Branding
echo "ecofi-vendo" > "$MOUNT_DIR/etc/hostname"
sed -i 's/pisofi/ecofi-vendo/g' "$MOUNT_DIR/etc/hosts" 2>/dev/null || true

mkdir -p "$MOUNT_DIR/opt/ecofi"
cat << 'EOF' > "$MOUNT_DIR/opt/ecofi/setup_network.sh"
#!/bin/bash
# Enable Kernel IPv4 Packet Forwarding
sysctl -w net.ipv4.ip_forward=1 &>/dev/null

# Dynamically detect LAN and WAN interfaces
WAN=$(ip route | grep default | awk '{print $5}')
SET_LAN=0
for iface in eth0 eth1 br-lan; do
    if [[ "$iface" != "$WAN" ]] && ip link show "$iface" &>/dev/null; then
        ip addr flush dev "$iface"
        ip addr add 10.0.0.1/19 dev "$iface"
        ip link set "$iface" up
        # Update dnsmasq interface dynamically
        sed -i "s/interface=.*/interface=$iface/" /etc/dnsmasq.conf
        systemctl restart dnsmasq
        echo "LAN interface set: $iface = 10.0.0.1/19"
        SET_LAN=1
        break
    fi
done
if [ "$SET_LAN" -eq 0 ]; then
    echo "WARNING: No dedicated LAN interface detected! Defaulting to eth0."
    ip addr add 10.0.0.1/19 dev eth0 2>/dev/null || true
fi

# Ensure WAN NAT Masquerade is active
if [[ -n "$WAN" ]]; then
    iptables -t nat -C POSTROUTING -o "$WAN" -j MASQUERADE 2>/dev/null || iptables -t nat -A POSTROUTING -o "$WAN" -j MASQUERADE
else
    iptables -t nat -C POSTROUTING -j MASQUERADE 2>/dev/null || iptables -t nat -A POSTROUTING -j MASQUERADE
fi
EOF
chmod +x "$MOUNT_DIR/opt/ecofi/setup_network.sh"
rm -f "$MOUNT_DIR/etc/network/interfaces.d/eth0" 2>/dev/null || true

# Step 5: Inject ECO-Fi software stack into /opt/ecofi
echo "[5/6] Injecting ECO-Fi software stack into /opt/ecofi..."
mkdir -p "$MOUNT_DIR/opt/ecofi"
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
ExecStartPost=/bin/bash -c "sysctl -w net.ipv4.ip_forward=1; ipset create ecofi_auth hash:ip timeout 86400 -exist; iptables -P FORWARD DROP; iptables -A FORWARD -m set --match-set ecofi_auth src -j ACCEPT; iptables -A FORWARD -m state --state ESTABLISHED,RELATED -j ACCEPT; iptables -A FORWARD -p udp --dport 53 -j ACCEPT; iptables -A FORWARD -p tcp --dport 53 -j ACCEPT; iptables -t nat -A PREROUTING -m set ! --match-set ecofi_auth src -p udp --dport 53 -j REDIRECT --to-port 53; iptables -t nat -A PREROUTING -m set ! --match-set ecofi_auth src -p tcp --dport 80 -j REDIRECT --to-port 80; iptables -t nat -A POSTROUTING -j MASQUERADE"
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF

# BUILD-05: First-Boot Dependency Installer
cat << 'EOF' > "$MOUNT_DIR/etc/systemd/system/ecofi_firstboot.service"
[Unit]
Description=ECO-Fi First Boot Dependency Installer
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
StandardOutput=journal
StandardError=journal
ExecStart=/bin/bash -c "pip3 install flask werkzeug pyserial openpyxl --break-system-packages && systemctl disable ecofi_firstboot.service"

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

# Main Portal Service
cat << 'EOF' > "$MOUNT_DIR/etc/systemd/system/ecofi_portal.service"
[Unit]
Description=ECO-Fi Captive Portal & Web Engine
After=network.target network-online.target nginx.service ecofi_firewall.service ecofi_firstboot.service

[Service]
Type=simple
User=root
WorkingDirectory=/opt/ecofi
ExecStart=/usr/bin/python3 /opt/ecofi/portal.py
StandardOutput=append:/opt/ecofi/portal.log
StandardError=append:/opt/ecofi/portal.log
Restart=always
RestartSec=3
Environment=PORT=5000

[Install]
WantedBy=multi-user.target
EOF

mkdir -p "$MOUNT_DIR/etc/systemd/system/multi-user.target.wants"
ln -sf /etc/systemd/system/ecofi_portal.service "$MOUNT_DIR/etc/systemd/system/multi-user.target.wants/ecofi_portal.service"
ln -sf /etc/systemd/system/ecofi_firewall.service "$MOUNT_DIR/etc/systemd/system/multi-user.target.wants/ecofi_firewall.service"
ln -sf /etc/systemd/system/ecofi_firstboot.service "$MOUNT_DIR/etc/systemd/system/multi-user.target.wants/ecofi_firstboot.service"


# Finalize and unmount
echo "Syncing filesystem buffers..."
sync
umount "$MOUNT_DIR"

echo "======================================================================"
echo " SUCCESS: Cleaned, Hardened ECO-Fi OS Image Ready at:"
echo " $TARGET_IMG"
echo " All legacy PisoFi services purged. Pure ECO-Fi stack running!"
echo "======================================================================"
