# Audit 01: Original PisoFi v5.3.0 Architecture Teardown

**Source:** `PisoFi_Opi1&PC_v5.3.0-05-10-26_EXT.img` (ext4, offset 4194304)  
**Date:** August 17, 2026

---

## Overview

The PisoFi OS is a commercial Filipino captive-portal WiFi vending system built on Armbian (Debian-based) for Orange Pi One and Raspberry Pi boards. It uses PHP 7.0 + MySQL + Nginx + dnsmasq as its core stack. All business logic PHP scripts are **obfuscated** using [YAK Pro PHP Obfuscator 2.0.5](https://github.com/pk-fr/yakpro-po), making them extremely difficult to read or modify.

The system stores all its operational scripts in a deeply hidden path:
```
/home/pi/.dat/devnull/.../
```
This triple-dot directory nesting is intentionally obscure to prevent casual tampering.

---

## Systemd Services (16 Total)

### Core Services

#### 1. `pisofi_startup.service` → `/home/pi/.dat/devnull/.../s`
**Role:** Master orchestrator. Runs once on boot.

Key actions:
- Runs `sysctl -p` and `rfkill unblock all`
- Starts MySQL (waits until active)
- Calls `check_ifnames()` to normalize network interface names in `/boot/armbianEnv.txt`
- Calls `resizefs` to auto-expand the filesystem on first boot
- Restores `/etc/dnsmasq.conf` from backup if dnsmasq fails to start
- Restores `/etc/nginx/sites-available/default` if Nginx fails to start
- Starts `pisofi_rules`, `pisofi_ngrok`, `pisofi_server`, `pisofi_inspector`, `pisofi_connectionchecker`, `pisofi_cron`, `pisofi_harvester`
- Runs `pauseconnections.php` to pause all sessions from before the reboot
- Runs `pisofier reload` to recalculate all firewall/bandwidth rules
- Runs `pkill -f pppoe-server` to clean stale PPPoE processes
- Restarts `networking` and `dnsmasq` as final step

#### 2. `pisofi_rules.service` → `/home/pi/.dat/devnull/.../r`
**Role:** Firewall initialization.

Key actions:
- Sets `HOST_IP=10.0.0.1`
- Completely flushes all iptables chains (`-F`, `-X`, nat `-F`, mangle `-F`)
- Sets default policies to ACCEPT
- Calls `.../tc startup` to initialize bandwidth control via `tc` (traffic control)
- Calls `pisofier` PHP script to apply per-client firewall rules

#### 3. `pisofi_kicker.service` → `/home/pi/.dat/devnull/.../k` → `kicker.php`
**Role:** Time decrement daemon (the "heartbeat" of the system).

Key actions (from obfuscated PHP):
- Runs in an infinite loop with `sleep(3)` between iterations
- Queries MySQL for the time since last kicker run
- Calculates elapsed seconds: `$qqyKB = max(0, time() - $last_kicker_run)`
- Executes: `UPDATE active_clients SET remaining_time = IF(elapsed > remaining_time, 0, GREATEST(0, remaining_time - elapsed)) WHERE status = 1`
- Also updates `charging_clients` table similarly
- When `remaining_time` reaches 0, calls `pisofier disconnect` to remove iptables/tc rules
- Also handles GPIO pin management for physical charging stations
- Syncs ngrok tunnel info periodically
- Manages `/etc/hosts` file entries for pisofiph.com domain

#### 4. `pisofi_server.service` → `/home/pi/.dat/devnull/.../srv` → `pisofi_server.php`
**Role:** Real-time WebSocket server for UI push notifications.

Technology:
- **Ratchet WebSocket** server listening on `0.0.0.0:8080`
- **ZMQ (React ZMQ)** context for internal IPC on `tcp://*:5555`
- Uses `React\EventLoop` for async event handling
- Provides real-time updates to the portal UI (time remaining, connection status)

#### 5. `pisofi_inspector.service` → `/home/pi/.dat/devnull/.../i`
**Role:** System health watchdog. Runs in infinite loop with `sleep 60`.

Key functions:
- **`check_frozen_time()`**: Queries MySQL for clients whose `updated_at` hasn't changed in 60+ seconds despite `status=1`. If found, restarts `pisofi_kicker` and `pisofi_cron`.
- **`check_disconnected_network()`**: Reads ARP table, cross-references with MySQL `active_clients`. If a paying client's MAC is NOT in ARP, pauses their session (`status=2`). When they reconnect (MAC reappears in ARP), resumes automatically (`status=1`).
- **`check_traffic_control()`**: Verifies that `tc` filter count matches class count. If mismatched, calls `pisofi_resetconnections`.
- **`check_interfaces()`**: Verifies that virtual IFB interfaces (`ifb5898`, `ifb5899`) and the main LAN interface exist. If missing and uptime > 900s, **reboots the device**.
- **`detect_dad()`**: Monitors `/var/log/syslog` for "DAD detected" (IPv6 Duplicate Address Detection). If found, restarts networking, dnsmasq, and PPPoE.
- **`detect_noaddress()`**: Monitors syslog for "DHCP packet received on [interface] which has no address". If found, restarts networking stack.
- **`check_services_restart()`**: At exactly 23:59 every day, restarts dnsmasq.

### Hardware Services

#### 6. `pisofi_coinreader.service` → `/home/pi/.dat/devnull/.../cr`
**Role:** Coin slot reader via GPIO pins.

- Detects board type (Raspberry Pi or Orange Pi) via PHP `getboard.php`
- Calls `/usr/local/bin/pins` — a compiled binary that reads GPIO interrupts for coin insertion
- Different GPIO mappings for different boards

#### 7. `pisofi_coinreaderindicator.service` → `/home/pi/.dat/devnull/.../cri`
**Role:** LED indicator that blinks when the coin reader is active.

#### 8. `pisofi_peripheral.service` → `/home/pi/.dat/devnull/.../p`
**Role:** General GPIO peripheral management (LEDs, buttons, relays).

### Network Services

#### 9. `pisofi_connectionchecker.service` → `/home/pi/.dat/devnull/.../c` → `connectionchecker.php`
**Role:** Periodically checks if the device has internet connectivity upstream.

#### 10. `pisofi_cron.service` → `/home/pi/.dat/devnull/.../cron`
**Role:** PHP-based cron scheduler for periodic maintenance tasks.

#### 11. `pisofi_harvester.service` → `/home/pi/.dat/devnull/.../h` → `tcharvester`
**Role:** Traffic control statistics harvester. Reads `tc` counters and stores bandwidth usage per client in MySQL.

### Remote Management Services

#### 12. `pisofi_ngrok.service` → `/home/pi/.dat/devnull/.../ngrok`
**Role:** Starts an Ngrok tunnel to expose the admin panel for remote management.

- Uses `/usr/local/bin/ngrok` (30MB compiled binary embedded in the image)
- Configured via `/home/pi/.ngrok2/ngrok.yml`

#### 13. `pisofi_datasync.service` → `/home/pi/.dat/devnull/.../ds`
**Role:** Cloud data synchronization (disabled by default in v5.3.0).

#### 14. `pisofi_remotebackup.service` → `/home/pi/.dat/devnull/.../rb`
**Role:** Automated database backup to remote server.

#### 15. `pisofi_remotesubscriber.service` → `/home/pi/.dat/devnull/.../rs`
**Role:** Subscribes to remote management commands from PisoFi cloud.

#### 16. `pisofi_charging.service`
**Role:** Manages coin-operated charging stations (physical power outlets with timers).

---

## Key Binaries in `/usr/local/bin/`

### `pisofier` (16KB bash script)
The core firewall and bandwidth manager. Key commands:
- `pisofier reload` — Recalculates all `tc` classes and `iptables` rules for all active clients
- `pisofier reset_ports` — Applies port-forwarding rules
- `pisofier connect <mac> <ip> <mark> <dl_rate> <ul_rate> <ceil>` — Creates `tc` HTB class with download/upload rate limits and `iptables` filter for a specific client
- `pisofier disconnect <mac> <ip> <mark>` — Removes the client's `tc` class and filters
- `pisofier reset` — Full firewall/bandwidth reset

Uses `tc` with HTB (Hierarchical Token Bucket) queuing discipline and IFB (Intermediate Functional Block) devices for upload shaping.

### `pisofi_resetconnections` (7KB bash script)
Recalculates per-client bandwidth allocation based on:
- Total number of connected clients
- Global bandwidth limits (download/upload)
- Per-client rate limits (if configured)
- Reads all settings from MySQL `connection_settings` JSON column

### `site_control` (3KB bash script)
Domain-level access control:
- Reads blocked/allowed domains from MySQL
- Creates `iptables` rules to DROP or ACCEPT traffic to specific domains

### `mac_control` (3.5KB bash script)
MAC address control:
- Reads MAC whitelist/blacklist from MySQL
- Creates `iptables` rules to DROP or ACCEPT traffic from specific MACs

---

## Database Schema (MySQL)

Database: `pisofi`, User: `wipi`, Password: `wipi`

Key tables (inferred from PHP/Bash scripts):
- `active_clients` — Currently connected WiFi clients (mac, ip_address, remaining_time, status, mark, allow_pause, pause_count, data JSON, session_id, last_paused, admin_pause_override)
- `desktop_clients` — Wired/ethernet clients (same schema as active_clients)
- `charging_clients` — Coin-op charging station sessions (mac, ip_address, connection_time, remaining_time, pin_name, status, sent, remarks, client_id)
- `connection_sessions` — Historical connection sessions
- `settings` — Key-value configuration store (netcard, access_point, bandwidth_limit, connection_settings JSON, remote_settings JSON, last_kicker_run, last_ngrok_sync, is_registered, etc.)
- `networks` — Network interface configuration (interface_name, ifb_name, status, is_wan)
- `bottle_logs` — (if coin-operated bottle vending is configured)

---

## Network Configuration

### `/etc/dnsmasq.conf`
```
listen-address=10.0.0.1
interface=eth1
dhcp-range=eth1,10.0.0.100,10.0.31.254,255.255.224.0,72h
dhcp-option=eth1,6,10.0.0.1,1.1.1.1,1.0.0.1
dhcp-option=160,http://portal.pisofiapp.com
dhcp-option=114,http://portal.pisofiapp.com
address=/portal.pisofiapp.com/10.0.0.1
server=1.1.1.1
server=1.0.0.1
```

Key observations:
- LAN interface is `eth1` (not `eth0`) — this is the WiFi AP bridge
- DHCP lease max: 20,000 clients
- DNS captive portal domain: `portal.pisofiapp.com` hijacked to `10.0.0.1`
- Apple captive detection is handled by resolving real Apple IPs to prevent false-positive captive portal loops

### `/etc/nginx/sites-available/default`
Four server blocks:
1. **Default (port 80)** — Catches all requests, redirects captive portal probes to `portal.pisofiapp.com`, proxies WebSocket on `/ws` to port 8080
2. **portal.pisofiapp.com (port 80)** — Serves the PHP captive portal
3. **10.0.0.1 (port 80)** — Direct IP access to the portal
4. **Port 88** — Secondary portal with WebSocket support

All use PHP-FPM (`php7.0-fpm.sock`) with 600s timeout.

### `/etc/network/interfaces`
```
source /etc/network/interfaces.d/*
auto lo
iface lo inet loopback
```
Network is managed by NetworkManager, with interface-specific configs in `interfaces.d/`.

---

## Environment Variables (`/etc/environment`)
The system loads MySQL credentials and other configuration from `/etc/environment`:
- `KCFGDBU` — MySQL username
- `KCFGDBP` — MySQL password  
- `KCFGDBN` — MySQL database name

These are sourced by every shell script via `source /etc/environment`.
