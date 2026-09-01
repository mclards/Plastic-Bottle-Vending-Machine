import os
import atexit
import secrets
import ipaddress
import re
import sqlite3
import threading
import json
import time
import random
import string
import platform
import subprocess
import urllib.request
import urllib.parse
import io
from datetime import datetime
from collections import deque
from flask import Flask, request, render_template_string, jsonify, session, redirect, url_for, Response, send_file
from werkzeug.security import generate_password_hash, check_password_hash
import logging

try:
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
except ImportError:
    openpyxl = None

try:
    import serial
except ImportError:
    serial = None

from esp32_simulator import ESP32Simulator
import license_manager

log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

app = Flask(__name__, static_folder="static")
# Secret key is initialized dynamically before app runs

DB_PATH = "vendo_sessions.db"

active_clients = {}
active_clients_lock = threading.RLock()
active_depositor_ip = None
active_depositor_timeout = 0
ser = None

def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS stats (date TEXT PRIMARY KEY, total_bottles INTEGER)''')
        c.execute('''CREATE TABLE IF NOT EXISTS admins (username TEXT PRIMARY KEY, password_hash TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS vouchers (code TEXT PRIMARY KEY, minutes INTEGER, is_used INTEGER DEFAULT 0, created_at TEXT, used_by TEXT, note TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS time_transfers (code TEXT PRIMARY KEY, from_ip TEXT, from_mac TEXT, seconds INTEGER, created_at REAL, is_claimed INTEGER DEFAULT 0)''')
        c.execute('''CREATE TABLE IF NOT EXISTS members (username TEXT PRIMARY KEY, pin_hash TEXT, wallet_minutes INTEGER DEFAULT 0, created_at TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS mac_control (mac TEXT PRIMARY KEY, type TEXT, note TEXT, dl_kbps INTEGER DEFAULT 0, ul_kbps INTEGER DEFAULT 0)''')
        c.execute('''CREATE TABLE IF NOT EXISTS promo_rates (bottles INTEGER PRIMARY KEY, minutes INTEGER, label TEXT, speed_profile TEXT DEFAULT '')''')
        c.execute('''CREATE TABLE IF NOT EXISTS announcements (id INTEGER PRIMARY KEY AUTOINCREMENT, message TEXT, active INTEGER DEFAULT 1)''')
        c.execute('''CREATE TABLE IF NOT EXISTS walled_garden (domain TEXT PRIMARY KEY, note TEXT)''')
        
        # Migrations
        try: c.execute("ALTER TABLE mac_control ADD COLUMN dl_kbps INTEGER DEFAULT 0")
        except sqlite3.OperationalError: pass
        try: c.execute("ALTER TABLE mac_control ADD COLUMN ul_kbps INTEGER DEFAULT 0")
        except sqlite3.OperationalError: pass
        try: c.execute("ALTER TABLE vouchers ADD COLUMN note TEXT")
        except sqlite3.OperationalError: pass
        try: c.execute("ALTER TABLE promo_rates ADD COLUMN speed_profile TEXT DEFAULT ''")
        except sqlite3.OperationalError: pass
        try: c.execute("ALTER TABLE active_sessions ADD COLUMN paused_at REAL DEFAULT 0")
        except sqlite3.OperationalError: pass
        try: c.execute("ALTER TABLE active_sessions ADD COLUMN expires_at REAL DEFAULT 0")
        except sqlite3.OperationalError: pass

        # Default configs
        c.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('minutes_per_bottle', '10')")
        c.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('drop_timeout', '30')")
        c.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('default_dl_kbps', '3072')")
        c.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('default_ul_kbps', '1536')")
        c.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('custom_css', '')")
        c.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('telegram_bot_token', '')")
        c.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('telegram_chat_id', '')")
        c.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('telegram_alert_bin', '1')")
        c.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('telegram_alert_daily', '1')")
        c.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('anti_tethering', '1')")
        c.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('vendo_name', 'ECO-Fi Hotspot')")
        c.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('vendo_subtitle', 'Smart Reverse Vending WiFi')")
        c.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('audio_bg', '/static/audio/eco_loop.wav')")
        c.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('audio_insert', '/static/audio/eco_chime.wav')")
        c.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('audio_success', '/static/audio/eco_success.wav')")
        c.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('audio_preset', '/static/audio/eco_chime.wav')")
        c.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('audio_custom_url', '')")
        c.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('audio_volume', '80')")
        
        # Auto-update legacy audio paths to ECO-Fi branded names
        c.execute("UPDATE config SET value = '/static/audio/eco_loop.wav' WHERE key = 'audio_bg' AND (value = '/static/audio/b1.wav' OR value = '')")
        c.execute("UPDATE config SET value = '/static/audio/eco_chime.wav' WHERE key = 'audio_insert' AND (value = '/static/audio/coin.wav' OR value = '')")
        c.execute("UPDATE config SET value = '/static/audio/eco_success.wav' WHERE key = 'audio_success' AND (value = '/static/audio/success_ding.wav' OR value = '')")

        # Default promo rates
        c.execute("INSERT OR IGNORE INTO promo_rates (bottles, minutes, label) VALUES (1, 10, '1 Bottle = 10 mins')")
        c.execute("INSERT OR IGNORE INTO promo_rates (bottles, minutes, label) VALUES (3, 40, '3 Bottles = 40 mins')")
        c.execute("INSERT OR IGNORE INTO promo_rates (bottles, minutes, label) VALUES (5, 75, '5 Bottles = 1h 15m')")
        c.execute("INSERT OR IGNORE INTO promo_rates (bottles, minutes, label) VALUES (10, 180, '10 Bottles = 3 Hours')")
        
        # Default announcement
        c.execute("INSERT OR IGNORE INTO announcements (id, message, active) VALUES (1, '♻️ Welcome to ECO-Fi! Deposit clean PET plastic bottles to earn high-speed Wi-Fi access.', 1)")

        # Default walled garden free sites
        c.execute("INSERT OR IGNORE INTO walled_garden (domain, note) VALUES ('connectivitycheck.gstatic.com', 'Android Captive Probe')")
        c.execute("INSERT OR IGNORE INTO walled_garden (domain, note) VALUES ('captive.apple.com', 'Apple Captive Probe')")

        default_hash = generate_password_hash("admin123")
        c.execute("INSERT OR IGNORE INTO admins (username, password_hash) VALUES ('admin', ?)", (default_hash,))
        conn.commit()

init_db()

def get_config(key, default=""):
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("SELECT value FROM config WHERE key=?", (key,))
        row = c.fetchone()
        return row[0] if row else default

def get_all_config():
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("SELECT key, value FROM config")
        return {row[0]: row[1] for row in c.fetchall()}

def set_config(key, value):
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("REPLACE INTO config (key, value) VALUES (?, ?)", (key, str(value)))
        conn.commit()

def _initialize_secret_key():
    secret = get_config("flask_secret_key", "")
    if not secret:
        secret = secrets.token_hex(32)
        set_config("flask_secret_key", secret)
    return secret

app.secret_key = _initialize_secret_key()
app.config.update(SESSION_COOKIE_SAMESITE="Lax")

def calculate_minutes_for_bottles(bottles_count):
    if bottles_count <= 0:
        return 0
        
    total_minutes = 0
    remaining_bottles = bottles_count
    
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("SELECT bottles, minutes FROM promo_rates ORDER BY bottles DESC")
        rates = c.fetchall()
        
        if not rates:
            # Fallback if no promo rates exist
            base_rate = int(get_config("minutes_per_bottle", "10"))
            return bottles_count * base_rate
            
        for tier_bottles, tier_minutes in rates:
            if remaining_bottles >= tier_bottles and tier_bottles > 0:
                multiplier = remaining_bottles // tier_bottles
                total_minutes += multiplier * tier_minutes
                remaining_bottles %= tier_bottles
                
        # If there are any remaining bottles (e.g., if no 1-bottle rate exists)
        if remaining_bottles > 0:
            c.execute("SELECT minutes FROM promo_rates WHERE bottles = 1")
            base_row = c.fetchone()
            base_rate = base_row[0] if base_row else int(get_config("minutes_per_bottle", "10"))
            total_minutes += remaining_bottles * base_rate
            
    return total_minutes

def record_bottle_drop(count=1):
    today = datetime.now().strftime('%Y-%m-%d')
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("INSERT OR IGNORE INTO stats (date, total_bottles) VALUES (?, 0)", (today,))
        c.execute("UPDATE stats SET total_bottles = total_bottles + ? WHERE date = ?", (count, today))
        conn.commit()

def send_telegram_alert(custom_msg=None):
    bot_token = get_config('telegram_bot_token')
    chat_id = get_config('telegram_chat_id')
    if not bot_token or not chat_id:
        return False
    try:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        text = custom_msg or "🚨 *ECO-Fi Alert*\n\nThe recycling bin has reached **100% capacity**! Please empty the bin."
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown"
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5)
        return True
    except Exception:
        return False

# ESP32 Simulator & Serial Bridge
def on_esp32_uart_output(raw_msg):
    try:
        data = json.loads(raw_msg)
        event = data.get("event")
        if event == "CREDIT_ADD":
            record_bottle_drop(1)
            # Send OPEN_GATE again for continuous dropping if session is still active
            if active_depositor_ip:
                timeout = int(get_config("drop_timeout", "30") or 30)
                transmit_to_esp32({"cmd": "OPEN_GATE", "timeout": timeout})
        elif event == "REJECTED":
            # Give the user another chance if session is still active
            if active_depositor_ip:
                timeout = int(get_config("drop_timeout", "30") or 30)
                transmit_to_esp32({"cmd": "OPEN_GATE", "timeout": timeout})
        elif event == "BIN_FULL":
            set_config("hw_bin_full", "1")
            if get_config("telegram_alert_bin", "1") == "1":
                send_telegram_alert()
        elif event == "BIN_OK":
            # Bin has been emptied — clear the hardware bin full flag
            set_config("hw_bin_full", "0")
        elif event == "CONFIG_SAVED":
            # ESP32 confirmed it parsed and saved the SET_CONFIG payload to NVS
            print("[ESP32] CONFIG_SAVED ACK received — config applied to hardware.")
    except Exception:
        pass

esp32 = ESP32Simulator(on_serial_output_callback=on_esp32_uart_output)

def transmit_to_esp32(payload_dict):
    msg_str = json.dumps(payload_dict) + "\n"
    esp32.receive_uart(msg_str)
    global ser
    if ser:
        try:
            ser.write(msg_str.encode())
        except Exception:
            pass

# ==============================================================================
# ADVANCED NETWORK MANAGEMENT
# ==============================================================================
import socket

def check_network_health():
    """Monitor for network configuration loss and recover."""
    if platform.system() == "Windows": return
    try:
        lan_iface = get_lan_interface()
        res = subprocess.run(["ip", "addr", "show", lan_iface], capture_output=True, text=True)
        # If we lost our static IP or interface went down entirely
        if "10.0.0.1" not in res.stdout:
            subprocess.run(["systemctl", "restart", "networking"])
            subprocess.run(["systemctl", "restart", "dnsmasq"])
            time.sleep(5)
            setup_firewall()
    except Exception:
        pass

def get_lan_interface():
    """Find the active LAN interface (eth0, eth1, br-lan)."""
    if platform.system() == "Windows": return "eth0"
    try:
        wan_iface = subprocess.run("ip route | grep default | awk '{print $5}'", 
                                   shell=True, capture_output=True, text=True).stdout.strip()
        all_ifaces = subprocess.run("ls /sys/class/net", shell=True, capture_output=True, text=True).stdout.split()
        for iface in ["eth0", "eth1", "br-lan", "wlan0"]:
            if iface in all_ifaces and iface != wan_iface:
                return iface
        return wan_iface or "eth0"
    except Exception:
        return "eth0"

def get_arp_table():
    if platform.system() == "Windows": return {}
    try:
        res = subprocess.run(["arp", "-n"], capture_output=True, text=True)
        arp_map = {}
        for line in res.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 3 and parts[1] == "ether":
                arp_map[parts[0]] = parts[2].lower()
        return arp_map
    except Exception:
        return {}

def get_connected_ips():
    return set(get_arp_table().keys())

def setup_bandwidth_control(interface):
    if platform.system() == "Windows": return
    try:
        subprocess.run(["tc", "qdisc", "del", "dev", interface, "root"], stderr=subprocess.DEVNULL)
        subprocess.run(["tc", "qdisc", "add", "dev", interface, "root", "handle", "1:", "htb", "default", "99"], check=True)
        subprocess.run(["tc", "class", "add", "dev", interface, "parent", "1:", "classid", "1:1", "htb", "rate", "100mbit"], check=True)
        subprocess.run(["tc", "class", "add", "dev", interface, "parent", "1:1", "classid", "1:99", "htb", "rate", "64kbit", "ceil", "128kbit"], check=True)
        
        # Upload Control via IFB
        subprocess.run(["modprobe", "ifb", "numifbs=1"], stderr=subprocess.DEVNULL)
        res = subprocess.run(["ip", "link", "set", "dev", "ifb0", "up"], stderr=subprocess.DEVNULL)
        if res.returncode == 0:
            subprocess.run(["tc", "qdisc", "add", "dev", interface, "ingress"], stderr=subprocess.DEVNULL)
            subprocess.run(["tc", "filter", "add", "dev", interface, "parent", "ffff:", "protocol", "ip", "u32", "match", "u32", "0", "0", "action", "mirred", "egress", "redirect", "dev", "ifb0"], stderr=subprocess.DEVNULL)
            subprocess.run(["tc", "qdisc", "del", "dev", "ifb0", "root"], stderr=subprocess.DEVNULL)
            subprocess.run(["tc", "qdisc", "add", "dev", "ifb0", "root", "handle", "1:", "htb", "default", "99"], check=True)
            subprocess.run(["tc", "class", "add", "dev", "ifb0", "parent", "1:", "classid", "1:1", "htb", "rate", "100mbit"], check=True)
            subprocess.run(["tc", "class", "add", "dev", "ifb0", "parent", "1:1", "classid", "1:99", "htb", "rate", "64kbit", "ceil", "128kbit"], check=True)
    except Exception:
        pass

def apply_client_bandwidth(ip, dl_kbps, ul_kbps):
    if platform.system() == "Windows": return
    interface = get_lan_interface()
    try:
        ip_int = int(ipaddress.IPv4Address(ip))
        # Ensure mark avoids reserved root class (1:1) and default unauth class (1:99)
        mark = 100 + (ip_int & 0x3FFF)
        # Download (egress on LAN interface towards client)
        subprocess.run(["tc", "class", "replace", "dev", interface, "parent", "1:1", "classid", f"1:{mark}", "htb", "rate", f"{dl_kbps}kbit", "ceil", f"{dl_kbps}kbit", "burst", "15k"], check=True)
        subprocess.run(["tc", "filter", "replace", "dev", interface, "protocol", "ip", "parent", "1:", "prio", str(mark), "u32", "match", "ip", "dst", f"{ip}/32", "flowid", f"1:{mark}"], check=True)
        
        # Upload (egress on ifb0 from client)
        res = subprocess.run(["ip", "link", "show", "ifb0"], stderr=subprocess.DEVNULL)
        if res.returncode == 0:
            subprocess.run(["tc", "class", "replace", "dev", "ifb0", "parent", "1:1", "classid", f"1:{mark}", "htb", "rate", f"{ul_kbps}kbit", "ceil", f"{ul_kbps}kbit", "burst", "15k"], check=True)
            subprocess.run(["tc", "filter", "replace", "dev", "ifb0", "protocol", "ip", "parent", "1:", "prio", str(mark), "u32", "match", "ip", "src", f"{ip}/32", "flowid", f"1:{mark}"], check=True)
    except Exception:
        pass

def remove_client_bandwidth(ip):
    if platform.system() == "Windows": return
    interface = get_lan_interface()
    try:
        ip_int = int(ipaddress.IPv4Address(ip))
        mark = 100 + (ip_int & 0x3FFF)
        subprocess.run(["tc", "filter", "del", "dev", interface, "protocol", "ip", "parent", "1:", "prio", str(mark)], stderr=subprocess.DEVNULL)
        subprocess.run(["tc", "class", "del", "dev", interface, "parent", "1:1", "classid", f"1:{mark}"], stderr=subprocess.DEVNULL)
        
        res = subprocess.run(["ip", "link", "show", "ifb0"], stderr=subprocess.DEVNULL)
        if res.returncode == 0:
            subprocess.run(["tc", "filter", "del", "dev", "ifb0", "protocol", "ip", "parent", "1:", "prio", str(mark)], stderr=subprocess.DEVNULL)
            subprocess.run(["tc", "class", "del", "dev", "ifb0", "parent", "1:1", "classid", f"1:{mark}"], stderr=subprocess.DEVNULL)
    except Exception:
        pass

def sync_client_firewall(ip):
    with active_clients_lock:
        if ip not in active_clients: return
        sess = active_clients[ip]
        dl = sess.get("dl_kbps") or int(get_config("default_dl_kbps", "3072"))
        ul = sess.get("ul_kbps") or int(get_config("default_ul_kbps", "1536"))
        if sess["remaining_seconds"] > 0 and not sess.get("is_paused", False):
            update_firewall(ip, "add", sess["remaining_seconds"], dl, ul)
        else:
            update_firewall(ip, "del")

def apply_walled_garden_and_macs():
    if platform.system() == "Windows": return
    
    # Create/flush custom chains
    subprocess.run(["iptables", "-t", "nat", "-N", "ECOFI_WALLED_GARDEN"], stderr=subprocess.DEVNULL)
    subprocess.run(["iptables", "-t", "nat", "-F", "ECOFI_WALLED_GARDEN"], stderr=subprocess.DEVNULL)
    # Ensure it's linked from PREROUTING (before portal redirect)
    res = subprocess.run(["iptables", "-t", "nat", "-C", "PREROUTING", "-j", "ECOFI_WALLED_GARDEN"], stderr=subprocess.DEVNULL)
    if res.returncode != 0:
        subprocess.run(["iptables", "-t", "nat", "-I", "PREROUTING", "1", "-j", "ECOFI_WALLED_GARDEN"])

    subprocess.run(["iptables", "-N", "ECOFI_MAC_BLOCK"], stderr=subprocess.DEVNULL)
    subprocess.run(["iptables", "-F", "ECOFI_MAC_BLOCK"], stderr=subprocess.DEVNULL)
    # Ensure it's linked from FORWARD
    res = subprocess.run(["iptables", "-C", "FORWARD", "-j", "ECOFI_MAC_BLOCK"], stderr=subprocess.DEVNULL)
    if res.returncode != 0:
        subprocess.run(["iptables", "-I", "FORWARD", "1", "-j", "ECOFI_MAC_BLOCK"])

    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        
        # Walled Garden
        try:
            c.execute("SELECT domain FROM walled_garden")
            for row in c.fetchall():
                domain = row[0]
                ips = socket.getaddrinfo(domain, None)
                for ip_info in ips:
                    ip = ip_info[4][0]
                    subprocess.run(["iptables", "-t", "nat", "-A", "ECOFI_WALLED_GARDEN", "-d", ip, "-j", "ACCEPT"])
        except Exception: pass
        
        # MAC Control (Block only here, Whitelist is handled dynamically in time_daemon)
        try:
            c.execute("SELECT mac FROM mac_control WHERE type='block'")
            for row in c.fetchall():
                mac = row[0]
                subprocess.run(["iptables", "-A", "ECOFI_MAC_BLOCK", "-m", "mac", "--mac-source", mac, "-j", "DROP"])
        except Exception: pass

# ==============================================================================
# FIREWALL & IPSET MANAGEMENT
# ==============================================================================
def setup_firewall():
    if platform.system() == "Windows":
        return
    try:
        subprocess.run(["ipset", "create", "ecofi_auth", "hash:ip", "timeout", "86400", "-exist"], check=True)
        
        # Check rule first, then insert if missing
        res = subprocess.run(["iptables", "-t", "nat", "-C", "PREROUTING", "-m", "set", "--match-set", "ecofi_auth", "dst", "-j", "ACCEPT"], stderr=subprocess.DEVNULL)
        if res.returncode != 0:
            subprocess.run(["iptables", "-t", "nat", "-I", "PREROUTING", "1", "-m", "set", "--match-set", "ecofi_auth", "dst", "-j", "ACCEPT"], check=True)
            
        if get_config("anti_tethering", "1") == "1":
            res = subprocess.run(["iptables", "-t", "mangle", "-C", "POSTROUTING", "-j", "TTL", "--ttl-set", "64"], stderr=subprocess.DEVNULL)
            if res.returncode != 0:
                subprocess.run(["iptables", "-t", "mangle", "-A", "POSTROUTING", "-j", "TTL", "--ttl-set", "64"], check=True)
                           
        # NET-03: FORWARD Chain Rules
        subprocess.run(["iptables", "-P", "FORWARD", "DROP"])
        
        res = subprocess.run(["iptables", "-C", "FORWARD", "-m", "set", "--match-set", "ecofi_auth", "src", "-j", "ACCEPT"], stderr=subprocess.DEVNULL)
        if res.returncode != 0:
            subprocess.run(["iptables", "-A", "FORWARD", "-m", "set", "--match-set", "ecofi_auth", "src", "-j", "ACCEPT"])
            
        res = subprocess.run(["iptables", "-C", "FORWARD", "-m", "state", "--state", "ESTABLISHED,RELATED", "-j", "ACCEPT"], stderr=subprocess.DEVNULL)
        if res.returncode != 0:
            subprocess.run(["iptables", "-A", "FORWARD", "-m", "state", "--state", "ESTABLISHED,RELATED", "-j", "ACCEPT"])
            
        res = subprocess.run(["iptables", "-C", "FORWARD", "-p", "udp", "--dport", "53", "-j", "ACCEPT"], stderr=subprocess.DEVNULL)
        if res.returncode != 0:
            subprocess.run(["iptables", "-A", "FORWARD", "-p", "udp", "--dport", "53", "-j", "ACCEPT"])
            
        # Also allow TCP DNS
        res = subprocess.run(["iptables", "-C", "FORWARD", "-p", "tcp", "--dport", "53", "-j", "ACCEPT"], stderr=subprocess.DEVNULL)
        if res.returncode != 0:
            subprocess.run(["iptables", "-A", "FORWARD", "-p", "tcp", "--dport", "53", "-j", "ACCEPT"])
        
        # Initialize Bandwidth Control
        setup_bandwidth_control(get_lan_interface())
        # Run apply_walled_garden_and_macs initially
        apply_walled_garden_and_macs()
        
    except Exception:
        pass

def update_firewall(ip, action, timeout_sec=0, dl_kbps=3072, ul_kbps=1536):
    if platform.system() == "Windows":
        return
    try:
        ip = str(ipaddress.ip_address(ip))
    except ValueError:
        return
    try:
        if action == "add":
            subprocess.run(["ipset", "add", "ecofi_auth", ip, "timeout", str(int(timeout_sec)), "-exist"], check=True)
            apply_client_bandwidth(ip, dl_kbps, ul_kbps)
        elif action == "del":
            subprocess.run(["ipset", "del", "ecofi_auth", ip, "-exist"], check=True)
            remove_client_bandwidth(ip)
    except Exception:
        pass

def sync_client_firewall(ip):
    with active_clients_lock:
        if ip not in active_clients: return
        sess = active_clients[ip]
        if sess["remaining_seconds"] > 0 and not sess.get("is_paused", False):
            update_firewall(ip, "add", sess["remaining_seconds"], sess.get("dl_kbps", 3072), sess.get("ul_kbps", 1536))
        else:
            update_firewall(ip, "del")

# Client & Time Background Daemon
# ==============================================================================
# MATHEMATICAL DYNAMIC EXPIRATION & HARDENED SESSION ENGINE
# ==============================================================================
import math

def calculate_pause_validity_seconds(remaining_seconds):
    """
    Computes dynamic validity duration in seconds based on remaining credit.
    Formula: V(T) = min(720h, max(24h, 12h + 1.2*sqrt(Mins) + 0.025*Mins))
    Minimum: 24 Hours (86,400s)
    Maximum: 30 Days (2,592,000s)
    """
    if remaining_seconds <= 0:
        return 0
    mins = remaining_seconds / 60.0
    validity_hours = 12.0 + (1.2 * math.sqrt(mins)) + (0.025 * mins)
    validity_hours = max(24.0, min(720.0, validity_hours))
    return int(validity_hours * 3600)

def compute_session_expiration(remaining_seconds, paused_at=None):
    """Returns the absolute Unix timestamp when the paused session expires."""
    if remaining_seconds <= 0:
        return 0
    base_time = paused_at or time.time()
    return int(base_time + calculate_pause_validity_seconds(remaining_seconds))

def save_sessions_to_db():
    with active_clients_lock:
        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            c.execute('''CREATE TABLE IF NOT EXISTS active_sessions (
                ip TEXT PRIMARY KEY, mac TEXT, remaining_seconds INTEGER, 
                is_paused INTEGER, dl_kbps INTEGER, ul_kbps INTEGER, 
                pending_bottles INTEGER, paused_at REAL, expires_at REAL, saved_at REAL)''')
            c.execute("DELETE FROM active_sessions")
            for ip, s in active_clients.items():
                c.execute("""INSERT INTO active_sessions 
                    (ip, mac, remaining_seconds, is_paused, dl_kbps, ul_kbps, pending_bottles, paused_at, expires_at, saved_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (ip, s["mac"], s["remaining_seconds"], 
                     1 if s.get("is_paused", False) else 0,
                     s.get("dl_kbps", 3072), s.get("ul_kbps", 1536),
                     s.get("pending_bottles", 0),
                     s.get("paused_at", 0),
                     s.get("expires_at", 0),
                     time.time()))
            conn.commit()

def restore_sessions_from_db():
    default_dl = int(get_config("default_dl_kbps", "3072"))
    default_ul = int(get_config("default_ul_kbps", "1536"))
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        try:
            c.execute('''CREATE TABLE IF NOT EXISTS active_sessions (
                ip TEXT PRIMARY KEY, mac TEXT, remaining_seconds INTEGER, 
                is_paused INTEGER, dl_kbps INTEGER, ul_kbps INTEGER, 
                pending_bottles INTEGER, paused_at REAL, expires_at REAL, saved_at REAL)''')
            c.execute("SELECT ip, mac, remaining_seconds, is_paused, dl_kbps, ul_kbps, pending_bottles, paused_at, expires_at FROM active_sessions")
            rows = c.fetchall()
            now = time.time()
            with active_clients_lock:
                for row in rows:
                    ip, mac, remaining, is_paused, dl, ul, pending, paused_at, expires_at = row
                    # Purge expired sessions on restore
                    if is_paused and expires_at and expires_at > 0 and now > expires_at:
                        continue
                    if remaining > 0:
                        client_dl = dl if (dl and dl not in [5120, 2048]) else default_dl
                        client_ul = ul if (ul and ul not in [5120, 1024]) else default_ul
                        active_clients[ip] = {
                            "mac": mac,
                            "remaining_seconds": remaining,
                            "is_paused": bool(is_paused),
                            "dl_kbps": client_dl,
                            "ul_kbps": client_ul,
                            "pending_bottles": pending,
                            "paused_at": paused_at or 0,
                            "expires_at": expires_at or 0
                        }
                        sync_client_firewall(ip)
            c.execute("DELETE FROM active_sessions")
            conn.commit()
        except sqlite3.OperationalError:
            pass

atexit.register(save_sessions_to_db)

def time_daemon():
    setup_firewall()
    tick = 0
    while True:
        tick += 1
        now = time.time()
        
        # Periodic Tasks with proper tick handling
        if tick % 30 == 0:
            save_sessions_to_db()
            
        if tick % 60 == 0:
            arp_table = get_arp_table()
            connected_ips = set(arp_table.keys())
            
            # Dynamically enforce MAC Whitelist
            if platform.system() != "Windows":
                try:
                    with sqlite3.connect(DB_PATH) as conn:
                        c = conn.cursor()
                        c.execute("SELECT mac FROM mac_control WHERE type='whitelist'")
                        whitelisted_macs = {row[0].lower() for row in c.fetchall()}
                        for ip, mac in arp_table.items():
                            if mac in whitelisted_macs:
                                subprocess.run(["ipset", "add", "ecofi_auth", ip, "-exist"])
                except Exception:
                    pass

            # Auto-pause on disconnect ONLY if explicitly enabled in config and valid ARP data exists on Linux
            auto_pause_enabled = (get_config("auto_pause_disconnect", "0") == "1")
            if auto_pause_enabled and platform.system() != "Windows" and len(connected_ips) > 0:
                with active_clients_lock:
                    for ip, session_data in list(active_clients.items()):
                        if ip != "127.0.0.1" and session_data["remaining_seconds"] > 0:
                            if ip not in connected_ips and not session_data.get("is_paused"):
                                session_data["is_paused"] = True
                                session_data["auto_paused"] = True
                                session_data["paused_at"] = now
                                session_data["expires_at"] = compute_session_expiration(session_data["remaining_seconds"], now)
                                sync_client_firewall(ip)
                            elif ip in connected_ips and session_data.get("auto_paused") and not session_data.get("user_paused") and not session_data.get("admin_paused"):
                                session_data["is_paused"] = False
                                session_data["auto_paused"] = False
                                session_data["paused_at"] = 0
                                session_data["expires_at"] = 0
                                sync_client_firewall(ip)
            
        if tick % 300 == 0:
            check_network_health()
        if tick % 3600 == 0:
            apply_walled_garden_and_macs()
        
        with active_clients_lock:
            for ip, session_data in list(active_clients.items()):
                # Hardened Dynamic Expiration Check for paused sessions
                if session_data.get("is_paused") and session_data.get("expires_at", 0) > 0:
                    if now > session_data["expires_at"]:
                        # Paused session expired mathematically
                        session_data["remaining_seconds"] = 0
                        session_data["is_paused"] = False
                        session_data["expires_at"] = 0
                        sync_client_firewall(ip)
                        continue

                was_active = session_data["remaining_seconds"] > 0 and not session_data.get("is_paused", False)
                if was_active:
                    session_data["remaining_seconds"] -= 1
                    if session_data["remaining_seconds"] <= 0:
                        sync_client_firewall(ip)
        
        time.sleep(1)

def ensure_client_session(ip):
    with active_clients_lock:
        if ip in active_clients:
            return active_clients[ip]
            
        arp_table = get_arp_table()
        mac = arp_table.get(ip, "").lower()
        if not mac:
            octets = [f"{(hash(ip + str(i)) & 0xFF):02X}" for i in range(6)]
            mac = ":".join(octets).lower()
        
        # Dual-Key Reconciliation: Check if client changed DHCP IP address
        for old_ip, old_sess in list(active_clients.items()):
            if old_ip != ip and old_sess.get("mac", "").lower() == mac and old_sess.get("remaining_seconds", 0) > 0:
                active_clients[ip] = old_sess
                del active_clients[old_ip]
                sync_client_firewall(ip)
                return active_clients[ip]
                
        active_clients[ip] = {
            "mac": mac,
            "pending_bottles": 0,
            "remaining_seconds": 0,
            "is_paused": False,
            "paused_at": 0,
            "expires_at": 0,
            "dl_kbps": int(get_config("default_dl_kbps", "3072")),
            "ul_kbps": int(get_config("default_ul_kbps", "1536"))
        }
        return active_clients[ip]


# ==============================================================================
# HTML TEMPLATES (AUTHENTIC FILIPINO PISOFI CLIENT EXPERIENCE)
# ==============================================================================

PORTAL_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>{{ vendo_name }} - Reverse Vending WiFi Portal</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/5.15.4/css/all.min.css">
    <style>
        * { box-sizing: border-box; }
        body { 
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; 
            margin: 0; padding: 0; min-height: 100vh;
            background: linear-gradient(rgba(15, 23, 42, 0.93), rgba(15, 23, 42, 0.97)), url('/static/banner.jpg') no-repeat center center fixed;
            background-size: cover;
            color: white; 
            display: flex; flex-direction: column; align-items: center; 
        }

        .portal-container {
            width: 94%; max-width: 440px;
            background: rgba(30, 41, 59, 0.85);
            backdrop-filter: blur(20px);
            -webkit-backdrop-filter: blur(20px);
            border-radius: 20px;
            box-shadow: 0 20px 45px -10px rgba(0,0,0,0.7);
            border: 1px solid rgba(255, 255, 255, 0.14);
            padding: 15px;
            text-align: center;
            margin-bottom: 15px;
            position: relative;
        }

        .brand-banner-box {
            width: 100%;
            border-radius: 12px;
            overflow: hidden;
            margin-bottom: 10px;
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.4);
            border: 1px solid rgba(255, 255, 255, 0.15);
        }
        .brand-banner-img {
            width: 100%;
            height: auto;
            display: block;
            object-fit: cover;
        }

        .announcement-bar {
            background: rgba(16, 185, 129, 0.12); border-left: 3px solid #10B981;
            padding: 6px 10px; border-radius: 6px; font-size: 11px; color: #a7f3d0;
            margin-bottom: 10px; text-align: left;
        }

        .bin-full-banner {
            background: rgba(239, 68, 68, 0.2); border-left: 3px solid #ef4444;
            padding: 8px 10px; border-radius: 6px; font-size: 12px; color: #fca5a5;
            margin-bottom: 10px; text-align: left; display: none; font-weight: 600;
        }

        .status-box {
            background: rgba(15, 23, 42, 0.65);
            border-radius: 14px; padding: 12px 14px; margin-bottom: 10px;
            border: 1px solid rgba(16, 185, 129, 0.25);
            box-shadow: inset 0 2px 8px rgba(0,0,0,0.3);
        }
        .time-display { 
            font-size: 34px; font-family: 'SF Mono', 'Roboto Mono', 'Courier New', monospace; 
            font-weight: 700; color: #10B981; margin: 3px 0; 
            letter-spacing: 2px;
        }
        .status-text { font-size: 11px; text-transform: uppercase; letter-spacing: 1.2px; color: #94a3b8; font-weight: 600; }
        .status-badge { display: inline-block; padding: 2px 10px; border-radius: 16px; font-size: 10px; font-weight: 700; }
        .bg-active { background: rgba(16, 185, 129, 0.2); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.4); }
        .bg-paused { background: rgba(245, 158, 11, 0.2); color: #fbbf24; border: 1px solid rgba(245, 158, 11, 0.4); }
        .bg-inactive { background: rgba(239, 68, 68, 0.2); color: #f87171; border: 1px solid rgba(239, 68, 68, 0.4); }
        .bg-binfull { background: rgba(239, 68, 68, 0.3); color: #fca5a5; border: 1px solid #ef4444; }

        .pulse-btn {
            animation: pulse-green 1.8s infinite;
        }
        @keyframes pulse-green {
            0% { transform: scale(0.98); box-shadow: 0 0 0 0 rgba(16, 185, 129, 0.7); }
            70% { transform: scale(1.02); box-shadow: 0 0 0 10px rgba(16, 185, 129, 0); }
            100% { transform: scale(0.98); box-shadow: 0 0 0 0 rgba(16, 185, 129, 0); }
        }

        .action-btn {
            width: 100%; padding: 12px 14px; border-radius: 12px; border: none; font-size: 15px; font-weight: 700;
            color: white; cursor: pointer; transition: all 0.2s ease; margin-bottom: 8px;
            display: flex; align-items: center; justify-content: center; gap: 8px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.25);
        }
        .action-btn:disabled {
            opacity: 0.6; cursor: not-allowed; animation: none !important;
            background: #475569 !important; border-color: #64748b !important;
        }
        .btn-insert { 
            background: linear-gradient(135deg, #10B981 0%, #059669 100%); 
            border: 1px solid #34d399; font-size: 15px; padding: 13px 14px;
        }
        .btn-pause { background: linear-gradient(135deg, #f59e0b 0%, #d97706 100%); border: 1px solid #fbbf24; font-size: 13px; padding: 10px 12px; }
        .btn-resume { background: linear-gradient(135deg, #3b82f6 0%, #2563eb 100%); border: 1px solid #60a5fa; font-size: 13px; padding: 10px 12px; }

        .nav-tabs {
            display: flex; gap: 4px; margin-bottom: 12px; background: rgba(15, 23, 42, 0.5);
            padding: 3px; border-radius: 10px;
        }
        .tab-btn {
            flex: 1; padding: 6px 3px; font-size: 11px; font-weight: 600; color: #94a3b8;
            background: transparent; border: none; border-radius: 7px; cursor: pointer;
            transition: all 0.2s;
        }
        .tab-btn.active { background: #10B981; color: white; }

        .tab-content { display: none; text-align: left; }
        .tab-content.active { display: block; }

        .custom-input {
            width: 100%; padding: 10px 12px; border-radius: 9px; background: rgba(15, 23, 42, 0.8);
            border: 1px solid #334155; color: white; font-size: 13px; margin-bottom: 8px;
        }
        .custom-input:focus { border-color: #10B981; outline: none; }

        .table-info {
            width: 100%; border-collapse: collapse; font-size: 11px; color: #cbd5e1;
            margin-top: 6px;
        }
        .table-info td { padding: 5px 3px; border-bottom: 1px solid rgba(255,255,255,0.05); }

        .modal-overlay {
            display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%;
            background: rgba(15, 23, 42, 0.88); backdrop-filter: blur(12px);
            z-index: 1000; align-items: center; justify-content: center;
        }
        .modal-box {
            width: 90%; max-width: 420px; background: #1E293B; border-radius: 24px;
            padding: 26px 20px; border: 2px solid #10B981; box-shadow: 0 20px 40px rgba(0,0,0,0.8);
            text-align: center; position: relative; overflow: hidden;
        }
        .countdown-circle {
            font-size: 40px; font-weight: 800; color: #f59e0b; margin: 10px 0;
            font-family: monospace;
        }
        .bottle-counter {
            font-size: 26px; font-weight: 800; color: #10B981; margin-bottom: 15px;
        }
        .progress-bar-bg {
            width: 100%; height: 14px; background: #334155; border-radius: 10px;
            overflow: hidden; margin-bottom: 15px;
        }
        .progress-bar-fill {
            height: 100%; width: 100%; background: linear-gradient(90deg, #10B981, #34d399);
            transition: width 0.3s ease;
        }
        .chute-stage-box {
            background: rgba(15, 23, 42, 0.6); padding: 8px 12px; border-radius: 10px;
            font-size: 12px; color: #94a3b8; margin-bottom: 15px; border: 1px dashed rgba(16, 185, 129, 0.3);
        }
        @keyframes drop-in {
            0% { transform: translateY(-50px) rotate(0deg) scale(0.5); opacity: 0; }
            50% { transform: translateY(0) rotate(15deg) scale(1.2); opacity: 1; }
            100% { transform: translateY(0) rotate(0deg) scale(1); opacity: 1; }
        }
        .bottle-pop {
            display: inline-block; animation: drop-in 0.4s ease-out;
        }
    </style>
</head>
<body>

    <div class="portal-container" style="margin-top: 15px;">
        <div class="brand-banner-box">
            <img src="/static/banner-main.jpg" alt="Smart ECO-Fi Vendo" class="brand-banner-img">
        </div>

        {% if announcement %}
        <div class="announcement-bar">
            <i class="fas fa-bullhorn"></i> {{ announcement }}
        </div>
        {% endif %}

        <div id="bin-full-banner" class="bin-full-banner">
            <i class="fas fa-exclamation-triangle"></i> Storage bin is currently full. Machine cannot accept new bottles at this moment.
        </div>

        <div class="status-box">
            <div class="status-text">Available Internet Time</div>
            <div class="time-display" id="time-display">0d 00h:00m:00s</div>
            <div id="status-badge" class="status-badge bg-inactive">DISCONNECTED</div>
        </div>

        <!-- MAIN ACTION: PULSING INSERT BOTTLE BUTTON -->
        <button id="btn-insert" class="action-btn btn-insert pulse-btn" onclick="startDepositSession()">
            <i class="fas fa-recycle"></i> INSERT PLASTIC BOTTLE
        </button>

        <div id="pause-ctrl-box" style="display:none;">
            <button id="btn-pause" class="action-btn btn-pause" onclick="togglePause('pause')">
                <i class="fas fa-pause"></i> PAUSE TIME
            </button>
            <button id="btn-resume" class="action-btn btn-resume" style="display:none;" onclick="togglePause('resume')">
                <i class="fas fa-play"></i> RESUME TIME
            </button>
        </div>

        <!-- MULTI-TAB FEATURES -->
        <div class="nav-tabs">
            <button class="tab-btn active" onclick="switchTab('tab-rates')">Rates</button>
            <button class="tab-btn" onclick="switchTab('tab-voucher')">Voucher</button>
            <button class="tab-btn" onclick="switchTab('tab-transfer')">Transfer</button>
            <button class="tab-btn" onclick="switchTab('tab-member')">Member</button>
        </div>

        <!-- TAB 1: PROMO RATES -->
        <div id="tab-rates" class="tab-content active">
            <div style="font-size: 13px; color:#94a3b8; font-weight:700; margin-bottom:8px;"><i class="fas fa-tags text-success mr-1"></i> RATES & PACKAGES:</div>
            <table class="table-info">
                {% for r in promo_rates %}
                <tr>
                    <td style="padding: 7px 6px;"><strong style="color:#34d399; font-size:13px;">{{ r.bottles }} Bottle{% if r.bottles > 1 %}s{% endif %}</strong></td>
                    <td style="text-align:right; font-weight:700; color:#f8fafc; font-size:13px; padding: 7px 6px;">
                        {% if r.minutes >= 60 %}
                            {% set hrs = (r.minutes // 60) %}
                            {% set mins = (r.minutes % 60) %}
                            {% if mins == 0 %}
                                {{ hrs }} Hour{% if hrs > 1 %}s{% endif %}
                            {% else %}
                                {{ hrs }}h {{ mins }}m
                            {% endif %}
                        {% else %}
                            {{ r.minutes }} mins
                        {% endif %}
                    </td>
                </tr>
                {% endfor %}
            </table>
        </div>

        <!-- TAB 2: VOUCHER REDEMPTION -->
        <div id="tab-voucher" class="tab-content">
            <div style="font-size: 13px; color:#94a3b8; font-weight:700; margin-bottom:6px;">ENTER VOUCHER CODE:</div>
            <input type="text" id="voucher-code-input" class="custom-input" placeholder="e.g. ECO-XXXX" oninput="this.value = this.value.toUpperCase().replace(/[^A-Z0-9-]/g, '')">
            <button class="action-btn btn-insert" style="padding:10px; font-size:14px;" onclick="redeemVoucher()">
                <i class="fas fa-ticket-alt"></i> REDEEM VOUCHER
            </button>
            <div id="voucher-msg" style="font-size:12px; margin-top:4px;"></div>
        </div>

        <!-- TAB 3: TIME TRANSFER -->
        <div id="tab-transfer" class="tab-content">
            <div style="font-size: 12px; color:#94a3b8; font-weight:700; margin-bottom:6px;">SHARE / TRANSFER YOUR TIME:</div>
            <div style="display:flex; gap:6px; margin-bottom:6px;">
                <input type="number" id="transfer-mins-input" class="custom-input" placeholder="Minutes to Share (e.g. 5)" min="1" style="margin-bottom:0; flex:1;">
                <button class="action-btn btn-pause" style="padding:8px 14px; margin-bottom:0; font-size:13px; width:auto; white-space:nowrap;" onclick="generateTransferCode()">
                    <i class="fas fa-share-alt"></i> Share
                </button>
            </div>
            <div id="transfer-code-display" style="font-size:13px; font-weight:800; color:#38bdf8; margin:4px 0; text-align:center;"></div>
            
            <hr style="border:0; border-top:1px solid rgba(255,255,255,0.1); margin:10px 0;">
            <div style="font-size: 12px; color:#94a3b8; font-weight:700; margin-bottom:6px;">CLAIM A TRANSFER CODE:</div>
            <div style="display:flex; gap:6px;">
                <input type="text" id="claim-code-input" class="custom-input" placeholder="6-Digit Code" maxlength="6" style="margin-bottom:0; flex:1;" oninput="this.value = this.value.replace(/[^0-9]/g,'')">
                <button class="action-btn btn-resume" style="padding:8px 14px; margin-bottom:0; font-size:13px; width:auto; white-space:nowrap;" onclick="claimTransferCode()">
                    <i class="fas fa-download"></i> Claim
                </button>
            </div>
            <div id="claim-status-msg" style="font-size:11px; margin-top:4px; text-align:center;"></div>
        </div>

        <!-- TAB 4: MEMBER WALLET -->
        <div id="tab-member" class="tab-content">
            <div id="mem-auth-section">
                <div style="background:rgba(16,185,129,0.12); border-left:3px solid #10B981; padding:8px 10px; border-radius:6px; font-size:11px; color:#a7f3d0; margin-bottom:10px; text-align:left;">
                    <i class="fas fa-shield-alt text-success"></i> <strong>Permanent Zero-Expiry Storage:</strong><br>
                    Register once to deposit bottles or save your Wi-Fi minutes permanently across all your phones, tablets, and devices.
                </div>
                <div style="font-size: 13px; color:#94a3b8; font-weight:700; margin-bottom:6px;">MEMBER LOGIN / REGISTER:</div>
                <input type="text" id="member-user" class="custom-input" placeholder="Username (letters & numbers)" maxlength="20">
                <input type="password" id="member-pin" class="custom-input" placeholder="4 to 6-Digit Secret PIN" maxlength="6" inputmode="numeric">
                <div style="display:flex; gap:8px;">
                    <button class="action-btn btn-insert" style="padding:10px; font-size:13px;" onclick="memberLogin()"><i class="fas fa-sign-in-alt"></i> LOGIN</button>
                    <button class="action-btn btn-pause" style="padding:10px; font-size:13px;" onclick="memberRegister()"><i class="fas fa-user-plus"></i> REGISTER</button>
                </div>
                <div id="member-status" style="font-size:12px; margin-top:8px;"></div>
            </div>

            <!-- Logged In Wallet Manager -->
            <div id="mem-wallet-section" style="display:none; text-align:left;">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
                    <span style="font-weight:700; color:#38bdf8;" id="mem-welcome-user">Member</span>
                    <button class="btn btn-xs" style="background:#ef4444; color:white; border:none; border-radius:6px; padding:3px 8px; font-size:11px; cursor:pointer;" onclick="memberLogout()"><i class="fas fa-sign-out-alt"></i> Logout</button>
                </div>
                <div style="background:rgba(16,185,129,0.15); border:1px solid rgba(16,185,129,0.3); padding:12px; border-radius:12px; margin-bottom:12px; text-align:center;">
                    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:4px;">
                        <span style="font-size:11px; color:#94a3b8; text-transform:uppercase;">Stored Wallet Balance</span>
                        <span style="background:rgba(16,185,129,0.3); color:#bbf7d0; font-size:10px; padding:2px 8px; border-radius:12px; font-weight:700;"><i class="fas fa-infinity"></i> PERPETUAL (Zero Expiry)</span>
                    </div>
                    <div style="font-size:28px; font-weight:800; color:#34d399;" id="mem-wallet-mins">0 Mins</div>
                </div>
                <div style="margin-bottom:8px;">
                    <label style="font-size:11px; color:#94a3b8;">Use Stored Minutes on this Device:</label>
                    <div style="display:flex; gap:6px;">
                        <input type="number" id="use-wallet-mins" class="custom-input" placeholder="Mins" style="margin-bottom:0; width:90px;" min="1">
                        <button class="action-btn btn-insert" style="padding:8px 12px; margin-bottom:0; font-size:12px;" onclick="useMemberWallet()"><i class="fas fa-wifi"></i> Connect</button>
                    </div>
                </div>
                <button class="action-btn btn-pause" style="padding:8px 12px; font-size:12px; margin-top:6px;" onclick="saveSessionToWallet()">
                    <i class="fas fa-save"></i> Save Active Session to Wallet
                </button>
            </div>
        </div>

        <!-- VISUAL GUIDE: ALLOWED VS NOT ALLOWED BOTTLES -->
        <img src="/static/info-graphic.jpg" alt="Bottle Acceptance Guide" style="width: 100%; border-radius: 12px; margin-top: 15px; border: 1px solid rgba(255, 255, 255, 0.15); box-shadow: 0 4px 12px rgba(0, 0, 0, 0.4);">

        <div style="font-size:11px; color:#64748b; margin-top:18px;">
            IP: {{ client_ip }} | MAC: {{ client_mac }}
        </div>
    </div>

    <!-- LIVE DEPOSIT MODAL WITH ANIMATED BOTTLE DROP & STATUS -->
    <div id="deposit-modal" class="modal-overlay">
        <div class="modal-box">
            <h3 style="margin-top:0; color:#34d399;"><i class="fas fa-door-open"></i> AIRLOCK GATE OPEN</h3>
            <div class="chute-stage-box" id="modal-stage-text">
                <i class="fas fa-spinner fa-spin text-success"></i> Ready! Drop your PET plastic bottle into the chute...
            </div>
            
            <div class="countdown-circle" id="modal-timer">30s</div>
            <div class="progress-bar-bg">
                <div id="modal-progress-bar" class="progress-bar-fill"></div>
            </div>

            <div class="bottle-counter">
                <span id="modal-bottle-icon" class="bottle-pop"><i class="fas fa-wine-bottle"></i></span>
                <span id="modal-bottles">0</span> Bottles (<span id="modal-added-time">+0m</span>)
            </div>

            <button class="action-btn btn-insert" onclick="closeDepositSession()">
                <i class="fas fa-check-circle"></i> DONE / START BROWSING
            </button>
        </div>
    </div>

    <script>
        const audioBgSrc = "{{ audio_bg }}";
        const audioInsertSrc = "{{ audio_insert }}";
        const audioSuccessSrc = "{{ audio_success }}";
        const audioVolume = (parseInt("{{ audio_volume or 80 }}") || 80) / 100.0;

        let bgAudioElem = null;
        if (audioBgSrc && audioBgSrc !== 'silent') {
            bgAudioElem = new Audio(audioBgSrc);
            bgAudioElem.loop = true;
            bgAudioElem.volume = audioVolume * 0.5;
        }

        let insertAudioElem = null;
        if (audioInsertSrc && audioInsertSrc !== 'silent' && audioInsertSrc !== 'arcade_powerup' && audioInsertSrc !== 'voice_filipino') {
            insertAudioElem = new Audio(audioInsertSrc);
            insertAudioElem.volume = audioVolume;
            insertAudioElem.load();
        }

        let successAudioElem = null;
        if (audioSuccessSrc && audioSuccessSrc !== 'silent' && audioSuccessSrc !== 'crystal_bell') {
            successAudioElem = new Audio(audioSuccessSrc);
            successAudioElem.volume = audioVolume;
            successAudioElem.load();
        }

        const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
        
        function unlockAudio() {
            if (audioCtx && audioCtx.state === 'suspended') audioCtx.resume();
            if (insertAudioElem) { insertAudioElem.play().then(()=>insertAudioElem.pause()).catch(()=>{}); }
            if (successAudioElem) { successAudioElem.play().then(()=>successAudioElem.pause()).catch(()=>{}); }
        }
        
        document.addEventListener('click', unlockAudio, { once: true });
        
        function playChimeTone(freq, type, duration, gainVal=0.3) {
            try {
                const osc = audioCtx.createOscillator();
                const gain = audioCtx.createGain();
                osc.type = type; osc.frequency.value = freq;
                osc.connect(gain); gain.connect(audioCtx.destination);
                osc.start();
                gain.gain.setValueAtTime(gainVal * audioVolume, audioCtx.currentTime);
                gain.gain.exponentialRampToValueAtTime(0.0001, audioCtx.currentTime + duration);
                osc.stop(audioCtx.currentTime + duration);
            } catch(e){}
        }

        function playInsertChime() {
            if (audioInsertSrc === 'arcade_powerup') {
                playChimeTone(493.88, 'square', 0.08);
                setTimeout(() => playChimeTone(659.25, 'square', 0.08), 80);
                setTimeout(() => playChimeTone(987.77, 'square', 0.25), 160);
            } else if (audioInsertSrc === 'voice_filipino') {
                playChimeTone(587.33, 'sine', 0.2);
                if ('speechSynthesis' in window) {
                    const utter = new SpeechSynthesisUtterance("Salamat sa pag-recycle! Dagdag minuto.");
                    utter.lang = 'tl-PH';
                    window.speechSynthesis.speak(utter);
                }
            } else if (insertAudioElem) {
                insertAudioElem.currentTime = 0;
                insertAudioElem.play().catch(e => {
                    playChimeTone(587.33, 'sine', 0.18);
                    setTimeout(() => playChimeTone(880.00, 'sine', 0.35), 140);
                });
            } else {
                playChimeTone(587.33, 'sine', 0.18);
                setTimeout(() => playChimeTone(880.00, 'sine', 0.35), 140);
            }
        }

        function playSuccessChime() {
            if (audioSuccessSrc === 'crystal_bell') {
                playChimeTone(1046.50, 'sine', 0.6, 0.4);
            } else if (successAudioElem) {
                successAudioElem.currentTime = 0;
                successAudioElem.play().catch(e => {
                    playChimeTone(880.00, 'sine', 0.4);
                });
            } else if (!audioSuccessSrc || audioSuccessSrc !== 'silent') {
                playChimeTone(880.00, 'sine', 0.4);
            }
        }

        let depositActive = false;
        let depositTimer = null;
        let depositSec = 30;
        let initialDepositTimeout = 30;
        let lastBottleCount = 0;
        let localRemainingSeconds = 0;
        let isClientPaused = false;
        let isSystemBinFull = false;

        function switchTab(tabId) {
            document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
            document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
            document.getElementById(tabId).classList.add('active');
            const btn = document.querySelector(`button[onclick="switchTab('${tabId}')"]`);
            if (btn) btn.classList.add('active');
        }

        function formatTime(totalSec) {
            if (totalSec <= 0) return '0d 00h:00m:00s';
            const d = Math.floor(totalSec / 86400);
            const h = Math.floor((totalSec % 86400) / 3600).toString().padStart(2, '0');
            const m = Math.floor((totalSec % 3600) / 60).toString().padStart(2, '0');
            const s = (totalSec % 60).toString().padStart(2, '0');
            return `${d}d ${h}h:${m}m:${s}s`;
        }

        function formatAddedTime(mins) {
            if (mins === 0) return '+0m';
            let res = '';
            const d = Math.floor(mins / 1440);
            const h = Math.floor((mins % 1440) / 60);
            const m = mins % 60;
            if (d > 0) res += `${d}d `;
            if (h > 0) res += `${h}h `;
            res += `${m}m`;
            return '+' + res.trim();
        }

        // Local ticker for smooth countdown
        setInterval(() => {
            if (localRemainingSeconds > 0 && !isClientPaused) {
                localRemainingSeconds--;
                document.getElementById('time-display').innerText = formatTime(localRemainingSeconds);
                if (localRemainingSeconds <= 0) {
                    syncPortal();
                }
            }
        }, 1000);

        function syncPortal() {
            fetch('/api/vendo/status')
                .then(r => r.json())
                .then(data => {
                    localRemainingSeconds = data.client_time_remaining || data.remaining_seconds || 0;
                    isClientPaused = data.is_paused || false;
                    isSystemBinFull = data.bin_full || false;
                    
                    document.getElementById('time-display').innerText = formatTime(localRemainingSeconds);
                    
                    const badge = document.getElementById('status-badge');
                    const pauseBox = document.getElementById('pause-ctrl-box');
                    const btnPause = document.getElementById('btn-pause');
                    const btnResume = document.getElementById('btn-resume');
                    const btnInsert = document.getElementById('btn-insert');
                    const binBanner = document.getElementById('bin-full-banner');

                    // 1. Bin full handling
                    if (isSystemBinFull) {
                        binBanner.style.display = 'block';
                        if (!depositActive) {
                            btnInsert.disabled = true;
                            btnInsert.innerHTML = '<i class="fas fa-ban"></i> BIN FULL - TEMPORARILY DISABLED';
                            btnInsert.classList.remove('pulse-btn');
                        }
                    } else {
                        binBanner.style.display = 'none';
                        if (!depositActive) {
                            btnInsert.disabled = false;
                            btnInsert.innerHTML = '<i class="fas fa-recycle"></i> INSERT PLASTIC BOTTLE';
                            btnInsert.classList.add('pulse-btn');
                        }
                    }

                    // 2. Connection and pause status
                    if (localRemainingSeconds > 0) {
                        pauseBox.style.display = 'block';
                        if (isClientPaused) {
                            badge.className = 'status-badge bg-paused';
                            badge.innerText = 'PAUSED';
                            btnPause.style.display = 'none';
                            btnResume.style.display = 'flex';
                        } else {
                            badge.className = 'status-badge bg-active';
                            badge.innerText = 'CONNECTED';
                            btnPause.style.display = 'flex';
                            btnResume.style.display = 'none';
                        }
                    } else {
                        badge.className = 'status-badge bg-inactive';
                        badge.innerText = isSystemBinFull ? 'BIN FULL' : 'DISCONNECTED';
                        pauseBox.style.display = 'none';
                    }

                    // 3. Deposit modal sync
                    if (depositActive) {
                        const bottles = data.session_bottles || 0;
                        const addedMins = data.session_added_minutes !== undefined ? data.session_added_minutes : 0;
                        document.getElementById('modal-bottles').innerText = bottles;
                        document.getElementById('modal-added-time').innerText = formatAddedTime(addedMins);
                        if (bottles > lastBottleCount) {
                            playInsertChime();
                            
                            const icon = document.getElementById('modal-bottle-icon');
                            icon.classList.remove('bottle-pop');
                            void icon.offsetWidth;
                            icon.classList.add('bottle-pop');

                            document.getElementById('modal-stage-text').innerHTML = 
                                `<span class="text-success font-weight-bold"><i class="fas fa-check-circle"></i> PET Bottle Verified! +${addedMins}m Added.</span>`;
                            
                            lastBottleCount = bottles;
                            depositSec = initialDepositTimeout; // Refresh countdown for next bottle
                        }
                    }
                }).catch(()=>{});
        }

        setInterval(syncPortal, 1200);

        function startDepositSession() {
            const btn = document.getElementById('btn-insert');
            if (btn.disabled || depositActive || isSystemBinFull) return;
            btn.disabled = true;
            
            unlockAudio();
            
            lastBottleCount = 0;
            document.getElementById('modal-bottles').innerText = '0';
            document.getElementById('modal-added-time').innerText = '+0m';
            document.getElementById('modal-stage-text').innerHTML = 
                '<i class="fas fa-spinner fa-spin text-success"></i> Airlock opening... Please wait.';
            
            fetch('/api/vendo/open_gate', { method: 'POST' })
                .then(r => r.json())
                .then(data => {
                    if (!data.success) {
                        alert(data.error || "Machine is in use.");
                        btn.disabled = isSystemBinFull;
                        return;
                    }
                    depositActive = true;
                    initialDepositTimeout = data.timeout || 30;
                    depositSec = initialDepositTimeout;
                    lastBottleCount = 0;
                    document.getElementById('deposit-modal').style.display = 'flex';
                    document.getElementById('modal-stage-text').innerHTML = 
                        '<i class="fas fa-arrow-down text-success"></i> Gate Open! Drop your PET bottle into the chute...';
                    
                    if (bgAudioElem) {
                        bgAudioElem.currentTime = 0;
                        bgAudioElem.play().catch(()=>{});
                    }

                    if (depositTimer) clearInterval(depositTimer);
                    depositTimer = setInterval(() => {
                        depositSec--;
                        document.getElementById('modal-timer').innerText = `${depositSec}s`;
                        const pct = Math.max(0, (depositSec / initialDepositTimeout) * 100);
                        document.getElementById('modal-progress-bar').style.width = `${pct}%`;
                        if (depositSec <= 0) {
                            closeDepositSession();
                        }
                    }, 1000);
                }).catch(err => {
                    btn.disabled = isSystemBinFull;
                });
        }

        function closeDepositSession() {
            if (!depositActive) return;
            depositActive = false;
            clearInterval(depositTimer);
            document.getElementById('deposit-modal').style.display = 'none';
            document.getElementById('btn-insert').disabled = isSystemBinFull;
            
            if (bgAudioElem) {
                bgAudioElem.pause();
                bgAudioElem.currentTime = 0;
            }
            playSuccessChime();
            
            fetch('/api/vendo/done', { method: 'POST' }).then(() => {
                syncPortal();
                setTimeout(() => {
                    fetch('/generate_204', { mode: 'no-cors' }).catch(()=>{});
                }, 300);
            });
        }

        function togglePause(action) {
            fetch('/api/client/pause', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ action: action })
            }).then(()=>syncPortal());
        }

        function redeemVoucher() {
            const code = document.getElementById('voucher-code-input').value.trim();
            if (!code) return;
            fetch('/api/voucher/redeem', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ code: code })
            }).then(r => r.json()).then(data => {
                const msg = document.getElementById('voucher-msg');
                if (data.success) {
                    msg.style.color = '#34d399';
                    msg.innerText = data.message;
                    document.getElementById('voucher-code-input').value = '';
                    playSuccessChime();
                    syncPortal();
                } else {
                    msg.style.color = '#f87171';
                    msg.innerText = data.error;
                }
            });
        }

        function generateTransferCode() {
            const m = parseInt(document.getElementById('transfer-mins-input').value) || 0;
            const disp = document.getElementById('transfer-code-display');
            if (m <= 0) {
                disp.style.color = '#f87171';
                disp.innerText = 'Please enter the exact minutes to share.';
                return;
            }
            fetch('/api/transfer/generate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ minutes: m })
            })
            .then(r => r.json())
            .then(data => {
                if (data.success) {
                    disp.style.color = '#38bdf8';
                    disp.innerHTML = `TRANSFER CODE: <strong style="font-size:16px; letter-spacing:2px; color:#34d399;">${data.code}</strong> (${data.minutes} Mins)`;
                    document.getElementById('transfer-mins-input').value = '';
                    syncPortal();
                } else {
                    disp.style.color = '#f87171';
                    disp.innerText = data.error;
                }
            });
        }

        function claimTransferCode() {
            const code = document.getElementById('claim-code-input').value.trim();
            const msg = document.getElementById('claim-status-msg');
            if (!code) {
                msg.style.color = '#f87171';
                msg.innerText = 'Please enter the 6-digit transfer code.';
                return;
            }
            fetch('/api/transfer/claim', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ code: code })
            }).then(r => r.json()).then(data => {
                if (data.success) {
                    msg.style.color = '#34d399';
                    msg.innerText = data.message;
                    document.getElementById('claim-code-input').value = '';
                    playInsertChime();
                    syncPortal();
                } else {
                    msg.style.color = '#f87171';
                    msg.innerText = data.error;
                }
            });
        }

        let loggedInMember = null;

        function memberLogin() {
            const u = document.getElementById('member-user').value.trim();
            const p = document.getElementById('member-pin').value.trim();
            if (!u || !p) {
                alert('Please enter your username and PIN.');
                return;
            }
            fetch('/api/member/login', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ username: u, pin: p })
            }).then(r => r.json()).then(data => {
                const s = document.getElementById('member-status');
                if (data.success) {
                    loggedInMember = { username: u, pin: p, wallet_minutes: data.wallet_minutes };
                    document.getElementById('mem-auth-section').style.display = 'none';
                    document.getElementById('mem-wallet-section').style.display = 'block';
                    document.getElementById('mem-welcome-user').innerText = `👤 ${u}`;
                    document.getElementById('mem-wallet-mins').innerText = `${data.wallet_minutes} Mins`;
                    s.innerText = '';
                } else {
                    s.style.color = '#f87171'; s.innerText = data.error;
                }
            });
        }

        function memberLogout() {
            loggedInMember = null;
            document.getElementById('mem-auth-section').style.display = 'block';
            document.getElementById('mem-wallet-section').style.display = 'none';
            document.getElementById('member-user').value = '';
            document.getElementById('member-pin').value = '';
        }

        function memberRegister() {
            const u = document.getElementById('member-user').value.trim();
            const p = document.getElementById('member-pin').value.trim();
            if (!u || !p) {
                alert('Please enter a username and PIN.');
                return;
            }
            fetch('/api/member/register', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ username: u, pin: p })
            }).then(r => r.json()).then(data => {
                const s = document.getElementById('member-status');
                if (data.success) {
                    s.style.color = '#34d399';
                    s.innerText = data.message;
                    setTimeout(() => memberLogin(), 400);
                } else {
                    s.style.color = '#f87171';
                    s.innerText = data.error;
                }
            });
        }

        function useMemberWallet() {
            if (!loggedInMember) return;
            const m = parseInt(document.getElementById('use-wallet-mins').value) || 0;
            if (m <= 0) {
                alert('Please enter valid minutes to use.');
                return;
            }
            fetch('/api/member/use_wallet', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ username: loggedInMember.username, pin: loggedInMember.pin, minutes: m })
            }).then(r => r.json()).then(data => {
                if (data.success) {
                    loggedInMember.wallet_minutes = data.wallet_minutes;
                    document.getElementById('mem-wallet-mins').innerText = `${data.wallet_minutes} Mins`;
                    document.getElementById('use-wallet-mins').value = '';
                    playInsertChime();
                    syncPortal();
                    alert(data.message);
                } else {
                    alert(data.error);
                }
            });
        }

        function saveSessionToWallet() {
            if (!loggedInMember) return;
            fetch('/api/member/save_time', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ username: loggedInMember.username, pin: loggedInMember.pin })
            }).then(r => r.json()).then(data => {
                if (data.success) {
                    loggedInMember.wallet_minutes = data.wallet_minutes;
                    document.getElementById('mem-wallet-mins').innerText = `${data.wallet_minutes} Mins`;
                    playSuccessChime();
                    syncPortal();
                    alert(data.message);
                } else {
                    alert(data.error);
                }
            });
        }
    </script>
</body>
</html>
"""

# ==============================================================================
# ROUTE HANDLERS & APIS
# ==============================================================================

@app.route("/")
def index():
    client_ip = request.remote_addr or "127.0.0.1"
    session_data = ensure_client_session(client_ip)
    
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("SELECT bottles, minutes, label FROM promo_rates ORDER BY bottles ASC")
        promos = [{"bottles": r[0], "minutes": r[1], "label": r[2]} for r in c.fetchall()]
        
        c.execute("SELECT message FROM announcements WHERE active = 1 ORDER BY id DESC LIMIT 1")
        ann_row = c.fetchone()
        announcement = ann_row[0] if ann_row else ""

        c.execute("SELECT domain, note FROM walled_garden ORDER BY domain ASC")
        walled_sites = [{"domain": r[0], "note": r[1]} for r in c.fetchall()]

    return render_template_string(
        PORTAL_HTML,
        client_ip=client_ip,
        client_mac=session_data.get("mac", "00:00:00:00:00:00"),
        vendo_name=get_config("vendo_name", "ECO-Fi Hotspot"),
        vendo_subtitle=get_config("vendo_subtitle", "Smart Reverse Vending WiFi"),
        promo_rates=promos,
        announcement=announcement,
        walled_sites=walled_sites,
        audio_bg=get_config("audio_bg", "/static/audio/eco_loop.wav"),
        audio_insert=get_config("audio_insert", "/static/audio/eco_chime.wav"),
        audio_success=get_config("audio_success", "/static/audio/eco_success.wav"),
        audio_volume=get_config("audio_volume", "80")
    )

@app.route("/api/vendo/status")
@app.route("/api/status")
def api_vendo_status():
    client_ip = request.remote_addr or "127.0.0.1"
    session_data = ensure_client_session(client_ip)
    sim_status = esp32.get_state()
    session_bottles = sim_status.get("session_bottles", 0)
    session_added_minutes = calculate_minutes_for_bottles(session_bottles)
    is_bin_full = sim_status.get("is_bin_full", False) or sim_status.get("bin_full_alert", False) or (get_config("hw_bin_full", "0") == "1")
    
    expires_at = session_data.get("expires_at", 0)
    expires_str = ""
    if session_data.get("is_paused") and expires_at > time.time():
        expires_str = datetime.fromtimestamp(expires_at).strftime("%b %d, %I:%M %p")
    elif not session_data.get("is_paused") and session_data.get("remaining_seconds", 0) > 0:
        proj_exp = compute_session_expiration(session_data["remaining_seconds"])
        expires_str = datetime.fromtimestamp(proj_exp).strftime("%b %d, %I:%M %p")

    return jsonify({
        "remaining_seconds": session_data.get("remaining_seconds", 0),
        "client_time_remaining": session_data.get("remaining_seconds", 0),
        "is_paused": session_data.get("is_paused", False),
        "paused_at": session_data.get("paused_at", 0),
        "expires_at": expires_at,
        "expires_str": expires_str,
        "validity_hours": calculate_pause_validity_seconds(session_data.get("remaining_seconds", 0)) // 3600,
        "session_bottles": session_bottles,
        "session_added_minutes": session_added_minutes,
        "gate_open": sim_status.get("entrance_servo", 0) > 45,
        "bin_full": is_bin_full
    })

@app.route("/api/vendo/open_gate", methods=["POST"])
@app.route("/api/open_gate", methods=["POST"])
def api_open_gate():
    global active_depositor_ip, active_depositor_timeout
    client_ip = request.remote_addr or "127.0.0.1"
    
    is_bin_full = (get_config("hw_bin_full", "0") == "1") or esp32.get_state().get("is_bin_full", False)
    if is_bin_full:
        return jsonify({"success": False, "error": "Storage bin is full. Please contact administrator to empty the bin."})

    with active_clients_lock:
        if active_depositor_ip and active_depositor_ip != client_ip:
            if time.time() < active_depositor_timeout:
                return jsonify({"success": False, "error": "Another user is currently depositing bottles. Please wait."})
        
        timeout = int(get_config("drop_timeout", "30") or 30)
        active_depositor_ip = client_ip
        active_depositor_timeout = time.time() + timeout + 5
        
        esp32.reset_session()
        if client_ip in active_clients:
            active_clients[client_ip]["pending_bottles"] = 0
            
    esp32.open_entrance_gate(timeout=timeout)
    transmit_to_esp32({"cmd": "OPEN_GATE", "timeout": timeout})
    return jsonify({"success": True, "timeout": timeout, "session_bottles": 0})

@app.route("/api/vendo/done", methods=["POST"])
def api_vendo_done():
    global active_depositor_ip, active_depositor_timeout
    client_ip = request.remote_addr or "127.0.0.1"
    
    with active_clients_lock:
        if active_depositor_ip and active_depositor_ip != client_ip:
            return jsonify({"success": False, "error": "You are not the active depositor."})
            
        sim_status = esp32.get_state()
        session_bottles = sim_status.get("session_bottles", 0)
        added_minutes = 0
        
        if session_bottles > 0:
            added_minutes = calculate_minutes_for_bottles(session_bottles)
            sess = ensure_client_session(client_ip)
            sess["remaining_seconds"] += added_minutes * 60
            sess["is_paused"] = False
            sess["user_paused"] = False
            sess["auto_paused"] = False
            sess["paused_at"] = 0
            sess["expires_at"] = 0
            sync_client_firewall(client_ip)
            
        active_depositor_ip = None
        active_depositor_timeout = 0
            
    esp32.reset_session()
    esp32.close_entrance_gate()
    transmit_to_esp32({"cmd": "CLOSE_GATE"})
    return jsonify({"success": True, "bottles_credited": session_bottles, "added_minutes": added_minutes})

# Auto-Connect Wi-Fi Captive Probe Handlers (Android, iOS, Windows)
@app.route("/generate_204")
@app.route("/gen_204")
def captive_generate_204():
    client_ip = request.remote_addr or "127.0.0.1"
    session_data = ensure_client_session(client_ip)
    if session_data.get("remaining_seconds", 0) > 0 and not session_data.get("is_paused", False):
        return ('', 204)
    return redirect("/")

@app.route("/hotspot-detect.html")
def captive_hotspot_detect():
    client_ip = request.remote_addr or "127.0.0.1"
    session_data = ensure_client_session(client_ip)
    if session_data.get("remaining_seconds", 0) > 0 and not session_data.get("is_paused", False):
        return "<HTML><HEAD><TITLE>Success</TITLE></HEAD><BODY>Success</BODY></HTML>", 200, {'Content-Type': 'text/html'}
    return redirect("/")

@app.route("/connecttest.txt")
@app.route("/ncsi.txt")
def captive_msft():
    client_ip = request.remote_addr or "127.0.0.1"
    session_data = ensure_client_session(client_ip)
    if session_data.get("remaining_seconds", 0) > 0 and not session_data.get("is_paused", False):
        return "Microsoft NCSI", 200, {'Content-Type': 'text/plain'}
    return redirect("/")

@app.route("/api/client/pause", methods=["POST"])
def api_client_pause():
    client_ip = request.remote_addr or "127.0.0.1"
    data = request.get_json() or {}
    action = data.get("action", "pause")
    with active_clients_lock:
        if client_ip in active_clients:
            sess = active_clients[client_ip]
            if action == "pause":
                sess["is_paused"] = True
                sess["user_paused"] = True
                sess["paused_at"] = time.time()
                sess["expires_at"] = compute_session_expiration(sess["remaining_seconds"], sess["paused_at"])
            else:
                sess["is_paused"] = False
                sess["user_paused"] = False
                sess["paused_at"] = 0
                sess["expires_at"] = 0
            sync_client_firewall(client_ip)
            return jsonify({
                "success": True, 
                "is_paused": sess["is_paused"],
                "expires_at": sess.get("expires_at", 0)
            })
    return jsonify({"success": False})

@app.route("/api/voucher/redeem", methods=["POST"])
def api_voucher_redeem():
    client_ip = request.remote_addr or "127.0.0.1"
    data = request.get_json() or {}
    code = data.get("code", "").strip().upper()
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("SELECT minutes, is_used FROM vouchers WHERE code = ?", (code,))
        row = c.fetchone()
        if not row:
            return jsonify({"success": False, "error": "Invalid voucher code."})
        if row[1] == 1:
            return jsonify({"success": False, "error": "Voucher already redeemed."})
        
        minutes = row[0]
        c.execute("UPDATE vouchers SET is_used = 1, used_by = ? WHERE code = ?", (client_ip, code))
        conn.commit()

    with active_clients_lock:
        sess = ensure_client_session(client_ip)
        sess["remaining_seconds"] += minutes * 60
        sess["is_paused"] = False
        sess["user_paused"] = False
        sess["paused_at"] = 0
        sess["expires_at"] = 0
        sync_client_firewall(client_ip)

    return jsonify({"success": True, "message": f"Successfully added {minutes} minutes!"})

@app.route("/api/transfer/generate", methods=["POST"])
def api_transfer_generate():
    client_ip = request.remote_addr or "127.0.0.1"
    data = request.get_json() or {}
    requested_mins = data.get("minutes")
    
    with active_clients_lock:
        sess = ensure_client_session(client_ip)
        rem = sess.get("remaining_seconds", 0)
        available_mins = rem // 60
        if available_mins < 1:
            return jsonify({"success": False, "error": "Minimum balance to transfer is 1 minute."})
        
        if requested_mins is not None and requested_mins != "":
            try:
                mins_to_transfer = int(requested_mins)
            except (ValueError, TypeError):
                return jsonify({"success": False, "error": "Please enter a valid number of minutes."})
            
            if mins_to_transfer <= 0:
                return jsonify({"success": False, "error": "Transfer minutes must be at least 1 minute."})
            if mins_to_transfer > available_mins:
                return jsonify({"success": False, "error": f"Insufficient balance ({available_mins}m available)."})
        else:
            mins_to_transfer = available_mins

        transfer_sec = mins_to_transfer * 60
        sess["remaining_seconds"] = max(0, rem - transfer_sec)
        sync_client_firewall(client_ip)
        code = ''.join(random.choices(string.digits, k=6))

    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("INSERT INTO time_transfers (code, from_ip, from_mac, seconds, created_at) VALUES (?, ?, ?, ?, ?)",
                  (code, client_ip, sess["mac"], transfer_sec, time.time()))
        conn.commit()

    return jsonify({
        "success": True, 
        "code": code, 
        "minutes": mins_to_transfer,
        "remaining_minutes": sess["remaining_seconds"] // 60
    })

@app.route("/api/transfer/claim", methods=["POST"])
def api_transfer_claim():
    client_ip = request.remote_addr or "127.0.0.1"
    data = request.get_json() or {}
    code = data.get("code", "").strip()
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("SELECT seconds, is_claimed FROM time_transfers WHERE code = ?", (code,))
        row = c.fetchone()
        if not row:
            return jsonify({"success": False, "error": "Invalid transfer code."})
        if row[1] == 1:
            return jsonify({"success": False, "error": "Transfer code already claimed."})
        
        sec = row[0]
        c.execute("UPDATE time_transfers SET is_claimed = 1 WHERE code = ?", (code,))
        conn.commit()

    with active_clients_lock:
        sess = ensure_client_session(client_ip)
        sess["remaining_seconds"] += sec
        sess["is_paused"] = False
        sess["user_paused"] = False
        sess["paused_at"] = 0
        sess["expires_at"] = 0
        sync_client_firewall(client_ip)

    return jsonify({"success": True, "message": f"Claimed {sec // 60} minutes successfully!"})

@app.route("/api/member/register", methods=["POST"])
def api_member_register():
    data = request.get_json() or {}
    username = data.get("username", "").strip()
    pin = data.get("pin", "").strip()
    
    # Hardened Input Validation
    if not username or not re.match(r"^[a-zA-Z0-9_]{3,20}$", username):
        return jsonify({"success": False, "error": "Username must be 3-20 characters (letters, numbers, underscores only)."})
    if not pin or not re.match(r"^\d{4,6}$", pin):
        return jsonify({"success": False, "error": "PIN must be strictly 4 to 6 numeric digits."})
    
    pin_hash = generate_password_hash(pin)
    try:
        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            c.execute("INSERT INTO members (username, pin_hash, wallet_minutes, created_at) VALUES (?, ?, 0, ?)",
                      (username, pin_hash, datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
            conn.commit()
        return jsonify({"success": True, "message": "Account registered successfully! Zero-expiry member wallet is active."})
    except sqlite3.IntegrityError:
        return jsonify({"success": False, "error": "Username already taken. Please choose another."})

login_attempts = {}

@app.route("/api/member/login", methods=["POST"])
def api_member_login():
    data = request.get_json() or {}
    username = data.get("username", "").strip()
    pin = data.get("pin", "").strip()
    
    # Rate Limiting
    now = time.time()
    if username in login_attempts:
        count, last_time = login_attempts[username]
        if now - last_time < 300:
            if count >= 5:
                return jsonify({"success": False, "error": "Too many failed attempts. Try again in 5 minutes."})
        else:
            login_attempts[username] = [0, now]
    else:
        login_attempts[username] = [0, now]
        
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("SELECT pin_hash, wallet_minutes FROM members WHERE username = ?", (username,))
        row = c.fetchone()
        if not row or not check_password_hash(row[0], pin):
            login_attempts[username][0] += 1
            login_attempts[username][1] = time.time()
            return jsonify({"success": False, "error": "Invalid username or PIN."})
            
        if username in login_attempts:
            del login_attempts[username]
        return jsonify({"success": True, "wallet_minutes": row[1]})

@app.route("/api/member/use_wallet", methods=["POST"])
def api_member_use_wallet():
    client_ip = request.remote_addr or "127.0.0.1"
    data = request.get_json() or {}
    username = data.get("username", "").strip()
    pin = data.get("pin", "").strip()
    try:
        minutes = int(data.get("minutes", 0))
    except (ValueError, TypeError):
        return jsonify({"success": False, "error": "Please enter a valid number of minutes."})
        
    if minutes <= 0:
        return jsonify({"success": False, "error": "Invalid minutes specified."})
    
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("SELECT pin_hash, wallet_minutes FROM members WHERE username = ?", (username,))
        row = c.fetchone()
        if not row or not check_password_hash(row[0], pin):
            return jsonify({"success": False, "error": "Invalid username or PIN."})
        
        current_wallet = row[1]
        if current_wallet < minutes:
            return jsonify({"success": False, "error": f"Insufficient wallet balance ({current_wallet}m available)."})
        
        c.execute("UPDATE members SET wallet_minutes = wallet_minutes - ? WHERE username = ?", (minutes, username))
        conn.commit()

    with active_clients_lock:
        sess = ensure_client_session(client_ip)
        sess["remaining_seconds"] += minutes * 60
        sess["is_paused"] = False
        sess["user_paused"] = False
        sess["paused_at"] = 0
        sess["expires_at"] = 0
        sync_client_firewall(client_ip)

    return jsonify({"success": True, "message": f"Added {minutes} minutes from wallet to session!", "wallet_minutes": current_wallet - minutes})

@app.route("/api/member/save_time", methods=["POST"])
def api_member_save_time():
    client_ip = request.remote_addr or "127.0.0.1"
    data = request.get_json() or {}
    username = data.get("username", "").strip()
    pin = data.get("pin", "").strip()
    
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("SELECT pin_hash, wallet_minutes FROM members WHERE username = ?", (username,))
        row = c.fetchone()
        if not row or not check_password_hash(row[0], pin):
            return jsonify({"success": False, "error": "Invalid username or PIN."})
        
        with active_clients_lock:
            sess = ensure_client_session(client_ip)
            rem_sec = sess.get("remaining_seconds", 0)
            if rem_sec < 60:
                return jsonify({"success": False, "error": "No active session time to save (minimum 1 minute required)."})
            
            mins_to_save = rem_sec // 60
            sess["remaining_seconds"] = 0
            sess["is_paused"] = False
            sess["paused_at"] = 0
            sess["expires_at"] = 0
            sync_client_firewall(client_ip)
        
        c.execute("UPDATE members SET wallet_minutes = wallet_minutes + ? WHERE username = ?", (mins_to_save, username))
        conn.commit()
        
        c.execute("SELECT wallet_minutes FROM members WHERE username = ?", (username,))
        new_wallet = c.fetchone()[0]

    return jsonify({"success": True, "message": f"Saved {mins_to_save} minutes to your member wallet!", "wallet_minutes": new_wallet})

# Licensing Endpoints
@app.route("/admin/api/license", methods=["GET"])
def admin_api_license():
    if not session.get('admin_logged_in'): return jsonify({"error": "unauthorized"}), 401
    return jsonify(license_manager.verify_license())

@app.route("/admin/api/license/activate", methods=["POST"])
def admin_api_license_activate():
    if not session.get('admin_logged_in'): return jsonify({"error": "unauthorized"}), 401
    data = request.get_json() or {}
    pin = data.get("pin", "").strip()
    licensee = data.get("licensee", "Store Owner")
    tier = data.get("tier", "COMMERCIAL")
    return jsonify(license_manager.activate_machine(pin, licensee, tier))

# ESP32 Hardware Routes
@app.route("/admin/api/esp32/save", methods=["POST"])
def admin_api_esp32_save():
    if not session.get('admin_logged_in'): return jsonify({"error": "unauthorized"}), 401
    data = request.get_json() or {}
    
    # Save to local DB for UI persistence
    for k, v in data.items():
        set_config(f"esp_{k}", v)

    # Transmit to ESP32 over serial
    data["cmd"] = "SET_CONFIG"
    transmit_to_esp32(data)
    return jsonify({"success": True})

@app.route("/admin/api/esp32/trigger", methods=["POST"])
def admin_api_esp32_trigger():
    if not session.get('admin_logged_in'): return jsonify({"error": "unauthorized"}), 401
    transmit_to_esp32({"cmd": "TRIGGER_CONFIG"})
    return jsonify({"success": True})

# Promo Rates API with Mathematical Conflict Prevention
def validate_promo_rate_conflict(bottles, minutes, exclude_bottles=None):
    """
    Validates a proposed promo rate against 3 mathematical invariants:
    1. Combination Floor: minutes >= greedy combo of lower tiers
    2. Monotonic Efficiency: minutes/bottle >= all smaller-tier efficiencies
    3. Higher-Tier Bound: minutes <= smallest higher-tier's minutes
    Returns (is_valid: bool, error_message: str)
    """
    if bottles <= 0 or minutes <= 0:
        return False, "Bottles and Minutes must be positive numbers."
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("SELECT bottles, minutes FROM promo_rates ORDER BY bottles ASC")
        existing = [(r[0], r[1]) for r in c.fetchall() if r[0] != exclude_bottles]

    eff_new = minutes / bottles

    # Rule 1: Monotonic Efficiency
    for eb, em in existing:
        eff_ex = em / eb
        if eb < bottles and eff_ex > eff_new:
            return False, (
                f"Efficiency conflict: {eb}B→{em}m tier gives {eff_ex:.1f} m/bottle, "
                f"but your {bottles}B→{minutes}m gives only {eff_new:.1f} m/bottle. "
                f"Higher bottle tiers must reward at least as much per bottle."
            )
        if eb > bottles and eff_ex < eff_new:
            return False, (
                f"Efficiency conflict: your {bottles}B→{minutes}m tier gives {eff_new:.1f} m/bottle, "
                f"which exceeds the {eb}B→{em}m tier at {eff_ex:.1f} m/bottle. "
                f"Larger tiers must always be at least as efficient."
            )

    # Rule 2: Combination Floor (greedy decomposition using lower tiers only)
    lower_tiers = sorted([(eb, em) for eb, em in existing if eb < bottles], reverse=True)
    combo_minutes = 0
    rem = bottles
    for eb, em in lower_tiers:
        if rem >= eb:
            times = rem // eb
            combo_minutes += times * em
            rem %= eb
    if combo_minutes > 0 and minutes < combo_minutes:
        return False, (
            f"Combination conflict: {bottles} bottles can be split into smaller tiers yielding {combo_minutes} mins, "
            f"but this rate only gives {minutes} mins. New rate must be at least {combo_minutes} mins to incentivize bulk deposit."
        )

    # Rule 3: Higher-Tier Bound
    higher_tiers = sorted([(eb, em) for eb, em in existing if eb > bottles])
    if higher_tiers:
        min_higher_mins = min(em for _, em in higher_tiers)
        if minutes >= min_higher_mins:
            hb, hm = min((eb, em) for eb, em in higher_tiers if em == min_higher_mins)
            return False, (
                f"Upper-bound conflict: your {bottles}B→{minutes}m would give same or more time than "
                f"the {hb}B→{hm}m tier. Reduce minutes or increase the higher tier."
            )

    return True, ""


@app.route("/admin/api/rates/list")
def admin_api_rates_list():
    if not session.get('admin_logged_in'): return jsonify({"error": "unauthorized"}), 401
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("SELECT bottles, minutes, label, speed_profile FROM promo_rates ORDER BY bottles ASC")
        return jsonify([{"bottles": r[0], "minutes": r[1], "label": r[2], "speed_profile": r[3] or ""} for r in c.fetchall()])


@app.route("/admin/api/rates/add", methods=["POST"])
def admin_api_rates_add():
    if not session.get('admin_logged_in'): return jsonify({"error": "unauthorized"}), 401
    data = request.get_json() or {}
    try:
        bottles = int(data.get("bottles", 0))
        minutes = int(data.get("minutes", 0))
        orig_bottles = int(data.get("orig_bottles")) if data.get("orig_bottles") else None
        label = data.get("label", "").strip() or f"{bottles} Bottle{'s' if bottles != 1 else ''} = {minutes} mins"
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "Bottles and Minutes must be positive numbers."}), 400

    if bottles <= 0 or minutes <= 0:
        return jsonify({"success": False, "error": "Bottles and Minutes must be positive numbers."}), 400

    is_valid, err_msg = validate_promo_rate_conflict(bottles, minutes, exclude_bottles=orig_bottles)
    if not is_valid:
        return jsonify({"success": False, "error": err_msg}), 400

    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        if orig_bottles and orig_bottles != bottles:
            c.execute("DELETE FROM promo_rates WHERE bottles = ?", (orig_bottles,))
        c.execute("REPLACE INTO promo_rates (bottles, minutes, label, speed_profile) VALUES (?, ?, ?, '')", (bottles, minutes, label))
        if bottles == 1:
            c.execute("REPLACE INTO config (key, value) VALUES ('minutes_per_bottle', ?)", (str(minutes),))
        conn.commit()
    return jsonify({"success": True})


@app.route("/admin/api/rates/delete", methods=["POST"])
def admin_api_rates_delete():
    if not session.get('admin_logged_in'): return jsonify({"error": "unauthorized"}), 401
    data = request.get_json() or {}
    bottles = data.get("bottles")
    if not bottles:
        return jsonify({"success": False, "error": "Missing bottles parameter."}), 400
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("DELETE FROM promo_rates WHERE bottles = ?", (int(bottles),))
        conn.commit()
    return jsonify({"success": True})


@app.route("/admin/api/rates/apply_preset", methods=["POST"])
def admin_api_rates_apply_preset():
    if not session.get('admin_logged_in'): return jsonify({"error": "unauthorized"}), 401
    data = request.get_json() or {}
    preset_key = data.get("preset", "standard")

    PRESETS = {
        "standard": {
            "label": "Standard Community Curve",
            "rates": [
                (1, 10, "1 Bottle = 10 mins"),
                (3, 40, "3 Bottles = 40 mins"),
                (5, 75, "5 Bottles = 1h 15m"),
                (10, 180, "10 Bottles = 3 Hours"),
            ]
        },
        "aggressive": {
            "label": "Aggressive Reward Curve",
            "rates": [
                (1, 10, "1 Bottle = 10 mins"),
                (5, 70, "5 Bottles = 1h 10m"),
                (10, 180, "10 Bottles = 3 Hours"),
                (20, 420, "20 Bottles = 7 Hours"),
            ]
        },
        "cafe": {
            "label": "Café / Study Hub Curve",
            "rates": [
                (1, 20, "1 Bottle = 20 mins"),
                (3, 75, "3 Bottles = 1h 15m"),
                (6, 180, "6 Bottles = 3 Hours"),
                (12, 420, "12 Bottles = 7 Hours"),
            ]
        }
    }

    if preset_key not in PRESETS:
        return jsonify({"success": False, "error": f"Unknown preset '{preset_key}'."}), 400

    preset = PRESETS[preset_key]
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("DELETE FROM promo_rates")
        for bottles, minutes, label in preset["rates"]:
            c.execute("REPLACE INTO promo_rates (bottles, minutes, label, speed_profile) VALUES (?, ?, ?, '')",
                      (bottles, minutes, label))
        # Sync base rate config
        base = next(((m) for b, m, _ in preset["rates"] if b == 1), None)
        if base:
            c.execute("REPLACE INTO config (key, value) VALUES ('minutes_per_bottle', ?)", (str(base),))
        conn.commit()
    return jsonify({"success": True, "message": f"'{preset['label']}' template applied with {len(preset['rates'])} tiers."})


# Audio Config & File Upload APIs
@app.route("/admin/api/audio/settings", methods=["POST"])
def admin_api_audio_settings():
    if not session.get('admin_logged_in'): return jsonify({"error": "unauthorized"}), 401
    data = request.get_json() or {}
    bg = data.get("audio_bg", "/static/audio/b1.wav")
    insert = data.get("audio_insert", "/static/audio/coin.wav")
    success = data.get("audio_success", "/static/audio/success_ding.wav")
    vol = data.get("volume", "80")
    
    set_config("audio_bg", bg)
    set_config("audio_insert", insert)
    set_config("audio_success", success)
    set_config("audio_volume", vol)
    
    # Backwards compatibility for preset
    set_config("audio_preset", insert)
    return jsonify({"success": True})

@app.route("/admin/api/audio/upload", methods=["POST"])
def admin_api_audio_upload():
    if not session.get('admin_logged_in'): return jsonify({"error": "unauthorized"}), 401
    if 'file' not in request.files:
        return jsonify({"success": False, "error": "No file uploaded."}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({"success": False, "error": "No file selected."}), 400
    
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ['.mp3', '.wav', '.ogg', '.m4a', '.aac']:
        return jsonify({"success": False, "error": "Invalid audio file format. Only MP3, WAV, OGG allowed."}), 400
    
    upload_dir = os.path.join(app.static_folder or "static", "audio", "uploads")
    os.makedirs(upload_dir, exist_ok=True)
    safe_name = f"custom_{int(time.time())}_{re.sub(r'[^a-zA-Z0-9_.-]', '', file.filename)}"
    filepath = os.path.join(upload_dir, safe_name)
    file.save(filepath)
    file_url = f"/static/audio/uploads/{safe_name}"
    return jsonify({"success": True, "url": file_url})

# Simulator API
@app.route("/simulator")
def simulator_ui():
    return render_template_string(esp32.render_simulator_html())

@app.route("/simulator/api/state")
def simulator_api_state():
    return jsonify(esp32.get_state())

@app.route("/simulator/api/drop", methods=["POST"])
@app.route("/simulator/api/trigger", methods=["POST"])
def simulator_drop():
    data = request.get_json() or {}
    item_type = data.get("item_type") or data.get("type", "valid_pet")
    esp32.simulate_insert(item_type=item_type)
    return jsonify({"success": True, "item_type": item_type})

@app.route("/simulator/api/bin", methods=["POST"])
def simulator_bin():
    data = request.get_json() or {}
    dist = int(data.get("distance_cm", 60))
    esp32.set_bin_distance(dist)
    return jsonify({"success": True, "distance_cm": dist})

@app.route("/simulator/api/reset", methods=["POST"])
def simulator_reset():
    esp32.reset_session()
    return jsonify({"success": True})

FORCE_PASS_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Update Admin Password</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/5.15.4/css/all.min.css">
    <style>
        body {
            background-color: #0b0f19;
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            margin: 0;
            padding: 16px;
        }
        .pass-box-clean {
            width: 100%;
            max-width: 340px;
            background: #111827;
            border: 1px solid #1f2937;
            border-radius: 12px;
            padding: 24px;
            box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.6);
        }
        .pass-title {
            font-size: 17px;
            font-weight: 700;
            color: #f9fafb;
            text-align: center;
            margin-bottom: 4px;
        }
        .pass-subtitle {
            font-size: 12px;
            color: #f87171;
            text-align: center;
            margin-bottom: 18px;
            line-height: 1.4;
        }
        .form-group-clean {
            margin-bottom: 14px;
        }
        .form-group-clean label {
            display: block;
            font-size: 12px;
            font-weight: 500;
            color: #9ca3af;
            margin-bottom: 5px;
        }
        .form-control-clean {
            width: 100%;
            height: 38px;
            background-color: #1f2937 !important;
            border: 1px solid #374151 !important;
            border-radius: 6px !important;
            color: #f9fafb !important;
            font-size: 13px !important;
            padding: 8px 12px !important;
            box-sizing: border-box;
            outline: none;
        }
        .form-control-clean:focus {
            border-color: #ef4444 !important;
            box-shadow: 0 0 0 2px rgba(239, 68, 68, 0.2) !important;
        }
        .btn-update {
            width: 100%;
            height: 38px;
            background: #ef4444;
            border: none;
            border-radius: 6px;
            color: #ffffff;
            font-size: 13.5px;
            font-weight: 600;
            cursor: pointer;
            margin-top: 6px;
            transition: background 0.15s ease;
        }
        .btn-update:hover {
            background: #dc2626;
        }
        .pass-footer {
            margin-top: 18px;
            text-align: center;
            font-size: 12px;
        }
        .pass-footer a {
            color: #6b7280;
            text-decoration: none;
        }
        .pass-footer a:hover {
            color: #9ca3af;
        }
    </style>
</head>
<body>
<div class="pass-box-clean">
    <div class="pass-title"><i class="fas fa-shield-alt text-danger mr-1"></i> Security Requirement</div>
    <div class="pass-subtitle">You must change the default password before accessing the admin dashboard.</div>
    
    <form method="POST" action="/admin/force_password_change">
        <div class="form-group-clean">
            <label for="new_password">New Password (min. 6 characters)</label>
            <input type="password" id="new_password" name="new_password" class="form-control-clean" placeholder="Enter new password" required minlength="6" autofocus>
        </div>
        
        <button type="submit" class="btn-update">Change Password</button>
    </form>
    
    <div class="pass-footer">
        <a href="/admin/logout">← Cancel & Logout</a>
    </div>
</div>
</body>
</html>
"""

@app.before_request
def admin_security_guard():
    # Only enforce on /admin and sub-paths
    if request.path == "/admin" or request.path.startswith("/admin/"):
        # Public routes in admin namespace
        if request.path == "/admin/login":
            return None

        # 1. Enforce strict authentication on all admin endpoints
        if not session.get('admin_logged_in'):
            # Direct browser visits, page navigation, and file exports redirect to login page
            if request.path in ["/admin", "/admin/force_password_change", "/admin/api/export_xlsx", "/admin/api/export_csv"] or not request.path.startswith("/admin/api/"):
                return redirect("/admin/login")
            # Background JSON API calls receive 401 Unauthorized JSON error
            return jsonify({"error": "unauthorized", "message": "Authentication required."}), 401

        # 2. Enforce mandatory password change if default admin123 is detected
        if session.get('must_change_password'):
            allowed_during_pw_change = ["/admin/force_password_change", "/admin/logout"]
            if request.path not in allowed_during_pw_change:
                if request.path in ["/admin", "/admin/api/export_xlsx", "/admin/api/export_csv"] or not request.path.startswith("/admin/api/"):
                    return redirect("/admin/force_password_change")
                return jsonify({"error": "password_change_required", "message": "Default password must be changed first."}), 403

@app.route("/admin/force_password_change", methods=["GET", "POST"])
def admin_force_password_change():
    if not session.get('admin_logged_in'): return redirect("/admin/login")
    if request.method == "POST":
        new_pw = request.form.get("new_password", "").strip()
        if new_pw and len(new_pw) >= 6 and new_pw != "admin123":
            admin_user = session.get('admin_username', 'admin')
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute("UPDATE admins SET password_hash=? WHERE username=?", (generate_password_hash(new_pw), admin_user))
            session.pop('must_change_password', None)
            return redirect("/admin")
    return render_template_string(FORCE_PASS_HTML)

# Admin Dashboard
admin_login_attempts = {}

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    error = None
    if request.method == "POST":
        client_ip = request.remote_addr or "127.0.0.1"
        now = time.time()
        if client_ip in admin_login_attempts:
            count, last_time = admin_login_attempts[client_ip]
            if now - last_time < 300:
                if count >= 5:
                    return render_template_string(LOGIN_HTML, error="Too many attempts. Try again in 5 minutes.")
            else:
                admin_login_attempts[client_ip] = [0, now]
        else:
            admin_login_attempts[client_ip] = [0, now]

        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            c.execute("SELECT password_hash FROM admins WHERE username=?", (username,))
            row = c.fetchone()
            if row and check_password_hash(row[0], password):
                if client_ip in admin_login_attempts:
                    del admin_login_attempts[client_ip]
                session['admin_logged_in'] = True
                session['admin_username'] = username
                if password == "admin123":
                    session['must_change_password'] = True
                return redirect("/admin")
            else:
                admin_login_attempts[client_ip][0] += 1
                admin_login_attempts[client_ip][1] = time.time()
                error = "Invalid username or password"
    return render_template_string(LOGIN_HTML, error=error)

@app.route("/admin/logout")
def admin_logout():
    session.pop('admin_logged_in', None)
    return redirect("/admin/login")

@app.route("/admin")
def admin_dashboard():
    if not session.get('admin_logged_in'):
        return redirect("/admin/login")
    return render_template_string(ADMIN_HTML, config=get_all_config())

@app.route("/admin/api/stats")
def admin_api_stats():
    if not session.get('admin_logged_in'): return jsonify({"error": "unauthorized"}), 401
    today = datetime.now().strftime('%Y-%m-%d')
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("SELECT total_bottles FROM stats WHERE date=?", (today,))
        row = c.fetchone()
        today_bottles = row[0] if row else 0
        
        c.execute("SELECT SUM(total_bottles) FROM stats")
        row = c.fetchone()
        total_bottles = row[0] if row and row[0] else 0

        c.execute("SELECT date, total_bottles FROM stats ORDER BY date DESC LIMIT 7")
        history = [{"date": r[0], "count": r[1]} for r in c.fetchall()]
        if not history:
            history = [{"date": today, "count": today_bottles}]

    with active_clients_lock:
        active_count = sum(1 for c in active_clients.values() if c["remaining_seconds"] > 0)

    cpu_val, ram_val, disk_val, uptime_val = 12, 28, 15, "2h 45m"
    try:
        if platform.system() == "Linux":
            with open('/proc/loadavg', 'r') as f:
                load = float(f.read().split()[0])
                cpu_val = min(100, int(load * 100 / (os.cpu_count() or 1)))
            with open('/proc/meminfo', 'r') as f:
                mem = {}
                for line in f:
                    parts = line.split()
                    mem[parts[0].strip(':')] = int(parts[1])
                ram_val = int((mem['MemTotal'] - mem['MemAvailable']) / mem['MemTotal'] * 100)
            with open('/proc/uptime', 'r') as f:
                up_sec = float(f.read().split()[0])
                uptime_val = f"{int(up_sec // 3600)}h {int((up_sec % 3600) // 60)}m"
            st = os.statvfs('/')
            disk_val = int((st.f_blocks - st.f_bavail) / st.f_blocks * 100)
        else:
            cpu_val = random.randint(5, 20)
            ram_val = random.randint(30, 45)
    except Exception:
        pass

    return jsonify({
        "today_bottles": today_bottles,
        "total_bottles": total_bottles,
        "active_clients": active_count,
        "cpu": cpu_val, "ram": ram_val, "disk": disk_val, "uptime": uptime_val,
        "history": list(reversed(history))
    })

@app.route("/admin/api/clients")
def admin_api_clients():
    if not session.get('admin_logged_in'): return jsonify({"error": "unauthorized"}), 401
    with active_clients_lock:
        res = []
        for ip, sess in active_clients.items():
            res.append({
                "ip": ip,
                "mac": sess.get("mac", "00:00:00:00:00:00"),
                "remaining_seconds": sess.get("remaining_seconds", 0),
                "is_paused": sess.get("is_paused", False),
                "dl_kbps": sess.get("dl_kbps", 3072),
                "ul_kbps": sess.get("ul_kbps", 1536)
            })
        return jsonify(res)

@app.route("/admin/api/client/action", methods=["POST"])
def admin_api_client_action():
    if not session.get('admin_logged_in'): return jsonify({"error": "unauthorized"}), 401
    data = request.get_json() or {}
    ip = data.get("ip")
    action = data.get("action")
    with active_clients_lock:
        if ip in active_clients:
            if action == "add15":
                active_clients[ip]["remaining_seconds"] += 15 * 60
                sync_client_firewall(ip)
            elif action == "add60":
                active_clients[ip]["remaining_seconds"] += 60 * 60
                sync_client_firewall(ip)
            elif action == "pause":
                active_clients[ip]["is_paused"] = True
                sync_client_firewall(ip)
            elif action == "resume":
                active_clients[ip]["is_paused"] = False
                sync_client_firewall(ip)
            elif action == "kick":
                active_clients[ip]["remaining_seconds"] = 0
                active_clients[ip]["is_paused"] = False
                sync_client_firewall(ip)
                sync_client_firewall(ip)
            return jsonify({"success": True})
    return jsonify({"success": False, "error": "Client not found."})

@app.route("/admin/api/client/edit", methods=["POST"])
def admin_api_client_edit():
    if not session.get('admin_logged_in'): return jsonify({"error": "unauthorized"}), 401
    data = request.get_json() or {}
    ip = data.get("ip")
    minutes = data.get("minutes")
    dl = data.get("dl_kbps")
    ul = data.get("ul_kbps")
    with active_clients_lock:
        if ip in active_clients:
            if dl is not None:
                active_clients[ip]["dl_kbps"] = max(128, int(dl))
            if ul is not None:
                active_clients[ip]["ul_kbps"] = max(64, int(ul))
            if minutes is not None:
                active_clients[ip]["remaining_seconds"] = max(0, int(minutes) * 60)
            sync_client_firewall(ip)
            save_sessions_to_db()
            return jsonify({"success": True, "client": active_clients[ip]})
    return jsonify({"success": False, "error": "Client not found."})

@app.route("/admin/api/vouchers/list")
def admin_api_vouchers_list():
    if not session.get('admin_logged_in'): return jsonify({"error": "unauthorized"}), 401
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("SELECT code, minutes, is_used, created_at, used_by, note FROM vouchers ORDER BY created_at DESC LIMIT 50")
        rows = [{"code": r[0], "minutes": r[1], "is_used": r[2], "created_at": r[3], "used_by": r[4], "note": r[5] or ""} for r in c.fetchall()]
        return jsonify(rows)

@app.route("/admin/api/vouchers/generate", methods=["POST"])
def admin_generate_vouchers():
    if not session.get('admin_logged_in'): return jsonify({"error": "unauthorized"}), 401
    data = request.get_json() or {}
    try:
        qty = max(1, min(100, int(data.get("qty", 5))))
        minutes = max(1, int(data.get("minutes", 60)))
    except (ValueError, TypeError):
        return jsonify({"success": False, "error": "Invalid quantity or duration."}), 400

    note = data.get("note", "").strip()
    prefix = data.get("prefix", "ECO-").strip().upper()
    created = []
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        for _ in range(qty):
            code = prefix + ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
            c.execute("INSERT OR REPLACE INTO vouchers (code, minutes, created_at, note) VALUES (?, ?, ?, ?)",
                      (code, minutes, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), note))
            created.append({"code": code, "minutes": minutes, "note": note})
        conn.commit()
    return jsonify({"success": True, "vouchers": created})

@app.route("/admin/api/vouchers/delete", methods=["POST"])
def admin_delete_voucher():
    if not session.get('admin_logged_in'): return jsonify({"error": "unauthorized"}), 401
    data = request.get_json() or {}
    code = data.get("code", "").strip().upper()
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("DELETE FROM vouchers WHERE code = ?", (code,))
        conn.commit()
        return jsonify({"success": True})

@app.route("/admin/api/members/list")
def admin_api_members_list():
    if not session.get('admin_logged_in'): return jsonify({"error": "unauthorized"}), 401
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("SELECT username, wallet_minutes, created_at FROM members ORDER BY created_at DESC")
        rows = [{"username": r[0], "wallet_minutes": r[1], "created_at": r[2]} for r in c.fetchall()]
        return jsonify(rows)

@app.route("/admin/api/members/add", methods=["POST"])
def admin_api_members_add():
    if not session.get('admin_logged_in'): return jsonify({"error": "unauthorized"}), 401
    data = request.get_json() or {}
    username = data.get("username", "").strip()
    pin = data.get("pin", "").strip()
    mins = int(data.get("wallet_minutes", 0))
    if not username or len(username) < 3:
        return jsonify({"success": False, "error": "Username must be at least 3 characters."}), 400
    if not pin or not re.match(r"^\d{4,6}$", pin):
        return jsonify({"success": False, "error": "PIN must be 4 to 6 digits."}), 400

    pin_hash = generate_password_hash(pin)
    try:
        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            c.execute("INSERT INTO members (username, pin_hash, wallet_minutes, created_at) VALUES (?, ?, ?, ?)",
                      (username, pin_hash, mins, datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
            conn.commit()
            return jsonify({"success": True})
    except sqlite3.IntegrityError:
        return jsonify({"success": False, "error": "Username already exists."}), 400

@app.route("/admin/api/members/topup", methods=["POST"])
def admin_api_members_topup():
    if not session.get('admin_logged_in'): return jsonify({"error": "unauthorized"}), 401
    data = request.get_json() or {}
    username = data.get("username")
    mins = int(data.get("minutes", 15))
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("UPDATE members SET wallet_minutes = MAX(0, wallet_minutes + ?) WHERE username = ?", (mins, username))
        conn.commit()
        return jsonify({"success": True})

@app.route("/admin/api/members/delete", methods=["POST"])
def admin_api_members_delete():
    if not session.get('admin_logged_in'): return jsonify({"error": "unauthorized"}), 401
    data = request.get_json() or {}
    username = data.get("username")
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("DELETE FROM members WHERE username = ?", (username,))
        conn.commit()
        return jsonify({"success": True})

@app.route("/admin/api/settings/save", methods=["POST"])
def admin_api_settings_save():
    if not session.get('admin_logged_in'): return jsonify({"error": "unauthorized"}), 401
    data = request.get_json() or {}
    for k, v in data.items():
        set_config(k, v)
    return jsonify({"success": True})

@app.route("/admin/api/mac_control/list")
def admin_api_mac_list():
    if not session.get('admin_logged_in'): return jsonify({"error": "unauthorized"}), 401
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("SELECT mac, type, note, dl_kbps, ul_kbps FROM mac_control ORDER BY mac ASC")
        return jsonify([{"mac": r[0], "type": r[1], "note": r[2] or "", "dl_kbps": r[3] or 0, "ul_kbps": r[4] or 0} for r in c.fetchall()])

@app.route("/admin/api/mac_control/add", methods=["POST"])
def admin_api_mac_add():
    if not session.get('admin_logged_in'): return jsonify({"error": "unauthorized"}), 401
    data = request.get_json() or {}
    raw_mac = data.get("mac", "").strip().upper()
    
    cleaned = re.sub(r"[^0-9A-F]", "", raw_mac)
    if len(cleaned) == 12:
        mac = ":".join(cleaned[i:i+2] for i in range(0, 12, 2))
    else:
        mac = raw_mac

    if not re.match(r"^([0-9A-F]{2}[:-]){5}([0-9A-F]{2})$", mac):
        return jsonify({"success": False, "error": "Invalid MAC address format! Must be 12 hex characters (e.g. AA:BB:CC:DD:EE:FF)."}), 400

    m_type = data.get("type", "whitelist")
    note = data.get("note", "").strip()
    dl = int(data.get("dl_kbps", 0))
    ul = int(data.get("ul_kbps", 0))

    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("REPLACE INTO mac_control (mac, type, note, dl_kbps, ul_kbps) VALUES (?, ?, ?, ?, ?)", (mac, m_type, note, dl, ul))
        conn.commit()
        return jsonify({"success": True, "mac": mac})

@app.route("/admin/api/mac_control/delete", methods=["POST"])
def admin_api_mac_delete():
    if not session.get('admin_logged_in'): return jsonify({"error": "unauthorized"}), 401
    data = request.get_json() or {}
    mac = data.get("mac", "").strip().upper()
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("DELETE FROM mac_control WHERE mac = ?", (mac,))
        conn.commit()
        return jsonify({"success": True})

@app.route("/admin/api/walled_garden/list")
def admin_api_walled_garden_list():
    if not session.get('admin_logged_in'): return jsonify({"error": "unauthorized"}), 401
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("SELECT domain, note FROM walled_garden ORDER BY domain ASC")
        return jsonify([{"domain": r[0], "note": r[1] or ""} for r in c.fetchall()])

@app.route("/admin/api/walled_garden/add", methods=["POST"])
def admin_api_walled_garden_add():
    if not session.get('admin_logged_in'): return jsonify({"error": "unauthorized"}), 401
    data = request.get_json() or {}
    domain = data.get("domain", "").strip().lower()
    note = data.get("note", "").strip()
    if not domain or "." not in domain or len(domain) < 4:
        return jsonify({"success": False, "error": "Invalid domain name. Example: gcash.com or deped.gov.ph"}), 400

    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("REPLACE INTO walled_garden (domain, note) VALUES (?, ?)", (domain, note))
        conn.commit()
        return jsonify({"success": True})

@app.route("/admin/api/walled_garden/delete", methods=["POST"])
def admin_api_walled_garden_delete():
    if not session.get('admin_logged_in'): return jsonify({"error": "unauthorized"}), 401
    data = request.get_json() or {}
    domain = data.get("domain", "").strip().lower()
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("DELETE FROM walled_garden WHERE domain = ?", (domain,))
        conn.commit()
        return jsonify({"success": True})

@app.route("/admin/api/telegram/test", methods=["POST"])
def admin_api_telegram_test():
    if not session.get('admin_logged_in'): return jsonify({"error": "unauthorized"}), 401
    ok = send_telegram_alert("🔔 *ECO-Fi Test Alert*\n\nThis is a successful test notification from your Reverse Vending Machine!")
    return jsonify({"success": ok})

def generate_ecofi_excel_report(db_path):
    if not openpyxl:
        return None
    wb = openpyxl.Workbook()
    
    FONT_FAMILY = "Segoe UI"
    title_font = Font(name=FONT_FAMILY, size=16, bold=True, color="FFFFFF")
    subtitle_font = Font(name=FONT_FAMILY, size=10, italic=True, color="E2E8F0")
    kpi_title_font = Font(name=FONT_FAMILY, size=9, bold=True, color="64748B")
    kpi_value_font = Font(name=FONT_FAMILY, size=14, bold=True, color="0F172A")
    header_font = Font(name=FONT_FAMILY, size=11, bold=True, color="FFFFFF")
    total_font = Font(name=FONT_FAMILY, size=11, bold=True, color="0F172A")
    data_font = Font(name=FONT_FAMILY, size=10, color="1E293B")
    
    title_fill = PatternFill(start_color="0F766E", end_color="0F766E", fill_type="solid")
    header_fill = PatternFill(start_color="10B981", end_color="10B981", fill_type="solid")
    kpi_fill = PatternFill(start_color="F8FAFC", end_color="F8FAFC", fill_type="solid")
    zebra_fill = PatternFill(start_color="F1F5F9", end_color="F1F5F9", fill_type="solid")
    white_fill = PatternFill(start_color="FFFFFF", end_color="FFFFFF", fill_type="solid")
    total_fill = PatternFill(start_color="E2E8F0", end_color="E2E8F0", fill_type="solid")
    
    status_active_fill = PatternFill(start_color="DCFCE7", end_color="DCFCE7", fill_type="solid")
    status_active_font = Font(name=FONT_FAMILY, size=10, bold=True, color="15803D")
    status_used_fill = PatternFill(start_color="F1F5F9", end_color="F1F5F9", fill_type="solid")
    status_used_font = Font(name=FONT_FAMILY, size=10, color="64748B")

    thin_border_side = Side(style="thin", color="CBD5E1")
    cell_border = Border(left=thin_border_side, right=thin_border_side, top=thin_border_side, bottom=thin_border_side)
    kpi_border = Border(left=thin_border_side, right=thin_border_side, top=thin_border_side, bottom=thin_border_side)
    total_border = Border(
        left=thin_border_side, right=thin_border_side,
        top=Side(style="thin", color="0F172A"),
        bottom=Side(style="double", color="0F172A")
    )
    
    align_center = Alignment(horizontal="center", vertical="center")
    align_left = Alignment(horizontal="left", vertical="center")
    align_right = Alignment(horizontal="right", vertical="center")
    
    # 1. SHEET: DAILY COLLECTIONS & IMPACT
    ws1 = wb.active
    ws1.title = "Daily Collections & Impact"
    ws1.views.sheetView[0].showGridLines = True
    
    with sqlite3.connect(db_path) as conn:
        c = conn.cursor()
        c.execute("SELECT date, total_bottles FROM stats ORDER BY date DESC")
        stats_rows = c.fetchall()
        c.execute("SELECT COUNT(*), SUM(wallet_minutes) FROM members")
        m_stats = c.fetchone()
        total_members = m_stats[0] or 0
        c.execute("SELECT COUNT(*), SUM(CASE WHEN is_used=0 THEN 1 ELSE 0 END) FROM vouchers")
        v_stats = c.fetchone()
        total_vouchers = v_stats[0] or 0
        unclaimed_vouchers = v_stats[1] or 0

    total_bottles_sum = sum(r[1] for r in stats_rows)
    est_plastic_kg = total_bottles_sum * 0.025
    est_co2_kg = est_plastic_kg * 1.5
    total_mins_sum = total_bottles_sum * 10
    total_hours_sum = total_mins_sum / 60.0

    ws1.merge_cells("A1:G1")
    ws1["A1"] = "ECO-FI REVERSE VENDING MACHINE (PBVM)"
    ws1["A1"].font = title_font
    ws1["A1"].fill = title_fill
    ws1["A1"].alignment = align_center
    ws1.row_dimensions[1].height = 28
    
    ws1.merge_cells("A2:G2")
    ws1["A2"] = f"Executive Operations & Environmental Impact Report • Generated on {datetime.now().strftime('%B %d, %Y at %I:%M %p')}"
    ws1["A2"].font = subtitle_font
    ws1["A2"].fill = title_fill
    ws1["A2"].alignment = align_center
    ws1.row_dimensions[2].height = 18

    kpis = [
        ("TOTAL BOTTLES", f"{total_bottles_sum:,}", "A", "B"),
        ("WIFI TIME ISSUED", f"{total_hours_sum:.1f} Hours", "C", "C"),
        ("PLASTIC RECYCLED", f"{est_plastic_kg:.2f} kg", "D", "D"),
        ("CO₂ OFFSET", f"{est_co2_kg:.2f} kg", "E", "E"),
        ("MEMBERS", f"{total_members} users", "F", "F"),
        ("ACTIVE VOUCHERS", f"{unclaimed_vouchers} avail", "G", "G"),
    ]
    ws1.row_dimensions[4].height = 16
    ws1.row_dimensions[5].height = 24
    for title, val, c1, c2 in kpis:
        if c1 != c2:
            ws1.merge_cells(f"{c1}4:{c2}4")
            ws1.merge_cells(f"{c1}5:{c2}5")
        top_cell = ws1[f"{c1}4"]
        top_cell.value = title
        top_cell.font = kpi_title_font
        top_cell.fill = kpi_fill
        top_cell.alignment = align_center
        top_cell.border = kpi_border
        val_cell = ws1[f"{c1}5"]
        val_cell.value = val
        val_cell.font = kpi_value_font
        val_cell.fill = kpi_fill
        val_cell.alignment = align_center
        val_cell.border = kpi_border
        if c1 != c2:
            ws1[f"{c2}4"].border = kpi_border
            ws1[f"{c2}5"].border = kpi_border

    headers = [
        ("Date", align_center),
        ("Bottles Recycled", align_right),
        ("WiFi Time (Minutes)", align_right),
        ("WiFi Time (Hours)", align_right),
        ("Plastic Weight (kg)", align_right),
        ("CO₂ Saved (kg)", align_right),
        ("Collection Status", align_center)
    ]
    ws1.row_dimensions[7].height = 24
    for col_idx, (h_title, h_align) in enumerate(headers, start=1):
        cell = ws1.cell(row=7, column=col_idx, value=h_title)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = h_align
        cell.border = cell_border

    current_row = 8
    for idx, (dt, count) in enumerate(stats_rows):
        mins = count * 10
        hrs = mins / 60.0
        kg = count * 0.025
        co2 = kg * 1.5
        status = "High Volume" if count >= 20 else ("Active" if count > 0 else "Idle")
        fill = zebra_fill if idx % 2 == 1 else white_fill
        ws1.row_dimensions[current_row].height = 20
        row_vals = [
            (dt, align_center, "@"),
            (count, align_right, "#,##0"),
            (mins, align_right, "#,##0"),
            (hrs, align_right, "0.00"),
            (kg, align_right, "0.000"),
            (co2, align_right, "0.000"),
            (status, align_center, "@")
        ]
        for col_idx, (val, c_align, num_fmt) in enumerate(row_vals, start=1):
            cell = ws1.cell(row=current_row, column=col_idx, value=val)
            cell.font = data_font
            cell.fill = fill
            cell.alignment = c_align
            cell.border = cell_border
            cell.number_format = num_fmt
        current_row += 1

    if stats_rows:
        ws1.row_dimensions[current_row].height = 22
        tot_cells = [
            ("TOTAL", align_center),
            (f"=SUM(B8:B{current_row-1})", align_right, "#,##0"),
            (f"=SUM(C8:C{current_row-1})", align_right, "#,##0"),
            (f"=SUM(D8:D{current_row-1})", align_right, "0.00"),
            (f"=SUM(E8:E{current_row-1})", align_right, "0.000"),
            (f"=SUM(F8:F{current_row-1})", align_right, "0.000"),
            ("", align_center)
        ]
        for col_idx, (val, c_align, *opt_fmt) in enumerate(tot_cells, start=1):
            cell = ws1.cell(row=current_row, column=col_idx, value=val)
            cell.font = total_font
            cell.fill = total_fill
            cell.alignment = c_align
            cell.border = total_border
            if opt_fmt:
                cell.number_format = opt_fmt[0]
    ws1.freeze_panes = "A8"

    # 2. SHEET: VOUCHERS INVENTORY
    ws2 = wb.create_sheet(title="Voucher Inventory")
    ws2.views.sheetView[0].showGridLines = True
    ws2.merge_cells("A1:G1")
    ws2["A1"] = "ECO-FI VOUCHER TICKETS INVENTORY"
    ws2["A1"].font = title_font
    ws2["A1"].fill = title_fill
    ws2["A1"].alignment = align_center
    ws2.row_dimensions[1].height = 26
    
    v_headers = ["Voucher Code", "Duration (Mins)", "Duration (Hours)", "Status", "Created Date", "Redeemed / Used By", "Batch / Admin Note"]
    ws2.row_dimensions[3].height = 22
    for col_idx, h_title in enumerate(v_headers, start=1):
        cell = ws2.cell(row=3, column=col_idx, value=h_title)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = align_center if col_idx in [1, 4] else (align_right if col_idx in [2, 3] else align_left)
        cell.border = cell_border

    with sqlite3.connect(db_path) as conn:
        c = conn.cursor()
        c.execute("SELECT code, minutes, is_used, created_at, used_by, note FROM vouchers ORDER BY created_at DESC")
        v_rows = c.fetchall()

    v_row_idx = 4
    for idx, (code, mins, is_used, created_at, used_by, note) in enumerate(v_rows):
        ws2.row_dimensions[v_row_idx].height = 19
        fill = zebra_fill if idx % 2 == 1 else white_fill
        status_str = "REDEEMED" if is_used else "ACTIVE"
        s_fill = status_used_fill if is_used else status_active_fill
        s_font = status_used_font if is_used else status_active_font
        row_data = [
            (code, align_center, fill, data_font, "@"),
            (mins, align_right, fill, data_font, "#,##0"),
            (mins/60.0, align_right, fill, data_font, "0.00"),
            (status_str, align_center, s_fill, s_font, "@"),
            (created_at or "--", align_left, fill, data_font, "@"),
            (used_by or "--", align_left, fill, data_font, "@"),
            (note or "", align_left, fill, data_font, "@")
        ]
        for col_idx, (val, c_align, c_fill, c_font, n_fmt) in enumerate(row_data, start=1):
            cell = ws2.cell(row=v_row_idx, column=col_idx, value=val)
            cell.font = c_font
            cell.fill = c_fill
            cell.alignment = c_align
            cell.border = cell_border
            cell.number_format = n_fmt
        v_row_idx += 1
    ws2.freeze_panes = "A4"

    # 3. SHEET: REGISTERED MEMBERS
    ws3 = wb.create_sheet(title="Member Wallets")
    ws3.views.sheetView[0].showGridLines = True
    ws3.merge_cells("A1:E1")
    ws3["A1"] = "ECO-FI REGISTERED MEMBERS & TIME WALLETS"
    ws3["A1"].font = title_font
    ws3["A1"].fill = title_fill
    ws3["A1"].alignment = align_center
    ws3.row_dimensions[1].height = 26
    
    m_headers = ["Member Username", "Wallet Balance (Mins)", "Wallet Balance (Hours)", "Registered Date", "Account Status"]
    ws3.row_dimensions[3].height = 22
    for col_idx, h_title in enumerate(m_headers, start=1):
        cell = ws3.cell(row=3, column=col_idx, value=h_title)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = align_right if col_idx in [2, 3] else (align_center if col_idx in [4, 5] else align_left)
        cell.border = cell_border

    with sqlite3.connect(db_path) as conn:
        c = conn.cursor()
        c.execute("SELECT username, wallet_minutes, created_at FROM members ORDER BY wallet_minutes DESC")
        m_rows = c.fetchall()

    m_row_idx = 4
    for idx, (uname, w_mins, m_created) in enumerate(m_rows):
        ws3.row_dimensions[m_row_idx].height = 19
        fill = zebra_fill if idx % 2 == 1 else white_fill
        row_data = [
            (uname, align_left, "@"),
            (w_mins, align_right, "#,##0"),
            (w_mins/60.0, align_right, "0.00"),
            (m_created or "--", align_center, "@"),
            ("Active User", align_center, "@")
        ]
        for col_idx, (val, c_align, n_fmt) in enumerate(row_data, start=1):
            cell = ws3.cell(row=m_row_idx, column=col_idx, value=val)
            cell.font = data_font
            cell.fill = fill
            cell.alignment = c_align
            cell.border = cell_border
            cell.number_format = n_fmt
        m_row_idx += 1
    ws3.freeze_panes = "A4"

    # 4. SHEET: ACTIVE PROMO RATES
    ws4 = wb.create_sheet(title="Promo Rate Curves")
    ws4.views.sheetView[0].showGridLines = True
    ws4.merge_cells("A1:E1")
    ws4["A1"] = "ECO-FI ACTIVE RATE TIERS & PROMO CURVES"
    ws4["A1"].font = title_font
    ws4["A1"].fill = title_fill
    ws4["A1"].alignment = align_center
    ws4.row_dimensions[1].height = 26
    
    r_headers = ["Bottles Required", "Time Credited (Mins)", "Time Credited (Hours)", "Rate Efficiency", "Package Display Label"]
    ws4.row_dimensions[3].height = 22
    for col_idx, h_title in enumerate(r_headers, start=1):
        cell = ws4.cell(row=3, column=col_idx, value=h_title)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = align_right if col_idx in [1, 2, 3] else (align_center if col_idx == 4 else align_left)
        cell.border = cell_border

    with sqlite3.connect(db_path) as conn:
        c = conn.cursor()
        c.execute("SELECT bottles, minutes, label FROM promo_rates ORDER BY bottles ASC")
        r_rows = c.fetchall()

    r_row_idx = 4
    for idx, (b, m, l) in enumerate(r_rows):
        ws4.row_dimensions[r_row_idx].height = 19
        fill = zebra_fill if idx % 2 == 1 else white_fill
        eff = f"{m/b:.1f} m/bottle"
        row_data = [
            (b, align_right, "#,##0"),
            (m, align_right, "#,##0"),
            (m/60.0, align_right, "0.00"),
            (eff, align_center, "@"),
            (l, align_left, "@")
        ]
        for col_idx, (val, c_align, n_fmt) in enumerate(row_data, start=1):
            cell = ws4.cell(row=r_row_idx, column=col_idx, value=val)
            cell.font = data_font
            cell.fill = fill
            cell.alignment = c_align
            cell.border = cell_border
            cell.number_format = n_fmt
        r_row_idx += 1
    ws4.freeze_panes = "A4"

    # Auto-fit Column Widths
    for ws in [ws1, ws2, ws3, ws4]:
        for col in ws.columns:
            max_len = 0
            col_letter = get_column_letter(col[0].column)
            for cell in col:
                if cell.row in [1, 2]:
                    continue
                if cell.value is not None:
                    s = str(cell.value)
                    if len(s) > max_len:
                        max_len = len(s)
            ws.column_dimensions[col_letter].width = max(max_len + 4, 13)

    return wb

@app.route("/admin/api/export_xlsx")
def admin_export_xlsx():
    if not session.get('admin_logged_in'): return redirect("/admin/login")
    if not openpyxl:
        return redirect("/admin/api/export_csv")
    wb = generate_ecofi_excel_report(DB_PATH)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    filename = f"ECO_Fi_Operations_Report_{datetime.now().strftime('%Y%m%d')}.xlsx"
    return send_file(
        buf,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=filename
    )

@app.route("/admin/api/export_csv")
def admin_export_csv():
    if not session.get('admin_logged_in'): return redirect("/admin/login")
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("SELECT date, total_bottles FROM stats ORDER BY date DESC")
        rows = c.fetchall()
    
    csv_data = "Date,Total Bottles,Equivalent Minutes\n"
    for r in rows:
        csv_data += f"{r[0]},{r[1]},{r[1]*10}\n"
    
    return Response(csv_data, mimetype="text/csv", headers={"Content-disposition": "attachment; filename=ecofi_sales_report.csv"})

LOGIN_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>ECO-Fi Admin Login</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/5.15.4/css/all.min.css">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/admin-lte/3.2.0/css/adminlte.min.css">
    <style>
        body {
            background-color: #0b0f19;
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            margin: 0;
            padding: 16px;
        }
        .login-box-clean {
            width: 100%;
            max-width: 340px;
            background: #111827;
            border: 1px solid #1f2937;
            border-radius: 12px;
            padding: 24px;
            box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.6);
        }
        .login-title {
            font-size: 18px;
            font-weight: 700;
            color: #f9fafb;
            text-align: center;
            margin-bottom: 2px;
        }
        .login-subtitle {
            font-size: 11px;
            color: #6b7280;
            text-align: center;
            margin-bottom: 20px;
            letter-spacing: 0.5px;
            text-transform: uppercase;
        }
        .form-group-clean {
            margin-bottom: 14px;
        }
        .form-group-clean label {
            display: block;
            font-size: 12px;
            font-weight: 500;
            color: #9ca3af;
            margin-bottom: 5px;
        }
        .form-control-clean {
            width: 100%;
            height: 38px;
            background-color: #1f2937 !important;
            border: 1px solid #374151 !important;
            border-radius: 6px !important;
            color: #f9fafb !important;
            font-size: 13px !important;
            padding: 8px 12px !important;
            box-sizing: border-box;
            outline: none;
            transition: border-color 0.15s ease-in-out;
        }
        .form-control-clean:focus {
            border-color: #10b981 !important;
            box-shadow: 0 0 0 2px rgba(16, 185, 129, 0.2) !important;
        }
        input:-webkit-autofill,
        input:-webkit-autofill:hover, 
        input:-webkit-autofill:focus {
            -webkit-text-fill-color: #f9fafb !important;
            -webkit-box-shadow: 0 0 0px 1000px #1f2937 inset !important;
            transition: background-color 5000s ease-in-out 0s;
        }
        .btn-signin {
            width: 100%;
            height: 38px;
            background: #10b981;
            border: none;
            border-radius: 6px;
            color: #ffffff;
            font-size: 13.5px;
            font-weight: 600;
            cursor: pointer;
            margin-top: 6px;
            transition: background 0.15s ease;
        }
        .btn-signin:hover {
            background: #059669;
        }
        .btn-signin:active {
            background: #047857;
        }
        .alert-error {
            background: rgba(239, 68, 68, 0.15);
            border: 1px solid rgba(239, 68, 68, 0.3);
            color: #fca5a5;
            padding: 8px 12px;
            border-radius: 6px;
            font-size: 12px;
            margin-bottom: 14px;
            text-align: center;
        }
        .login-footer {
            margin-top: 18px;
            text-align: center;
            font-size: 12px;
        }
        .login-footer a {
            color: #6b7280;
            text-decoration: none;
        }
        .login-footer a:hover {
            color: #9ca3af;
        }
    </style>
</head>
<body>
<div class="login-box-clean">
    <div class="login-title"><i class="fas fa-recycle text-success mr-1"></i> ECO-Fi VENDO</div>
    <div class="login-subtitle">Master Control Panel</div>
    
    {% if error %}
    <div class="alert-error">{{ error }}</div>
    {% endif %}
    
    <form method="post">
        <div class="form-group-clean">
            <label for="username">Username</label>
            <input type="text" id="username" name="username" class="form-control-clean" placeholder="Admin username" required autofocus autocomplete="username">
        </div>
        
        <div class="form-group-clean">
            <label for="password">Password</label>
            <input type="password" id="password" name="password" class="form-control-clean" placeholder="Password" required autocomplete="current-password">
        </div>
        
        <button type="submit" class="btn-signin">Sign In</button>
    </form>
    
    <div class="login-footer">
        <a href="/">← Return to Client Portal</a>
    </div>
</div>
</body>
</html>
"""

ADMIN_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <title>ECO-Fi Master Admin Control Panel</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/5.15.4/css/all.min.css">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/admin-lte/3.2.0/css/adminlte.min.css">
    <script src="https://cdnjs.cloudflare.com/ajax/libs/jquery/3.6.0/jquery.min.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/twitter-bootstrap/4.6.0/js/bootstrap.bundle.min.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/admin-lte/3.2.0/js/adminlte.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/sweetalert2@11"></script>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@2.9.4/dist/Chart.min.js"></script>
    <style>
      body.dark-mode {
        background-color: #0b0f19;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      }
      .content-wrapper {
        background-color: #0b0f19 !important;
        padding: 16px 20px !important;
      }
      .section-view { display: none; }
      .section-view.active { display: block; animation: fadeIn 0.2s ease-in-out; }
      @keyframes fadeIn { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: translateY(0); } }
      
      .brand-link { font-weight: 700; letter-spacing: 0.5px; border-bottom: 1px solid rgba(255,255,255,0.08) !important; }
      .pulse-indicator { display: inline-block; width: 10px; height: 10px; border-radius: 50%; background: #10b981; box-shadow: 0 0 8px #10b981; margin-right: 5px; }
      .badge-custom { font-size: 0.85rem; padding: 0.35em 0.6em; }
      .modal-header { border-bottom: 1px solid rgba(255,255,255,0.1); }
      .modal-footer { border-top: 1px solid rgba(255,255,255,0.1); }
      .close { color: #fff; }
      .table-responsive { -webkit-overflow-scrolling: touch; overflow-x: auto; margin-bottom: 0; }

      /* Sleek Modern Dark Cards */
      .card {
        background: #1e293b !important;
        border: 1px solid rgba(255, 255, 255, 0.08) !important;
        border-radius: 12px !important;
        box-shadow: 0 4px 20px rgba(0, 0, 0, 0.25) !important;
        margin-bottom: 18px !important;
        overflow: hidden;
      }
      .card-header {
        background: rgba(15, 23, 42, 0.75) !important;
        border-bottom: 1px solid rgba(255, 255, 255, 0.08) !important;
        padding: 12px 18px !important;
      }
      .card-title {
        font-size: 1rem !important;
        font-weight: 700 !important;
        letter-spacing: 0.3px;
        margin: 0;
      }
      .card-body {
        padding: 16px 18px !important;
      }

      /* Polished Form Inputs */
      .form-control, .custom-select {
        background-color: #0f172a !important;
        border: 1px solid #334155 !important;
        color: #f8fafc !important;
        border-radius: 8px !important;
        height: 38px;
        font-size: 0.9rem;
      }
      .form-control:focus, .custom-select:focus {
        border-color: #10b981 !important;
        box-shadow: 0 0 0 3px rgba(16, 185, 129, 0.25) !important;
      }
      .form-group label {
        font-size: 0.82rem;
        font-weight: 600;
        color: #94a3b8;
        margin-bottom: 5px;
      }

      /* Clean Table Styling */
      .table-striped tbody tr:nth-of-type(odd) {
        background-color: rgba(255, 255, 255, 0.02) !important;
      }
      .table-hover tbody tr:hover {
        background-color: rgba(255, 255, 255, 0.05) !important;
      }
      .table {
        width: 100% !important;
        margin-bottom: 0 !important;
      }
      .table th {
        background: rgba(15, 23, 42, 0.95) !important;
        border-bottom: 1px solid rgba(255, 255, 255, 0.12) !important;
        border-top: none !important;
        font-size: 0.78rem !important;
        text-transform: uppercase !important;
        letter-spacing: 0.6px !important;
        color: #94a3b8 !important;
        font-weight: 700 !important;
        padding: 12px 14px !important;
        vertical-align: middle !important;
      }
      .table td {
        border-top: 1px solid rgba(255, 255, 255, 0.05) !important;
        vertical-align: middle !important;
        font-size: 0.88rem !important;
        padding: 12px 14px !important;
      }
      .table th.text-center, .table td.text-center {
        text-align: center !important;
      }
      .table th.text-right, .table td.text-right {
        text-align: right !important;
      }
      .btn-xs {
        padding: 4px 10px;
        font-size: 0.78rem;
        border-radius: 6px;
        font-weight: 600;
        line-height: 1.4;
      }
      
      /* Mobile Adaptive UI Optimization */
      @media (max-width: 767.98px) {
        .content-wrapper { padding: 10px !important; }
        .card { margin-bottom: 12px; }
        .card-header { padding: 0.6rem 0.8rem !important; }
        .card-body { padding: 0.8rem !important; }
        .card-title { font-size: 0.95rem !important; font-weight: 700; }
        .small-box { margin-bottom: 10px; border-radius: 12px; }
        .small-box .inner { padding: 10px; }
        .small-box .inner h3 { font-size: 1.45rem; margin-bottom: 2px; }
        .small-box .inner p { font-size: 0.72rem; margin-bottom: 0; line-height: 1.2; }
        .small-box .icon { font-size: 38px; right: 8px; top: 8px; opacity: 0.25; }
        .table th, .table td { padding: 0.5rem 0.4rem; font-size: 0.8rem; white-space: nowrap; }
        .btn-sm { padding: 0.28rem 0.5rem; font-size: 0.78rem; }
        .btn-block { margin-top: 4px; }
        .modal-dialog { margin: 12px auto; max-width: 95vw; }
        .navbar-nav .nav-link { padding-left: 0.5rem; padding-right: 0.5rem; }
        .brand-link { font-size: 0.95rem; }
        .main-header { padding: 0.25rem 0.5rem; }
      }
    </style>
</head>
<body class="hold-transition sidebar-mini dark-mode">
<div class="wrapper">
  <!-- Top Navbar -->
  <nav class="main-header navbar navbar-expand navbar-dark">
    <ul class="navbar-nav">
      <li class="nav-item"><a class="nav-link" data-widget="pushmenu" href="#" role="button"><i class="fas fa-bars"></i></a></li>
      <li class="nav-item"><a href="/" target="_blank" class="nav-link"><i class="fas fa-wifi text-success"></i> <span class="d-none d-sm-inline">Portal</span></a></li>
    </ul>
    <ul class="navbar-nav ml-auto">
      <li class="nav-item"><a href="/admin/api/export_xlsx" class="btn btn-sm btn-success mr-2 shadow-sm"><i class="fas fa-file-excel mr-1"></i> <span class="d-none d-sm-inline">Export Data</span></a></li>
      <li class="nav-item"><a href="/admin/logout" class="btn btn-sm btn-danger"><i class="fas fa-sign-out-alt"></i> <span class="d-none d-sm-inline">Logout</span></a></li>
    </ul>
  </nav>

  <!-- Complete Filipino PisoFi-Style AdminLTE Sidebar -->
  <aside class="main-sidebar sidebar-dark-primary elevation-4">
    <a href="#" class="brand-link text-center">
      <span class="brand-text font-weight-bold text-success"><i class="fas fa-recycle"></i> ECO-Fi MASTER</span>
    </a>
    <div class="sidebar">
      <nav class="mt-2">
        <ul class="nav nav-pills nav-sidebar flex-column" data-widget="treeview" role="menu">
          <li class="nav-header">MAIN NAVIGATION</li>
          <li class="nav-item"><a href="javascript:showSection('sec-dashboard')" id="nav-dashboard" class="nav-link active"><i class="nav-icon fas fa-tachometer-alt"></i><p>Dashboard & Stats</p></a></li>
          <li class="nav-item"><a href="javascript:showSection('sec-clients')" id="nav-clients" class="nav-link"><i class="nav-icon fas fa-users"></i><p>Active Clients</p></a></li>
          <li class="nav-item"><a href="javascript:showSection('sec-vouchers')" id="nav-vouchers" class="nav-link"><i class="nav-icon fas fa-ticket-alt"></i><p>Voucher Tickets</p></a></li>
          <li class="nav-item"><a href="javascript:showSection('sec-members')" id="nav-members" class="nav-link"><i class="nav-icon fas fa-user-friends"></i><p>Member Wallets</p></a></li>
          <li class="nav-item"><a href="javascript:showSection('sec-rates')" id="nav-rates" class="nav-link"><i class="nav-icon fas fa-tags"></i><p>Rates & Promos</p></a></li>
          
          <li class="nav-header">SYSTEM & HARDWARE</li>
          <li class="nav-item"><a href="javascript:showSection('sec-esp32')" id="nav-esp32" class="nav-link"><i class="nav-icon fas fa-microchip"></i><p>ESP32 Hardware</p></a></li>
          <li class="nav-item"><a href="javascript:showSection('sec-audio')" id="nav-audio" class="nav-link"><i class="nav-icon fas fa-volume-up"></i><p>Audio & Chimes</p></a></li>
          <li class="nav-item"><a href="javascript:showSection('sec-portal-custom')" id="nav-portal-custom" class="nav-link"><i class="nav-icon fas fa-palette"></i><p>Portal & Banners</p></a></li>
          <li class="nav-item"><a href="javascript:showSection('sec-bandwidth')" id="nav-bandwidth" class="nav-link"><i class="nav-icon fas fa-tachometer-alt"></i><p>Bandwidth & Speed</p></a></li>
          <li class="nav-item"><a href="javascript:showSection('sec-walled')" id="nav-walled" class="nav-link"><i class="nav-icon fas fa-globe-americas"></i><p>Walled Garden Sites</p></a></li>
          <li class="nav-item"><a href="javascript:showSection('sec-security')" id="nav-security" class="nav-link"><i class="nav-icon fas fa-shield-alt"></i><p>MAC Filtering</p></a></li>
          <li class="nav-item"><a href="javascript:showSection('sec-telegram')" id="nav-telegram" class="nav-link"><i class="nav-icon fab fa-telegram-plane"></i><p>Telegram Alerts</p></a></li>
          <li class="nav-item"><a href="javascript:showSection('sec-licensing')" id="nav-licensing" class="nav-link"><i class="nav-icon fas fa-key"></i><p>Hardware Licensing</p></a></li>
        </ul>
      </nav>
    </div>
  </aside>

  <!-- Main Content Wrapper -->
  <div class="content-wrapper p-2 p-md-4">
    
    <!-- 1. DASHBOARD OVERVIEW SECTION -->
    <div id="sec-dashboard" class="section-view active">
      <div class="row">
        <div class="col-lg-3 col-6">
          <div class="small-box bg-success">
            <div class="inner"><h3 id="stat-today">0</h3><p>Today's Bottles</p></div>
            <div class="icon"><i class="fas fa-recycle"></i></div>
          </div>
        </div>
        <div class="col-lg-3 col-6">
          <div class="small-box bg-info">
            <div class="inner"><h3 id="stat-total">0</h3><p>Lifetime Bottles</p></div>
            <div class="icon"><i class="fas fa-chart-line"></i></div>
          </div>
        </div>
        <div class="col-lg-3 col-6">
          <div class="small-box bg-warning">
            <div class="inner"><h3 id="stat-clients">0</h3><p>Active Clients</p></div>
            <div class="icon"><i class="fas fa-wifi"></i></div>
          </div>
        </div>
        <div class="col-lg-3 col-6">
          <div class="small-box bg-danger">
            <div class="inner"><h3 id="stat-lic">ACTIVE</h3><p>License Status</p></div>
            <div class="icon"><i class="fas fa-shield-alt"></i></div>
          </div>
        </div>
      </div>

      <div class="row">
        <div class="col-lg-8">
          <div class="card card-dark">
            <div class="card-header"><h3 class="card-title"><i class="fas fa-chart-bar"></i> 7-Day Recycling Intake History</h3></div>
            <div class="card-body">
              <div class="chart-container" style="position: relative; height:250px; width:100%">
                <canvas id="historyChart"></canvas>
              </div>
            </div>
          </div>
        </div>
        <div class="col-lg-4">
          <div class="card card-dark" style="height: 312px;">
            <div class="card-header"><h3 class="card-title"><i class="fas fa-microchip"></i> System Resources</h3></div>
            <div class="card-body">
              <div style="margin-bottom: 15px;">
                <div class="d-flex justify-content-between"><span>CPU Load</span><strong id="sys-cpu">0%</strong></div>
                <div class="progress" style="height: 6px;"><div id="sys-cpu-bar" class="progress-bar bg-success" style="width: 0%"></div></div>
              </div>
              <div style="margin-bottom: 15px;">
                <div class="d-flex justify-content-between"><span>Memory (RAM)</span><strong id="sys-ram">0%</strong></div>
                <div class="progress" style="height: 6px;"><div id="sys-ram-bar" class="progress-bar bg-info" style="width: 0%"></div></div>
              </div>
              <div style="margin-bottom: 15px;">
                <div class="d-flex justify-content-between"><span>Storage (eMMC)</span><strong id="sys-disk">22%</strong></div>
                <div class="progress" style="height: 6px;"><div id="sys-disk-bar" class="progress-bar bg-warning" style="width: 22%"></div></div>
              </div>
              <div>
                <div class="d-flex justify-content-between"><span>System Uptime</span><strong id="sys-uptime">0h 0m</strong></div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- 1B. ESP32 HARDWARE SECTION -->
    <div id="sec-esp32" class="section-view">
      <div class="card card-purple">
        <div class="card-header d-flex justify-content-between align-items-center">
          <h3 class="card-title m-0"><i class="fas fa-microchip"></i> ESP32 Hardware Calibration</h3>
          <button class="btn btn-warning btn-sm ml-auto" onclick="triggerEsp32Config()"><i class="fas fa-wifi"></i> Reboot to Captive Portal</button>
        </div>
        <div class="card-body">
          <div class="row">
            <div class="col-md-4 form-group">
              <label>Bin Full Distance (cm):</label>
              <input type="number" id="esp-bin" class="form-control" value="{{ config.get('esp_bin_full_threshold_cm', 15) }}">
            </div>
            <div class="col-md-4 form-group">
              <label>Entrance Timeout (sec):</label>
              <input type="number" id="esp-ent-tout" class="form-control" value="{{ config.get('esp_entrance_gate_timeout', 30) }}">
            </div>
            <div class="col-md-4 form-group">
              <label>Bottle Settle Time (ms):</label>
              <input type="number" id="esp-settle" class="form-control" value="{{ config.get('esp_settle_time_ms', 500) }}">
            </div>
            <div class="col-md-4 form-group">
              <label>Success Drop Time (ms):</label>
              <input type="number" id="esp-suc-time" class="form-control" value="{{ config.get('esp_success_drop_tout_ms', 3000) }}">
            </div>
            <div class="col-md-4 form-group">
              <label>Reject Drop Time (ms):</label>
              <input type="number" id="esp-rej-time" class="form-control" value="{{ config.get('esp_reject_drop_time_ms', 2000) }}">
            </div>
            <div class="col-md-4 form-group">
              <label>NIR W Min / Max:</label>
              <div class="d-flex"><input type="number" id="esp-nir-min" class="form-control mr-1" value="{{ config.get('esp_pet_nir_w_min', 200) }}"><input type="number" id="esp-nir-max" class="form-control ml-1" value="{{ config.get('esp_pet_nir_w_max', 5000) }}"></div>
            </div>
            
            <div class="col-12 mt-3 mb-2"><h5 class="text-info border-bottom border-secondary pb-1">Servo Tuning (Angles 0-180)</h5></div>
            
            <div class="col-md-4 form-group">
              <label>Entrance Gate (Close / Open):</label>
              <div class="d-flex"><input type="number" id="esp-ent-close" class="form-control mr-1" value="{{ config.get('esp_ent_close_angle', 0) }}" placeholder="Close"><input type="number" id="esp-ent-open" class="form-control ml-1" value="{{ config.get('esp_ent_open_angle', 90) }}" placeholder="Open"></div>
            </div>
            <div class="col-md-4 form-group">
              <label>Success Gate (Close / Open):</label>
              <div class="d-flex"><input type="number" id="esp-suc-close" class="form-control mr-1" value="{{ config.get('esp_suc_close_angle', 0) }}" placeholder="Close"><input type="number" id="esp-suc-open" class="form-control ml-1" value="{{ config.get('esp_suc_open_angle', 90) }}" placeholder="Open"></div>
            </div>
            <div class="col-md-4 form-group">
              <label>Reject Gate (Close / Open):</label>
              <div class="d-flex"><input type="number" id="esp-rej-close" class="form-control mr-1" value="{{ config.get('esp_rej_close_angle', 0) }}" placeholder="Close"><input type="number" id="esp-rej-open" class="form-control ml-1" value="{{ config.get('esp_rej_open_angle', 90) }}" placeholder="Open"></div>
            </div>
          </div>
          <button class="btn btn-success mt-3" onclick="saveEsp32Config()"><i class="fas fa-save"></i> Save & Push to ESP32</button>
        </div>
      </div>
    </div>

    <!-- 2. ACTIVE CLIENTS SECTION -->
    <div id="sec-clients" class="section-view">
      <div class="card card-primary">
        <div class="card-header"><h3 class="card-title"><i class="fas fa-users"></i> Connected Client Sessions</h3></div>
        <div class="card-body p-0">
          <div class="table-responsive">
            <table class="table table-striped table-hover mb-0">
              <thead>
                <tr>
                  <th style="padding: 10px 14px; width: 18%;">IP Address</th>
                  <th style="padding: 10px 14px; width: 18%;">MAC Address</th>
                  <th style="padding: 10px 14px; width: 18%;">Remaining Time</th>
                  <th style="padding: 10px 14px; width: 12%; text-align: center;">Status</th>
                  <th style="padding: 10px 14px; width: 16%;">Speed (DL/UL)</th>
                  <th style="padding: 10px 14px; width: 18%; text-align: right; min-width: 220px; white-space: nowrap;">Actions</th>
                </tr>
              </thead>
              <tbody id="clients-table-body"></tbody>
            </table>
          </div>
        </div>
      </div>
    </div>

    <!-- 3. VOUCHER & TICKETS SECTION -->
    <div id="sec-vouchers" class="section-view">
      <div class="card card-success">
        <div class="card-header"><h3 class="card-title"><i class="fas fa-magic"></i> Generate Prepaid Vouchers</h3></div>
        <div class="card-body">
          <div class="row">
            <div class="col-12 col-sm-6 col-md-3 form-group">
              <label>Number of Vouchers (1-100):</label>
              <input type="number" id="v-qty" class="form-control" value="5" min="1" max="100">
            </div>
            <div class="col-12 col-sm-6 col-md-3 form-group">
              <label>Duration (Minutes):</label>
              <select id="v-mins" class="form-control">
                <option value="10">10 Minutes (1 Bottle Equivalent)</option>
                <option value="45">45 Minutes</option>
                <option value="60" selected>1 Hour</option>
                <option value="180">3 Hours</option>
                <option value="1440">24 Hours (1 Day Pass)</option>
              </select>
            </div>
            <div class="col-12 col-sm-6 col-md-3 form-group">
              <label>Custom Note / Batch Tag:</label>
              <input type="text" id="v-note" class="form-control" placeholder="e.g. Student Promo Batch">
            </div>
            <div class="col-12 col-sm-6 col-md-3 form-group">
              <label class="d-none d-md-block">&nbsp;</label>
              <button class="btn btn-success btn-block" onclick="generateVouchers()"><i class="fas fa-ticket-alt"></i> Generate Vouchers</button>
            </div>
          </div>
          <div id="v-results" class="mt-3"></div>
        </div>
      </div>
      
      <div class="card card-dark mt-3">
        <div class="card-header"><h3 class="card-title"><i class="fas fa-history"></i> Voucher History</h3></div>
        <div class="card-body p-0">
          <div class="table-responsive">
            <table class="table table-striped table-hover mb-0" id="voucher-history-table">
              <thead>
                <tr>
                  <th style="padding: 10px 14px; width: 18%;">Voucher Code</th>
                  <th style="padding: 10px 14px; width: 12%;">Duration</th>
                  <th style="padding: 10px 14px; width: 12%; text-align: center;">Status</th>
                  <th style="padding: 10px 14px; width: 18%;">Note / Tag</th>
                  <th style="padding: 10px 14px; width: 18%;">Created Date</th>
                  <th style="padding: 10px 14px; width: 12%;">Used By (IP)</th>
                  <th style="padding: 10px 14px; width: 10%; text-align: right; min-width: 100px; white-space: nowrap;">Actions</th>
                </tr>
              </thead>
              <tbody id="voucher-history-body"></tbody>
            </table>
          </div>
        </div>
      </div>
    </div>

    <!-- 4. MEMBER WALLETS SECTION -->
    <div id="sec-members" class="section-view">
      <div class="card card-success">
        <div class="card-header"><h3 class="card-title"><i class="fas fa-user-plus"></i> Add New Member Account</h3></div>
        <div class="card-body">
          <div class="row">
            <div class="col-12 col-sm-6 col-md-4 form-group">
              <label>Username (min 3 chars):</label>
              <input type="text" id="new-mem-user" class="form-control" placeholder="e.g. student01">
            </div>
            <div class="col-12 col-sm-6 col-md-4 form-group">
              <label>4-Digit Security PIN:</label>
              <input type="password" id="new-mem-pin" class="form-control" placeholder="e.g. 1234" maxlength="6">
            </div>
            <div class="col-12 col-sm-6 col-md-2 form-group">
              <label>Initial Wallet (Mins):</label>
              <input type="number" id="new-mem-mins" class="form-control" value="0" min="0">
            </div>
            <div class="col-12 col-sm-6 col-md-2 form-group">
              <label class="d-none d-md-block">&nbsp;</label>
              <button class="btn btn-success btn-block" onclick="addMember()"><i class="fas fa-plus"></i> Create</button>
            </div>
          </div>
        </div>
      </div>

      <div class="card card-info mt-3">
        <div class="card-header"><h3 class="card-title"><i class="fas fa-user-friends"></i> Registered Member Accounts</h3></div>
        <div class="card-body p-0">
          <div class="table-responsive">
            <table class="table table-striped table-hover mb-0">
              <thead>
                <tr>
                  <th style="padding: 10px 14px; width: 30%;">Username</th>
                  <th style="padding: 10px 14px; width: 25%;">Wallet Balance</th>
                  <th style="padding: 10px 14px; width: 25%;">Registered At</th>
                  <th style="padding: 10px 14px; width: 20%; text-align: right; min-width: 170px; white-space: nowrap;">Actions</th>
                </tr>
              </thead>
              <tbody id="members-table-body"></tbody>
            </table>
          </div>
        </div>
      </div>
    </div>

    <!-- 5. RATES & PROMOS SECTION (DYNAMIC ADD+ & EDIT RATE TIERS) -->
    <div id="sec-rates" class="section-view">
      <div class="row">
        <!-- Card 1: General Timing -->
        <div class="col-12 col-lg-6">
          <div class="card mb-3">
            <div class="card-header"><h3 class="card-title text-warning"><i class="fas fa-sliders-h mr-1"></i> Basic Rate & Drop Timeout</h3></div>
            <div class="card-body">
              <div class="row">
                <div class="col-6 form-group mb-2">
                  <label>Base Rate (Mins / Bottle):</label>
                  <input type="number" id="rate-1" class="form-control" value="{{ config.minutes_per_bottle }}" min="1" oninput="onBaseRateInput()">
                </div>
                <div class="col-6 form-group mb-2">
                  <label>Chute Timeout (Seconds):</label>
                  <input type="number" id="rate-timeout" class="form-control" value="{{ config.drop_timeout }}" min="10" max="120">
                </div>
                <div class="col-12 mt-2">
                  <button class="btn btn-warning btn-block font-weight-bold" onclick="saveRates()"><i class="fas fa-save mr-1"></i> Save Base Timing</button>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- Card 2: Template Presets -->
        <div class="col-12 col-lg-6">
          <div class="card mb-3">
            <div class="card-header"><h3 class="card-title text-info"><i class="fas fa-magic mr-1"></i> Quick-Load Rate Templates</h3></div>
            <div class="card-body">
              <div class="form-group mb-2">
                <label>Balanced Rate Curve Templates:</label>
                <select id="rate-preset-select" class="form-control">
                  <option value="standard">🌟 Standard Community (1b=10m, 3b=40m, 5b=1h15m, 10b=3h)</option>
                  <option value="aggressive">⚡ Aggressive Reward (1b=10m, 5b=1h10m, 10b=3h, 20b=7h)</option>
                  <option value="cafe">☕ Café / Study Hub (1b=20m, 3b=1h15m, 6b=3h, 12b=7h)</option>
                </select>
              </div>
              <div class="mt-2 pt-1">
                <button class="btn btn-info btn-block font-weight-bold" onclick="applyRatePreset()"><i class="fas fa-file-import mr-1"></i> Apply Selected Template</button>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- Add / Edit Custom Promo Rate Form -->
      <div class="card mb-3" id="promo-form-card">
        <div class="card-header"><h3 class="card-title text-success" id="promo-form-title"><i class="fas fa-plus-circle mr-1"></i> Add Custom Promo Rate Package</h3></div>
        <div class="card-body">
          <input type="hidden" id="edit-original-bottles" value="">
          <div class="row align-items-end">
            <div class="col-12 col-sm-6 col-md-3 form-group mb-2">
              <label>Bottles Required:</label>
              <div class="input-group">
                <input type="number" id="new-rate-bottles" class="form-control" placeholder="e.g. 5" min="1" max="100" oninput="validatePromoFormMath()">
                <div class="input-group-append">
                  <button type="button" class="btn btn-outline-secondary dropdown-toggle dropdown-toggle-split" data-toggle="dropdown"></button>
                  <div class="dropdown-menu dropdown-menu-right">
                    <a class="dropdown-item" href="javascript:setRateBottles(2)">2 Bottles</a>
                    <a class="dropdown-item" href="javascript:setRateBottles(3)">3 Bottles</a>
                    <a class="dropdown-item" href="javascript:setRateBottles(5)">5 Bottles</a>
                    <a class="dropdown-item" href="javascript:setRateBottles(6)">6 Bottles</a>
                    <a class="dropdown-item" href="javascript:setRateBottles(10)">10 Bottles</a>
                    <a class="dropdown-item" href="javascript:setRateBottles(12)">12 Bottles</a>
                    <a class="dropdown-item" href="javascript:setRateBottles(15)">15 Bottles</a>
                    <a class="dropdown-item" href="javascript:setRateBottles(20)">20 Bottles</a>
                  </div>
                </div>
              </div>
            </div>
            
            <div class="col-12 col-sm-6 col-md-3 form-group mb-2">
              <label>Time Credited:</label>
              <div class="input-group">
                <input type="number" id="new-rate-time-val" class="form-control" placeholder="e.g. 90" min="1" oninput="validatePromoFormMath()">
                <div class="input-group-append">
                  <select id="new-rate-time-unit" class="custom-select" style="max-width: 85px;" onchange="validatePromoFormMath()">
                    <option value="mins" selected>Mins</option>
                    <option value="hours">Hours</option>
                    <option value="days">Days</option>
                  </select>
                </div>
              </div>
            </div>

            <div class="col-12 col-sm-8 col-md-4 form-group mb-2">
              <label>Package Display Label:</label>
              <div class="input-group">
                <input type="text" id="new-rate-label" class="form-control" placeholder="Auto-generated if blank">
                <div class="input-group-append">
                  <button class="btn btn-outline-secondary" type="button" onclick="autoGenerateRateLabel()" title="Auto-generate friendly label">🪄</button>
                </div>
              </div>
            </div>

            <div class="col-12 col-sm-4 col-md-2 form-group mb-2">
              <div class="d-flex">
                <button class="btn btn-success btn-block mr-1 font-weight-bold" id="btn-save-promo" onclick="addPromoRate()"><i class="fas fa-plus mr-1"></i> Add</button>
                <button class="btn btn-secondary font-weight-bold" id="btn-cancel-promo" onclick="cancelEditPromoRate()" style="display:none;" title="Cancel Edit"><i class="fas fa-times"></i></button>
              </div>
            </div>
          </div>

          <!-- Live Validator Feedback Box -->
          <div id="rate-validator-feedback" class="alert alert-info py-2 px-3 mt-2 mb-0" style="display:none; font-size:12px; border-radius:8px;">
            <div class="d-flex justify-content-between align-items-center">
              <span id="rate-validator-eff" class="font-weight-bold">📊 Efficiency: --</span>
              <span id="rate-validator-status" class="font-weight-bold">✔ Status: OK</span>
            </div>
            <div id="rate-validator-msg" style="margin-top:4px;"></div>
          </div>
        </div>
      </div>

      <!-- Active Rate Packages Table -->
      <div class="card mb-3">
        <div class="card-header"><h3 class="card-title text-light"><i class="fas fa-tags mr-1"></i> Active Rate Tiers & Promo Curves</h3></div>
        <div class="card-body p-0">
          <div class="table-responsive">
            <table class="table table-striped table-hover mb-0">
              <thead>
                <tr>
                  <th style="padding: 10px 14px; width: 20%;">Bottles Required</th>
                  <th style="padding: 10px 14px; width: 22%;">Time Credited</th>
                  <th style="padding: 10px 14px; width: 22%;">Rate Efficiency</th>
                  <th style="padding: 10px 14px; width: 20%;">Package Label</th>
                  <th style="padding: 10px 14px; width: 16%; min-width: 170px; text-align: right; white-space: nowrap;">Actions</th>
                </tr>
              </thead>
              <tbody id="rates-table-body"></tbody>
            </table>
          </div>
        </div>
      </div>
    </div>

    <!-- 6. AUDIO CUSTOMIZER SECTION -->
    <div id="sec-audio" class="section-view">
      <div class="card card-warning mb-3">
        <div class="card-header"><h3 class="card-title font-weight-bold"><i class="fas fa-volume-up"></i> Portal Audio & Event Chimes</h3></div>
        <div class="card-body p-0">
          <div class="d-none d-md-flex bg-dark text-white p-2 font-weight-bold" style="font-size: 13px;">
            <div class="col-md-3">Event Stage</div>
            <div class="col-md-3">Audio Preset</div>
            <div class="col-md-4">Custom URL or Upload</div>
            <div class="col-md-2 text-center">Preview</div>
          </div>
          
          <!-- EVENT 1: BG LOOP -->
          <div class="row m-0 p-3 border-bottom align-items-center">
            <div class="col-12 col-md-3 mb-2 mb-md-0">
              <span class="badge badge-secondary p-2 d-block text-left"><i class="fas fa-music"></i> 1. Standby Loop</span>
            </div>
            <div class="col-12 col-md-3 mb-2 mb-md-0">
              <select id="audio-bg-preset" class="form-control form-control-sm" onchange="onAudioPresetChange('bg')">
                <option value="/static/audio/eco_loop.wav" {% if config.audio_bg == '/static/audio/eco_loop.wav' or config.audio_bg == '/static/audio/b1.wav' or not config.audio_bg %}selected{% endif %}>📻 Default ECO-Fi Standby Loop</option>
                <option value="silent" {% if config.audio_bg == 'silent' %}selected{% endif %}>🔇 Silent</option>
                <option value="custom" {% if config.audio_bg and config.audio_bg not in ['/static/audio/eco_loop.wav', '/static/audio/b1.wav', 'silent'] %}selected{% endif %}>📁 Custom File / URL</option>
              </select>
            </div>
            <div class="col-12 col-md-4 mb-2 mb-md-0">
              <div class="input-group input-group-sm">
                <input type="text" id="audio-bg-custom" class="form-control" placeholder="Custom URL..." value="{{ config.audio_bg or '/static/audio/eco_loop.wav' }}" oninput="updateAudioPlayer('bg')">
                <div class="input-group-append">
                  <label class="btn btn-secondary mb-0 rounded-right" style="cursor:pointer;" title="Upload File"><i class="fas fa-upload"></i>
                    <input type="file" id="upload-file-bg" accept="audio/*" onchange="uploadAudioFile('bg')" style="display:none;">
                  </label>
                </div>
              </div>
            </div>
            <div class="col-12 col-md-2 text-center">
              <audio id="audio-player-bg" controls src="{{ config.audio_bg or '/static/audio/eco_loop.wav' }}" style="height: 30px; width: 100%; max-width: 250px;"></audio>
            </div>
          </div>

          <!-- EVENT 2: DEPOSIT CHIME -->
          <div class="row m-0 p-3 border-bottom align-items-center">
            <div class="col-12 col-md-3 mb-2 mb-md-0">
              <span class="badge badge-success p-2 d-block text-left"><i class="fas fa-coins"></i> 2. Deposit Chime</span>
            </div>
            <div class="col-12 col-md-3 mb-2 mb-md-0">
              <select id="audio-insert-preset" class="form-control form-control-sm" onchange="onAudioPresetChange('insert')">
                <option value="/static/audio/eco_chime.wav" {% if config.audio_insert == '/static/audio/eco_chime.wav' or config.audio_insert == '/static/audio/coin.wav' or not config.audio_insert %}selected{% endif %}>🔔 Classic ECO-Fi Chime</option>
                <option value="/static/audio/eco_drop.wav" {% if config.audio_insert == '/static/audio/eco_drop.wav' or config.audio_insert == '/static/audio/coin_insert.wav' %}selected{% endif %}>🍾 Mechanical Bottle Drop</option>
                <option value="/static/audio/eco_pulse.wav" {% if config.audio_insert == '/static/audio/eco_pulse.wav' or config.audio_insert == '/static/audio/insert_coin.wav' %}selected{% endif %}>🎶 Double Pulse Alert</option>
                <option value="arcade_powerup" {% if config.audio_insert == 'arcade_powerup' %}selected{% endif %}>🎮 8-Bit Power-Up</option>
                <option value="voice_filipino" {% if config.audio_insert == 'voice_filipino' %}selected{% endif %}>🗣️ Filipino Voice</option>
                <option value="custom" {% if config.audio_insert and config.audio_insert not in ['/static/audio/eco_chime.wav', '/static/audio/eco_drop.wav', '/static/audio/eco_pulse.wav', '/static/audio/coin.wav', '/static/audio/coin_insert.wav', '/static/audio/insert_coin.wav', 'arcade_powerup', 'voice_filipino'] %}selected{% endif %}>📁 Custom File / URL</option>
              </select>
            </div>
            <div class="col-12 col-md-4 mb-2 mb-md-0">
              <div class="input-group input-group-sm">
                <input type="text" id="audio-insert-custom" class="form-control" placeholder="Custom URL..." value="{{ config.audio_insert or '/static/audio/eco_chime.wav' }}" oninput="updateAudioPlayer('insert')">
                <div class="input-group-append">
                  <label class="btn btn-secondary mb-0 rounded-right" style="cursor:pointer;" title="Upload File"><i class="fas fa-upload"></i>
                    <input type="file" id="upload-file-insert" accept="audio/*" onchange="uploadAudioFile('insert')" style="display:none;">
                  </label>
                </div>
              </div>
            </div>
            <div class="col-12 col-md-2 text-center">
              <audio id="audio-player-insert" controls src="{{ config.audio_insert or '/static/audio/eco_chime.wav' }}" style="height: 30px; width: 100%; max-width: 250px;"></audio>
            </div>
          </div>

          <!-- EVENT 3: SUCCESS CHIME -->
          <div class="row m-0 p-3 border-bottom align-items-center">
            <div class="col-12 col-md-3 mb-2 mb-md-0">
              <span class="badge badge-info p-2 d-block text-left"><i class="fas fa-check-circle"></i> 3. Session Complete</span>
            </div>
            <div class="col-12 col-md-3 mb-2 mb-md-0">
              <select id="audio-success-preset" class="form-control form-control-sm" onchange="onAudioPresetChange('success')">
                <option value="/static/audio/eco_success.wav" {% if config.audio_success == '/static/audio/eco_success.wav' or config.audio_success == '/static/audio/success_ding.wav' or not config.audio_success %}selected{% endif %}>✨ ECO-Fi Success</option>
                <option value="crystal_bell" {% if config.audio_success == 'crystal_bell' %}selected{% endif %}>🛎️ Crystal Bell</option>
                <option value="silent" {% if config.audio_success == 'silent' %}selected{% endif %}>🔇 Silent</option>
                <option value="custom" {% if config.audio_success and config.audio_success not in ['/static/audio/eco_success.wav', '/static/audio/success_ding.wav', 'crystal_bell', 'silent'] %}selected{% endif %}>📁 Custom File / URL</option>
              </select>
            </div>
            <div class="col-12 col-md-4 mb-2 mb-md-0">
              <div class="input-group input-group-sm">
                <input type="text" id="audio-success-custom" class="form-control" placeholder="Custom URL..." value="{{ config.audio_success or '/static/audio/eco_success.wav' }}" oninput="updateAudioPlayer('success')">
                <div class="input-group-append">
                  <label class="btn btn-secondary mb-0 rounded-right" style="cursor:pointer;" title="Upload File"><i class="fas fa-upload"></i>
                    <input type="file" id="upload-file-success" accept="audio/*" onchange="uploadAudioFile('success')" style="display:none;">
                  </label>
                </div>
              </div>
            </div>
            <div class="col-12 col-md-2 text-center">
              <audio id="audio-player-success" controls src="{{ config.audio_success or '/static/audio/eco_success.wav' }}" style="height: 30px; width: 100%; max-width: 250px;"></audio>
            </div>
          </div>
          <div class="card-footer bg-light d-flex flex-wrap align-items-center justify-content-between p-3 border-top-0">
            <div class="d-flex align-items-center mb-2 mb-md-0" style="min-width: 250px; max-width: 400px; flex-grow: 1;">
              <span class="mr-3 font-weight-bold" style="font-size:13px;"><i class="fas fa-volume-up"></i> Master Volume:</span>
              <input type="range" id="audio-vol-input" class="custom-range flex-grow-1" min="10" max="100" value="{{ config.audio_volume or 80 }}" oninput="updatePreviewVolume()">
              <span id="vol-lbl" class="ml-2 badge badge-dark" style="width:45px;">{{ config.audio_volume or 80 }}%</span>
            </div>
            <button class="btn btn-success btn-sm px-4 shadow-sm" onclick="saveAudioSettings()"><i class="fas fa-save"></i> Save Audio Settings</button>
          </div>
        </div>
      </div>
    </div>

    <!-- 7. PORTAL & BANNERS SECTION -->
    <div id="sec-portal-custom" class="section-view">
      <div class="card card-primary">
        <div class="card-header"><h3 class="card-title"><i class="fas fa-palette"></i> Portal Branding & Announcements</h3></div>
        <div class="card-body">
          <div class="row">
            <div class="col-12 col-md-6 form-group">
              <label>Hotspot Vendo Name:</label>
              <input type="text" id="cfg-vendo-name" class="form-control" value="{{ config.vendo_name }}">
            </div>
            <div class="col-12 col-md-6 form-group">
              <label>Subtitle / Tagline:</label>
              <input type="text" id="cfg-vendo-sub" class="form-control" value="{{ config.vendo_subtitle }}">
            </div>
          </div>
          <div class="form-group">
            <label>Announcement Banner Message:</label>
            <textarea id="cfg-announcement" class="form-control" rows="2">♻️ Welcome to ECO-Fi! Deposit clean PET plastic bottles to earn high-speed Wi-Fi access.</textarea>
          </div>
          <button class="btn btn-primary" onclick="savePortalCustom()"><i class="fas fa-save"></i> Update Portal Branding</button>
        </div>
      </div>
    </div>

    <!-- 8. BANDWIDTH & ANTI-TETHERING SECTION -->
    <div id="sec-bandwidth" class="section-view">
      <div class="card card-info">
        <div class="card-header"><h3 class="card-title"><i class="fas fa-tachometer-alt"></i> Bandwidth & Anti-Tethering Rules</h3></div>
        <div class="card-body">
          <div class="row">
            <div class="col-12 col-md-4 form-group">
              <label>Default Download Speed Limit (Kbps):</label>
              <input type="number" id="cfg-dl" class="form-control" value="{{ config.default_dl_kbps }}" min="128">
            </div>
            <div class="col-12 col-md-4 form-group">
              <label>Default Upload Speed Limit (Kbps):</label>
              <input type="number" id="cfg-ul" class="form-control" value="{{ config.default_ul_kbps }}" min="64">
            </div>
            <div class="col-12 col-md-4 form-group">
              <label>Anti-Tethering Protection (TTL=64):</label>
              <select id="cfg-tether" class="form-control">
                <option value="1" {% if config.anti_tethering == '1' %}selected{% endif %}>🟢 ENABLED (Blocks Hotspot Resharing)</option>
                <option value="0" {% if config.anti_tethering == '0' %}selected{% endif %}>🔴 DISABLED</option>
              </select>
            </div>
          </div>
          <button class="btn btn-info mt-2" onclick="saveBandwidth()"><i class="fas fa-save"></i> Apply Traffic Limits</button>
        </div>
      </div>
    </div>

    <!-- 9. WALLED GARDEN FREE DOMAINS SECTION -->
    <div id="sec-walled" class="section-view">
      <div class="card card-primary">
        <div class="card-header"><h3 class="card-title"><i class="fas fa-globe-americas"></i> Walled Garden Free Whitelisted Websites</h3></div>
        <div class="card-body">
          <p class="text-muted" style="font-size:13px;">Domains added here are accessible to users even without inserting bottles or logging in (e.g. government, school portals, payment portals):</p>
          <div class="row">
            <div class="col-12 col-md-6 form-group">
              <label>Domain Name (e.g. gcash.com or deped.gov.ph):</label>
              <input type="text" id="walled-domain" class="form-control" placeholder="e.g. portal.school.edu.ph">
            </div>
            <div class="col-12 col-md-4 form-group">
              <label>Note / Purpose:</label>
              <input type="text" id="walled-note" class="form-control" placeholder="e.g. School Portal">
            </div>
            <div class="col-12 col-md-2 form-group">
              <label class="d-none d-md-block">&nbsp;</label>
              <button class="btn btn-primary btn-block" onclick="addWalledDomain()"><i class="fas fa-plus"></i> Whitelist Site</button>
            </div>
          </div>
          <div class="table-responsive">
            <table class="table table-striped table-hover mb-0 mt-3">
              <thead>
                <tr>
                  <th style="padding: 10px 14px; width: 45%;">Whitelisted Domain</th>
                  <th style="padding: 10px 14px; width: 40%;">Note / Purpose</th>
                  <th style="padding: 10px 14px; width: 15%; text-align: right; min-width: 120px; white-space: nowrap;">Actions</th>
                </tr>
              </thead>
              <tbody id="walled-table-body"></tbody>
            </table>
          </div>
        </div>
      </div>
    </div>

    <!-- 10. MAC FILTERING SECTION WITH VALIDATIONS & EDIT HANDLERS -->
    <div id="sec-security" class="section-view">
      <div class="card card-danger" id="mac-form-card">
        <div class="card-header"><h3 class="card-title" id="mac-form-title"><i class="fas fa-shield-alt"></i> Add / Edit MAC Filter Rule</h3></div>
        <div class="card-body">
          <div class="row mb-2">
            <div class="col-12">
              <label class="text-info"><i class="fas fa-bolt"></i> Quick-Fill from Connected Devices:</label>
              <select id="mac-quick-pick" class="form-control form-control-sm" onchange="quickFillMac(this.value)">
                <option value="">-- Choose active connected device to auto-fill --</option>
              </select>
            </div>
          </div>
          
          <input type="hidden" id="edit-original-mac" value="">
          <div class="row">
            <div class="col-12 col-sm-6 col-md-4 form-group">
              <label>MAC Address (12 Hex Characters):</label>
              <input type="text" id="mac-input" class="form-control" placeholder="AA:BB:CC:DD:EE:FF" maxlength="17" oninput="formatMacInput(this)">
              <div id="mac-valid-msg" class="valid-feedback-custom">✓ Valid MAC format</div>
              <div id="mac-invalid-msg" class="invalid-feedback-custom">✗ Invalid MAC address format (e.g. AA:BB:CC:DD:EE:FF)</div>
            </div>
            <div class="col-12 col-sm-6 col-md-3 form-group">
              <label>Rule Type:</label>
              <select id="mac-type" class="form-control">
                <option value="whitelist">🟢 VIP Whitelist (Permanent Free Access)</option>
                <option value="blacklist">🔴 Blacklist (Block / Ban Device)</option>
              </select>
            </div>
            <div class="col-12 col-sm-6 col-md-3 form-group">
              <label>Device Owner / Label:</label>
              <input type="text" id="mac-note" class="form-control" placeholder="e.g. Owner Phone or Abusive User">
            </div>
            <div class="col-12 col-sm-6 col-md-2 form-group">
              <label class="d-none d-md-block">&nbsp;</label>
              <div class="d-flex">
                <button class="btn btn-danger btn-block mr-1 font-weight-bold" id="btn-save-mac" onclick="saveMacControl()"><i class="fas fa-plus mr-1"></i> Add Rule</button>
                <button class="btn btn-secondary font-weight-bold" id="btn-cancel-mac" style="display:none;" onclick="cancelEditMac()" title="Cancel Edit"><i class="fas fa-times"></i></button>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div class="card card-dark mt-3">
        <div class="card-header"><h3 class="card-title"><i class="fas fa-list-ul"></i> Active MAC Control Table</h3></div>
        <div class="card-body p-0">
          <div class="table-responsive">
            <table class="table table-striped table-hover mb-0" id="mac-table">
              <thead>
                <tr>
                  <th style="padding: 10px 14px; width: 30%;">MAC Address</th>
                  <th style="padding: 10px 14px; width: 20%; text-align: center;">Rule Type</th>
                  <th style="padding: 10px 14px; width: 30%;">Device Note</th>
                  <th style="padding: 10px 14px; width: 20%; text-align: right; min-width: 170px; white-space: nowrap;">Actions</th>
                </tr>
              </thead>
              <tbody id="mac-table-body"></tbody>
            </table>
          </div>
        </div>
      </div>
    </div>

    <!-- 11. TELEGRAM NOTIFICATIONS SECTION -->
    <div id="sec-telegram" class="section-view">
      <div class="card card-primary">
        <div class="card-header"><h3 class="card-title"><i class="fab fa-telegram-plane"></i> Telegram Bot Automated Alerts</h3></div>
        <div class="card-body">
          
          <div class="p-3 mb-4 rounded" style="background: rgba(255, 255, 255, 0.03); border: 1px solid rgba(255, 255, 255, 0.1); color: #cbd5e1; font-size: 0.9rem; line-height: 1.5;">
            <h5 class="font-weight-bold mb-3" style="font-size: 1.05rem; color: #38bdf8;"><i class="fas fa-info-circle mr-1"></i> How to setup Telegram Alerts</h5>
            <p class="mb-2 text-white">Follow these exact steps to connect ECO-Fi to your Telegram account:</p>
            <ol class="mb-0 pl-3">
              <li class="mb-2">Open Telegram and search for <strong class="text-white">@BotFather</strong>. Send it <code style="color: #60a5fa; background: rgba(0,0,0,0.3); padding: 2px 6px; border-radius: 4px;">/newbot</code> and follow the prompts to create your bot.</li>
              <li class="mb-2">BotFather will give you a <strong class="text-white">Bot Token</strong> (e.g., <code style="color: #60a5fa; background: rgba(0,0,0,0.3); padding: 2px 6px; border-radius: 4px;">123456789:ABCdefGh...</code>). Paste it into the Bot Token field below.</li>
              <li class="mb-2">Next, search for <strong class="text-white">@userinfobot</strong> in Telegram and send it <code style="color: #60a5fa; background: rgba(0,0,0,0.3); padding: 2px 6px; border-radius: 4px;">/start</code>. It will reply with your <strong class="text-white">Chat ID</strong> (e.g., <code style="color: #60a5fa; background: rgba(0,0,0,0.3); padding: 2px 6px; border-radius: 4px;">123456789</code>). Paste it into the Chat ID field below.</li>
              <li class="mb-2"><strong class="text-warning">CRITICAL STEP:</strong> Telegram blocks bots from messaging users to prevent spam. You MUST search for your new bot's username in Telegram and click <strong class="text-white">Start</strong> (or send <code style="color: #60a5fa; background: rgba(0,0,0,0.3); padding: 2px 6px; border-radius: 4px;">/start</code>) to authorize it to message you.</li>
              <li>Once you have started the chat with your bot, click <strong class="text-white">Save Settings</strong> below, and then click <strong class="text-white">Test Message</strong>.</li>
            </ol>
          </div>

          <div class="form-group mt-4">
            <label>Telegram Bot Token:</label>
            <input type="text" id="cfg-tg-token" class="form-control" placeholder="123456789:ABCdefGhIJKlmNoPQRstuVWXyz" value="{{ config.telegram_bot_token }}">
          </div>
          <div class="form-group">
            <label>Admin Telegram Chat ID:</label>
            <input type="text" id="cfg-tg-chat" class="form-control" placeholder="123456789" value="{{ config.telegram_chat_id }}">
          </div>
          <div class="row">
            <div class="col-12 col-md-6 form-group">
              <label>Alert when Storage Bin reaches 100%:</label>
              <select id="cfg-tg-bin" class="form-control">
                <option value="1" {% if config.telegram_alert_bin == '1' %}selected{% endif %}>🟢 ENABLED</option>
                <option value="0" {% if config.telegram_alert_bin == '0' %}selected{% endif %}>🔴 DISABLED</option>
              </select>
            </div>
            <div class="col-12 col-md-6 form-group">
              <label>Daily Midnight Revenue & Bottle Summary:</label>
              <select id="cfg-tg-daily" class="form-control">
                <option value="1" {% if config.telegram_alert_daily == '1' %}selected{% endif %}>🟢 ENABLED</option>
                <option value="0" {% if config.telegram_alert_daily == '0' %}selected{% endif %}>🔴 DISABLED</option>
              </select>
            </div>
          </div>
          <div class="d-flex flex-wrap gap-2">
            <button class="btn btn-primary mr-2 mb-2" onclick="saveTelegram()"><i class="fas fa-save"></i> Save Settings</button>
            <button class="btn btn-outline-info mb-2" onclick="testTelegram()"><i class="fas fa-paper-plane"></i> Test Message</button>
          </div>
        </div>
      </div>
    </div>

    <!-- 12. LICENSING & ACTIVATION SECTION -->
    <div id="sec-licensing" class="section-view">
      <div class="card card-info">
        <div class="card-header"><h3 class="card-title"><i class="fas fa-key"></i> Machine Hardware Licensing & Sovereign Authorization</h3></div>
        <div class="card-body">
          <p><strong>Machine Hardware ID (HWID):</strong> <br class="d-block d-sm-none"><code id="lic-hwid" class="text-warning font-weight-bold" style="font-size:15px; word-break:break-all;">Loading...</code></p>
          <p><strong>License Status:</strong> <span id="lic-status" class="badge badge-success" style="font-size:13px;">CHECKING</span></p>
          <p><strong>License Tier:</strong> <span id="lic-tier" class="badge badge-info">COMMERCIAL</span></p>
          <hr>
          <h5>Offline Machine Activation:</h5>
          <p class="text-muted" style="font-size:13px;">Enter the 16-character offline activation PIN provided by your vendor to authorize this station:</p>
          <div class="input-group mb-3">
            <input type="text" id="act-pin" class="form-control" placeholder="16-Char PIN (XXXX-XXXX-XXXX-XXXX)">
            <div class="input-group-append">
              <button class="btn btn-info" onclick="activateLicense()"><i class="fas fa-check"></i> Activate</button>
            </div>
          </div>
        </div>
      </div>
    </div>

  </div>
</div>

<!-- ========================================================================= -->
<!-- COMPLETE BOOTSTRAP / ADMINLTE MODAL SUITE                                 -->
<!-- ========================================================================= -->

<!-- 1. EDIT CLIENT MODAL -->
<div class="modal fade" id="modal-edit-client" tabindex="-1" role="dialog">
  <div class="modal-dialog modal-dialog-centered" role="document">
    <div class="modal-content">
      <div class="modal-header bg-primary">
        <h5 class="modal-title"><i class="fas fa-user-edit"></i> Edit Client Session</h5>
        <button type="button" class="close text-white" data-dismiss="modal"><span>&times;</span></button>
      </div>
      <div class="modal-body">
        <input type="hidden" id="modal-client-ip">
        <div class="form-group">
          <label>Client IP / MAC:</label>
          <input type="text" id="modal-client-info" class="form-control" readonly>
        </div>
        <div class="form-group">
          <label>Remaining Time (Minutes):</label>
          <input type="number" id="modal-client-mins" class="form-control" min="0">
        </div>
        <div class="row">
          <div class="col-12 col-md-6 form-group">
            <label>Download Limit (Kbps):</label>
            <input type="number" id="modal-client-dl" class="form-control" min="128">
          </div>
          <div class="col-12 col-md-6 form-group">
            <label>Upload Limit (Kbps):</label>
            <input type="number" id="modal-client-ul" class="form-control" min="64">
          </div>
        </div>
      </div>
      <div class="modal-footer">
        <button type="button" class="btn btn-secondary" data-dismiss="modal">Cancel</button>
        <button type="button" class="btn btn-primary" onclick="submitEditClientModal()"><i class="fas fa-save"></i> Save Changes</button>
      </div>
    </div>
  </div>
</div>

<!-- 2. MEMBER TOP-UP / PIN MODAL -->
<div class="modal fade" id="modal-member-topup" tabindex="-1" role="dialog">
  <div class="modal-dialog modal-dialog-centered" role="document">
    <div class="modal-content">
      <div class="modal-header bg-info">
        <h5 class="modal-title"><i class="fas fa-coins"></i> Manage Member Wallet</h5>
        <button type="button" class="close text-white" data-dismiss="modal"><span>&times;</span></button>
      </div>
      <div class="modal-body">
        <input type="hidden" id="modal-member-user">
        <div class="form-group">
          <label>Member Username:</label>
          <input type="text" id="modal-member-user-display" class="form-control" readonly>
        </div>
        <div class="form-group">
          <label>Adjust Minutes (+ to Add, - to Deduct):</label>
          <input type="number" id="modal-member-adj-mins" class="form-control" value="30">
        </div>
      </div>
      <div class="modal-footer">
        <button type="button" class="btn btn-secondary" data-dismiss="modal">Cancel</button>
        <button type="button" class="btn btn-info" onclick="submitMemberTopupModal()"><i class="fas fa-check"></i> Apply Adjustment</button>
      </div>
    </div>
  </div>
</div>

<script>
function showSection(secId) {
    document.querySelectorAll('.section-view').forEach(el => el.classList.remove('active'));
    document.querySelectorAll('.nav-sidebar .nav-link').forEach(el => el.classList.remove('active'));
    
    document.getElementById(secId).classList.add('active');
    const navLink = document.getElementById(secId.replace('sec-', 'nav-'));
    if (navLink) navLink.classList.add('active');

    // Auto-close sidebar on mobile devices when section link is clicked
    if ($(window).width() < 992) {
        $('body').removeClass('sidebar-open').addClass('sidebar-collapse');
    }

    if (secId === 'sec-clients') loadClients();
    if (secId === 'sec-vouchers') loadVouchers();
    if (secId === 'sec-members') loadMembers();
    if (secId === 'sec-rates') loadRates();
    if (secId === 'sec-walled') loadWalledGarden();
    if (secId === 'sec-security') { loadMacs(); populateMacQuickPick(); }
}

function refreshStats() {
    fetch('/admin/api/stats').then(r=>r.json()).then(d=>{
        document.getElementById('stat-today').innerText = d.today_bottles;
        document.getElementById('stat-total').innerText = d.total_bottles;
        document.getElementById('stat-clients').innerText = d.active_clients;

        if(document.getElementById('sys-cpu')) document.getElementById('sys-cpu').innerText = d.cpu + '%';
        if(document.getElementById('sys-cpu-bar')) document.getElementById('sys-cpu-bar').style.width = d.cpu + '%';
        if(document.getElementById('sys-ram')) document.getElementById('sys-ram').innerText = d.ram + '%';
        if(document.getElementById('sys-ram-bar')) document.getElementById('sys-ram-bar').style.width = d.ram + '%';
        if(document.getElementById('sys-disk')) document.getElementById('sys-disk').innerText = d.disk + '%';
        if(document.getElementById('sys-disk-bar')) document.getElementById('sys-disk-bar').style.width = d.disk + '%';
        if(document.getElementById('sys-uptime')) document.getElementById('sys-uptime').innerText = d.uptime;

        const labels = d.history.map(h => h.date);
        const data = d.history.map(h => h.count);

        if (!window.historyChart) {
            const ctx = document.getElementById('historyChart').getContext('2d');
            window.historyChart = new Chart(ctx, {
                type: 'bar',
                data: {
                    labels: labels,
                    datasets: [{
                        label: 'Bottles Recycled',
                        data: data,
                        backgroundColor: 'rgba(16, 185, 129, 0.8)',
                        borderColor: 'rgba(16, 185, 129, 1)',
                        borderWidth: 1,
                        borderRadius: 4
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    scales: {
                        y: { beginAtZero: true, ticks: { precision: 0, color: '#94a3b8' }, grid: { color: 'rgba(255,255,255,0.1)' } },
                        x: { ticks: { color: '#94a3b8' }, grid: { display: false } }
                    },
                    plugins: {
                        legend: { display: false }
                    }
                }
            });
        } else {
            window.historyChart.data.labels = labels;
            window.historyChart.data.datasets[0].data = data;
            window.historyChart.update('none');
        }
    });

    fetch('/admin/api/license').then(r=>r.json()).then(d=>{
        document.getElementById('lic-hwid').innerText = d.hwid;
        document.getElementById('lic-status').innerText = d.status;
        document.getElementById('lic-tier').innerText = d.tier || 'COMMERCIAL';
        document.getElementById('stat-lic').innerText = d.status;
    });
}
setInterval(refreshStats, 3000);
refreshStats();

// MAC Address Validation & Auto-Formatting
function formatMacInput(input) {
    let v = input.value.toUpperCase().replace(/[^0-9A-F]/g, '');
    let formatted = '';
    for (let i = 0; i < v.length && i < 12; i += 2) {
        if (i > 0) formatted += ':';
        formatted += v.substr(i, 2);
    }
    input.value = formatted;
    
    const isValid = /^([0-9A-F]{2}:){5}[0-9A-F]{2}$/.test(formatted);
    document.getElementById('mac-valid-msg').style.display = isValid ? 'block' : 'none';
    document.getElementById('mac-invalid-msg').style.display = (formatted.length > 0 && !isValid) ? 'block' : 'none';
}

function populateMacQuickPick() {
    fetch('/admin/api/clients').then(r=>r.json()).then(clients=>{
        let optHtml = '<option value="">-- Choose active connected device to auto-fill --</option>';
        clients.forEach(c=>{
            optHtml += `<option value="${c.mac}">${c.ip} (${c.mac})</option>`;
        });
        document.getElementById('mac-quick-pick').innerHTML = optHtml;
    });
}

function quickFillMac(val) {
    if (!val) return;
    document.getElementById('mac-input').value = val;
    formatMacInput(document.getElementById('mac-input'));
}

function loadMacs() {
    fetch('/admin/api/mac_control/list').then(r=>r.json()).then(d=>{
        let html = '';
        d.forEach(m=>{
            const safeNote = encodeURIComponent(m.note);
            html += `<tr>
                <td style="padding: 10px 14px;"><code>${m.mac}</code></td>
                <td style="padding: 10px 14px; text-align: center;"><span class="badge ${m.type==='whitelist'?'badge-success':'badge-danger'}">${m.type.toUpperCase()}</span></td>
                <td style="padding: 10px 14px;"><span class="text-light">${m.note || '-'}</span></td>
                <td style="padding: 10px 14px; text-align: right; white-space: nowrap;">
                    <div class="d-inline-flex align-items-center justify-content-end" style="gap: 6px; white-space: nowrap; flex-wrap: nowrap;">
                        <button class="btn btn-xs btn-outline-warning text-nowrap" onclick="editMacControl('${m.mac}', '${m.type}', '${safeNote}')"><i class="fas fa-edit mr-1"></i>Edit</button>
                        <button class="btn btn-xs btn-outline-danger text-nowrap" onclick="deleteMacControl('${m.mac}')"><i class="fas fa-trash mr-1"></i>Delete</button>
                    </div>
                </td>
            </tr>`;
        });
        document.getElementById('mac-table-body').innerHTML = html || '<tr><td colspan="4" class="text-center p-3 text-muted">No MAC filtering rules set.</td></tr>';
    });
}

function saveMacControl() {
    const mac = document.getElementById('mac-input').value.trim().toUpperCase();
    const type = document.getElementById('mac-type').value;
    const note = document.getElementById('mac-note').value.trim();
    const origMac = document.getElementById('edit-original-mac').value;

    if (!/^([0-9A-F]{2}:){5}[0-9A-F]{2}$/.test(mac)) {
        Swal.fire('Invalid MAC', 'MAC Address must be in 12 hex format: AA:BB:CC:DD:EE:FF', 'warning');
        return;
    }

    if (origMac && origMac !== mac) {
        fetch('/admin/api/mac_control/delete', {
            method: 'POST',
            headers: {'Content-Type':'application/json'},
            body: JSON.stringify({mac: origMac})
        });
    }

    fetch('/admin/api/mac_control/add', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({mac: mac, type: type, note: note})
    }).then(r=>r.json()).then(d=>{
        if (d.success) {
            cancelEditMac();
            loadMacs();
            Swal.fire('Saved!', 'MAC Rule has been saved.', 'success');
        } else {
            Swal.fire('Error', d.error || 'Failed to save MAC rule.', 'error');
        }
    });
}

function editMacControl(mac, type, encNote) {
    const note = decodeURIComponent(encNote);
    document.getElementById('edit-original-mac').value = mac;
    document.getElementById('mac-input').value = mac;
    document.getElementById('mac-type').value = type;
    document.getElementById('mac-note').value = note;
    
    document.getElementById('mac-form-title').innerHTML = `<i class="fas fa-edit text-warning"></i> Edit MAC Filter (${mac})`;
    document.getElementById('btn-save-mac').innerHTML = `<i class="fas fa-save"></i> Update Rule`;
    document.getElementById('btn-save-mac').className = `btn btn-warning btn-block mr-1`;
    document.getElementById('btn-cancel-mac').style.display = 'inline-block';
    
    formatMacInput(document.getElementById('mac-input'));
    document.getElementById('mac-form-card').scrollIntoView({ behavior: 'smooth' });
}

function cancelEditMac() {
    document.getElementById('edit-original-mac').value = '';
    document.getElementById('mac-input').value = '';
    document.getElementById('mac-note').value = '';
    document.getElementById('mac-quick-pick').value = '';
    
    document.getElementById('mac-form-title').innerHTML = `<i class="fas fa-shield-alt"></i> Add / Edit MAC Filter Rule`;
    document.getElementById('btn-save-mac').innerHTML = `<i class="fas fa-plus"></i> Add Rule`;
    document.getElementById('btn-save-mac').className = `btn btn-danger btn-block mr-1`;
    document.getElementById('btn-cancel-mac').style.display = 'none';
    formatMacInput(document.getElementById('mac-input'));
}

function deleteMacControl(mac) {
    Swal.fire({
        title: `Delete MAC Rule?`,
        text: `Are you sure you want to remove rule for ${mac}?`,
        icon: 'warning',
        showCancelButton: true,
        confirmButtonColor: '#d33',
        confirmButtonText: 'Yes, delete it!'
    }).then((res) => {
        if (res.isConfirmed) {
            fetch('/admin/api/mac_control/delete', {
                method: 'POST',
                headers: {'Content-Type':'application/json'},
                body: JSON.stringify({mac: mac})
            }).then(()=>{
                loadMacs();
                Swal.fire('Deleted!', 'MAC rule deleted.', 'success');
            });
        }
    });
}

// Promo Rates Management with Real-Time Mathematical Conflict Prevention
let currentRatesCache = [];

function loadRates() {
    fetch('/admin/api/rates/list').then(r=>r.json()).then(d=>{
        currentRatesCache = d || [];
        let html = '';
        currentRatesCache.forEach(r=>{
            let timeStr = '';
            if (r.minutes >= 60) {
                const hrs = (r.minutes / 60).toFixed(1);
                timeStr = `${hrs} Hours (${r.minutes} mins)`;
            } else {
                timeStr = `${r.minutes} Minutes`;
            }
            const eff = (r.minutes / r.bottles).toFixed(1);
            const baseRate = parseInt(document.getElementById('rate-1').value) || 10;
            const bonusPct = Math.round(((eff - baseRate) / baseRate) * 100);
            const bonusTag = bonusPct > 0 ? `<span class="badge badge-success ml-1">+${bonusPct}% Bonus</span>` : `<span class="badge badge-secondary ml-1">Base</span>`;
            const safeLabel = encodeURIComponent(r.label || '');
            html += `<tr>
                <td style="padding: 10px 14px;"><strong class="text-success"><i class="fas fa-wine-bottle mr-1"></i>${r.bottles} Bottle${r.bottles > 1 ? 's' : ''}</strong></td>
                <td style="padding: 10px 14px;"><strong>${timeStr}</strong></td>
                <td style="padding: 10px 14px;"><code>${eff} m/b</code> ${bonusTag}</td>
                <td style="padding: 10px 14px;"><span class="text-light">${r.label || '-'}</span></td>
                <td style="padding: 10px 14px; text-align: right; white-space: nowrap;">
                    <div class="d-inline-flex align-items-center justify-content-end" style="gap: 6px; white-space: nowrap; flex-wrap: nowrap;">
                        <button class="btn btn-xs btn-outline-warning text-nowrap" onclick="editPromoRate(${r.bottles}, ${r.minutes}, '${safeLabel}')"><i class="fas fa-edit mr-1"></i>Edit</button>
                        ${r.bottles > 1 ? `<button class="btn btn-xs btn-outline-danger text-nowrap" onclick="deletePromoRate(${r.bottles})"><i class="fas fa-trash mr-1"></i>Delete</button>` : `<span class="text-muted small ml-1 text-nowrap">(Base)</span>`}
                    </div>
                </td>
            </tr>`;
        });
        document.getElementById('rates-table-body').innerHTML = html || '<tr><td colspan="5" class="text-center p-3 text-muted">No promo rates configured.</td></tr>';
    });
}

function setRateBottles(n) {
    document.getElementById('new-rate-bottles').value = n;
    validatePromoFormMath();
    autoGenerateRateLabel();
}

function getSelectedTotalMinutes() {
    const rawVal = parseFloat(document.getElementById('new-rate-time-val').value) || 0;
    const unit = document.getElementById('new-rate-time-unit').value;
    if (unit === 'hours') return Math.round(rawVal * 60);
    if (unit === 'days') return Math.round(rawVal * 1440);
    return Math.round(rawVal);
}

function onBaseRateInput() {
    validatePromoFormMath();
    loadRates();
}

function autoGenerateRateLabel() {
    const b = parseInt(document.getElementById('new-rate-bottles').value) || 0;
    const m = getSelectedTotalMinutes();
    if (!b || !m) return;
    let timeStr = '';
    if (m >= 60) {
        const h = Math.floor(m / 60);
        const remM = m % 60;
        timeStr = (remM === 0) ? `${h} Hour${h > 1 ? 's' : ''}` : `${h}h ${remM}m`;
    } else {
        timeStr = `${m} mins`;
    }
    const label = `${b} Bottle${b > 1 ? 's' : ''} = ${timeStr}`;
    document.getElementById('new-rate-label').value = label;
}

function validatePromoFormMath() {
    const b = parseInt(document.getElementById('new-rate-bottles').value) || 0;
    const m = getSelectedTotalMinutes();
    const origB = parseInt(document.getElementById('edit-original-bottles').value) || null;
    const fb = document.getElementById('rate-validator-feedback');
    const effSpan = document.getElementById('rate-validator-eff');
    const statusSpan = document.getElementById('rate-validator-status');
    const msgDiv = document.getElementById('rate-validator-msg');
    const saveBtn = document.getElementById('btn-save-promo');

    if (!b || !m) {
        fb.style.display = 'none';
        saveBtn.disabled = false;
        return;
    }

    fb.style.display = 'block';
    const eff = (m / b).toFixed(2);
    effSpan.innerText = `📊 Efficiency: ${eff} mins/bottle (${m}m for ${b}B)`;

    // Check invariants against currentRatesCache (excluding editing tier)
    const existing = currentRatesCache.filter(r => r.bottles !== origB);
    let conflict = null;

    // Invariant 1: Monotonic Efficiency
    for (let r of existing) {
        const exEff = r.minutes / r.bottles;
        if (r.bottles < b && exEff > (m / b)) {
            conflict = `Efficiency conflict: ${r.bottles}B tier gives ${exEff.toFixed(1)} m/b, but this gives only ${eff} m/b. Larger bundles must be at least as rewarding.`;
            break;
        }
        if (r.bottles > b && exEff < (m / b)) {
            conflict = `Efficiency conflict: this tier gives ${eff} m/b, which exceeds the larger ${r.bottles}B tier (${exEff.toFixed(1)} m/b).`;
            break;
        }
    }

    // Invariant 2: Combination Floor
    if (!conflict) {
        const lowerTiers = existing.filter(r => r.bottles < b).sort((a,b) => b.bottles - a.bottles);
        let comboMins = 0;
        let rem = b;
        for (let lt of lowerTiers) {
            if (rem >= lt.bottles) {
                comboMins += Math.floor(rem / lt.bottles) * lt.minutes;
                rem %= lt.bottles;
            }
        }
        if (comboMins > 0 && m < comboMins) {
            conflict = `Combination conflict: Depositing ${b} bottles in smaller packages yields ${comboMins} mins, but this package gives only ${m} mins. Minimum required is ${comboMins} mins.`;
        }
    }

    // Invariant 3: Higher-Tier Upper Bound
    if (!conflict) {
        const higherTiers = existing.filter(r => r.bottles > b);
        if (higherTiers.length > 0) {
            const minHigher = Math.min(...higherTiers.map(r => r.minutes));
            if (m >= minHigher) {
                conflict = `Upper bound conflict: ${m} mins equals or exceeds a larger tier (${minHigher} mins).`;
            }
        }
    }

    if (conflict) {
        fb.className = 'alert alert-danger py-2 px-3 mb-0';
        statusSpan.innerText = '❌ Conflict Detected';
        msgDiv.innerText = conflict;
        saveBtn.disabled = true;
    } else {
        fb.className = 'alert alert-success py-2 px-3 mb-0';
        statusSpan.innerText = '✔ Mathematically Balanced';
        msgDiv.innerText = 'No rate curve conflicts. Bundle incentivizes bulk deposit.';
        saveBtn.disabled = false;
    }
}

function editPromoRate(bottles, minutes, encLabel) {
    const label = decodeURIComponent(encLabel || '');
    document.getElementById('edit-original-bottles').value = bottles;
    document.getElementById('new-rate-bottles').value = bottles;
    document.getElementById('new-rate-time-val').value = minutes;
    document.getElementById('new-rate-time-unit').value = 'mins';
    document.getElementById('new-rate-label').value = label;

    document.getElementById('promo-form-title').innerHTML = `<i class="fas fa-edit text-warning"></i> Edit Promo Rate Tier (${bottles} Bottles)`;
    document.getElementById('btn-save-promo').innerHTML = `<i class="fas fa-save"></i> Update Rate`;
    document.getElementById('btn-save-promo').className = 'btn btn-warning btn-block mr-1';
    document.getElementById('btn-cancel-promo').style.display = 'inline-block';

    validatePromoFormMath();
    document.getElementById('promo-form-card').scrollIntoView({ behavior: 'smooth' });
}

function cancelEditPromoRate() {
    document.getElementById('edit-original-bottles').value = '';
    document.getElementById('new-rate-bottles').value = '';
    document.getElementById('new-rate-time-val').value = '';
    document.getElementById('new-rate-time-unit').value = 'mins';
    document.getElementById('new-rate-label').value = '';
    document.getElementById('rate-validator-feedback').style.display = 'none';

    document.getElementById('promo-form-title').innerHTML = `<i class="fas fa-plus-circle"></i> Add Custom Promo Rate Package`;
    document.getElementById('btn-save-promo').innerHTML = `<i class="fas fa-plus"></i> Add Rate`;
    document.getElementById('btn-save-promo').className = 'btn btn-success btn-block mr-1';
    document.getElementById('btn-save-promo').disabled = false;
    document.getElementById('btn-cancel-promo').style.display = 'none';
}

function addPromoRate() {
    const b = parseInt(document.getElementById('new-rate-bottles').value);
    const m = getSelectedTotalMinutes();
    const origB = document.getElementById('edit-original-bottles').value;
    let l = document.getElementById('new-rate-label').value.trim();

    if (!b || !m || isNaN(b) || isNaN(m)) {
        Swal.fire('Input Error', 'Please enter valid numbers for Bottles and Duration.', 'warning');
        return;
    }

    if (!l) {
        autoGenerateRateLabel();
        l = document.getElementById('new-rate-label').value.trim();
    }

    fetch('/admin/api/rates/add', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({bottles: b, minutes: m, label: l, orig_bottles: origB ? parseInt(origB) : null})
    }).then(r=>r.json()).then(d=>{
        if (d.success) {
            cancelEditPromoRate();
            loadRates();
            Swal.fire('Saved!', 'Promo rate tier saved successfully.', 'success');
        } else {
            Swal.fire('Conflict Error', d.error || 'Failed to save rate.', 'error');
        }
    }).catch(e=>{
        Swal.fire('Error', 'Server error while saving promo rate.', 'error');
    });
}

function deletePromoRate(b) {
    Swal.fire({
        title: `Delete Rate Tier?`,
        text: `Delete package for ${b} bottle(s)?`,
        icon: 'warning',
        showCancelButton: true,
        confirmButtonColor: '#d33',
        confirmButtonText: 'Yes, delete it!'
    }).then((res) => {
        if (res.isConfirmed) {
            fetch('/admin/api/rates/delete', {
                method: 'POST',
                headers: {'Content-Type':'application/json'},
                body: JSON.stringify({bottles: b})
            }).then(r=>r.json()).then(d=>{
                if (d.success) {
                    loadRates();
                    Swal.fire('Deleted!', 'Rate tier removed.', 'success');
                } else {
                    Swal.fire('Error', d.error || 'Could not delete rate.', 'error');
                }
            });
        }
    });
}

function applyRatePreset() {
    const p = document.getElementById('rate-preset-select').value;
    Swal.fire({
        title: 'Apply Rate Template?',
        text: 'This will replace all active promo rates with the selected conflict-free curve template.',
        icon: 'question',
        showCancelButton: true,
        confirmButtonColor: '#17a2b8',
        confirmButtonText: 'Yes, Apply Template'
    }).then(res => {
        if (res.isConfirmed) {
            fetch('/admin/api/rates/apply_preset', {
                method: 'POST',
                headers: {'Content-Type':'application/json'},
                body: JSON.stringify({preset: p})
            }).then(r=>r.json()).then(d=>{
                if (d.success) {
                    loadRates();
                    Swal.fire('Applied!', d.message, 'success');
                } else {
                    Swal.fire('Error', d.error || 'Could not apply template.', 'error');
                }
            });
        }
    });
}

function saveRates() {
    const minPerBottle = document.getElementById('rate-1').value;
    const dropTimeout = document.getElementById('rate-timeout').value;
    fetch('/admin/api/settings/save', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({minutes_per_bottle: minPerBottle, drop_timeout: dropTimeout})
    }).then(r=>r.json()).then(d=>{
        if (d.success) {
            loadRates();
            Swal.fire('Saved!', 'Base timing settings updated.', 'success');
        } else {
            Swal.fire('Error', d.error || 'Failed to save settings.', 'error');
        }
    });
}

// Clients & Client Modal
function loadClients() {
    fetch('/admin/api/clients').then(r=>r.json()).then(d=>{
        let html = '';
        d.forEach(c=>{
            const mins = Math.floor(c.remaining_seconds / 60);
            html += `<tr>
                <td style="padding: 10px 14px;"><strong>${c.ip}</strong></td>
                <td style="padding: 10px 14px;"><code>${c.mac}</code></td>
                <td style="padding: 10px 14px;"><strong>${mins}m</strong> <small class="text-muted">(${c.remaining_seconds}s)</small></td>
                <td style="padding: 10px 14px; text-align: center;"><span class="badge ${c.is_paused ? 'badge-warning' : 'badge-success'}">${c.is_paused ? 'PAUSED' : 'ACTIVE'}</span></td>
                <td style="padding: 10px 14px;"><span class="text-info font-weight-bold">${c.dl_kbps || 3072} / ${c.ul_kbps || 1536}</span> <small class="text-muted">Kbps</small></td>
                <td style="padding: 10px 14px; text-align: right; white-space: nowrap;">
                    <div class="d-inline-flex align-items-center justify-content-end" style="gap: 5px; white-space: nowrap; flex-wrap: nowrap;">
                        <button class="btn btn-xs btn-outline-success text-nowrap" onclick="clientAction('${c.ip}', 'add15')"><i class="fas fa-plus mr-1"></i>15m</button>
                        <button class="btn btn-xs btn-outline-warning text-nowrap" onclick="clientAction('${c.ip}', '${c.is_paused ? 'resume' : 'pause'}')">${c.is_paused ? '<i class="fas fa-play mr-1"></i>Resume' : '<i class="fas fa-pause mr-1"></i>Pause'}</button>
                        <button class="btn btn-xs btn-outline-info text-nowrap" onclick="openEditClientModal('${c.ip}', '${c.mac}', ${c.remaining_seconds}, ${c.dl_kbps || 3072}, ${c.ul_kbps || 1536})"><i class="fas fa-edit mr-1"></i>Edit</button>
                        <button class="btn btn-xs btn-outline-danger text-nowrap" onclick="clientAction('${c.ip}', 'kick')"><i class="fas fa-user-slash mr-1"></i>Kick</button>
                    </div>
                </td>
            </tr>`;
        });
        document.getElementById('clients-table-body').innerHTML = html || '<tr><td colspan="6" class="text-center p-3 text-muted">No active clients connected.</td></tr>';
    });
}

function openEditClientModal(ip, mac, remSeconds, dl, ul) {
    document.getElementById('modal-client-ip').value = ip;
    document.getElementById('modal-client-info').value = `${ip} (${mac})`;
    document.getElementById('modal-client-mins').value = Math.max(0, Math.floor((remSeconds || 0) / 60));
    document.getElementById('modal-client-dl').value = dl || 3072;
    document.getElementById('modal-client-ul').value = ul || 1536;
    $('#modal-edit-client').modal('show');
}

function submitEditClientModal() {
    const ip = document.getElementById('modal-client-ip').value;
    const mins = parseInt(document.getElementById('modal-client-mins').value) || 0;
    const dl = parseInt(document.getElementById('modal-client-dl').value) || 3072;
    const ul = parseInt(document.getElementById('modal-client-ul').value) || 1536;

    fetch('/admin/api/client/edit', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ip: ip, minutes: mins, dl_kbps: dl, ul_kbps: ul})
    }).then(r=>r.json()).then(d=>{
        $('#modal-edit-client').modal('hide');
        loadClients();
        Swal.fire('Updated!', 'Client session updated.', 'success');
    });
}

function clientAction(ip, act) {
    fetch('/admin/api/client/action', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ip: ip, action: act})
    }).then(()=>loadClients());
}

// Vouchers
function loadVouchers() {
    fetch('/admin/api/vouchers/list').then(r=>r.json()).then(d=>{
        let html = '';
        d.forEach(v=>{
            html += `<tr>
                <td style="padding: 10px 14px;"><strong class="text-success"><i class="fas fa-ticket-alt mr-1"></i>${v.code}</strong></td>
                <td style="padding: 10px 14px;"><strong>${v.minutes}m</strong></td>
                <td style="padding: 10px 14px; text-align: center;"><span class="badge ${v.is_used ? 'badge-secondary' : 'badge-success'}">${v.is_used ? 'REDEEMED' : 'ACTIVE'}</span></td>
                <td style="padding: 10px 14px;"><span class="text-light">${v.note || '-'}</span></td>
                <td style="padding: 10px 14px;"><small class="text-muted">${v.created_at}</small></td>
                <td style="padding: 10px 14px;"><code>${v.used_by || '-'}</code></td>
                <td style="padding: 10px 14px; text-align: right; white-space: nowrap;"><button class="btn btn-xs btn-outline-danger text-nowrap" onclick="deleteVoucher('${v.code}')"><i class="fas fa-trash mr-1"></i>Delete</button></td>
            </tr>`;
        });
        document.getElementById('voucher-history-body').innerHTML = html || '<tr><td colspan="7" class="text-center p-3 text-muted">No vouchers generated yet.</td></tr>';
    });
}

function generateVouchers() {
    const q = document.getElementById('v-qty').value;
    const m = document.getElementById('v-mins').value;
    const note = document.getElementById('v-note').value.trim();
    fetch('/admin/api/vouchers/generate', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({qty: q, minutes: m, note: note})
    }).then(r=>r.json()).then(d=>{
        let html = '<div class="alert alert-success"><h5>Generated Vouchers:</h5><ul>';
        d.vouchers.forEach(v=>{ html += `<li><strong>${v.code}</strong> (${v.minutes} Minutes) - ${v.note || ''}</li>`; });
        html += '</ul></div>';
        document.getElementById('v-results').innerHTML = html;
        loadVouchers();
        Swal.fire('Generated!', `${d.vouchers.length} vouchers generated.`, 'success');
    });
}

function deleteVoucher(code) {
    Swal.fire({
        title: 'Delete Voucher?',
        text: `Delete voucher code ${code}?`,
        icon: 'warning',
        showCancelButton: true,
        confirmButtonColor: '#d33',
        confirmButtonText: 'Yes, delete'
    }).then((res) => {
        if (res.isConfirmed) {
            fetch('/admin/api/vouchers/delete', {
                method: 'POST',
                headers: {'Content-Type':'application/json'},
                body: JSON.stringify({code: code})
            }).then(()=>{
                loadVouchers();
                Swal.fire('Deleted!', 'Voucher deleted.', 'success');
            });
        }
    });
}

// Members & Member Modals
function loadMembers() {
    fetch('/admin/api/members/list').then(r=>r.json()).then(d=>{
        let html = '';
        d.forEach(m=>{
            const hrs = (m.wallet_minutes / 60).toFixed(1);
            html += `<tr>
                <td style="padding: 10px 14px;"><strong class="text-info"><i class="fas fa-user-circle mr-1"></i>${m.username}</strong></td>
                <td style="padding: 10px 14px;"><span class="badge badge-info">${m.wallet_minutes} Mins (${hrs} Hrs)</span></td>
                <td style="padding: 10px 14px;"><small class="text-muted">${m.created_at}</small></td>
                <td style="padding: 10px 14px; text-align: right; white-space: nowrap;">
                    <div class="d-inline-flex align-items-center justify-content-end" style="gap: 6px; white-space: nowrap; flex-wrap: nowrap;">
                        <button class="btn btn-xs btn-outline-success text-nowrap" onclick="openMemberTopupModal('${m.username}')"><i class="fas fa-coins mr-1"></i>Adjust</button>
                        <button class="btn btn-xs btn-outline-danger text-nowrap" onclick="deleteMember('${m.username}')"><i class="fas fa-trash mr-1"></i>Delete</button>
                    </div>
                </td>
            </tr>`;
        });
        document.getElementById('members-table-body').innerHTML = html || '<tr><td colspan="4" class="text-center p-3 text-muted">No registered members yet.</td></tr>';
    });
}

function openMemberTopupModal(username) {
    document.getElementById('modal-member-user').value = username;
    document.getElementById('modal-member-user-display').value = username;
    document.getElementById('modal-member-adj-mins').value = '30';
    $('#modal-member-topup').modal('show');
}

function submitMemberTopupModal() {
    const u = document.getElementById('modal-member-user').value;
    const mins = parseInt(document.getElementById('modal-member-adj-mins').value) || 0;
    fetch('/admin/api/members/topup', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({username: u, minutes: mins})
    }).then(()=>{
        $('#modal-member-topup').modal('hide');
        loadMembers();
        Swal.fire('Adjusted!', `Wallet for ${u} updated.`, 'success');
    });
}

function addMember() {
    const u = document.getElementById('new-mem-user').value.trim();
    const p = document.getElementById('new-mem-pin').value.trim();
    const m = parseInt(document.getElementById('new-mem-mins').value) || 0;
    if (!u || !p) {
        Swal.fire('Required', 'Username and PIN are required.', 'warning');
        return;
    }
    fetch('/admin/api/members/add', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({username: u, pin: p, wallet_minutes: m})
    }).then(r=>r.json()).then(d=>{
        if (d.success) {
            document.getElementById('new-mem-user').value = '';
            document.getElementById('new-mem-pin').value = '';
            document.getElementById('new-mem-mins').value = '0';
            loadMembers();
            Swal.fire('Created!', 'Member account created successfully!', 'success');
        } else {
            Swal.fire('Error', d.error || 'Failed to create member.', 'error');
        }
    });
}

function deleteMember(u) {
    Swal.fire({
        title: 'Delete Member?',
        text: `Delete account ${u}?`,
        icon: 'warning',
        showCancelButton: true,
        confirmButtonColor: '#d33',
        confirmButtonText: 'Yes, delete'
    }).then((res) => {
        if (res.isConfirmed) {
            fetch('/admin/api/members/delete', {
                method: 'POST',
                headers: {'Content-Type':'application/json'},
                body: JSON.stringify({username: u})
            }).then(()=>{
                loadMembers();
                Swal.fire('Deleted!', 'Member account deleted.', 'success');
            });
        }
    });
}

// Walled Garden
function loadWalledGarden() {
    fetch('/admin/api/walled_garden/list').then(r=>r.json()).then(d=>{
        let html = '';
        d.forEach(w=>{
            html += `<tr>
                <td style="padding: 10px 14px;"><code class="text-success">${w.domain}</code></td>
                <td style="padding: 10px 14px;"><span class="text-light">${w.note || '-'}</span></td>
                <td style="padding: 10px 14px; text-align: right; white-space: nowrap;"><button class="btn btn-xs btn-outline-danger text-nowrap" onclick="deleteWalledDomain('${w.domain}')"><i class="fas fa-trash mr-1"></i>Delete</button></td>
            </tr>`;
        });
        document.getElementById('walled-table-body').innerHTML = html || '<tr><td colspan="3" class="text-center p-3 text-muted">No walled garden sites whitelisted.</td></tr>';
    });
}

function addWalledDomain() {
    const domain = document.getElementById('walled-domain').value.trim().toLowerCase();
    const note = document.getElementById('walled-note').value.trim();
    if (!domain) return;
    fetch('/admin/api/walled_garden/add', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({domain: domain, note: note})
    }).then(r=>r.json()).then(d=>{
        if (d.success) {
            document.getElementById('walled-domain').value = '';
            document.getElementById('walled-note').value = '';
            loadWalledGarden();
            Swal.fire('Whitelisted!', 'Domain whitelisted in Walled Garden.', 'success');
        } else {
            Swal.fire('Invalid Domain', d.error || 'Invalid domain format.', 'warning');
        }
    });
}

function deleteWalledDomain(domain) {
    Swal.fire({
        title: 'Remove Domain?',
        text: `Remove ${domain} from whitelist?`,
        icon: 'warning',
        showCancelButton: true,
        confirmButtonColor: '#d33',
        confirmButtonText: 'Yes, remove'
    }).then((res) => {
        if (res.isConfirmed) {
            fetch('/admin/api/walled_garden/delete', {
                method: 'POST',
                headers: {'Content-Type':'application/json'},
                body: JSON.stringify({domain: domain})
            }).then(()=>{
                loadWalledGarden();
                Swal.fire('Removed!', 'Domain removed.', 'success');
            });
        }
    });
}

function savePortalCustom() {
    const n = document.getElementById('cfg-vendo-name').value;
    const sub = document.getElementById('cfg-vendo-sub').value;
    const ann = document.getElementById('cfg-announcement').value;
    fetch('/admin/api/settings/save', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({vendo_name: n, vendo_subtitle: sub})
    }).then(()=>Swal.fire('Saved!', 'Portal branding updated.', 'success'));
}

function saveBandwidth() {
    const dl = document.getElementById('cfg-dl').value;
    const ul = document.getElementById('cfg-ul').value;
    const t = document.getElementById('cfg-tether').value;
    fetch('/admin/api/settings/save', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({default_dl_kbps: dl, default_ul_kbps: ul, anti_tethering: t})
    }).then(()=>Swal.fire('Saved!', 'Bandwidth & Anti-Tethering rules applied.', 'success'));
}

function saveTelegram() {
    const tok = document.getElementById('cfg-tg-token').value;
    const cid = document.getElementById('cfg-tg-chat').value;
    const bin = document.getElementById('cfg-tg-bin').value;
    const daily = document.getElementById('cfg-tg-daily').value;
    fetch('/admin/api/settings/save', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({
            telegram_bot_token: tok,
            telegram_chat_id: cid,
            telegram_alert_bin: bin,
            telegram_alert_daily: daily
        })
    }).then(()=>Swal.fire('Saved!', 'Telegram alerts configured.', 'success'));
}

function testTelegram() {
    fetch('/admin/api/telegram/test', {method:'POST'}).then(r=>r.json()).then(d=>{
        if (d.success) {
            Swal.fire('Sent!', 'Test alert sent successfully to Telegram!', 'success');
        } else {
            Swal.fire('Failed', 'Could not send alert. Please check your Bot Token and Chat ID.', 'error');
        }
    });
}

function onAudioPresetChange(type) {
    const sel = document.getElementById(`audio-${type}-preset`).value;
    const customInp = document.getElementById(`audio-${type}-custom`);
    const player = document.getElementById(`audio-player-${type}`);
    
    if (sel === 'silent') {
        customInp.value = 'silent';
        player.src = '';
    } else if (sel === 'custom') {
        player.src = customInp.value;
    } else if (sel === 'arcade_powerup' || sel === 'voice_filipino' || sel === 'crystal_bell') {
        customInp.value = sel;
        player.src = '';
    } else {
        customInp.value = sel;
        player.src = sel;
    }
}

function updateAudioPlayer(type) {
    const url = document.getElementById(`audio-${type}-custom`).value.trim();
    const player = document.getElementById(`audio-player-${type}`);
    if (url && url !== 'silent' && !['arcade_powerup', 'voice_filipino', 'crystal_bell'].includes(url)) {
        player.src = url;
    }
}

function uploadAudioFile(type) {
    const fileInp = document.getElementById(`upload-file-${type}`);
    if (!fileInp.files || fileInp.files.length === 0) return;
    
    const formData = new FormData();
    formData.append('file', fileInp.files[0]);
    
    Swal.fire({
        title: 'Uploading Audio...',
        text: 'Please wait while your custom audio file is uploaded.',
        allowOutsideClick: false,
        didOpen: () => { Swal.showLoading(); }
    });
    
    fetch('/admin/api/audio/upload', {
        method: 'POST',
        body: formData
    }).then(r=>r.json()).then(d=>{
        if (d.success) {
            document.getElementById(`audio-${type}-custom`).value = d.url;
            document.getElementById(`audio-${type}-preset`).value = 'custom';
            document.getElementById(`audio-player-${type}`).src = d.url;
            Swal.fire('Uploaded!', 'Custom audio file uploaded successfully!', 'success');
        } else {
            Swal.fire('Upload Failed', d.error || 'Could not upload audio.', 'error');
        }
    }).catch(err => {
        Swal.fire('Upload Error', 'Error communicating with server.', 'error');
    });
}

function saveAudioSettings() {
    const bg = document.getElementById('audio-bg-custom').value.trim() || '/static/audio/eco_loop.wav';
    const insert = document.getElementById('audio-insert-custom').value.trim() || '/static/audio/eco_chime.wav';
    const success = document.getElementById('audio-success-custom').value.trim() || '/static/audio/eco_success.wav';
    const vol = document.getElementById('audio-vol-input').value;
    
    fetch('/admin/api/audio/settings', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({audio_bg: bg, audio_insert: insert, audio_success: success, volume: vol})
    }).then(r=>r.json()).then(d=>{
        Swal.fire('Saved!', 'All portal audio settings saved successfully!', 'success');
    });
}

function updatePreviewVolume() {
    const vol = document.getElementById('audio-vol-input').value;
    document.getElementById('vol-lbl').innerText = vol + '%';
    const vDecimal = vol / 100.0;
    try {
        document.getElementById('audio-player-bg').volume = vDecimal;
        document.getElementById('audio-player-insert').volume = vDecimal;
        document.getElementById('audio-player-success').volume = vDecimal;
    } catch(e) {}
}

// Call on load to set initial volume of previews
window.addEventListener('DOMContentLoaded', () => {
    updatePreviewVolume();
});

function activateLicense() {
    const p = document.getElementById('act-pin').value.trim();
    fetch('/admin/api/license/activate', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({pin: p})
    }).then(r=>r.json()).then(d=>{
        Swal.fire('Activation', d.message, d.success ? 'success' : 'error');
        refreshStats();
    });
}
    function saveEsp32Config() {
        const payload = {
            bin_full_threshold_cm: parseInt($('#esp-bin').val()),
            entrance_gate_timeout: parseInt($('#esp-ent-tout').val()),
            settle_time_ms: parseInt($('#esp-settle').val()),
            success_drop_tout_ms: parseInt($('#esp-suc-time').val()),
            reject_drop_time_ms: parseInt($('#esp-rej-time').val()),
            pet_nir_w_min: parseInt($('#esp-nir-min').val()),
            pet_nir_w_max: parseInt($('#esp-nir-max').val()),
            ent_close_angle: parseInt($('#esp-ent-close').val()),
            ent_open_angle: parseInt($('#esp-ent-open').val()),
            suc_close_angle: parseInt($('#esp-suc-close').val()),
            suc_open_angle: parseInt($('#esp-suc-open').val()),
            rej_close_angle: parseInt($('#esp-rej-close').val()),
            rej_open_angle: parseInt($('#esp-rej-open').val())
        };
        fetch('/admin/api/esp32/save', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload)
        }).then(r=>r.json()).then(d=>{
            if(d.success) Swal.fire('Saved!', 'Config pushed to ESP32 memory. Servos snapped to closed positions.', 'success');
        });
    }
    
    function triggerEsp32Config() {
        if(confirm("This will reboot the ESP32 into Captive Portal mode and stop vending temporarily. Continue?")) {
            fetch('/admin/api/esp32/trigger', {method:'POST'}).then(()=>Swal.fire('Triggered', 'ESP32 is rebooting into Wi-Fi Config Mode.', 'info'));
        }
    }
</script>
</body>
</html>
"""

# Hardware Serial Daemon
SERIAL_PORTS = ["/dev/ttyUSB0", "/dev/ttyUSB1", "/dev/ttyS1", "/dev/ttyS0"]
BAUD_RATE = 115200

def hardware_serial_daemon():
    global ser
    while True:
        port = None
        for p in SERIAL_PORTS:
            if os.path.exists(p):
                port = p
                break
        if not port:
            time.sleep(3)
            continue
        try:
            with serial.Serial(port, BAUD_RATE, timeout=2) as s:
                ser = s
                while True:
                    raw_line = s.readline().decode('utf-8', errors='ignore').strip()
                    if not raw_line: continue
                    try:
                        data = json.loads(raw_line)
                        on_esp32_uart_output(raw_line)
                    except json.JSONDecodeError:
                        pass
        except Exception:
            ser = None
            time.sleep(3)

if __name__ == "__main__":
    restore_sessions_from_db()

    threading.Thread(target=time_daemon, daemon=True).start()
    if serial:
        threading.Thread(target=hardware_serial_daemon, daemon=True).start()
    port = int(os.environ.get("PORT", 5000))
    print(f"==================================================")
    print(f"  SMART ECO-FI REVERSE VENDING MACHINE")
    print(f"  Captive Portal : http://localhost:{port}/")
    print(f"  Simulator UI   : http://localhost:{port}/simulator")
    print(f"  Admin Panel    : http://localhost:{port}/admin")
    print(f"==================================================")
    app.run(host="0.0.0.0", port=port, debug=False)
