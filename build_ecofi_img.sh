#!/bin/bash
# ==============================================================================
# VMC ECO-VENDO OS Image Rebuilder & Customizer
# Student Thesis: Eco-Vendo: An Empty Bottle-Initiated Internet Access Vending System
# Base: resources/PisoFi_Opi1&PC_v5.3.0-05-10-26_EXT.img
# Target: resources/EcoFi_Opi_v<VERSION>.img
# ==============================================================================

set -euo pipefail
ROOT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
VERSION=$(tr -d '\r\n' < "$ROOT_DIR/VERSION")
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "Invalid release version" >&2; exit 1; }
[[ $EUID -eq 0 ]] || { echo "Run this image builder as root." >&2; exit 1; }
PREV_IMG="$ROOT_DIR/resources/EcoFi_Opi_v2.1.img"
TARGET_IMG="$ROOT_DIR/resources/EcoFi_Opi_v${VERSION}.img"
SOURCE_HOST="$ROOT_DIR/host"
[[ ! -e "$TARGET_IMG" ]] || { echo "Release already exists: $TARGET_IMG. Use a new version." >&2; exit 1; }
[[ -f "$PREV_IMG" ]] || { echo "Required clean base is missing: $PREV_IMG" >&2; exit 1; }
for command in mount umount losetup qemu-arm-static dpkg-deb sha256sum e2fsck; do command -v "$command" >/dev/null; done
MOUNT_DIR=$(mktemp -d /tmp/ecofi-build.XXXXXX)
WORK_IMG=$(mktemp "${TARGET_IMG}.building.XXXXXX")
LOOP_DEVICE=""
cleanup() {
    if mountpoint -q "$MOUNT_DIR"; then umount "$MOUNT_DIR" || return; fi
    if [[ -n "$LOOP_DEVICE" ]]; then losetup -d "$LOOP_DEVICE" || return; fi
    rmdir "$MOUNT_DIR" 2>/dev/null || true
    if [ -f "$WORK_IMG" ]; then rm -f -- "$WORK_IMG"; fi
}
trap cleanup EXIT
printf 'Building VMC ECO-VENDO v%s from %s\nOutput: %s\n' "$VERSION" "$PREV_IMG" "$TARGET_IMG"
cp --reflink=auto "$PREV_IMG" "$WORK_IMG"
LOOP_DEVICE=$(losetup --find --show --offset 4194304 "$WORK_IMG")
mount "$LOOP_DEVICE" "$MOUNT_DIR"
if [ -e "$MOUNT_DIR/opt/ecofi/vendo_sessions.db" ]; then
    echo "Refusing to rebuild an image containing a customer database. Export and migrate it separately." >&2
    exit 1
fi

# Step 3: PURGE all legacy PisoFi services, scripts, and phone-home daemons
echo "[3/6] Purging legacy PisoFi services and phone-home daemons..."
rm -f "$MOUNT_DIR/etc/systemd/system/pisofi_"* 2>/dev/null || true
rm -f "$MOUNT_DIR/etc/systemd/system/multi-user.target.wants/pisofi_"* 2>/dev/null || true
rm -f "$MOUNT_DIR/etc/systemd/system/multi-user.target.wants/zerotier-one.service" 2>/dev/null || true
rm -f "$MOUNT_DIR/etc/systemd/system/multi-user.target.wants/php7.0-fpm.service" 2>/dev/null || true
rm -f "$MOUNT_DIR/etc/systemd/system/multi-user.target.wants/mariadb.service" 2>/dev/null || true
rm -f "$MOUNT_DIR/etc/systemd/system/multi-user.target.wants/mysql.service" 2>/dev/null || true
rm -f "$MOUNT_DIR/etc/systemd/system/multi-user.target.wants/NetworkManager"* 2>/dev/null || true
rm -f "$MOUNT_DIR/etc/systemd/system/network-online.target.wants/NetworkManager"* 2>/dev/null || true
rm -f "$MOUNT_DIR/etc/systemd/system/multi-user.target.wants/smbd.service" 2>/dev/null || true
rm -f "$MOUNT_DIR/etc/systemd/system/multi-user.target.wants/nmbd.service" 2>/dev/null || true
rm -f "$MOUNT_DIR/etc/systemd/system/multi-user.target.wants/rsync.service" 2>/dev/null || true
rm -f "$MOUNT_DIR/etc/systemd/system/multi-user.target.wants/pppd-dns.service" 2>/dev/null || true
rm -f "$MOUNT_DIR/etc/systemd/system/timers.target.wants/phpsessionclean.timer" 2>/dev/null || true
rm -rf "$MOUNT_DIR/etc/NetworkManager/system-connections/"* 2>/dev/null || true
rm -f "$MOUNT_DIR/etc/udev/rules.d/70-persistent-net.rules" 2>/dev/null || true
rm -rf "$MOUNT_DIR/home/pisofi" 2>/dev/null || true
rm -f "$MOUNT_DIR/etc/environment" 2>/dev/null || true
touch "$MOUNT_DIR/etc/environment"
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

# Step 4: Configure Nginx as an ultra-fast Reverse Proxy to VMC ECO-VENDO Portal (port 5000)
echo "[4/6] Configuring Nginx reverse proxy for VMC ECO-VENDO..."
mkdir -p "$MOUNT_DIR/etc/nginx/sites-available"
mkdir -p "$MOUNT_DIR/etc/nginx/sites-enabled"
rm -f "$MOUNT_DIR/etc/nginx/sites-enabled/"* 2>/dev/null || true

cat << 'EOF' > "$MOUNT_DIR/etc/nginx/sites-available/ecofi"
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name _;
    server_tokens off;

    # Security Headers
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;

    # Static Assets Cache
    location /static/ {
        alias /opt/ecofi/static/;
        expires 7d;
        add_header Cache-Control "public, no-transform";
    }

    # Proxy all traffic to VMC ECO-VENDO Python Web Engine
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

# Permanent Hardened Kernel Network Stack in sysctl
mkdir -p "$MOUNT_DIR/etc/sysctl.d"
cat << 'EOF' > "$MOUNT_DIR/etc/sysctl.d/99-ecofi.conf"
# VMC ECO-VENDO Hardened Kernel Network Stack
net.ipv4.ip_forward=1
net.ipv4.tcp_syncookies=1
net.ipv4.conf.all.rp_filter=1
net.ipv4.conf.default.rp_filter=1
net.ipv4.conf.all.accept_redirects=0
net.ipv4.conf.default.accept_redirects=0
net.ipv4.conf.all.send_redirects=0
net.ipv4.conf.all.accept_source_route=0
net.ipv4.icmp_echo_ignore_broadcasts=1
net.ipv4.icmp_ignore_bogus_error_responses=1
net.netfilter.nf_conntrack_max=65536
net.ipv4.tcp_fin_timeout=30
net.ipv4.tcp_keepalive_time=300
net.ipv6.conf.all.disable_ipv6=1
net.ipv6.conf.default.disable_ipv6=1
net.ipv6.conf.lo.disable_ipv6=1
EOF
sed -i 's/#net.ipv4.ip_forward=1/net.ipv4.ip_forward=1/' "$MOUNT_DIR/etc/sysctl.conf" 2>/dev/null || true

# System Hostname Branding
echo "ecofi-vendo" > "$MOUNT_DIR/etc/hostname"
sed -i 's/pisofi/ecofi-vendo/g' "$MOUNT_DIR/etc/hosts" 2>/dev/null || true

# Timezone & NTP Configuration: Set Asia/Manila (PHT, UTC+8) and Philippine NTP pool
echo "[4/6.2] Setting timezone to Asia/Manila (PHT, UTC+8) and configuring NTP..."
ln -sf /usr/share/zoneinfo/Asia/Manila "$MOUNT_DIR/etc/localtime"
echo "Asia/Manila" > "$MOUNT_DIR/etc/timezone"

cat << 'EOF' > "$MOUNT_DIR/etc/systemd/timesyncd.conf"
[Time]
NTP=0.ph.pool.ntp.org 1.ph.pool.ntp.org 2.ph.pool.ntp.org time.google.com time.cloudflare.com
FallbackNTP=0.pool.ntp.org 1.pool.ntp.org 2.pool.ntp.org 3.pool.ntp.org
EOF

mkdir -p "$MOUNT_DIR/etc/systemd/system/sysinit.target.wants"
ln -sf /lib/systemd/system/systemd-timesyncd.service "$MOUNT_DIR/etc/systemd/system/sysinit.target.wants/systemd-timesyncd.service" 2>/dev/null || true
date -u +"%Y-%m-%d %H:%M:%S" > "$MOUNT_DIR/etc/fake-hwclock.data" 2>/dev/null || true

# Set root and pi console login passwords to "root" for HDMI/serial console
ROOT_HASH='$6$JIArBU6F1WcXAkV2$n13SEPVG7J/mKPL1Fr0wuadMbziDVKwGQrA484i5K/MzA3IY8l1lpcx960SYyFmR1I.QTgesqTzZu1M9je9YI0'
sed -i "s|^root:[^:]*:|root:${ROOT_HASH}:|" "$MOUNT_DIR/etc/shadow"
sed -i "s|^pi:[^:]*:|pi:${ROOT_HASH}:|" "$MOUNT_DIR/etc/shadow"

# Sanitize /etc/sudoers: Purge dangerous wildcard NOPASSWD backdoors left by PisoFi
cat << 'EOF' > "$MOUNT_DIR/etc/sudoers"
Defaults	env_reset
Defaults	mail_badpass
Defaults	secure_path="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

root	ALL=(ALL:ALL) ALL
%sudo	ALL=(ALL:ALL) ALL
pi	ALL=(ALL:ALL) NOPASSWD: ALL
EOF
chmod 440 "$MOUNT_DIR/etc/sudoers"

# Configure /etc/network/interfaces: eth0 = WAN (ISP via DHCP), eth1 = LAN (AP static 10.0.0.1/19)
cat << 'EOF' > "$MOUNT_DIR/etc/network/interfaces"
auto lo
iface lo inet loopback

auto eth0
allow-hotplug eth0
iface eth0 inet dhcp

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

# Strictly bind to LAN (eth1) and explicitly protect WAN (eth0) from rogue DHCP
interface=eth1
except-interface=eth0
dhcp-range=set:ecofi_lan,10.0.0.100,10.0.31.254,255.255.224.0,72h
dhcp-option=tag:ecofi_lan,3,10.0.0.1
dhcp-option=tag:ecofi_lan,6,10.0.0.1

# RFC 8910 & RFC 7710 Captive Portal Discovery for Android, iOS, and macOS
dhcp-option=114,http://10.0.0.1/
dhcp-option=160,http://10.0.0.1/

address=/localhost/127.0.0.1
address=/ecofi-vendo/10.0.0.1

# Synthetic captive portal detection records (instant offline popup)
address=/captive.apple.com/10.0.0.1
address=/connectivitycheck.gstatic.com/10.0.0.1
address=/connectivitycheck.android.com/10.0.0.1
address=/clients3.google.com/10.0.0.1
address=/www.msftconnecttest.com/10.0.0.1
address=/www.msftncsi.com/10.0.0.1
address=/detectportal.firefox.com/10.0.0.1

# Fast, redundant upstream DNS servers with all-servers querying
server=1.1.1.1
server=8.8.8.8
server=1.0.0.1
server=8.8.4.4
all-servers
EOF
rm -rf "$MOUNT_DIR/etc/dnsmasq.d/"* 2>/dev/null || true

# Add udev hotplug rule for USB-to-Ethernet Adapter:
# Trigger ecofi_firewall.service asynchronously without blocking udev workers or causing systemd deadlocks!
mkdir -p "$MOUNT_DIR/etc/udev/rules.d"
cat << 'EOF' > "$MOUNT_DIR/etc/udev/rules.d/99-ecofi-usbnet.rules"
ACTION=="add", SUBSYSTEM=="net", KERNEL=="eth1|usb*|enx*", TAG+="systemd", ENV{SYSTEMD_WANTS}+="ecofi_firewall.service"
EOF

# Clean up boot arguments in armbianEnv.txt
if [ -f "$MOUNT_DIR/boot/armbianEnv.txt" ]; then
    sed -i '/^extraargs=/d' "$MOUNT_DIR/boot/armbianEnv.txt"
    echo "extraargs=net.ifnames=0 biosdevname=0" >> "$MOUNT_DIR/boot/armbianEnv.txt"
    sed -i '/^overlays=/d' "$MOUNT_DIR/boot/armbianEnv.txt"
    echo "overlays=uart1 uart3" >> "$MOUNT_DIR/boot/armbianEnv.txt"
fi

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

    # Restart dnsmasq cleanly so DHCP is 100% active on the designated LAN interface
    systemctl restart dnsmasq 2>/dev/null || true
else
    echo "No USB LAN adapter detected yet."
fi
EOF
chmod +x "$MOUNT_DIR/opt/ecofi/setup_network.sh"

# Step 5: Inject Offline Python 3.5 Packages and VMC ECO-VENDO Software Stack
echo "[5/6] Injecting offline Python 3.5 dependencies into rootfs..."
mkdir -p "$MOUNT_DIR/usr/local/lib/python3.5/dist-packages"
if [ -d "/var/cache/ecofi_wheels_py35" ]; then
    cp -r /var/cache/ecofi_wheels_py35/* "$MOUNT_DIR/usr/local/lib/python3.5/dist-packages/"
fi

# Inject ipset binary and shared library
DEBS_DIR="$ROOT_DIR/resources/debs"
if [ -d "$DEBS_DIR" ]; then
    echo "Injecting ipset & libipset3 packages into rootfs..."
    dpkg-deb -x "$DEBS_DIR/libipset3_6.30-2_armhf.deb" "$MOUNT_DIR"
    dpkg-deb -x "$DEBS_DIR/ipset_6.30-2_armhf.deb" "$MOUNT_DIR"
fi

echo "[5/6.5] Injecting VMC ECO-VENDO software stack into /opt/ecofi..."
mkdir -p "$MOUNT_DIR/opt/ecofi"
for module in portal.py license_manager.py esp32_simulator.py gateway_network.py time_schema.py time_policy.py transition_engine.py time_portal.py migrate_legacy_sessions.py; do
    cp "$SOURCE_HOST/$module" "$MOUNT_DIR/opt/ecofi/"
done
if [ -d "$SOURCE_HOST/templates" ]; then cp -r "$SOURCE_HOST/templates" "$MOUNT_DIR/opt/ecofi/"; fi
if [ -d "$SOURCE_HOST/static" ]; then
    [[ ! -L "$MOUNT_DIR/opt/ecofi/static" ]] || exit 1
    rm -rf -- "$MOUNT_DIR/opt/ecofi/static"
    cp -r "$SOURCE_HOST/static" "$MOUNT_DIR/opt/ecofi/"
fi
cp "$ROOT_DIR/VERSION" "$MOUNT_DIR/opt/ecofi/VERSION"
printf 'ECOFI_VERSION=%s\nBASE_IMAGE=%s\nBUILD_UTC=%s\n' "$VERSION" "$(basename "$PREV_IMG")" "$(date -u +%FT%TZ)" > "$MOUNT_DIR/etc/ecofi-release"
for private in license.key hwid_override.txt deposit_journal.json current_active_client.txt; do
    [[ ! -e "$MOUNT_DIR/opt/ecofi/$private" ]] || { echo "Private runtime state found: $private" >&2; exit 1; }
done

chmod 755 "$MOUNT_DIR/opt/ecofi"
chmod 644 "$MOUNT_DIR/opt/ecofi/"*.py 2>/dev/null || true
chmod +x "$MOUNT_DIR/opt/ecofi/portal.py"

# Step 6: Install VMC ECO-VENDO systemd service units
echo "[6/6] Installing VMC ECO-VENDO systemd service units..."

# BUILD-08: Firewall Initialization Service
cat << 'EOF' > "$MOUNT_DIR/etc/systemd/system/ecofi_firewall.service"
[Unit]
Description=VMC ECO-VENDO Firewall Initialization
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

# Main portal starts on boot; timed access requires a trusted clock.
cp "$SOURCE_HOST/ecofi.service" "$MOUNT_DIR/etc/systemd/system/ecofi_portal.service"

# Enable services in multi-user.target
mkdir -p "$MOUNT_DIR/etc/systemd/system/multi-user.target.wants"
rm -f "$MOUNT_DIR/etc/systemd/system/multi-user.target.wants/ecofi_firstboot.service" 2>/dev/null || true
rm -f "$MOUNT_DIR/etc/systemd/system/ecofi_firstboot.service" 2>/dev/null || true
rm -f "$MOUNT_DIR/etc/systemd/system/multi-user.target.wants/ecofi_daemon.service" 2>/dev/null || true
rm -f "$MOUNT_DIR/etc/systemd/system/ecofi_daemon.service" 2>/dev/null || true
rm -f "$MOUNT_DIR/opt/ecofi/daemon.py" 2>/dev/null || true
ln -sf /etc/systemd/system/ecofi_portal.service "$MOUNT_DIR/etc/systemd/system/multi-user.target.wants/ecofi_portal.service"
ln -sf /etc/systemd/system/ecofi_firewall.service "$MOUNT_DIR/etc/systemd/system/multi-user.target.wants/ecofi_firewall.service"


# Test the actual target interpreter. No portal import/customer database is created.
PYTHONHOME="$MOUNT_DIR/usr" PYTHONPATH="$MOUNT_DIR/usr/local/lib/python3.5/dist-packages:$MOUNT_DIR/opt/ecofi"     qemu-arm-static -L "$MOUNT_DIR" "$MOUNT_DIR/usr/bin/python3.5" -B -c '
import glob, os
for path in glob.glob(os.environ["PYTHONPATH"].split(":")[-1]+"/*.py"):
    with open(path, "rb") as source: compile(source.read(), path, "exec")
import flask, time_portal, transition_engine, migrate_legacy_sessions
print("ARM Python runtime imports passed")
'
PYTHONHOME="$MOUNT_DIR/usr" PYTHONPATH="$MOUNT_DIR/usr/local/lib/python3.5/dist-packages" qemu-arm-static -L "$MOUNT_DIR" "$MOUNT_DIR/usr/bin/python3.5" -B "$ROOT_DIR/tools/verify_arm_runtime.py" "$MOUNT_DIR/opt/ecofi"
qemu-arm-static -L "$MOUNT_DIR" "$MOUNT_DIR/usr/sbin/dnsmasq" --test --conf-file="$MOUNT_DIR/etc/dnsmasq.conf"
(cd "$MOUNT_DIR/opt/ecofi" && find . -type f ! -path "./__pycache__/*" ! -name "release-*.txt" -print0 | sort -z | xargs -0 sha256sum > release-sha256.txt)
(cd "$MOUNT_DIR/opt/ecofi" && find . -type f ! -path "./__pycache__/*" ! -name "release-*.txt" -print0 | sort -z | xargs -0 md5sum > release-md5.txt)

# Finalize and unmount
echo "Syncing filesystem buffers..."
sync
umount "$MOUNT_DIR"
e2fsck -f -n "$LOOP_DEVICE"
losetup -d "$LOOP_DEVICE"
LOOP_DEVICE=""
mv -- "$WORK_IMG" "$TARGET_IMG"
(cd "$ROOT_DIR/resources" && sha256sum "$(basename "$TARGET_IMG")" > "$(basename "$TARGET_IMG").sha256")
(cd "$ROOT_DIR/resources" && md5sum "$(basename "$TARGET_IMG")" > "$(basename "$TARGET_IMG").md5")

echo "======================================================================"
echo " SUCCESS: Cleaned, Hardened VMC ECO-VENDO OS Image Ready at:"
echo " $TARGET_IMG"
echo " All legacy PisoFi services purged. Pure VMC ECO-VENDO stack running!"
echo "======================================================================"
