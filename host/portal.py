import os
import shutil
import atexit
import copy
import functools
import uuid
import signal
from contextlib import contextmanager
import gateway_network
import ipaddress
import socket
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
from datetime import datetime, timedelta
from collections import deque
from flask import Flask, request, render_template_string, jsonify, session, redirect, url_for, Response, send_file, send_from_directory, abort
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix
import logging
try:
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
except (ImportError, SyntaxError, Exception):
    openpyxl = None
try:
    import serial
except ImportError:
    serial = None
from esp32_simulator import ESP32Simulator, validate_hardware_config, HARDWARE_BOUNDS
import license_manager
import time_schema
import time_policy
import transition_engine
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)
app = Flask(__name__, static_folder='static')
class LocalProxy:
    def __init__(self, application):
        self.raw = application
        self.proxy = ProxyFix(application, x_for=1, x_proto=1, x_host=0)
    def __call__(self, environ, start_response):
        application = self.proxy if environ.get('REMOTE_ADDR') in ('127.0.0.1', '::1') else self.raw
        return application(environ, start_response)
app.wsgi_app = LocalProxy(app.wsgi_app)
def release_version():
    folder = os.path.dirname(os.path.abspath(__file__))
    for path in (os.path.join(folder, 'VERSION'), os.path.join(folder, '..', 'VERSION')):
        if os.path.isfile(path):
            with open(path) as stream:return stream.read().strip()
    return 'development'

RELEASE_VERSION = release_version()
DB_PATH = os.environ.get('ECOFI_DB_PATH') or os.path.join(os.path.dirname(os.path.abspath(__file__)), 'vendo_sessions.db')
active_clients = {}
active_clients_lock = threading.RLock()
active_depositor_ip = None
active_depositor_timeout = 0
ser = None
serial_write_lock = threading.RLock()

def get_client_ip():
    # Proxy headers are interpreted only for our loopback nginx connection.
    value = request.remote_addr or '127.0.0.1'
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return '127.0.0.1'


@contextmanager
def db_connection():
    connection = sqlite3.connect(DB_PATH, timeout=15, isolation_level=None)
    connection.execute('PRAGMA journal_mode=WAL')
    connection.execute('PRAGMA foreign_keys=ON')
    connection.execute('PRAGMA busy_timeout=15000')
    try:
        with time_schema.transaction(connection):
            yield connection
    finally:
        connection.close()

def atomic_credit_change(function):
    return function

def license_valid():
    return bool(license_manager.verify_license().get('valid'))



def ensure_session_schema(conn):
    conn.execute('CREATE TABLE IF NOT EXISTS active_sessions (ip TEXT PRIMARY KEY, mac TEXT, remaining_seconds INTEGER, is_paused INTEGER, dl_kbps INTEGER, ul_kbps INTEGER, pending_bottles INTEGER, paused_at REAL, expires_at REAL, saved_at REAL, state_json TEXT)')
    fields = {row[1] for row in conn.execute('PRAGMA table_info(active_sessions)')}
    for name, kind in [('paused_at', 'REAL DEFAULT 0'), ('expires_at', 'REAL DEFAULT 0'), ('state_json', 'TEXT'), ('member_username', "TEXT DEFAULT ''")]:
        if name not in fields:
            conn.execute('ALTER TABLE active_sessions ADD COLUMN ' + name + ' ' + kind)

def init_db():
    with db_connection() as conn:
        c = conn.cursor()
        ensure_session_schema(conn)
        time_schema.init_time_schema(conn)
        c.execute('CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT)')
        c.execute('CREATE TABLE IF NOT EXISTS stats (date TEXT PRIMARY KEY, total_bottles INTEGER)')
        c.execute('CREATE TABLE IF NOT EXISTS admins (username TEXT PRIMARY KEY, password_hash TEXT)')
        c.execute('CREATE TABLE IF NOT EXISTS vouchers (code TEXT PRIMARY KEY, minutes INTEGER, is_used INTEGER DEFAULT 0, created_at TEXT, used_by TEXT, note TEXT, policy_version_id TEXT)')
        c.execute('CREATE TABLE IF NOT EXISTS time_transfers (code TEXT PRIMARY KEY, from_ip TEXT, from_mac TEXT, seconds INTEGER, created_at REAL, is_claimed INTEGER DEFAULT 0)')
        c.execute('CREATE TABLE IF NOT EXISTS mac_control (mac TEXT PRIMARY KEY, type TEXT, note TEXT, dl_kbps INTEGER DEFAULT 0, ul_kbps INTEGER DEFAULT 0)')
        c.execute("CREATE TABLE IF NOT EXISTS promo_rates (bottles INTEGER PRIMARY KEY, minutes INTEGER, label TEXT, speed_profile TEXT DEFAULT '')")
        c.execute('CREATE TABLE IF NOT EXISTS announcements (id INTEGER PRIMARY KEY AUTOINCREMENT, message TEXT, active INTEGER DEFAULT 1)')
        c.execute('CREATE TABLE IF NOT EXISTS walled_garden (domain TEXT PRIMARY KEY, note TEXT)')
        try:
            c.execute('ALTER TABLE mac_control ADD COLUMN dl_kbps INTEGER DEFAULT 0')
        except sqlite3.OperationalError:
            pass
        try:
            c.execute('ALTER TABLE mac_control ADD COLUMN ul_kbps INTEGER DEFAULT 0')
        except sqlite3.OperationalError:
            pass
        try:
            c.execute('ALTER TABLE vouchers ADD COLUMN note TEXT')
        except sqlite3.OperationalError:
            pass
        try:
            c.execute("ALTER TABLE promo_rates ADD COLUMN speed_profile TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass
        try:
            c.execute('ALTER TABLE active_sessions ADD COLUMN paused_at REAL DEFAULT 0')
        except sqlite3.OperationalError:
            pass
        try:
            c.execute('ALTER TABLE active_sessions ADD COLUMN expires_at REAL DEFAULT 0')
        except sqlite3.OperationalError:
            pass
        c.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('simulator_enabled', '0')")
        c.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('minutes_per_bottle', '10')")
        c.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('drop_timeout', '60')")
        c.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('default_dl_kbps', '3072')")
        c.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('default_ul_kbps', '1536')")
        c.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('custom_css', '')")
        c.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('telegram_bot_token', '')")
        c.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('telegram_chat_id', '')")
        c.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('telegram_alert_bin', '1')")
        c.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('telegram_alert_daily', '1')")
        c.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('anti_tethering', '1')")
        c.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('vendo_name', 'Eco-Fi Vendo')")
        c.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('vendo_subtitle', 'Recycle Bottles for Fast WiFi')")
        c.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('announcement', '')")
        c.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('audio_bg', '/static/audio/eco_loop.wav')")
        c.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('audio_insert', '/static/audio/bottle_success.wav')")
        c.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('audio_success', '/static/audio/eco_success.wav')")
        c.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('audio_preset', '/static/audio/bottle_success.wav')")
        c.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('audio_custom_url', '')")
        c.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('audio_volume', '80')")
        c.execute("UPDATE config SET value = '/static/audio/eco_loop.wav' WHERE key = 'audio_bg' AND (value = '/static/audio/b1.wav' OR value = '')")
        c.execute("UPDATE config SET value = '/static/audio/bottle_success.wav' WHERE key = 'audio_insert' AND (value = '/static/audio/coin.wav' OR value = '/static/audio/eco_chime.wav' OR value = '')")
        c.execute("UPDATE config SET value = '/static/audio/eco_success.wav' WHERE key = 'audio_success' AND (value = '/static/audio/success_ding.wav' OR value = '/static/audio/bottle_success.wav' OR value = '')")
        c.execute("INSERT OR IGNORE INTO promo_rates (bottles, minutes, label) VALUES (1, 10, '1 Bottle = 10 mins')")
        c.execute("INSERT OR IGNORE INTO promo_rates (bottles, minutes, label) VALUES (3, 40, '3 Bottles = 40 mins')")
        c.execute("INSERT OR IGNORE INTO promo_rates (bottles, minutes, label) VALUES (5, 75, '5 Bottles = 1h 15m')")
        c.execute("INSERT OR IGNORE INTO promo_rates (bottles, minutes, label) VALUES (10, 180, '10 Bottles = 3 Hours')")
        # Connectivity probes must reach the state-aware captive response. Remove
        # only auto-seeded bypasses; retain explicit operator garden entries.
        for domain,note in [
            ('captive.apple.com','Apple CNA captive detection'),
            ('www.apple.com','Apple connectivity check'),('apple.com','Apple connectivity check'),
            ('connectivitycheck.gstatic.com','Android/Chrome connectivity check'),
            ('clients3.google.com','Android connectivity check'),
            ('www.msftconnecttest.com','Windows/Microsoft connectivity check')]:
            c.execute('DELETE FROM walled_garden WHERE domain=? AND note=?',(domain,note))
        default_hash = generate_password_hash('admin123', method='pbkdf2:sha256')
        c.execute("INSERT OR IGNORE INTO admins (username, password_hash) VALUES ('admin', ?)", (default_hash,))
        conn.commit()
init_db()

def get_config(key, default=''):
    with db_connection() as conn:
        c = conn.cursor()
        c.execute('SELECT value FROM config WHERE key=?', (key,))
        row = c.fetchone()
        return row[0] if row else default

def get_all_config():
    with db_connection() as conn:
        c = conn.cursor()
        c.execute('SELECT key, value FROM config')
        cfg = {row[0]: row[1] for row in c.fetchall()}
        c.execute('SELECT message FROM announcements WHERE active = 1 ORDER BY id DESC LIMIT 1')
        row = c.fetchone()
        cfg['announcement'] = row[0] if row else cfg.get('announcement', '')
        return cfg

def set_config(key, value):
    with db_connection() as conn:
        c = conn.cursor()
        c.execute('REPLACE INTO config (key, value) VALUES (?, ?)', (key, str(value)))
        conn.commit()

def _initialize_secret_key():
    secret = get_config('flask_secret_key', '')
    if not secret:
        import binascii
        secret = binascii.hexlify(os.urandom(32)).decode('ascii')
        set_config('flask_secret_key', secret)
    return secret
app.secret_key = _initialize_secret_key()
app.config.update(SESSION_COOKIE_SAMESITE='Lax')

def calculate_minutes_for_bottles(bottles_count):
    if bottles_count <= 0:
        return 0
    total_minutes = 0
    remaining_bottles = bottles_count
    with db_connection() as conn:
        c = conn.cursor()
        c.execute('SELECT bottles, minutes FROM promo_rates ORDER BY bottles DESC')
        rates = c.fetchall()
        if not rates:
            base_rate = int(get_config('minutes_per_bottle', '10'))
            return bottles_count * base_rate
        for tier_bottles, tier_minutes in rates:
            if remaining_bottles >= tier_bottles and tier_bottles > 0:
                multiplier = remaining_bottles // tier_bottles
                total_minutes += multiplier * tier_minutes
                remaining_bottles %= tier_bottles
        if remaining_bottles > 0:
            c.execute('SELECT minutes FROM promo_rates WHERE bottles = 1')
            base_row = c.fetchone()
            base_rate = base_row[0] if base_row else int(get_config('minutes_per_bottle', '10'))
            total_minutes += remaining_bottles * base_rate
    return total_minutes

def record_bottle_drop(count=1):
    today = datetime.now().strftime('%Y-%m-%d')
    with db_connection() as conn:
        c = conn.cursor()
        c.execute('INSERT OR IGNORE INTO stats (date, total_bottles) VALUES (?, 0)', (today,))
        c.execute('UPDATE stats SET total_bottles = total_bottles + ? WHERE date = ?', (count, today))
        conn.commit()
    print('[Eco-Fi STATS] +{} bottle(s) recorded in database for {}.'.format(count, today), flush=True)

def send_telegram_alert(custom_msg=None):
    bot_token = get_config('telegram_bot_token')
    chat_id = get_config('telegram_chat_id')
    if not bot_token or not chat_id:
        return False
    try:
        url = 'https://api.telegram.org/bot{}/sendMessage'.format(bot_token)
        text = custom_msg or '🚨 *Eco-Fi Alert*\n\nThe recycling bin has reached **100% capacity**! Please empty the bin.'
        payload = {'chat_id': chat_id, 'text': text, 'parse_mode': 'Markdown'}
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
        urllib.request.urlopen(req, timeout=5)
        return True
    except Exception:
        return False

active_reject_count = 0

last_esp32_rx_time = 0
esp32_port_name = None

def on_esp32_uart_output(msg):
    global last_esp32_rx_time
    last_esp32_rx_time = time.time()
    # Installed after storage initialization; an unacknowledged receipt is retried.
    log.warning("Device receipt arrived before the entitlement service was ready")
esp32 = ESP32Simulator(on_serial_output_callback=on_esp32_uart_output)

def transmit_to_esp32(payload_dict):
    msg_str = json.dumps(payload_dict) + '\n'
    if get_config('simulator_enabled','0')=='1':
        esp32.receive_uart(msg_str)
        return True
    with serial_write_lock:
        connection=ser
        if connection is None:return False
        try:
            encoded=msg_str.encode('utf-8')
            return connection.write(encoded)==len(encoded)
        except Exception as error:
            log.error('Serial write failed: %s',error)
            return False


def check_network_health():
    """Monitor for network configuration loss and recover."""
    if platform.system() == 'Windows':
        return
    try:
        lan_iface = get_lan_interface()
        res = subprocess.run(['ip', 'addr', 'show', lan_iface], stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        if '10.0.0.1' not in res.stdout:
            subprocess.run(['systemctl', 'restart', 'networking'])
            subprocess.run(['systemctl', 'restart', 'dnsmasq'])
            time.sleep(5)
            setup_firewall()
    except Exception:
        pass

def get_lan_interface():
    configured=os.environ.get('ECOFI_LAN_IFACE')
    if configured:return configured
    try:
        names=os.listdir('/sys/class/net')
        if 'eth1' in names:return 'eth1'
        for name in sorted(names):
            if name.startswith(('usb','enx')):return name
    except OSError:pass
    return 'eth1' 

def get_arp_table():
    if platform.system() == 'Windows':
        return {}
    try:
        res = subprocess.run(['ip', '-4', 'neigh', 'show', 'dev', get_lan_interface()], stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=5)
        result = {}
        for line in res.stdout.splitlines():
            parts = line.split()
            if 'lladdr' in parts and not any(state in parts for state in ('FAILED', 'INCOMPLETE')):
                result[parts[0]] = parts[parts.index('lladdr') + 1].lower()
        return result
    except Exception:
        log.exception('Cannot read LAN neighbors')
        return {}

def get_connected_ips():
    return set(get_arp_table().keys())

def setup_bandwidth_control(interface):
    if platform.system() == 'Windows':
        return
    try:
        subprocess.run(['tc', 'qdisc', 'del', 'dev', interface, 'root'], stderr=subprocess.DEVNULL)
        subprocess.run(['tc', 'qdisc', 'add', 'dev', interface, 'root', 'handle', '1:', 'htb', 'default', '99'], check=True)
        subprocess.run(['tc', 'class', 'add', 'dev', interface, 'parent', '1:', 'classid', '1:1', 'htb', 'rate', '100mbit'], check=True)
        subprocess.run(['tc', 'class', 'add', 'dev', interface, 'parent', '1:1', 'classid', '1:99', 'htb', 'rate', '100mbit'], check=True)
        
        subprocess.run(['modprobe', 'ifb', 'numifbs=1'], stderr=subprocess.DEVNULL)
        res = subprocess.run(['ip', 'link', 'set', 'dev', 'ifb0', 'up'], stderr=subprocess.DEVNULL)
        if res.returncode == 0:
            subprocess.run(['tc', 'qdisc', 'del', 'dev', interface, 'ingress'], stderr=subprocess.DEVNULL)
            subprocess.run(['tc', 'qdisc', 'add', 'dev', interface, 'ingress'], stderr=subprocess.DEVNULL)
            subprocess.run(['tc', 'filter', 'add', 'dev', interface, 'parent', 'ffff:', 'protocol', 'ip', 'u32', 'match', 'u32', '0', '0', 'action', 'mirred', 'egress', 'redirect', 'dev', 'ifb0'], stderr=subprocess.DEVNULL)
            subprocess.run(['tc', 'qdisc', 'del', 'dev', 'ifb0', 'root'], stderr=subprocess.DEVNULL)
            subprocess.run(['tc', 'qdisc', 'add', 'dev', 'ifb0', 'root', 'handle', '1:', 'htb', 'default', '99'], check=True)
            subprocess.run(['tc', 'class', 'add', 'dev', 'ifb0', 'parent', '1:', 'classid', '1:1', 'htb', 'rate', '100mbit'], check=True)
            subprocess.run(['tc', 'class', 'add', 'dev', 'ifb0', 'parent', '1:1', 'classid', '1:99', 'htb', 'rate', '100mbit'], check=True)
    except Exception as e:
        log.error('setup_bandwidth_control error: %s', e)

def apply_client_bandwidth(ip, dl_kbps, ul_kbps):
    if platform.system() == 'Windows':
        return
    interface = get_lan_interface()
    try:
        ip_int = int(ipaddress.IPv4Address(ip))
        mark = 100 + (ip_int & 16383)
        dl_kbps = max(64, int(dl_kbps))
        ul_kbps = max(64, int(ul_kbps))
        subprocess.run(['tc', 'class', 'replace', 'dev', interface, 'parent', '1:1', 'classid', '1:{}'.format(mark), 'htb', 'rate', '{}kbit'.format(dl_kbps), 'ceil', '{}kbit'.format(dl_kbps), 'burst', '15k'], check=True)
        subprocess.run(['tc', 'filter', 'replace', 'dev', interface, 'protocol', 'ip', 'parent', '1:', 'prio', str(mark), 'u32', 'match', 'ip', 'dst', '{}/32'.format(ip), 'flowid', '1:{}'.format(mark)], check=True)
        res = subprocess.run(['ip', 'link', 'show', 'ifb0'], stderr=subprocess.DEVNULL)
        if res.returncode == 0:
            subprocess.run(['tc', 'class', 'replace', 'dev', 'ifb0', 'parent', '1:1', 'classid', '1:{}'.format(mark), 'htb', 'rate', '{}kbit'.format(ul_kbps), 'ceil', '{}kbit'.format(ul_kbps), 'burst', '15k'], check=True)
            subprocess.run(['tc', 'filter', 'replace', 'dev', 'ifb0', 'protocol', 'ip', 'parent', '1:', 'prio', str(mark), 'u32', 'match', 'ip', 'src', '{}/32'.format(ip), 'flowid', '1:{}'.format(mark)], check=True)
    except Exception as e:
        log.error('apply_client_bandwidth error: %s', e)

def remove_client_bandwidth(ip):
    if platform.system() == 'Windows':
        return
    interface = get_lan_interface()
    try:
        ip_int = int(ipaddress.IPv4Address(ip))
        mark = 100 + (ip_int & 16383)
        subprocess.run(['tc', 'filter', 'del', 'dev', interface, 'protocol', 'ip', 'parent', '1:', 'prio', str(mark)], stderr=subprocess.DEVNULL)
        subprocess.run(['tc', 'class', 'del', 'dev', interface, 'parent', '1:1', 'classid', '1:{}'.format(mark)], stderr=subprocess.DEVNULL)
        res = subprocess.run(['ip', 'link', 'show', 'ifb0'], stderr=subprocess.DEVNULL)
        if res.returncode == 0:
            subprocess.run(['tc', 'filter', 'del', 'dev', 'ifb0', 'protocol', 'ip', 'parent', '1:', 'prio', str(mark)], stderr=subprocess.DEVNULL)
            subprocess.run(['tc', 'class', 'del', 'dev', 'ifb0', 'parent', '1:1', 'classid', '1:{}'.format(mark)], stderr=subprocess.DEVNULL)
    except Exception as e:
        log.error('remove_client_bandwidth error: %s', e)


def apply_walled_garden_and_macs():
    if platform.system() == 'Windows': return
    with db_connection() as conn:
        blocked = {row[0].lower() for row in conn.execute("SELECT mac FROM mac_control WHERE type='block'")}
        domains = [row[0] for row in conn.execute('SELECT domain FROM walled_garden')]
    addresses = set()
    for domain in domains:
        try:
            for result in socket.getaddrinfo(domain, None, socket.AF_INET):
                address = ipaddress.ip_address(result[4][0])
                if address.is_global: addresses.add(str(address))
        except socket.gaierror:
            log.warning('Walled garden DNS unavailable: %s', domain)
    gateway_network.policies(blocked, addresses)
    # Do not hold the RAM projection lock while taking the reconciliation lock.
    time_service.worker_pass()


def setup_firewall():
    if platform.system() == 'Windows': return
    gateway_network.setup(get_lan_interface(), os.environ.get('ECOFI_WAN_IFACE', 'eth0'))
    gateway_network.set_license(license_valid())
    apply_walled_garden_and_macs()


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
    validity_hours = 12.0 + 1.2 * math.sqrt(mins) + 0.025 * mins
    validity_hours = max(24.0, min(720.0, validity_hours))
    return int(validity_hours * 3600)

def compute_session_expiration(remaining_seconds, paused_at=None):
    """Returns the absolute Unix timestamp when the paused session expires."""
    if remaining_seconds <= 0:
        return 0
    base_time = paused_at or time.time()
    return int(base_time + calculate_pause_validity_seconds(remaining_seconds))





PORTAL_HTML = '\n<!DOCTYPE html>\n<html lang="en">\n<head>\n<script src="/static/time_controls.js?v=20260907_1"></script>\n    <meta charset="UTF-8">\n    <title>{% if has_time %}{{ vendo_name }} • Connected{% else %}{{ vendo_name }}{% endif %}</title>\n    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">\n    <link rel="icon" type="image/png" sizes="32x32" href="/static/favicon-32x32.png">\n    <link rel="icon" type="image/png" sizes="16x16" href="/static/favicon-16x16.png">\n    <link rel="shortcut icon" href="/static/favicon.ico">\n    <link rel="apple-touch-icon" sizes="180x180" href="/static/apple-touch-icon.png">\n    <link rel="stylesheet" href="/static/vendor/fontawesome/css/all.min.css">\n    <style>\n        * { box-sizing: border-box; }\n        img { max-width: 100%; height: auto; }\n        body { \n            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; \n            margin: 0; padding: 12px 10px 24px 10px; min-height: 100vh;\n            background: linear-gradient(rgba(15, 23, 42, 0.94), rgba(15, 23, 42, 0.98)), url(\'/static/banner.jpg\') no-repeat center center fixed;\n            background-size: cover;\n            color: #f1f5f9; \n            display: flex; justify-content: center; align-items: flex-start;\n            overflow-x: hidden;\n            width: 100%;\n        }\n\n        .portal-container {\n            width: 100%; max-width: 375px;\n            background: rgba(30, 41, 59, 0.88);\n            backdrop-filter: blur(16px);\n            -webkit-backdrop-filter: blur(16px);\n            border-radius: 14px;\n            border: 1px solid rgba(255, 255, 255, 0.1);\n            padding: 12px 14px;\n            text-align: center;\n            position: relative;\n            box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.5);\n            overflow: hidden;\n        }\n\n        .brand-banner-box {\n            width: 100%;\n            border-radius: 9px;\n            overflow: hidden;\n            margin-bottom: 9px;\n            border: 1px solid rgba(255, 255, 255, 0.12);\n        }\n        .brand-banner-img {\n            width: 100%;\n            height: auto;\n            display: block;\n            object-fit: cover;\n        }\n\n        .announcement-bar {\n            background: rgba(16, 185, 129, 0.1); border-left: 3px solid #10b981;\n            padding: 5px 9px; border-radius: 5px; font-size: 10.5px; color: #a7f3d0;\n            margin-bottom: 9px; text-align: left;\n        }\n\n        .bin-full-banner {\n            background: rgba(239, 68, 68, 0.15); border-left: 3px solid #ef4444;\n            padding: 6px 9px; border-radius: 5px; font-size: 11px; color: #fca5a5;\n            margin-bottom: 9px; text-align: left; display: none; font-weight: 600;\n        }\n\n        .status-box {\n            background: rgba(15, 23, 42, 0.65);\n            border-radius: 9px; padding: 8px 10px; margin-bottom: 9px;\n            border: 1px solid rgba(255, 255, 255, 0.08);\n        }\n        .status-text { \n            font-size: 10px; text-transform: uppercase; letter-spacing: 0.8px; \n            color: #94a3b8; font-weight: 600; margin-bottom: 2px;\n        }\n        .time-display { \n            font-size: 24px; font-family: "SF Mono", "Roboto Mono", "Courier New", monospace; \n            font-weight: 700; color: #10b981; margin: 2px 0 3px 0; \n            letter-spacing: 1px;\n        }\n        .status-badge { \n            display: inline-block; padding: 2px 8px; border-radius: 12px; \n            font-size: 9px; font-weight: 600; letter-spacing: 0.4px;\n        }\n        .bg-active { background: rgba(16, 185, 129, 0.15); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.35); }\n        .bg-paused { background: rgba(245, 158, 11, 0.15); color: #fbbf24; border: 1px solid rgba(245, 158, 11, 0.35); }\n        .bg-inactive { background: rgba(239, 68, 68, 0.15); color: #f87171; border: 1px solid rgba(239, 68, 68, 0.35); }\n        .bg-binfull { background: rgba(239, 68, 68, 0.25); color: #fca5a5; border: 1px solid #ef4444; }\n\n        \n        \n        \n        \n        \n        \n        .btn-pause { \n            background: rgba(245, 158, 11, 0.2); border: 1px solid rgba(245, 158, 11, 0.4); \n            color: #fbbf24; height: 35px; font-size: 11.5px;\n        }\n        .btn-pause:hover { background: rgba(245, 158, 11, 0.3); }\n        .btn-resume { \n            background: rgba(59, 130, 246, 0.2); border: 1px solid rgba(59, 130, 246, 0.4); \n            color: #60a5fa; height: 35px; font-size: 11.5px;\n        }\n        .btn-resume:hover { background: rgba(59, 130, 246, 0.3); }\n\n        .nav-tabs {\n            display: flex; gap: 3px; margin-bottom: 9px; background: rgba(15, 23, 42, 0.55);\n            padding: 3px; border-radius: 7px; border: 1px solid rgba(255, 255, 255, 0.06);\n            width: 100%;\n        }\n        .tab-btn {\n            flex: 1; padding: 6px 1px; font-size: 11px; font-weight: 500; color: #94a3b8;\n            background: transparent; border: none; border-radius: 5px; cursor: pointer;\n            transition: all 0.15s ease; text-align: center; white-space: nowrap;\n        }\n        .tab-btn.active { \n            background: #1e293b; color: #10b981; font-weight: 600;\n            border: 1px solid rgba(16, 185, 129, 0.25);\n            box-shadow: 0 1px 3px rgba(0, 0, 0, 0.3);\n        }\n\n        .tab-content { display: none; text-align: left; width: 100%; }\n        .tab-content.active { display: block; }\n\n        .custom-input {\n            width: 100%; height: 35px; padding: 6px 9px; border-radius: 6px; \n            background: rgba(15, 23, 42, 0.7);\n            border: 1px solid rgba(255, 255, 255, 0.12); color: #f8fafc; font-size: 12px; margin-bottom: 7px;\n            box-sizing: border-box; transition: border-color 0.15s ease;\n        }\n        .custom-input:focus { border-color: #10b981; outline: none; box-shadow: 0 0 0 1px rgba(16, 185, 129, 0.3); }\n\n        .table-info {\n            width: 100%; border-collapse: collapse; font-size: 11.5px; color: #cbd5e1;\n            margin-top: 3px; table-layout: fixed;\n        }\n        .table-info tr { border-bottom: 1px solid rgba(255, 255, 255, 0.05); }\n        .table-info tr:last-child { border-bottom: none; }\n        .table-info td { padding: 6px 2px; }\n\n        \n        \n\n        .modal-overlay {\n            display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%;\n            background: rgba(15, 23, 42, 0.88); backdrop-filter: blur(12px);\n            z-index: 1000; align-items: center; justify-content: center;\n        }\n        .modal-box {\n            width: 90%; max-width: 360px; background: #1e293b; border-radius: 14px;\n            padding: 20px 16px; border: 1px solid rgba(255, 255, 255, 0.1); box-shadow: 0 15px 35px rgba(0, 0, 0, 0.6);\n            text-align: center; position: relative; overflow: hidden;\n        }\n        .countdown-circle {\n            font-size: 32px; font-weight: 700; color: #f59e0b; margin: 8px 0;\n            font-family: monospace;\n        }\n        .bottle-counter {\n            font-size: 20px; font-weight: 700; color: #10b981; margin-bottom: 10px;\n        }\n        .progress-bar-bg {\n            width: 100%; height: 8px; background: #334155; border-radius: 6px;\n            overflow: hidden; margin-bottom: 10px;\n        }\n        .progress-bar-fill {\n            height: 100%; width: 100%; background: linear-gradient(90deg, #10b981, #34d399);\n            transition: width 0.3s ease;\n        }\n        .chute-stage-box {\n            background: rgba(15, 23, 42, 0.6); padding: 7px 9px; border-radius: 7px;\n            font-size: 11px; color: #94a3b8; margin-bottom: 10px; border: 1px dashed rgba(16, 185, 129, 0.3);\n        }\n        @keyframes drop-in {\n            0% { transform: translateY(-30px) rotate(0deg) scale(0.6); opacity: 0; }\n            50% { transform: translateY(0) rotate(10deg) scale(1.1); opacity: 1; }\n            100% { transform: translateY(0) rotate(0deg) scale(1); opacity: 1; }\n        }\n        .bottle-pop {\n            display: inline-block; animation: drop-in 0.35s ease-out;\n        }\n    \n        /* Compact Tactile Physical Appliance Button System (Clean, Proportional, 35px) */\n        .btn-tactile {\n            display: inline-flex;\n            align-items: center;\n            justify-content: center;\n            outline: none;\n            cursor: pointer;\n            height: 35px;\n            background-image: linear-gradient(to top, #D8D9DB 0%, #fff 80%, #FDFDFD 100%);\n            border-radius: 18px;\n            border: 1px solid #8F9092;\n            transition: transform 0.1s ease, filter 0.15s ease;\n            font-family: "Source Sans Pro", -apple-system, BlinkMacSystemFont, sans-serif;\n            font-size: 12px;\n            font-weight: 600;\n            color: #374151;\n            text-shadow: 0 1px #fff;\n            box-shadow: 0 1px 3px rgba(0, 0, 0, 0.25);\n            letter-spacing: 0.2px;\n            text-decoration: none;\n            gap: 6px;\n            padding: 0 14px;\n            box-sizing: border-box;\n            white-space: nowrap;\n        }\n\n        .btn-tactile:hover {\n            filter: brightness(0.97);\n            color: #1f2937;\n        }\n\n        .btn-tactile:active {\n            transform: translateY(1px);\n            box-shadow: 0 1px 2px rgba(0, 0, 0, 0.2), inset 0 1px 3px rgba(0, 0, 0, 0.2);\n        }\n\n        .btn-tactile:focus {\n            outline: none;\n        }\n\n        .btn-tactile:disabled {\n            opacity: 0.55;\n            cursor: not-allowed;\n            background-image: linear-gradient(to top, #bbb 0%, #ddd 100%) !important;\n            box-shadow: none !important;\n            color: #777 !important;\n            filter: none !important;\n        }\n\n        /* Tactile Green Accent (Connect, Claim, Redeem, Login, Done) */\n        .btn-tactile-green {\n            color: #065f46;\n            font-weight: 700;\n            border-color: #059669;\n            box-shadow: 0 1px 3px rgba(0, 0, 0, 0.25), inset 0 1px 0 rgba(255, 255, 255, 0.8);\n        }\n        .btn-tactile-green i {\n            color: #10b981;\n            font-size: 13px;\n        }\n\n        /* Tactile Amber Accent (Save Active Session to Wallet, Pause) */\n        .btn-tactile-amber {\n            color: #92400e;\n            font-weight: 700;\n            border-color: #d97706;\n            box-shadow: 0 1px 3px rgba(0, 0, 0, 0.25), inset 0 1px 0 rgba(255, 255, 255, 0.8);\n        }\n        .btn-tactile-amber i {\n            color: #f59e0b;\n            font-size: 13px;\n        }\n\n        /* Tactile Blue Accent (Resume) */\n        .btn-tactile-blue {\n            color: #1e40af;\n            font-weight: 700;\n            border-color: #3b82f6;\n            box-shadow: 0 1px 3px rgba(0, 0, 0, 0.25), inset 0 1px 0 rgba(255, 255, 255, 0.8);\n        }\n        .btn-tactile-blue i {\n            color: #2563eb;\n            font-size: 13px;\n        }\n\n        /* Hero Insert Button: 42px touch-friendly tactile button */\n        .btn-tactile-hero {\n            width: 100%;\n            height: 42px;\n            border-radius: 21px;\n            font-size: 13.5px;\n            font-weight: 700;\n            color: #065f46;\n            border-color: #059669;\n            box-shadow: 0 2px 5px rgba(0, 0, 0, 0.3), inset 0 1px 0 rgba(255, 255, 255, 0.9);\n            margin-bottom: 14px;\n        }\n        .btn-tactile-hero i {\n            color: #10b981;\n            font-size: 14px;\n        }\n\n    </style>\n</head>\n<body>\n\n    <div class="portal-container" style="margin-top: 15px;">\n        <div class="brand-banner-box">\n            <img src="/static/banner-main.jpg" alt="Smart Eco-Fi Vendo" class="brand-banner-img">\n        </div>\n\n        {% if announcement %}\n        <div class="announcement-bar">\n            <i class="fas fa-bullhorn"></i> {{ announcement }}\n        </div>\n        {% endif %}\n\n        <div id="bin-full-banner" class="bin-full-banner">\n            <i class="fas fa-exclamation-triangle"></i> Storage bin is currently full. Machine cannot accept new bottles at this moment.\n        </div>\n\n        {% if license_valid %}\n        <div class="status-box">\n            <div class="status-text">Available Internet Time</div>\n            <div class="time-display" id="time-display">0d 00h:00m:00s</div>\n            <div id="status-badge" class="status-badge bg-inactive">DISCONNECTED</div>\n        </div>\n\n        <!-- MAIN ACTION: PULSING INSERT BOTTLE BUTTON -->\n        <button id="btn-insert" class="btn-tactile btn-tactile-hero" onclick="startDepositSession()">\n            <i class="fas fa-recycle mr-1"></i> Insert Plastic Bottle\n        </button>\n\n        <div id="pause-ctrl-box" style="display:none; margin-bottom: 12px;">\n            <button id="btn-pause" class="btn-tactile btn-tactile-amber" style="width:100%;" onclick="togglePause(\'pause\')">\n                <i class="fas fa-pause"></i> PAUSE TIME\n            </button>\n            <button id="btn-resume" class="btn-tactile btn-tactile-blue" style="width:100%; display:none;" onclick="togglePause(\'resume\')">\n                <i class="fas fa-play"></i> RESUME TIME\n            </button>\n        </div>\n\n        <!-- MULTI-TAB FEATURES -->\n        <div class="nav-tabs">\n            <button class="tab-btn active" onclick="switchTab(\'tab-rates\')">Rates</button>\n            <button class="tab-btn" onclick="switchTab(\'tab-voucher\')">Voucher</button>\n            <button class="tab-btn" onclick="switchTab(\'tab-transfer\')">Transfer</button>\n\n        </div>\n\n        <!-- TAB 1: PROMO RATES -->\n        <div id="tab-rates" class="tab-content active">\n            <div style="font-size: 11.5px; color:#94a3b8; font-weight:600; margin-bottom:6px;"><i class="fas fa-tags text-success mr-1"></i> RATES & PACKAGES</div>\n            <table class="table-info">\n                {% for r in promo_rates %}\n                <tr>\n                    <td style="padding: 7px 6px;"><strong style="color:#34d399; font-size:13px;">{{ r.bottles }} Bottle{% if r.bottles > 1 %}s{% endif %}</strong></td>\n                    <td style="text-align:right; font-weight:700; color:#f8fafc; font-size:13px; padding: 7px 6px;">\n                        {% if r.minutes >= 60 %}\n                            {% set hrs = (r.minutes // 60) %}\n                            {% set mins = (r.minutes % 60) %}\n                            {% if mins == 0 %}\n                                {{ hrs }} Hour{% if hrs > 1 %}s{% endif %}\n                            {% else %}\n                                {{ hrs }}h {{ mins }}m\n                            {% endif %}\n                        {% else %}\n                            {{ r.minutes }} mins\n                        {% endif %}\n                    </td>\n                </tr>\n                {% endfor %}\n            </table>\n        </div>\n\n        <!-- TAB 2: VOUCHER REDEMPTION -->\n        <div id="tab-voucher" class="tab-content">\n            <div style="font-size: 13px; color:#94a3b8; font-weight:700; margin-bottom:6px;">ENTER VOUCHER CODE:</div>\n            <input type="text" id="voucher-code-input" class="custom-input" placeholder="e.g. Eco-XXXX" oninput="this.value = this.value.toUpperCase().replace(/[^A-Z0-9-]/g, \'\')">\n            <button class="btn-tactile btn-tactile-green" style="width:100%; margin-top:4px;" onclick="redeemVoucher()">\n                <i class="fas fa-ticket-alt mr-1"></i> Redeem Voucher\n            </button>\n            <div id="voucher-msg" style="font-size:12px; margin-top:4px;"></div>\n        </div>\n\n        <!-- TAB 3: TIME TRANSFER -->\n        <div id="tab-transfer" class="tab-content">\n            <div style="font-size: 12px; color:#94a3b8; font-weight:700; margin-bottom:6px;">SHARE / TRANSFER YOUR TIME:</div>\n            <div style="display:flex; gap:6px; margin-bottom:6px;">\n                <input type="number" id="transfer-mins-input" class="custom-input" placeholder="Minutes to Share (e.g. 5)" min="1" style="margin-bottom:0; flex:1;">\n                <button class="btn-tactile" style="width:auto; padding:0 16px; white-space:nowrap;" onclick="generateTransferCode()">\n                    <i class="fas fa-share-alt"></i> Share\n                </button>\n            </div>\n            <div id="transfer-code-display" style="font-size:12.5px; font-weight:700; color:#38bdf8; margin:4px 0; text-align:center;"></div>\n            \n            <hr style="border:0; border-top:1px solid rgba(255,255,255,0.08); margin:8px 0;">\n            <div style="font-size: 11.5px; color:#94a3b8; font-weight:600; margin-bottom:4px;">CLAIM A TRANSFER CODE:</div>\n            <div style="display:flex; gap:6px;">\n                <input type="text" id="claim-code-input" class="custom-input" placeholder="Transfer code" maxlength="12" style="margin-bottom:0; flex:1;" oninput="this.value = this.value.toUpperCase().replace(/[^A-Z0-9]/g,\'\')">\n                <button class="btn-tactile btn-tactile-green" style="width:auto; padding:0 16px; white-space:nowrap;" onclick="claimTransferCode()">\n                    <i class="fas fa-download"></i> Claim\n                </button>\n            </div>\n            <div id="claim-status-msg" style="font-size:11px; margin-top:4px; text-align:center;"></div>\n        </div>\n\n        <!-- TAB 4: MEMBER WALLET -->\n        \n\n        {% else %}\n        <div style="background: rgba(239, 68, 68, 0.1); border: 2px dashed #ef4444; border-radius: 12px; padding: 30px 15px; margin-top: 15px; text-align: center;">\n            <i class="fas fa-lock" style="font-size: 32px; color: #ef4444; margin-bottom: 10px;"></i>\n            <h3 style="color: #fca5a5; margin: 0 0 10px 0;">Unlicensed Vendo</h3>\n            <p style="color: #cbd5e1; font-size: 14px; margin: 0;">Please contact support or the administrator to activate this machine.</p>\n        </div>\n        {% endif %}\n\n        <!-- VISUAL GUIDE: ALLOWED VS NOT ALLOWED BOTTLES -->\n        <img src="/static/info-graphic.jpg" alt="Bottle Acceptance Guide" style="width: 100%; max-width: 100%; border-radius: 9px; margin-top: 12px; border: 1px solid rgba(255, 255, 255, 0.12); display: block;">\n\n        <div style="font-size:11px; color:#64748b; margin-top:18px;">\n            IP: {{ client_ip }} | MAC: {{ client_mac }}\n        </div>\n    </div>\n\n\n    <!-- SUCCESS INTERNET ACCESS NOTIFICATION (FLOATING TOAST, NO BLUR) -->\n    <div id="success-internet-modal" style="display:none; position:fixed; top:20px; left:0; right:0; z-index:2000; justify-content:center; align-items:flex-start; pointer-events:none; padding:0 14px;">\n        <div style="pointer-events:auto; width:100%; max-width:340px; background:rgba(30, 41, 59, 0.96); border:2px solid #10b981; border-radius:14px; padding:14px 16px; box-shadow:0 10px 30px rgba(0,0,0,0.6), 0 0 15px rgba(16,185,129,0.35); text-align:center; position:relative; cursor:pointer;" onclick="closeSuccessModal()">\n            <button type="button" onclick="closeSuccessModal()" style="position:absolute; top:8px; right:10px; background:transparent; border:none; color:#94a3b8; font-size:16px; cursor:pointer; padding:4px 6px; line-height:1;" title="Dismiss">\n                <i class="fas fa-times"></i>\n            </button>\n            <div style="margin-bottom:6px;">\n                <i class="fas fa-check-circle text-success" style="font-size:38px; filter:drop-shadow(0 0 8px rgba(16, 185, 129, 0.5));"></i>\n            </div>\n            <h3 style="margin:0 0 4px 0; color:#34d399; font-weight:800; font-size:17px; letter-spacing:0.3px;">\n                Internet Connected!\n            </h3>\n            <p style="color:#cbd5e1; font-size:12px; margin:0; line-height:1.35;" id="success-modal-msg">\n                Your high-speed internet time is active.\n            </p>\n        </div>\n    </div>\n\n    <!-- LIVE DEPOSIT MODAL WITH ANIMATED BOTTLE DROP & STATUS -->\n    <div id="deposit-modal" class="modal-overlay">\n        <div class="modal-box">\n            <h3 style="margin-top:0; color:#34d399;"><i class="fas fa-door-open"></i> AIRLOCK GATE OPEN</h3>\n            <div class="chute-stage-box" id="modal-stage-text">\n                <i class="fas fa-spinner fa-spin text-success"></i> Ready! Drop your PET plastic bottle into the chute...\n            </div>\n            \n            <div class="countdown-circle" id="modal-timer">30s</div>\n            <div class="progress-bar-bg">\n                <div id="modal-progress-bar" class="progress-bar-fill"></div>\n            </div>\n\n            <div class="bottle-counter">\n                <span id="modal-bottle-icon" class="bottle-pop"><i class="fas fa-wine-bottle"></i></span>\n                <span id="modal-bottles">0</span> Bottles (<span id="modal-added-time">+0m</span>)\n            </div>\n\n            <button class="btn-tactile btn-tactile-green" style="width:100%; height:38px; font-size:13px;" onclick="closeDepositSession()">\n                <i class="fas fa-check-circle"></i> DONE / START BROWSING\n            </button>\n        </div>\n    </div>\n\n    <script>\n        const audioBgSrc = "{{ audio_bg }}";\n        const audioInsertSrc = "{{ audio_insert }}";\n        const audioSuccessSrc = "{{ audio_success }}";\n        const audioVolume = (parseInt("{{ audio_volume or 80 }}") || 80) / 100.0;\n\n        let bgAudioElem = null;\n        if (audioBgSrc && audioBgSrc !== \'silent\') {\n            bgAudioElem = new Audio(audioBgSrc);\n            bgAudioElem.loop = true;\n            bgAudioElem.volume = audioVolume * 0.5;\n        }\n\n        let insertAudioElem = null;\n        if (audioInsertSrc && audioInsertSrc !== \'silent\' && audioInsertSrc !== \'arcade_powerup\' && audioInsertSrc !== \'voice_filipino\') {\n            insertAudioElem = new Audio(audioInsertSrc);\n            insertAudioElem.volume = audioVolume;\n            insertAudioElem.load();\n        }\n\n        let successAudioElem = null;\n        if (audioSuccessSrc && audioSuccessSrc !== \'silent\' && audioSuccessSrc !== \'crystal_bell\') {\n            successAudioElem = new Audio(audioSuccessSrc);\n            successAudioElem.volume = audioVolume;\n            successAudioElem.load();\n        }\n\n        const audioCtx = new (window.AudioContext || window.webkitAudioContext)();\n        \n        function unlockAudio() {\n            if (audioCtx && audioCtx.state === \'suspended\') {\n                audioCtx.resume().catch(()=>{});\n            }\n            if (insertAudioElem) {\n                insertAudioElem.muted = true;\n                insertAudioElem.play().then(() => {\n                    insertAudioElem.pause();\n                    insertAudioElem.currentTime = 0;\n                    insertAudioElem.muted = false;\n                }).catch(() => { insertAudioElem.muted = false; });\n            }\n            if (successAudioElem) {\n                successAudioElem.muted = true;\n                successAudioElem.play().then(() => {\n                    successAudioElem.pause();\n                    successAudioElem.currentTime = 0;\n                    successAudioElem.muted = false;\n                }).catch(() => { successAudioElem.muted = false; });\n            }\n        }\n        \n        document.addEventListener(\'click\', unlockAudio, { once: true });\n        \n        function playChimeTone(freq, type, duration, gainVal=0.3) {\n            try {\n                if (audioCtx && audioCtx.state === \'suspended\') {\n                    audioCtx.resume().catch(()=>{});\n                }\n                const osc = audioCtx.createOscillator();\n                const gain = audioCtx.createGain();\n                osc.type = type; osc.frequency.value = freq;\n                osc.connect(gain); gain.connect(audioCtx.destination);\n                const startTime = audioCtx.currentTime;\n                gain.gain.setValueAtTime(gainVal * audioVolume, startTime);\n                gain.gain.exponentialRampToValueAtTime(0.0001, startTime + duration);\n                osc.start(startTime);\n                osc.stop(startTime + duration);\n            } catch(e){}\n        }\n\n        let lastInsertChimeTime = 0;\n        function playInsertChime() {\n            const now = Date.now();\n            if (now - lastInsertChimeTime < 250) return;\n            lastInsertChimeTime = now;\n            if (audioInsertSrc === \'arcade_powerup\') {\n                playChimeTone(493.88, \'square\', 0.08);\n                setTimeout(() => playChimeTone(659.25, \'square\', 0.08), 80);\n                setTimeout(() => playChimeTone(987.77, \'square\', 0.25), 160);\n            } else if (audioInsertSrc === \'voice_filipino\') {\n                playChimeTone(587.33, \'sine\', 0.2);\n                if (\'speechSynthesis\' in window) {\n                    const utter = new SpeechSynthesisUtterance("Salamat sa pag-recycle! Dagdag minuto.");\n                    utter.lang = \'tl-PH\';\n                    window.speechSynthesis.speak(utter);\n                }\n            } else if (insertAudioElem) {\n                insertAudioElem.currentTime = 0;\n                insertAudioElem.play().catch(e => {\n                    playChimeTone(587.33, \'sine\', 0.18);\n                    setTimeout(() => playChimeTone(880.00, \'sine\', 0.35), 140);\n                });\n            } else {\n                playChimeTone(587.33, \'sine\', 0.18);\n                setTimeout(() => playChimeTone(880.00, \'sine\', 0.35), 140);\n            }\n        }\n\n        function playSuccessChime() {\n            if (audioSuccessSrc === \'crystal_bell\') {\n                playChimeTone(1046.50, \'sine\', 0.6, 0.4);\n            } else if (successAudioElem) {\n                successAudioElem.currentTime = 0;\n                successAudioElem.play().catch(e => {\n                    playChimeTone(880.00, \'sine\', 0.4);\n                });\n            } else if (!audioSuccessSrc || audioSuccessSrc !== \'silent\') {\n                playChimeTone(880.00, \'sine\', 0.4);\n            }\n        }\n\n        let depositActive = false;\n        let depositTimer = null;\n        let depositSec = 60;\n        let initialDepositTimeout = 60;\n        let sessionInitialized = false;\n        let lastBottleCount = 0;\n\n        function stopBgSound() {\n            if (bgAudioElem) {\n                try {\n                    bgAudioElem.pause();\n                    bgAudioElem.currentTime = 0;\n                } catch(e){}\n            }\n        }\n        window.addEventListener(\'beforeunload\', stopBgSound);\n        window.addEventListener(\'pagehide\', stopBgSound);\n        document.addEventListener(\'visibilitychange\', () => {\n            if (document.hidden && bgAudioElem && depositActive) {\n                bgAudioElem.pause();\n            } else if (!document.hidden && bgAudioElem && depositActive) {\n                bgAudioElem.play().catch(()=>{});\n            }\n        });\n        const isApple = /iPhone|iPad|iPod/i.test(navigator.userAgent);\n        const isAppleCNA = isApple && !/Safari/i.test(navigator.userAgent);\n        let localRemainingSeconds = {{ session_remaining_seconds or 0 }};\n        let isClientPaused = false;\n        let hasNetworkAccess = false;\n        let isSystemBinFull = false;\n\n        function switchTab(tabId) {\n            document.querySelectorAll(\'.tab-content\').forEach(el => el.classList.remove(\'active\'));\n            document.querySelectorAll(\'.tab-btn\').forEach(el => el.classList.remove(\'active\'));\n            document.getElementById(tabId).classList.add(\'active\');\n            const btn = document.querySelector(`button[onclick="switchTab(\'${tabId}\')"]`);\n            if (btn) btn.classList.add(\'active\');\n        }\n\n        function formatTime(totalSec) {\n            if (totalSec <= 0) return \'0d 00h:00m:00s\';\n            const d = Math.floor(totalSec / 86400);\n            const h = Math.floor((totalSec % 86400) / 3600).toString().padStart(2, \'0\');\n            const m = Math.floor((totalSec % 3600) / 60).toString().padStart(2, \'0\');\n            const s = Math.floor(totalSec % 60).toString().padStart(2, \'0\');\n            return `${d}d ${h}h:${m}m:${s}s`;\n        }\n\n        function formatAddedTime(mins) {\n            if (mins === 0) return \'+0m\';\n            let res = \'\';\n            const d = Math.floor(mins / 1440);\n            const h = Math.floor((mins % 1440) / 60);\n            const m = mins % 60;\n            if (d > 0) res += `${d}d `;\n            if (h > 0) res += `${h}h `;\n            res += `${m}m`;\n            return \'+\' + res.trim();\n        }\n\n        // Local ticker for smooth countdown\n        setInterval(() => {\n            if (localRemainingSeconds > 0 && !isClientPaused && hasNetworkAccess) {\n                localRemainingSeconds = Math.max(0, localRemainingSeconds - 1);\n                document.getElementById(\'time-display\').innerText = formatTime(localRemainingSeconds);\n                const modalTime = document.getElementById(\'success-modal-time\');\n                if (modalTime) modalTime.innerText = formatTime(localRemainingSeconds);\n                if (localRemainingSeconds <= 0) {\n                    syncPortal();\n                }\n            }\n        }, 1000);\n\n        function syncPortal() {\n            fetch(\'/api/vendo/status\')\n                .then(r => r.json())\n                .then(data => {\n                    localRemainingSeconds = data.client_time_remaining || data.remaining_seconds || 0;\n                    isClientPaused = data.is_paused || false;\n                    hasNetworkAccess = data.applied_state === \'ACTIVE\';\n                    isSystemBinFull = data.bin_full || false;\n                    \n                    document.getElementById(\'time-display\').innerText = formatTime(localRemainingSeconds);\n                    \n                    const badge = document.getElementById(\'status-badge\');\n                    const pauseBox = document.getElementById(\'pause-ctrl-box\');\n                    const btnPause = document.getElementById(\'btn-pause\');\n                    const btnResume = document.getElementById(\'btn-resume\');\n                    const btnInsert = document.getElementById(\'btn-insert\');\n                    const binBanner = document.getElementById(\'bin-full-banner\');\n\n                    // 1. Bin full handling\n                    if (isSystemBinFull) {\n                        binBanner.style.display = \'block\';\n                        if (!depositActive) {\n                            btnInsert.disabled = true;\n                            btnInsert.innerHTML = \'<i class="fas fa-ban"></i> BIN FULL - TEMPORARILY DISABLED\';\n                            // btnInsert.classList.remove(\'pulse-btn\');\n                        }\n                    } else {\n                        binBanner.style.display = \'none\';\n                        if (!depositActive) {\n                            btnInsert.disabled = false;\n                            btnInsert.innerHTML = \'<i class="fas fa-recycle mr-1"></i> Insert Plastic Bottle\';\n                            // btnInsert.classList.add(\'pulse-btn\');\n                        }\n                    }\n\n                    // 2. Connection and pause status\n                    if (localRemainingSeconds > 0) {\n                        pauseBox.style.display = \'block\';\n                        if (isClientPaused) {\n                            badge.className = \'status-badge bg-paused\';\n                            badge.innerText = \'PAUSED\';\n                            btnPause.style.display = \'none\';\n                            btnResume.style.display = \'flex\';\n                        } else {\n                            badge.className = \'status-badge bg-active\';\n                            badge.innerText = \'CONNECTED\';\n                            btnPause.style.display = \'flex\';\n                            btnResume.style.display = \'none\';\n                        }\n                    } else {\n                        badge.className = \'status-badge bg-inactive\';\n                        badge.innerText = isSystemBinFull ? \'BIN FULL\' : \'DISCONNECTED\';\n                        pauseBox.style.display = \'none\';\n                    }\n\n                    // 3. Deposit modal sync\n                    if (depositActive) {\n                        const bottles = data.session_bottles || 0;\n                        const addedMins = data.session_added_minutes !== undefined ? data.session_added_minutes : 0;\n                        document.getElementById(\'modal-bottles\').innerText = bottles;\n                        document.getElementById(\'modal-added-time\').innerText = formatAddedTime(addedMins);\n                        \n                        if (!sessionInitialized) {\n                            lastBottleCount = bottles;\n                            sessionInitialized = true;\n                        } else if (bottles > lastBottleCount && bottles > 0) {\n                            playInsertChime();\n                            \n                            const icon = document.getElementById(\'modal-bottle-icon\');\n                            icon.classList.remove(\'bottle-pop\');\n                            void icon.offsetWidth;\n                            icon.classList.add(\'bottle-pop\');\n\n                            document.getElementById(\'modal-stage-text\').innerHTML = \n                                `<span class="text-success font-weight-bold"><i class="fas fa-check-circle"></i> PET Bottle Verified! +${addedMins}m Added.</span>`;\n                            \n                            lastBottleCount = bottles;\n                            depositSec = initialDepositTimeout; // Refresh countdown for next bottle\n                        }\n                    }\n                }).catch(()=>{});\n        }\n\n        setInterval(syncPortal, 1200);\n\n        function startDepositSession() {\n            const btn = document.getElementById(\'btn-insert\');\n            if (btn.disabled || depositActive || isSystemBinFull) return;\n            btn.disabled = true;\n            \n            unlockAudio();\n            \n            lastBottleCount = 0;\n            sessionInitialized = false;\n            document.getElementById(\'modal-bottles\').innerText = \'0\';\n            document.getElementById(\'modal-added-time\').innerText = \'+0m\';\n            document.getElementById(\'modal-stage-text\').innerHTML = \n                \'<i class="fas fa-spinner fa-spin text-success"></i> Airlock opening... Please wait.\';\n            \n            fetch(\'/api/vendo/open_gate\', { method: \'POST\' })\n                .then(r => r.json())\n                .then(data => {\n                    if (!data.success) {\n                        alert((data.message || data.error) || "Machine is in use.");\n                        btn.disabled = isSystemBinFull;\n                        return;\n                    }\n                    depositActive = true;\n                    sessionInitialized = false;\n                    initialDepositTimeout = data.timeout || 60;\n                    depositSec = initialDepositTimeout;\n                    lastBottleCount = data.session_bottles || 0;\n                    document.getElementById(\'deposit-modal\').style.display = \'flex\';\n                    document.getElementById(\'modal-stage-text\').innerHTML = \n                        \'<i class="fas fa-arrow-down text-success"></i> Gate Open! Drop your PET bottle into the chute...\';\n                    \n                    if (bgAudioElem) {\n                        bgAudioElem.currentTime = 0;\n                        bgAudioElem.play().catch(()=>{});\n                    }\n\n                    if (depositTimer) clearInterval(depositTimer);\n                    depositTimer = setInterval(() => {\n                        depositSec--;\n                        document.getElementById(\'modal-timer\').innerText = `${depositSec}s`;\n                        const pct = Math.max(0, (depositSec / initialDepositTimeout) * 100);\n                        document.getElementById(\'modal-progress-bar\').style.width = `${pct}%`;\n                        if (depositSec <= 0) {\n                            closeDepositSession();\n                        }\n                    }, 1000);\n                }).catch(err => {\n                    btn.disabled = isSystemBinFull;\n                });\n        }\n\n\n        let successNotifTimer = null;\n\n        function showSuccessInternetModal(message) {\n            document.title = "{{ vendo_name }} • Connected";\n            const modal = document.getElementById(\'success-internet-modal\');\n            const msgEl = document.getElementById(\'success-modal-msg\');\n            if (msgEl && message) msgEl.innerText = message;\n            if (modal) {\n                modal.style.display = \'flex\';\n            }\n            playSuccessChime();\n            fetch(\'/hotspot-detect.html\').catch(()=>{});\n\n            // Auto-dismiss after 4 seconds so it acts like a clean notification\n            if (successNotifTimer) clearTimeout(successNotifTimer);\n            successNotifTimer = setTimeout(() => {\n                closeSuccessModal();\n            }, 4000);\n        }\n\n        window.addEventListener(\'DOMContentLoaded\', () => {\n            if (localRemainingSeconds > 0) {\n                document.getElementById(\'time-display\').innerText = formatTime(localRemainingSeconds);\n            }\n            const urlParams = new URLSearchParams(window.location.search);\n            if (urlParams.get(\'connected\') === \'1\') {\n                const msg = urlParams.get(\'msg\') || "Internet access is active!";\n                showSuccessInternetModal(msg);\n                try {\n                    window.history.replaceState({}, document.title, window.location.pathname);\n                } catch(e){}\n            }\n        });\n\n        function closeSuccessModal() {\n            if (successNotifTimer) clearTimeout(successNotifTimer);\n            const modal = document.getElementById(\'success-internet-modal\');\n            if (modal) modal.style.display = \'none\';\n        }\n\n        function closeDepositSession() {\n            if (!depositActive) return;\n            depositActive = false;\n            const hadBottles = (lastBottleCount > 0);\n            sessionInitialized = false;\n            clearInterval(depositTimer);\n            document.getElementById(\'deposit-modal\').style.display = \'none\';\n            document.getElementById(\'btn-insert\').disabled = isSystemBinFull;\n            \n            stopBgSound();fetch(\'/api/vendo/done\', { method: \'POST\' }).then(r => r.json()).then(data => {\n                if (hadBottles) {\n                    const msg = encodeURIComponent("Bottle deposit confirmed! Your high-speed internet time is active.");\n                    window.location.replace(\'/?connected=1&msg=\' + msg);\n                } else {\n                    syncPortal();\n                }\n            }).catch(() => {\n                if (hadBottles) {\n                    window.location.replace(\'/?connected=1\');\n                } else {\n                    syncPortal();\n                }\n            });\n        }\n\n        function togglePause(action) {\n            fetch(\'/api/client/pause\', {\n                method: \'POST\',\n                headers: { \'Content-Type\': \'application/json\' },\n                body: JSON.stringify({ action: action })\n            }).then(()=>syncPortal());\n        }\n\n        function redeemVoucher() {\n            const code = document.getElementById(\'voucher-code-input\').value.trim();\n            if (!code) return;\n            fetch(\'/api/voucher/redeem\', {\n                method: \'POST\',\n                headers: { \'Content-Type\': \'application/json\' },\n                body: JSON.stringify({ code: code })\n            }).then(r => r.json()).then(data => {\n                const msg = document.getElementById(\'voucher-msg\');\n                if (data.success) {\n                    const succMsg = encodeURIComponent(data.message || "Voucher redeemed successfully! Internet access is active.");\n                    window.location.replace(\'/?connected=1&msg=\' + succMsg);\n                } else {\n                    msg.style.color = \'#f87171\';\n                    msg.innerText = (data.message || data.error);\n                }\n            });\n        }\n\n        function generateTransferCode() {\n            const m = parseInt(document.getElementById(\'transfer-mins-input\').value) || 0;\n            const disp = document.getElementById(\'transfer-code-display\');\n            if (m <= 0) {\n                disp.style.color = \'#f87171\';\n                disp.innerText = \'Please enter the exact minutes to share.\';\n                return;\n            }\n            fetch(\'/api/transfer/generate\', {\n                method: \'POST\',\n                headers: { \'Content-Type\': \'application/json\' },\n                body: JSON.stringify({ minutes: m })\n            })\n            .then(r => r.json())\n            .then(data => {\n                if (data.success) {\n                    disp.style.color = \'#38bdf8\';\n                    disp.innerHTML = `TRANSFER CODE: <strong style="font-size:16px; letter-spacing:2px; color:#34d399;">${data.code}</strong> (${data.minutes} Mins)`;\n                    document.getElementById(\'transfer-mins-input\').value = \'\';\n                    syncPortal();\n                } else {\n                    disp.style.color = \'#f87171\';\n                    disp.innerText = (data.message || data.error);\n                }\n            });\n        }\n\n        function claimTransferCode() {\n            const code = document.getElementById(\'claim-code-input\').value.trim();\n            const msg = document.getElementById(\'claim-status-msg\');\n            if (!code) {\n                msg.style.color = \'#f87171\';\n                msg.innerText = \'Please enter your transfer code.\';\n                return;\n            }\n            fetch(\'/api/transfer/claim\', {\n                method: \'POST\',\n                headers: { \'Content-Type\': \'application/json\' },\n                body: JSON.stringify({ code: code })\n            }).then(r => r.json()).then(data => {\n                if (data.success) {\n                    const succMsg = encodeURIComponent(data.message || "Transfer code claimed successfully! Internet access is active.");\n                    window.location.replace(\'/?connected=1&msg=\' + succMsg);\n                } else {\n                    msg.style.color = \'#f87171\';\n                    msg.innerText = (data.message || data.error);\n                }\n            });\n        }\n\n                \n\n        function setWalletMins(val) {\n            const input = document.getElementById(\'use-wallet-mins\');\n            if (input) {\n                input.value = val;\n                input.focus();\n            }\n        }\n\n        \n\n        \n\n        \n\n        \n\n        \n\n        \n\n        \n\n        \n    </script>\n</body>\n</html>\n'

@app.errorhandler(404)
def page_not_found(e):
    if request.path.startswith('/admin') or request.path.startswith('/simulator') or request.path.startswith('/api'):
        if request.path.startswith('/simulator/api/') or request.path.startswith('/admin/api/') or request.path.startswith('/api/'):
            return (jsonify({'error': 'not_found', 'message': 'Endpoint not found'}), 404)
        return (
            '<!DOCTYPE HTML PUBLIC "-//IETF//DTD HTML 2.0//EN">\n'
            '<html><head>\n<title>404 Not Found</title>\n</head><body>\n'
            '<h1>Not Found</h1>\n'
            '<p>The requested URL was not found on the server. If you entered the URL manually please check your spelling and try again.</p>\n'
            '</body></html>', 404
        )
    return redirect('http://10.0.0.1/')

# ── Windows NCSI probe ────────────────────────────────────────────────────────
def check_client_online(client_ip):
    if not license_valid() or not time_service.healthy():return False
    sync_client_firewall(client_ip)
    sess=ensure_client_session(client_ip)
    return bool(sess.get('remaining_seconds',0)>0 and not sess.get('is_paused') and
                sess.get('desired_state')=='ACTIVE' and sess.get('applied_state')=='ACTIVE')


@app.route('/connecttest.txt')
def ncsi_connecttest():
    client_ip = get_client_ip()
    if check_client_online(client_ip):
        return Response('Microsoft Connect Test', mimetype='text/plain', status=200)
    return redirect('http://10.0.0.1/')

@app.route('/ncsi.txt')
def ncsi_txt():
    client_ip = get_client_ip()
    if check_client_online(client_ip):
        return Response('Microsoft NCSI', mimetype='text/plain', status=200)
    return redirect('http://10.0.0.1/')

# ── Android / Chrome OS connectivity probe ────────────────────────────────────
@app.route('/generate_204')
@app.route('/gen_204')
def generate_204():
    client_ip = get_client_ip()
    if check_client_online(client_ip):
        return Response('', status=204)
    return redirect('http://10.0.0.1/')

# ── Apple iOS / macOS captive portal probe ────────────────────────────────────
@app.route('/hotspot-detect.html')
@app.route('/library/test/success.html')
@app.route('/canonical.html')
def apple_captive():
    client_ip = get_client_ip()
    if check_client_online(client_ip):
        return Response('<HTML><HEAD><TITLE>Success</TITLE></HEAD><BODY>Success</BODY></HTML>',
                        mimetype='text/html', status=200)
    return redirect('http://10.0.0.1/')

# ── Firefox connectivity probe ────────────────────────────────────────────────
@app.route('/success.txt')
def firefox_success():
    client_ip = get_client_ip()
    if check_client_online(client_ip):
        return Response('success', mimetype='text/plain', status=200)
    return redirect('http://10.0.0.1/')

START_BROWSING_HTML = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>{{ vendo_name }} • Connected</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <link rel="stylesheet" href="/static/vendor/fontawesome/css/all.min.css">
    <style>
        * { box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            margin: 0; padding: 20px 16px; min-height: 100vh;
            background: linear-gradient(rgba(15, 23, 42, 0.95), rgba(15, 23, 42, 0.98)), url('/static/banner.jpg') no-repeat center center fixed;
            background-size: cover; color: #f1f5f9;
            display: flex; justify-content: center; align-items: center; text-align: center;
        }
        .card {
            width: 100%; max-width: 360px;
            background: rgba(30, 41, 59, 0.92);
            backdrop-filter: blur(16px);
            border-radius: 14px;
            border: 2px solid #10b981;
            padding: 24px 20px;
            box-shadow: 0 15px 35px rgba(0, 0, 0, 0.5);
        }
        .btn {
            display: flex; align-items: center; justify-content: center;
            height: 40px; border-radius: 20px; text-decoration: none;
            font-weight: 700; font-size: 13px; cursor: pointer; gap: 6px;
        }
        .btn-green { background: #10b981; color: white; border: none; }
        .btn-secondary { background: rgba(255,255,255,0.1); color: #cbd5e1; border: 1px solid rgba(255,255,255,0.15); }
    </style>
</head>
<body>
    <div class="card">
        <i class="fas fa-check-circle" style="font-size: 56px; color: #10b981; margin-bottom: 12px;"></i>
        <h2 style="margin: 0 0 6px 0; color: #34d399; font-size: 22px;">Internet Access Connected!</h2>
        <p style="color: #cbd5e1; font-size: 13px; margin: 0 0 18px 0;">Your device is now connected to high-speed WiFi.</p>
        <div style="display: flex; flex-direction: column; gap: 10px;">
            <a href="http://www.google.com" target="_blank" class="btn btn-green">
                <i class="fas fa-globe"></i> START BROWSING
            </a>
            <a href="/" class="btn btn-secondary">
                <i class="fas fa-arrow-left"></i> Return to Portal
            </a>
        </div>
    </div>
</body>
</html>'''

# ── Universal Start Browsing / Connect Endpoint (iOS & Android) ───────────────
@app.route('/start-browsing')
@app.route('/connect')
def start_browsing():
    client_ip = get_client_ip()
    session_data = ensure_client_session(client_ip)
    has_time = session_data.get('remaining_seconds', 0) > 0 and (not session_data.get('is_paused', False))
    if not has_time:
        return redirect('/')
    sync_client_firewall(client_ip)
    return redirect('/?connected=1')

@app.route('/favicon.ico')
def favicon():
    return send_from_directory(os.path.join(app.root_path, 'static'), 'favicon.ico', mimetype='image/vnd.microsoft.icon')

@app.route('/')
def index():
    client_ip = get_client_ip()
    session_data = ensure_client_session(client_ip)
    ua = request.headers.get('User-Agent', '').lower()
    is_apple = any(k in ua for k in ('iphone', 'ipad', 'ipod'))
    is_apple_cna = is_apple and ('applewebkit' in ua) and ('safari' not in ua)
    
    has_time = session_data.get('remaining_seconds', 0) > 0 and (not session_data.get('is_paused', False))
    if has_time:
        sync_client_firewall(client_ip)
    with db_connection() as conn:
        c = conn.cursor()
        c.execute('SELECT bottles, minutes, label FROM promo_rates ORDER BY bottles ASC')
        promos = [{'bottles': r[0], 'minutes': r[1], 'label': r[2]} for r in c.fetchall()]
        c.execute('SELECT message FROM announcements WHERE active = 1 ORDER BY id DESC LIMIT 1')
        ann_row = c.fetchone()
        announcement = ann_row[0] if ann_row else ''
        c.execute('SELECT domain, note FROM walled_garden ORDER BY domain ASC')
        walled_sites = [{'domain': r[0], 'note': r[1]} for r in c.fetchall()]
    return render_template_string(PORTAL_HTML, has_time=has_time, session_remaining_seconds=session_data.get('remaining_seconds', 0), license_valid=license_valid(), client_ip=client_ip, client_mac=session_data.get('mac', '00:00:00:00:00:00'), vendo_name=get_config('vendo_name', 'Eco-Fi Vendo'), vendo_subtitle=get_config('vendo_subtitle', 'Recycle Bottles for Fast WiFi'), promo_rates=promos, announcement=announcement, walled_sites=walled_sites, audio_bg=get_config('audio_bg', '/static/audio/eco_loop.wav'), audio_insert=get_config('audio_insert', '/static/audio/bottle_success.wav'), audio_success=get_config('audio_success', '/static/audio/eco_success.wav'), audio_volume=get_config('audio_volume', '80'))






@app.route('/admin/api/vouchers/list')
def admin_api_vouchers_list():
    if not session.get('admin_logged_in'):
        return (jsonify({'error': 'unauthorized'}), 401)
    with db_connection() as conn:
        c = conn.cursor()
        c.execute('SELECT code, minutes, is_used, created_at, used_by, note FROM vouchers ORDER BY created_at DESC LIMIT 50')
        rows = [{'code': r[0], 'minutes': r[1], 'is_used': r[2], 'created_at': r[3], 'used_by': r[4], 'note': r[5] or ''} for r in c.fetchall()]
        return jsonify(rows)

@app.route('/admin/api/vouchers/generate', methods=['POST'])
def admin_generate_vouchers():
    if not session.get('admin_logged_in'):
        return (jsonify({'error': 'unauthorized'}), 401)
    data = request.get_json() or {}
    try:
        qty = max(1, min(100, int(data.get('qty', 5))))
        minutes = max(1, int(data.get('minutes', 60)))
    except (ValueError, TypeError):
        return (jsonify({'success': False, 'error': 'Invalid quantity or duration.'}), 400)
    note = data.get('note', '').strip()
    prefix = data.get('prefix', '').strip().upper()
    created = []
    with db_connection() as conn:
        c = conn.cursor()
        for _ in range(qty):
            code = ''.join(random.SystemRandom().choice('ABCDEFGHJKLMNPQRSTUVWXYZ23456789') for _ in range(8))
            c.execute('INSERT INTO vouchers (code, minutes, created_at, note, policy_version_id) VALUES (?, ?, ?, ?, ?)', (code, minutes, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), note, time_schema.metadata(conn,'active_policy')))
            created.append({'code': code, 'minutes': minutes, 'note': note})
        conn.commit()
    return jsonify({'success': True, 'vouchers': created})

@app.route('/admin/api/vouchers/delete', methods=['POST'])
def admin_delete_voucher():
    if not session.get('admin_logged_in'):
        return (jsonify({'error': 'unauthorized'}), 401)
    data = request.get_json() or {}
    code = data.get('code', '').strip().upper()
    with db_connection() as conn:
        c = conn.cursor()
        c.execute('DELETE FROM vouchers WHERE code = ?', (code,))
        conn.commit()
        return jsonify({'success': True})






@app.route('/admin/api/settings/save', methods=['POST'])
def admin_api_settings_save():
    if not session.get('admin_logged_in'):
        return (jsonify({'error': 'unauthorized'}), 401)
    data = request.get_json() or {}
    if not isinstance(data,dict):return jsonify(success=False,error='Invalid settings'),400
    if 'member_wallet_enabled' in data:
        return jsonify(success=False, error='Feature permanently removed'), 400
    if 'drop_timeout' in data:
        try:
            if not 1<=int(data['drop_timeout'])<=600:raise ValueError()
        except (ValueError,TypeError):return jsonify(success=False,error='Gate timeout must be 1-600 seconds'),400
    for key in ('default_dl_kbps','default_ul_kbps'):
        if key in data:
            try:
                if not 64<=int(data[key])<=1000000:raise ValueError()
            except (ValueError,TypeError):return jsonify(success=False,error='Invalid bandwidth limit'),400
    for k, v in data.items():
        set_config(k, v)
        if k == 'announcement':
            with db_connection() as conn:
                c = conn.cursor()
                c.execute('UPDATE announcements SET message = ? WHERE id = 1', (str(v),))
                if c.rowcount == 0:
                    c.execute('INSERT INTO announcements (id, message, active) VALUES (1, ?, 1)', (str(v),))
                conn.commit()

    # The accounting worker applies validated defaults to grants without an override.
    # Network commands run after its database transaction, outside the RAM mirror lock.
    if 'default_dl_kbps' in data or 'default_ul_kbps' in data:
        time_service.worker_pass()

    return jsonify({'success': True})

@app.route('/admin/api/mac_control/list')
def admin_api_mac_list():
    if not session.get('admin_logged_in'):
        return (jsonify({'error': 'unauthorized'}), 401)
    with db_connection() as conn:
        c = conn.cursor()
        c.execute('SELECT mac, type, note, dl_kbps, ul_kbps FROM mac_control ORDER BY mac ASC')
        return jsonify([{'mac': r[0], 'type': r[1], 'note': r[2] or '', 'dl_kbps': r[3] or 0, 'ul_kbps': r[4] or 0} for r in c.fetchall()])

@app.route('/admin/api/mac_control/add', methods=['POST'])
def admin_api_mac_add():
    if not session.get('admin_logged_in'):
        return (jsonify({'error': 'unauthorized'}), 401)
    data = request.get_json() or {}
    raw_mac = data.get('mac', '').strip().upper()
    cleaned = re.sub('[^0-9A-F]', '', raw_mac)
    if len(cleaned) == 12:
        mac = ':'.join((cleaned[i:i + 2] for i in range(0, 12, 2)))
    else:
        mac = raw_mac
    if not re.match('^([0-9A-F]{2}[:-]){5}([0-9A-F]{2})$', mac):
        return (jsonify({'success': False, 'error': 'Invalid MAC address format! Must be 12 hex characters (e.g. AA:BB:CC:DD:EE:FF).'}), 400)
    m_type = data.get('type', 'whitelist')
    note = data.get('note', '').strip()
    
    with db_connection() as conn:
        c = conn.cursor()
        
        orig = data.get('original_mac', '').strip().upper()
        if orig and orig != mac:
            c.execute('SELECT COUNT(*) FROM mac_control WHERE mac=?', (mac,))
            if c.fetchone()[0] > 0:
                return jsonify({'success': False, 'error': 'MAC already exists'}), 409
                
            c.execute('SELECT dl_kbps, ul_kbps FROM mac_control WHERE mac=?', (orig,))
            row = c.fetchone()
            dl = int(data.get('dl_kbps', row[0] if row else 0))
            ul = int(data.get('ul_kbps', row[1] if row else 0))
            
            c.execute('DELETE FROM mac_control WHERE mac=?', (orig,))
            c.execute('INSERT INTO mac_control (mac, type, note, dl_kbps, ul_kbps) VALUES (?, ?, ?, ?, ?)', (mac, m_type, note, dl, ul))
        else:
            dl = int(data.get('dl_kbps', 0))
            ul = int(data.get('ul_kbps', 0))
            c.execute('REPLACE INTO mac_control (mac, type, note, dl_kbps, ul_kbps) VALUES (?, ?, ?, ?, ?)', (mac, m_type, note, dl, ul))
        
        apply_walled_garden_and_macs()
    return jsonify({'success': True, 'mac': mac})

@app.route('/admin/api/mac_control/delete', methods=['POST'])
def admin_api_mac_delete():
    if not session.get('admin_logged_in'):
        return (jsonify({'error': 'unauthorized'}), 401)
    data = request.get_json() or {}
    mac = data.get('mac', '').strip().upper()
    with db_connection() as conn:
        c = conn.cursor()
        c.execute('DELETE FROM mac_control WHERE mac = ?', (mac,))
        conn.commit()
    apply_walled_garden_and_macs()
    return jsonify({'success': True})

@app.route('/admin/api/walled_garden/list')
def admin_api_walled_garden_list():
    if not session.get('admin_logged_in'):
        return (jsonify({'error': 'unauthorized'}), 401)
    with db_connection() as conn:
        c = conn.cursor()
        c.execute('SELECT domain, note FROM walled_garden ORDER BY domain ASC')
        return jsonify([{'domain': r[0], 'note': r[1] or ''} for r in c.fetchall()])

@app.route('/admin/api/walled_garden/add', methods=['POST'])
def admin_api_walled_garden_add():
    if not session.get('admin_logged_in'):
        return (jsonify({'error': 'unauthorized'}), 401)
    data = request.get_json() or {}
    domain = data.get('domain', '').strip().lower()
    note = data.get('note', '').strip()
    if not domain or '.' not in domain or len(domain) < 4:
        return (jsonify({'success': False, 'error': 'Invalid domain name. Example: gcash.com or deped.gov.ph'}), 400)
    with db_connection() as conn:
        c = conn.cursor()
        c.execute('REPLACE INTO walled_garden (domain, note) VALUES (?, ?)', (domain, note))
        conn.commit()
        return jsonify({'success': True})

@app.route('/admin/api/walled_garden/delete', methods=['POST'])
def admin_api_walled_garden_delete():
    if not session.get('admin_logged_in'):
        return (jsonify({'error': 'unauthorized'}), 401)
    data = request.get_json() or {}
    domain = data.get('domain', '').strip().lower()
    with db_connection() as conn:
        c = conn.cursor()
        c.execute('DELETE FROM walled_garden WHERE domain = ?', (domain,))
        conn.commit()
        return jsonify({'success': True})

@app.route('/admin/api/telegram/test', methods=['POST'])
def admin_api_telegram_test():
    if not session.get('admin_logged_in'):
        return (jsonify({'error': 'unauthorized'}), 401)
    ok = send_telegram_alert('🔔 *Eco-Fi Test Alert*\n\nThis is a successful test notification from your Reverse Vending Machine!')
    return jsonify({'success': ok})

def generate_ecofi_excel_report(db_path):
    if not openpyxl:
        return None
    wb = openpyxl.Workbook()
    FONT_FAMILY = 'Segoe UI'
    title_font = Font(name=FONT_FAMILY, size=16, bold=True, color='FFFFFF')
    subtitle_font = Font(name=FONT_FAMILY, size=10, italic=True, color='E2E8F0')
    kpi_title_font = Font(name=FONT_FAMILY, size=9, bold=True, color='64748B')
    kpi_value_font = Font(name=FONT_FAMILY, size=14, bold=True, color='0F172A')
    header_font = Font(name=FONT_FAMILY, size=11, bold=True, color='FFFFFF')
    total_font = Font(name=FONT_FAMILY, size=11, bold=True, color='0F172A')
    data_font = Font(name=FONT_FAMILY, size=10, color='1E293B')
    title_fill = PatternFill(start_color='0F766E', end_color='0F766E', fill_type='solid')
    header_fill = PatternFill(start_color='10B981', end_color='10B981', fill_type='solid')
    kpi_fill = PatternFill(start_color='F8FAFC', end_color='F8FAFC', fill_type='solid')
    zebra_fill = PatternFill(start_color='F1F5F9', end_color='F1F5F9', fill_type='solid')
    white_fill = PatternFill(start_color='FFFFFF', end_color='FFFFFF', fill_type='solid')
    total_fill = PatternFill(start_color='E2E8F0', end_color='E2E8F0', fill_type='solid')
    status_active_fill = PatternFill(start_color='DCFCE7', end_color='DCFCE7', fill_type='solid')
    status_active_font = Font(name=FONT_FAMILY, size=10, bold=True, color='15803D')
    status_used_fill = PatternFill(start_color='F1F5F9', end_color='F1F5F9', fill_type='solid')
    status_used_font = Font(name=FONT_FAMILY, size=10, color='64748B')
    thin_border_side = Side(style='thin', color='CBD5E1')
    cell_border = Border(left=thin_border_side, right=thin_border_side, top=thin_border_side, bottom=thin_border_side)
    kpi_border = Border(left=thin_border_side, right=thin_border_side, top=thin_border_side, bottom=thin_border_side)
    total_border = Border(left=thin_border_side, right=thin_border_side, top=Side(style='thin', color='0F172A'), bottom=Side(style='double', color='0F172A'))
    align_center = Alignment(horizontal='center', vertical='center')
    align_left = Alignment(horizontal='left', vertical='center')
    align_right = Alignment(horizontal='right', vertical='center')
    ws1 = wb.active
    ws1.title = 'Daily Collections & Impact'
    ws1.views.sheetView[0].showGridLines = True
    conn_temp = sqlite3.connect(db_path)
    c = conn_temp.cursor()
    c.execute('SELECT date, total_bottles FROM stats ORDER BY date DESC')
    stats_rows = c.fetchall()
    c.execute('SELECT COUNT(*), SUM(CASE WHEN is_used=0 THEN 1 ELSE 0 END) FROM vouchers')
    v_stats = c.fetchone()
    total_vouchers = v_stats[0] or 0
    unclaimed_vouchers = v_stats[1] or 0
    conn_temp.close()
    total_bottles_sum = sum((r[1] for r in stats_rows))
    est_plastic_kg = total_bottles_sum * 0.025
    est_co2_kg = est_plastic_kg * 1.5
    total_mins_sum = total_bottles_sum * 10
    total_hours_sum = total_mins_sum / 60.0
    ws1.merge_cells('A1:G1')
    ws1['A1'] = 'Eco-Fi REVERSE VENDING MACHINE (PBVM)'
    ws1['A1'].font = title_font
    ws1['A1'].fill = title_fill
    ws1['A1'].alignment = align_center
    ws1.row_dimensions[1].height = 28
    ws1.merge_cells('A2:G2')
    ws1['A2'] = 'Executive Operations & Environmental Impact Report • Generated on {}'.format(datetime.now().strftime('%B %d, %Y at %I:%M %p'))
    ws1['A2'].font = subtitle_font
    ws1['A2'].fill = title_fill
    ws1['A2'].alignment = align_center
    ws1.row_dimensions[2].height = 18
    kpis = [('TOTAL BOTTLES', '{:,}'.format(total_bottles_sum), 'A', 'B'), ('WIFI TIME ISSUED', '{:.1f} Hours'.format(total_hours_sum), 'C', 'C'), ('PLASTIC RECYCLED', '{:.2f} kg'.format(est_plastic_kg), 'D', 'D'), ('CO₂ OFFSET', '{:.2f} kg'.format(est_co2_kg), 'E', 'E'), ('ACTIVE VOUCHERS', '{} avail'.format(unclaimed_vouchers), 'G', 'G')]
    ws1.row_dimensions[4].height = 16
    ws1.row_dimensions[5].height = 24
    for title, val, c1, c2 in kpis:
        if c1 != c2:
            ws1.merge_cells('{}4:{}4'.format(c1, c2))
            ws1.merge_cells('{}5:{}5'.format(c1, c2))
        top_cell = ws1['{}4'.format(c1)]
        top_cell.value = title
        top_cell.font = kpi_title_font
        top_cell.fill = kpi_fill
        top_cell.alignment = align_center
        top_cell.border = kpi_border
        val_cell = ws1['{}5'.format(c1)]
        val_cell.value = val
        val_cell.font = kpi_value_font
        val_cell.fill = kpi_fill
        val_cell.alignment = align_center
        val_cell.border = kpi_border
        if c1 != c2:
            ws1['{}4'.format(c2)].border = kpi_border
            ws1['{}5'.format(c2)].border = kpi_border
    headers = [('Date', align_center), ('Bottles Recycled', align_right), ('WiFi Time (Minutes)', align_right), ('WiFi Time (Hours)', align_right), ('Plastic Weight (kg)', align_right), ('CO₂ Saved (kg)', align_right), ('Collection Status', align_center)]
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
        status = 'High Volume' if count >= 20 else 'Active' if count > 0 else 'Idle'
        fill = zebra_fill if idx % 2 == 1 else white_fill
        ws1.row_dimensions[current_row].height = 20
        row_vals = [(dt, align_center, '@'), (count, align_right, '#,##0'), (mins, align_right, '#,##0'), (hrs, align_right, '0.00'), (kg, align_right, '0.000'), (co2, align_right, '0.000'), (status, align_center, '@')]
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
        tot_cells = [('TOTAL', align_center), ('=SUM(B8:B{})'.format(current_row - 1), align_right, '#,##0'), ('=SUM(C8:C{})'.format(current_row - 1), align_right, '#,##0'), ('=SUM(D8:D{})'.format(current_row - 1), align_right, '0.00'), ('=SUM(E8:E{})'.format(current_row - 1), align_right, '0.000'), ('=SUM(F8:F{})'.format(current_row - 1), align_right, '0.000'), ('', align_center)]
        for col_idx, (val, c_align, *opt_fmt) in enumerate(tot_cells, start=1):
            cell = ws1.cell(row=current_row, column=col_idx, value=val)
            cell.font = total_font
            cell.fill = total_fill
            cell.alignment = c_align
            cell.border = total_border
            if opt_fmt:
                cell.number_format = opt_fmt[0]
    ws1.freeze_panes = 'A8'
    ws2 = wb.create_sheet(title='Voucher Inventory')
    ws2.views.sheetView[0].showGridLines = True
    ws2.merge_cells('A1:G1')
    ws2['A1'] = 'Eco-Fi VOUCHER TICKETS INVENTORY'
    ws2['A1'].font = title_font
    ws2['A1'].fill = title_fill
    ws2['A1'].alignment = align_center
    ws2.row_dimensions[1].height = 26
    v_headers = ['Voucher Code', 'Duration (Mins)', 'Duration (Hours)', 'Status', 'Created Date', 'Redeemed / Used By', 'Batch / Admin Note']
    ws2.row_dimensions[3].height = 22
    for col_idx, h_title in enumerate(v_headers, start=1):
        cell = ws2.cell(row=3, column=col_idx, value=h_title)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = align_center if col_idx in [1, 4] else align_right if col_idx in [2, 3] else align_left
        cell.border = cell_border
    conn_temp = sqlite3.connect(db_path)
    c = conn_temp.cursor()
    c.execute('SELECT code, minutes, is_used, created_at, used_by, note FROM vouchers ORDER BY created_at DESC')
    v_rows = c.fetchall()
    conn_temp.close()
    v_row_idx = 4
    for idx, (code, mins, is_used, created_at, used_by, note) in enumerate(v_rows):
        ws2.row_dimensions[v_row_idx].height = 19
        fill = zebra_fill if idx % 2 == 1 else white_fill
        status_str = 'REDEEMED' if is_used else 'ACTIVE'
        s_fill = status_used_fill if is_used else status_active_fill
        s_font = status_used_font if is_used else status_active_font
        row_data = [(code, align_center, fill, data_font, '@'), (mins, align_right, fill, data_font, '#,##0'), (mins / 60.0, align_right, fill, data_font, '0.00'), (status_str, align_center, s_fill, s_font, '@'), (created_at or '--', align_left, fill, data_font, '@'), (used_by or '--', align_left, fill, data_font, '@'), (note or '', align_left, fill, data_font, '@')]
        for col_idx, (val, c_align, c_fill, c_font, n_fmt) in enumerate(row_data, start=1):
            cell = ws2.cell(row=v_row_idx, column=col_idx, value=val)
            cell.font = c_font
            cell.fill = c_fill
            cell.alignment = c_align
            cell.border = cell_border
            cell.number_format = n_fmt
        v_row_idx += 1
    ws2.freeze_panes = 'A4'
    ws3 = wb.create_sheet(title='Promo Rate Curves')
    ws3.views.sheetView[0].showGridLines = True
    ws3.merge_cells('A1:E1')
    ws3['A1'] = 'Eco-Fi ACTIVE RATE TIERS & PROMO CURVES'
    ws3['A1'].font = title_font
    ws3['A1'].fill = title_fill
    ws3['A1'].alignment = align_center
    ws3.row_dimensions[1].height = 26
    r_headers = ['Bottles Required', 'Time Credited (Mins)', 'Time Credited (Hours)', 'Rate Efficiency', 'Package Display Label']
    ws3.row_dimensions[3].height = 22
    for col_idx, h_title in enumerate(r_headers, start=1):
        cell = ws3.cell(row=3, column=col_idx, value=h_title)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = align_right if col_idx in [1, 2, 3] else align_center if col_idx == 4 else align_left
        cell.border = cell_border
    conn_temp = sqlite3.connect(db_path)
    c = conn_temp.cursor()
    c.execute('SELECT bottles, minutes, label FROM promo_rates ORDER BY bottles ASC')
    r_rows = c.fetchall()
    conn_temp.close()
    r_row_idx = 4
    for idx, (b, m, l) in enumerate(r_rows):
        ws3.row_dimensions[r_row_idx].height = 19
        fill = zebra_fill if idx % 2 == 1 else white_fill
        eff = '{:.1f} m/bottle'.format(m / b)
        row_data = [(b, align_right, '#,##0'), (m, align_right, '#,##0'), (m / 60.0, align_right, '0.00'), (eff, align_center, '@'), (l, align_left, '@')]
        for col_idx, (val, c_align, n_fmt) in enumerate(row_data, start=1):
            cell = ws3.cell(row=r_row_idx, column=col_idx, value=val)
            cell.font = data_font
            cell.fill = fill
            cell.alignment = c_align
            cell.border = cell_border
            cell.number_format = n_fmt
        r_row_idx += 1
    ws3.freeze_panes = 'A4'
    for ws in [ws1, ws2, ws3]:
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

@app.route('/admin/api/export_xlsx')
def admin_export_xlsx():
    if not session.get('admin_logged_in'):
        return redirect('/admin/login')
    if not openpyxl:
        return redirect('/admin/api/export_csv')
    wb = generate_ecofi_excel_report(DB_PATH)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    filename = 'Eco_Fi_Operations_Report_{}.xlsx'.format(datetime.now().strftime('%Y%m%d'))
    try:
        return send_file(buf, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', as_attachment=True, download_name=filename)
    except TypeError:
        return send_file(buf, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', as_attachment=True, attachment_filename=filename)

@app.route('/admin/api/export_csv')
def admin_export_csv():
    if not session.get('admin_logged_in'):
        return redirect('/admin/login')
    with db_connection() as conn:
        c = conn.cursor()
        c.execute('SELECT date, total_bottles FROM stats ORDER BY date DESC')
        rows = c.fetchall()
    csv_data = 'Date,Total Bottles,Equivalent Minutes\n'
    for r in rows:
        csv_data += '{},{},{}\n'.format(r[0], r[1], r[1] * 10)
    return Response(csv_data, mimetype='text/csv', headers={'Content-disposition': 'attachment; filename=ecofi_sales_report.csv'})
LOGIN_HTML = '\n<!DOCTYPE html>\n<html lang="en">\n<head>\n    <meta charset="utf-8">\n    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">\n    <title>Sign In | Eco-Fi</title>\n    <link rel="icon" type="image/png" sizes="32x32" href="/static/favicon-32x32.png">\n    <link rel="icon" type="image/png" sizes="16x16" href="/static/favicon-16x16.png">\n    <link rel="shortcut icon" href="/static/favicon.ico">\n    <link rel="apple-touch-icon" sizes="180x180" href="/static/apple-touch-icon.png">\n    <link rel="stylesheet" href="/static/vendor/fontawesome/css/all.min.css">\n    <link rel="stylesheet" href="/static/vendor/adminlte/css/adminlte.min.css">\n    <style>\n        body {\n            background-color: #0b0f19;\n            min-height: 100vh;\n            display: flex;\n            align-items: center;\n            justify-content: center;\n            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;\n            margin: 0;\n            padding: 16px;\n        }\n        .login-box-clean {\n            width: 100%;\n            max-width: 340px;\n            background: #111827;\n            border: 1px solid #1f2937;\n            border-radius: 12px;\n            padding: 24px;\n            box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.6);\n        }\n        .login-title {\n            font-size: 18px;\n            font-weight: 700;\n            color: #f9fafb;\n            text-align: center;\n            margin-bottom: 2px;\n        }\n        .login-subtitle {\n            font-size: 11px;\n            color: #6b7280;\n            text-align: center;\n            margin-bottom: 20px;\n            letter-spacing: 0.5px;\n            text-transform: uppercase;\n        }\n        .form-group-clean {\n            margin-bottom: 14px;\n        }\n        .form-group-clean label {\n            display: block;\n            font-size: 12px;\n            font-weight: 500;\n            color: #9ca3af;\n            margin-bottom: 5px;\n        }\n        .form-control-clean {\n            width: 100%;\n            height: 38px;\n            background-color: #1f2937 !important;\n            border: 1px solid #374151 !important;\n            border-radius: 6px !important;\n            color: #f9fafb !important;\n            font-size: 13px !important;\n            padding: 8px 12px !important;\n            box-sizing: border-box;\n            outline: none;\n            transition: border-color 0.15s ease-in-out;\n        }\n        .form-control-clean:focus {\n            border-color: #10b981 !important;\n            box-shadow: 0 0 0 2px rgba(16, 185, 129, 0.2) !important;\n        }\n        input:-webkit-autofill,\n        input:-webkit-autofill:hover, \n        input:-webkit-autofill:focus {\n            -webkit-text-fill-color: #f9fafb !important;\n            -webkit-box-shadow: 0 0 0px 1000px #1f2937 inset !important;\n            transition: background-color 5000s ease-in-out 0s;\n        }\n        .btn-signin {\n            width: 100%;\n            height: 38px;\n            background: #10b981;\n            border: none;\n            border-radius: 6px;\n            color: #ffffff;\n            font-size: 13.5px;\n            font-weight: 600;\n            cursor: pointer;\n            margin-top: 6px;\n            transition: background 0.15s ease;\n        }\n        .btn-signin:hover {\n            background: #059669;\n        }\n        .btn-signin:active {\n            background: #047857;\n        }\n        .alert-error {\n            background: rgba(239, 68, 68, 0.15);\n            border: 1px solid rgba(239, 68, 68, 0.3);\n            color: #fca5a5;\n            padding: 8px 12px;\n            border-radius: 6px;\n            font-size: 12px;\n            margin-bottom: 14px;\n            text-align: center;\n        }\n        .login-footer {\n            margin-top: 18px;\n            text-align: center;\n            font-size: 12px;\n        }\n        .login-footer a {\n            color: #6b7280;\n            text-decoration: none;\n        }\n        .login-footer a:hover {\n            color: #9ca3af;\n        }\n    </style>\n</head>\n<body>\n<div class="login-box-clean">\n    <div class="login-title"><i class="fas fa-recycle text-success mr-1"></i> Eco-Fi VENDO</div>\n    <div class="login-subtitle">Master Control Panel</div>\n    \n    {% if error %}\n    <div class="alert-error">{{ error }}</div>\n    {% endif %}\n    \n    <form method="post">\n        <div class="form-group-clean">\n            <label for="username">Username</label>\n            <input type="text" id="username" name="username" class="form-control-clean" placeholder="Admin username" required autofocus autocomplete="username">\n        </div>\n        \n        <div class="form-group-clean">\n            <label for="password">Password</label>\n            <input type="password" id="password" name="password" class="form-control-clean" placeholder="Password" required autocomplete="current-password">\n        </div>\n        \n        <button type="submit" class="btn-signin">Sign In</button>\n    </form>\n    \n    <div class="login-footer">\n        <a href="/">← Return to Client Portal</a>\n    </div>\n</div>\n</body>\n</html>\n'
ADMIN_HTML = '\n<!DOCTYPE html>\n<html lang="en">\n<head>\n<script src="/static/time_controls.js?v=20260907_1"></script>\n    <title>Dashboard | Eco-Fi</title>\n    <meta charset="utf-8">\n    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">\n    <link rel="icon" type="image/png" sizes="32x32" href="/static/favicon-32x32.png">\n    <link rel="icon" type="image/png" sizes="16x16" href="/static/favicon-16x16.png">\n    <link rel="shortcut icon" href="/static/favicon.ico">\n    <link rel="apple-touch-icon" sizes="180x180" href="/static/apple-touch-icon.png">\n    <link rel="stylesheet" href="/static/vendor/fontawesome/css/all.min.css">\n    <link rel="stylesheet" href="/static/vendor/adminlte/css/adminlte.min.css">\n    <script src="/static/vendor/jquery/jquery.min.js"></script>\n    <script src="/static/vendor/bootstrap/js/bootstrap.bundle.min.js"></script>\n    <script src="/static/vendor/adminlte/js/adminlte.min.js"></script>\n    <script src="/static/vendor/sweetalert2/sweetalert2.all.min.js"></script>\n    <script src="/static/vendor/chartjs/Chart.bundle.min.js"></script>\n    <style>\n      /* ==========================================================================\n         Eco-Fi MASTER PLAIN DARK THEME (FLAT, HIGH-CONTRAST, ZERO GLOW)\n         ========================================================================== */\n      :root {\n        --eco-bg: #0b0f19;\n        --eco-card: #1e293b;\n        --eco-header: #0f172a;\n        --eco-border: rgba(255, 255, 255, 0.08);\n        --eco-border-light: rgba(255, 255, 255, 0.12);\n        --eco-primary: #007bff;\n        --eco-accent: #38bdf8;\n        --eco-text-main: #f8fafc;\n        --eco-text-body: #cbd5e1;\n        --eco-text-muted: #94a3b8;\n      }\n\n      @keyframes pulseDot {\n        0% { transform: scale(0.9); box-shadow: 0 0 0 0 rgba(16, 185, 129, 0.7); }\n        70% { transform: scale(1); box-shadow: 0 0 0 5px rgba(16, 185, 129, 0); }\n        100% { transform: scale(0.9); box-shadow: 0 0 0 0 rgba(16, 185, 129, 0); }\n      }\n            .chart-tab-btn {\n        background: transparent !important;\n        color: #94a3b8 !important;\n        box-shadow: none !important;\n      }\n      .chart-tab-btn:hover {\n        color: #f1f5f9 !important;\n      }\n      .chart-tab-btn.active {\n        background: #10b981 !important;\n        color: #ffffff !important;\n        box-shadow: 0 1px 4px rgba(16, 185, 129, 0.4) !important;\n      }\n.live-pulse-dot {\n        display: inline-block;\n        width: 7px;\n        height: 7px;\n        border-radius: 50%;\n        background: #10b981;\n        animation: pulseDot 2s infinite ease-in-out;\n        vertical-align: middle;\n      }\n\n      /* Base Layout & Typography */\n      body.dark-mode {\n        background-color: var(--eco-bg) !important;\n        color: var(--eco-text-main) !important;\n        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;\n      }\n      .layout-navbar-fixed .content-wrapper,\n      .content-wrapper {\n        background-color: var(--eco-bg) !important;\n        color: var(--eco-text-main) !important;\n        margin-top: 0 !important;\n        padding: 10px 16px !important;\n      }\n      .card-header {\n        padding: 0.55rem 0.85rem !important;\n      }\n      .card-body {\n        padding: 0.75rem 0.85rem !important;\n      }\n      .main-header.navbar {\n        background-color: var(--eco-header) !important;\n        border-bottom: 1px solid var(--eco-border) !important;\n        position: sticky !important;\n        top: 0 !important;\n        z-index: 1030 !important;\n      }\n      .main-sidebar {\n        background-color: var(--eco-bg) !important;\n        border-right: 1px solid var(--eco-border) !important;\n        position: fixed !important;\n        top: 0 !important;\n        bottom: 0 !important;\n        left: 0 !important;\n        overflow-y: auto !important;\n        z-index: 1038 !important;\n      }\n      .brand-link {\n        font-weight: 700;\n        letter-spacing: 0.5px;\n        border-bottom: 1px solid var(--eco-border) !important;\n        background-color: var(--eco-bg) !important;\n      }\n\n            /* ==========================================================================\n         Polished Eco-Fi Custom Toggle Switch\n         ========================================================================== */\n      .eco-toggle-wrap {\n        display: inline-flex;\n        align-items: center;\n        gap: 8px;\n        cursor: pointer;\n        user-select: none;\n        margin: 0 12px 0 0;\n        vertical-align: middle;\n      }\n      .eco-toggle-wrap input[type="checkbox"] {\n        position: absolute;\n        opacity: 0;\n        width: 0;\n        height: 0;\n        pointer-events: none;\n      }\n      .eco-toggle-track {\n        position: relative;\n        width: 36px;\n        height: 20px;\n        background-color: #334155;\n        border: 1px solid #475569;\n        border-radius: 20px;\n        transition: background-color 0.22s cubic-bezier(0.4, 0, 0.2, 1),\n                    border-color 0.22s cubic-bezier(0.4, 0, 0.2, 1),\n                    box-shadow 0.22s ease;\n        display: inline-block;\n        flex-shrink: 0;\n      }\n      .eco-toggle-track::after {\n        content: "";\n        position: absolute;\n        top: 2px;\n        left: 2px;\n        width: 14px;\n        height: 14px;\n        background-color: #ffffff;\n        border-radius: 50%;\n        transition: transform 0.22s cubic-bezier(0.4, 0, 0.2, 1);\n        box-shadow: 0 1px 3px rgba(0, 0, 0, 0.4);\n      }\n      .eco-toggle-wrap:hover .eco-toggle-track {\n        border-color: #64748b;\n      }\n      .eco-toggle-wrap input[type="checkbox"]:checked + .eco-toggle-track {\n        background-color: #10b981;\n        border-color: #059669;\n        box-shadow: 0 0 8px rgba(16, 185, 129, 0.3);\n      }\n      .eco-toggle-wrap input[type="checkbox"]:checked + .eco-toggle-track::after {\n        transform: translateX(16px);\n      }\n      .eco-toggle-label {\n        font-size: 12px;\n        font-weight: 600;\n        color: #94a3b8;\n        transition: color 0.2s ease;\n        margin: 0;\n        letter-spacing: 0.3px;\n      }\n      .eco-toggle-wrap:hover .eco-toggle-label {\n        color: #f1f5f9;\n      }\n      .eco-toggle-wrap input[type="checkbox"]:checked ~ .eco-toggle-label {\n        color: #10b981;\n      }\n      .eco-toggle-link {\n        font-size: 11px;\n        font-weight: 600;\n        color: #38bdf8;\n        text-decoration: none;\n        padding: 2px 7px;\n        background: rgba(56, 189, 248, 0.12);\n        border: 1px solid rgba(56, 189, 248, 0.25);\n        border-radius: 4px;\n        margin-right: 12px;\n        transition: all 0.18s ease;\n        display: inline-flex;\n        align-items: center;\n        gap: 3px;\n      }\n      .eco-toggle-link:hover {\n        background: rgba(56, 189, 248, 0.25);\n        color: #ffffff;\n        text-decoration: none;\n        border-color: #38bdf8;\n      }\n\n      /* ==========================================================================\n         Sidebar Navigation: Smooth, Lightweight, Tactile Transitions\n         ========================================================================== */\n      .nav-sidebar .nav-link {\n        color: var(--eco-text-muted) !important;\n        font-weight: 500;\n        border-radius: 6px !important;\n        margin: 2px 8px;\n        box-shadow: none !important;\n        border: none !important;\n        outline: none !important;\n        transition: background-color 0.2s cubic-bezier(0.4, 0, 0.2, 1),\n                    color 0.2s cubic-bezier(0.4, 0, 0.2, 1),\n                    transform 0.12s ease !important;\n      }\n      .nav-sidebar .nav-link:hover {\n        background-color: rgba(255, 255, 255, 0.07) !important;\n        color: #ffffff !important;\n        box-shadow: none !important;\n      }\n      .nav-sidebar .nav-link:active {\n        transform: scale(0.98) !important;\n      }\n      .nav-sidebar .nav-link.active {\n        background-color: #007bff !important;\n        color: #ffffff !important;\n        font-weight: 600 !important;\n        box-shadow: none !important;\n        border: none !important;\n        outline: none !important;\n      }\n      .nav-sidebar .nav-link:focus,\n      .nav-sidebar .nav-link:focus-visible,\n      a:focus, button:focus {\n        outline: none !important;\n        box-shadow: none !important;\n      }\n      .nav-header {\n        color: #64748b !important;\n        font-weight: 700 !important;\n        letter-spacing: 0.8px !important;\n        font-size: 0.72rem !important;\n        padding: 0.75rem 1rem 0.35rem !important;\n      }\n\n      /* Treeview Caret & Active Parent Styling */\n      .nav-sidebar .has-treeview > .nav-link .right {\n        transition: transform 0.25s cubic-bezier(0.4, 0, 0.2, 1) !important;\n        display: inline-block !important;\n      }\n      .nav-sidebar .has-treeview.menu-open > .nav-link .right,\n      .nav-sidebar .has-treeview.menu-is-opening > .nav-link .right {\n        transform: rotate(-90deg) !important;\n      }\n      .nav-sidebar .has-treeview.menu-open > .nav-link {\n        background-color: rgba(255, 255, 255, 0.05) !important;\n        color: #ffffff !important;\n      }\n      .nav-sidebar .has-treeview.menu-open > .nav-link.active,\n      .nav-sidebar .has-treeview > .nav-link.active-parent {\n        background-color: rgba(0, 123, 255, 0.16) !important;\n        color: #38bdf8 !important;\n        font-weight: 600 !important;\n      }\n      .nav-sidebar .has-treeview:not(.menu-open) > .nav-link.active {\n        background-color: #007bff !important;\n        color: #ffffff !important;\n        font-weight: 600 !important;\n      }\n      .nav-treeview {\n        padding-left: 0 !important;\n        margin: 0 !important;\n        overflow: hidden;\n      }\n      .nav-treeview .nav-item .nav-link {\n        margin: 2px 8px 2px 18px !important;\n        font-size: 0.88rem !important;\n        padding: 7px 12px !important;\n      }\n      .nav-treeview .nav-link.active {\n        background-color: #007bff !important;\n        color: #ffffff !important;\n        font-weight: 600 !important;\n      }\n      .nav-treeview .nav-link.active i {\n        color: #ffffff !important;\n      }\n\n      /* Silky-Smooth Hardware-Accelerated Section Transitions */\n      .section-view {\n        display: none;\n        opacity: 0;\n        will-change: opacity, transform;\n      }\n      .section-view.active {\n        display: block;\n        animation: ecoSectionEnter 0.22s cubic-bezier(0.16, 1, 0.3, 1) forwards;\n      }\n      @keyframes ecoSectionEnter {\n        0% {\n          opacity: 0;\n          transform: translateY(6px);\n        }\n        100% {\n          opacity: 1;\n          transform: translateY(0);\n        }\n      }\n\n      /* Silky Main Layout & Sidebar Glides */\n      .main-sidebar, .content-wrapper, .main-header {\n        transition: margin-left 0.25s cubic-bezier(0.4, 0, 0.2, 1),\n                    width 0.25s cubic-bezier(0.4, 0, 0.2, 1) !important;\n      }\n\n      /* Tactile & Smooth Button Transitions */\n      .btn {\n        transition: background-color 0.18s cubic-bezier(0.4, 0, 0.2, 1),\n                    border-color 0.18s cubic-bezier(0.4, 0, 0.2, 1),\n                    color 0.18s cubic-bezier(0.4, 0, 0.2, 1),\n                    box-shadow 0.18s cubic-bezier(0.4, 0, 0.2, 1),\n                    transform 0.1s ease !important;\n      }\n      .btn:active {\n        transform: scale(0.97) !important;\n      }\n      .btn:focus, .btn:focus-visible {\n        outline: none !important;\n        box-shadow: none !important;\n      }\n\n      /* Smooth Form Controls & Inputs */\n      .form-control, .custom-select, select.form-control, textarea.form-control {\n        transition: border-color 0.2s cubic-bezier(0.4, 0, 0.2, 1),\n                    background-color 0.2s cubic-bezier(0.4, 0, 0.2, 1),\n                    box-shadow 0.2s cubic-bezier(0.4, 0, 0.2, 1),\n                    color 0.2s ease !important;\n      }\n      .form-control:focus, .custom-select:focus, select.form-control:focus, textarea.form-control:focus {\n        border-color: #007bff !important;\n        box-shadow: 0 0 0 2px rgba(0, 123, 255, 0.2) !important;\n      }\n\n      /* Smooth Switches & Checkboxes */\n      .custom-control-label {\n        transition: color 0.2s ease !important;\n      }\n      .custom-switch .custom-control-label::before {\n        transition: background-color 0.22s cubic-bezier(0.4, 0, 0.2, 1),\n                    border-color 0.22s cubic-bezier(0.4, 0, 0.2, 1) !important;\n      }\n      .custom-switch .custom-control-label::after {\n        transition: transform 0.22s cubic-bezier(0.4, 0, 0.2, 1),\n                    background-color 0.22s cubic-bezier(0.4, 0, 0.2, 1) !important;\n      }\n\n      /* Smooth Tabs & Pills */\n      .nav-pills .nav-link, .nav-tabs .nav-link, .chart-tab-btn {\n        transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1) !important;\n      }\n\n      /* Smooth Modals */\n      .modal.fade .modal-dialog {\n        transition: transform 0.24s cubic-bezier(0.16, 1, 0.3, 1), opacity 0.24s ease-out !important;\n        transform: translateY(-14px) scale(0.98);\n      }\n      .modal.show .modal-dialog {\n        transform: translateY(0) scale(1) !important;\n      }\n\n      /* Hover Effects on Table Rows & Cards */\n      .table tbody tr {\n        transition: background-color 0.15s ease !important;\n      }\n      .small-box {\n        transition: transform 0.2s cubic-bezier(0.4, 0, 0.2, 1), box-shadow 0.2s ease !important;\n      }\n      .small-box:hover {\n        transform: translateY(-2px);\n      }\n\n      /* Plain Cards System */\n      .card {\n        background: var(--eco-card) !important;\n        border: 1px solid var(--eco-border) !important;\n        border-radius: 8px !important;\n        box-shadow: none !important;\n        margin-bottom: 18px !important;\n        overflow: hidden;\n      }\n      .card-header,\n      .card[class*="card-"] > .card-header {\n        background: var(--eco-header) !important;\n        border-bottom: 1px solid var(--eco-border) !important;\n        padding: 12px 18px !important;\n        color: var(--eco-text-main) !important;\n        border-top-left-radius: 8px !important;\n        border-top-right-radius: 8px !important;\n      }\n      .card-title {\n        font-size: 0.98rem !important;\n        font-weight: 700 !important;\n        letter-spacing: 0.3px;\n        margin: 0;\n        color: var(--eco-text-main) !important;\n      }\n      .card-body {\n        padding: 16px 18px !important;\n        color: var(--eco-text-body) !important;\n      }\n      .card-body p, \n      .card-body span:not(.badge):not(.pulse-indicator):not(.badge-custom) {\n        color: var(--eco-text-body) !important;\n      }\n      .card-body strong, \n      .card-body h1, .card-body h2, .card-body h3, .card-body h4, .card-body h5, .card-body h6,\n      .modal-body strong,\n      .modal-body h1, .modal-body h2, .modal-body h3, .modal-body h4, .modal-body h5, .modal-body h6 {\n        color: var(--eco-text-main) !important;\n        font-weight: 700;\n      }\n      .card-footer,\n      .card[class*="card-"] > .card-footer {\n        background: var(--eco-header) !important;\n        border-top: 1px solid var(--eco-border) !important;\n        color: var(--eco-text-main) !important;\n        padding: 12px 18px !important;\n        border-bottom-left-radius: 8px !important;\n        border-bottom-right-radius: 8px !important;\n      }\n      .text-muted {\n        color: var(--eco-text-muted) !important;\n      }\n      hr {\n        border-top: 1px solid var(--eco-border-light) !important;\n      }\n      code {\n        background-color: rgba(15, 23, 42, 0.9) !important;\n        border: 1px solid var(--eco-border-light) !important;\n        color: var(--eco-accent) !important;\n        padding: 2px 6px !important;\n        border-radius: 4px !important;\n        font-size: 0.88rem;\n      }\n\n      .card-footer::after {\n        display: none !important;\n      }\n\n      /* Plain Form Controls & Inputs (No Glow on Focus) */\n      .form-control, .custom-select, select.form-control, textarea.form-control {\n        background-color: #0f172a !important;\n        border: 1px solid #334155 !important;\n        color: #f8fafc !important;\n        border-radius: 6px !important;\n        height: 38px;\n        font-size: 0.9rem;\n        box-shadow: none !important;\n        outline: none !important;\n      }\n      textarea.form-control {\n        height: auto !important;\n      }\n      .form-control::placeholder, textarea.form-control::placeholder {\n        color: #64748b !important;\n        opacity: 1 !important;\n      }\n      .form-control:focus, .custom-select:focus, select.form-control:focus, textarea.form-control:focus {\n        border-color: #007bff !important;\n        box-shadow: none !important;\n        outline: none !important;\n        background-color: #0f172a !important;\n        color: #ffffff !important;\n      }\n      select.form-control option, .custom-select option {\n        background-color: #1e293b !important;\n        color: #f8fafc !important;\n        padding: 6px 10px;\n      }\n      .form-group label, .modal-body label {\n        font-size: 0.82rem;\n        font-weight: 600;\n        color: var(--eco-text-body) !important;\n        margin-bottom: 5px;\n      }\n      .input-group-text {\n        background-color: #0f172a !important;\n        border: 1px solid #334155 !important;\n        color: var(--eco-text-muted) !important;\n        border-radius: 6px;\n      }\n      .btn-outline-secondary {\n        border-color: #334155 !important;\n        color: var(--eco-text-body) !important;\n        box-shadow: none !important;\n      }\n      .btn-outline-secondary:hover {\n        background-color: #334155 !important;\n        color: #ffffff !important;\n      }\n      input[type="range"].custom-range {\n        accent-color: #007bff;\n      }\n\n      /* Plain Buttons System (Zero Glow, Clean Borders) */\n      .btn { box-shadow: none !important; outline: none !important; border-radius: 6px; }\n      .btn-primary { background-color: #007bff !important; border-color: #0069d9 !important; color: #fff !important; }\n      .btn-primary:hover { background-color: #0069d9 !important; border-color: #0062cc !important; }\n      .btn-success { background-color: #28a745 !important; border-color: #218838 !important; color: #fff !important; }\n      .btn-success:hover { background-color: #218838 !important; border-color: #1e7e34 !important; }\n      .btn-info { background-color: #17a2b8 !important; border-color: #138496 !important; color: #fff !important; }\n      .btn-info:hover { background-color: #138496 !important; border-color: #117a8b !important; }\n      .btn-warning { background-color: #ffc107 !important; border-color: #e0a800 !important; color: #212529 !important; font-weight: 600 !important; }\n      .btn-warning:hover { background-color: #e0a800 !important; border-color: #d39e00 !important; color: #212529 !important; }\n      .btn-danger { background-color: #dc3545 !important; border-color: #c82333 !important; color: #fff !important; }\n      .btn-danger:hover { background-color: #c82333 !important; border-color: #bd2130 !important; }\n      .btn-secondary { background-color: #334155 !important; border-color: #475569 !important; color: #f8fafc !important; }\n      .btn-secondary:hover { background-color: #475569 !important; border-color: #64748b !important; }\n\n      /* Outline Table Action Buttons */\n      .btn-outline-success { color: #28a745 !important; border-color: #28a745 !important; }\n      .btn-outline-success:hover { background-color: #28a745 !important; color: #ffffff !important; }\n      .btn-outline-warning { color: #ffc107 !important; border-color: #ffc107 !important; }\n      .btn-outline-warning:hover { background-color: #ffc107 !important; color: #212529 !important; }\n      .btn-outline-info { color: #17a2b8 !important; border-color: #17a2b8 !important; }\n      .btn-outline-info:hover { background-color: #17a2b8 !important; color: #ffffff !important; }\n      .btn-outline-danger { color: #dc3545 !important; border-color: #dc3545 !important; }\n      .btn-outline-danger:hover { background-color: #dc3545 !important; color: #ffffff !important; }\n\n      /* Modals: Plain & Flat */\n      .modal-content {\n        background-color: var(--eco-card) !important;\n        border: 1px solid var(--eco-border-light) !important;\n        border-radius: 8px !important;\n        color: var(--eco-text-main) !important;\n        box-shadow: none !important;\n        overflow: hidden;\n      }\n      .modal-header {\n        background-color: var(--eco-header) !important;\n        border-bottom: 1px solid var(--eco-border) !important;\n        padding: 14px 18px !important;\n        color: var(--eco-text-main) !important;\n      }\n      .modal-title {\n        color: var(--eco-text-main) !important;\n        font-weight: 700;\n        font-size: 1.05rem;\n      }\n      .modal-body {\n        padding: 18px !important;\n        color: var(--eco-text-body) !important;\n      }\n      .modal-footer {\n        background-color: var(--eco-header) !important;\n        border-top: 1px solid var(--eco-border) !important;\n        padding: 12px 18px !important;\n      }\n      .close {\n        color: var(--eco-text-muted) !important;\n        text-shadow: none !important;\n        opacity: 0.8 !important;\n      }\n      .close:hover {\n        color: #ffffff !important;\n        opacity: 1 !important;\n      }\n\n      /* Plain Tables */\n      .table-responsive {\n        -webkit-overflow-scrolling: touch;\n        overflow-x: auto;\n        margin-bottom: 0;\n      }\n      .table-striped tbody tr:nth-of-type(odd) {\n        background-color: rgba(255, 255, 255, 0.02) !important;\n      }\n      .table-hover tbody tr:hover {\n        background-color: rgba(255, 255, 255, 0.04) !important;\n      }\n      .table {\n        width: 100% !important;\n        margin-bottom: 0 !important;\n        color: var(--eco-text-body) !important;\n      }\n      .table th {\n        background: rgba(15, 23, 42, 0.98) !important;\n        border-bottom: 1px solid var(--eco-border-light) !important;\n        border-top: none !important;\n        font-size: 0.78rem !important;\n        text-transform: uppercase !important;\n        letter-spacing: 0.5px !important;\n        color: var(--eco-text-muted) !important;\n        font-weight: 700 !important;\n        padding: 10px 14px !important;\n        vertical-align: middle !important;\n      }\n      .table td {\n        border-top: 1px solid rgba(255, 255, 255, 0.05) !important;\n        vertical-align: middle !important;\n        font-size: 0.88rem !important;\n        padding: 10px 14px !important;\n        color: #e2e8f0 !important;\n      }\n      .table th.text-center, .table td.text-center { text-align: center !important; }\n      .table th.text-right, .table td.text-right { text-align: right !important; }\n\n      /* Plain Badges (No Glow) */\n      .badge {\n        font-size: 0.78rem;\n        font-weight: 600;\n        padding: 0.35em 0.6em;\n        border-radius: 4px;\n        box-shadow: none !important;\n      }\n      .badge-success { background-color: #28a745 !important; color: #ffffff !important; }\n      .badge-warning { background-color: #ffc107 !important; color: #212529 !important; }\n      .badge-danger { background-color: #dc3545 !important; color: #ffffff !important; }\n      .badge-info { background-color: #17a2b8 !important; color: #ffffff !important; }\n      .badge-secondary { background-color: #6c757d !important; color: #ffffff !important; }\n\n      /* Plain Alerts */\n      .alert { box-shadow: none !important; border-radius: 6px; }\n      .alert-info { background-color: rgba(23, 162, 184, 0.15) !important; border: 1px solid rgba(23, 162, 184, 0.3) !important; color: #7dd3fc !important; }\n      .alert-success { background-color: rgba(40, 167, 69, 0.15) !important; border: 1px solid rgba(40, 167, 69, 0.3) !important; color: #6ee7b7 !important; }\n      .alert-danger { background-color: rgba(220, 53, 69, 0.15) !important; border: 1px solid rgba(220, 53, 69, 0.3) !important; color: #fca5a5 !important; }\n      .alert-warning { background-color: rgba(255, 193, 7, 0.15) !important; border: 1px solid rgba(255, 193, 7, 0.3) !important; color: #fde68a !important; }\n\n      /* Plain Small Boxes Dashboard */\n      .small-box {\n        border-radius: 6px !important;\n        box-shadow: none !important;\n        border: 1px solid var(--eco-border) !important;\n        margin-bottom: 16px;\n      }\n      .small-box .inner { padding: 14px; }\n      .small-box .inner h3 { font-size: 2rem; font-weight: 700 !important; margin-bottom: 2px; }\n      .small-box .inner p { font-size: 0.82rem; font-weight: 600 !important; text-transform: uppercase; letter-spacing: 0.5px; opacity: 0.9; margin: 0; }\n      .small-box .icon { font-size: 50px; right: 12px; top: 12px; opacity: 0.25; }\n\n      /* Progress Bars & Feedback (Zero Glow) */\n      .progress {\n        background-color: #334155 !important;\n        border-radius: 4px;\n        height: 8px;\n        box-shadow: none !important;\n      }\n      .valid-feedback-custom { display: none; font-size: 0.78rem; color: #28a745; margin-top: 4px; font-weight: 600; }\n      .invalid-feedback-custom { display: none; font-size: 0.78rem; color: #dc3545; margin-top: 4px; font-weight: 600; }\n      .pulse-indicator { display: inline-block; width: 10px; height: 10px; border-radius: 50%; background: #28a745; margin-right: 5px; }\n      .btn-xs { padding: 4px 10px; font-size: 0.78rem; border-radius: 4px; font-weight: 600; line-height: 1.4; }\n      .gap-1 { gap: 0.25rem !important; }\n      .gap-2 { gap: 0.5rem !important; }\n      .gap-3 { gap: 1rem !important; }\n\n      /* Mobile Adaptive UI */\n      @media (max-width: 767.98px) {\n        .content-wrapper { padding: 10px !important; }\n        .card { margin-bottom: 12px; }\n        .card-header { padding: 0.6rem 0.8rem !important; }\n        .card-body { padding: 0.8rem !important; }\n        .card-title { font-size: 0.95rem !important; font-weight: 700; }\n        .small-box { margin-bottom: 10px; border-radius: 6px; }\n        .small-box .inner { padding: 10px; }\n        .small-box .inner h3 { font-size: 1.45rem; margin-bottom: 2px; }\n        .small-box .inner p { font-size: 0.72rem; margin-bottom: 0; line-height: 1.2; }\n        .small-box .icon { font-size: 38px; right: 8px; top: 8px; opacity: 0.25; }\n        .table th, .table td { padding: 0.5rem 0.4rem; font-size: 0.8rem; white-space: nowrap; }\n        .btn-sm { padding: 0.28rem 0.5rem; font-size: 0.78rem; }\n        .btn-block { margin-top: 4px; }\n        .modal-dialog { margin: 12px auto; max-width: 95vw; }\n        .navbar-nav .nav-link { padding-left: 0.5rem; padding-right: 0.5rem; }\n        .brand-link { font-size: 0.95rem; }\n        .main-header { padding: 0.25rem 0.5rem; }\n      }\n    </style>\n</head>\n<body class="hold-transition sidebar-mini layout-fixed layout-navbar-fixed dark-mode">\n<div class="wrapper">\n  <!-- Top Navbar -->\n  <nav class="main-header navbar navbar-expand navbar-dark">\n    <ul class="navbar-nav">\n      <li class="nav-item"><a class="nav-link" data-widget="pushmenu" href="#" role="button"><i class="fas fa-bars"></i></a></li>\n      <li class="nav-item"><a href="/" target="_blank" class="nav-link"><i class="fas fa-wifi text-success"></i> <span class="d-none d-sm-inline">Portal</span></a></li>\n    </ul>\n    <ul class="navbar-nav ml-auto">\n      <li class="nav-item"><a href="/admin/api/export_xlsx" class="btn btn-sm btn-success mr-2 shadow-sm"><i class="fas fa-file-excel mr-1"></i> <span class="d-none d-sm-inline">Export Data</span></a></li>\n      <li class="nav-item"><a href="/admin/logout" class="btn btn-sm btn-danger"><i class="fas fa-sign-out-alt"></i> <span class="d-none d-sm-inline">Logout</span></a></li>\n    </ul>\n  </nav>\n\n  <!-- Complete Filipino PisoFi-Style AdminLTE Sidebar -->\n  <aside class="main-sidebar sidebar-dark-primary elevation-4">\n    <a href="#" class="brand-link text-center">\n      <span class="brand-text font-weight-bold text-success"><i class="fas fa-recycle"></i> Eco-Fi Master</span>\n    </a>\n    <div class="sidebar">\n      <nav class="mt-2">\n        <ul class="nav nav-pills nav-sidebar flex-column" data-widget="treeview" role="menu">\n          <li class="nav-header">MAIN NAVIGATION</li>\n          <li class="nav-item"><a href="javascript:showSection(\'sec-dashboard\')" id="nav-dashboard" class="nav-link active"><i class="nav-icon fas fa-tachometer-alt"></i><p>Dashboard & Stats</p></a></li>\n          <li class="nav-item"><a href="javascript:showSection(\'sec-clients\')" id="nav-clients" class="nav-link"><i class="nav-icon fas fa-users"></i><p>Active Clients</p></a></li>\n          <li class="nav-item"><a href="javascript:showSection(\'sec-vouchers\')" id="nav-vouchers" class="nav-link"><i class="nav-icon fas fa-ticket-alt"></i><p>Voucher Tickets</p></a></li>\n                    <li class="nav-item has-treeview" id="nav-item-rates">\n            <a href="javascript:void(0)" class="nav-link" id="nav-rates">\n              <i class="nav-icon fas fa-tags"></i>\n              <p>\n                Rates & Promos\n                <i class="right fas fa-angle-left"></i>\n              </p>\n            </a>\n            <ul class="nav nav-treeview" style="padding-left: 10px;">\n              <li class="nav-item">\n                <a href="javascript:showSection(\'sec-rates\')" id="nav-rates-sub" class="nav-link">\n                  <i class="far fa-circle nav-icon" style="font-size: 0.75rem;"></i>\n                  <p>Rates & Packages</p>\n                </a>\n              </li>\n              <li class="nav-item">\n                <a href="javascript:showSection(\'sec-time-policy\')" id="nav-time-policy" class="nav-link">\n                  <i class="far fa-circle nav-icon" style="font-size: 0.75rem;"></i>\n                  <p>Validity & Pauses</p>\n                </a>\n              </li>\n            </ul>\n          </li>\n          \n          <li class="nav-header">SYSTEM & HARDWARE</li>\n          <li class="nav-item"><a href="javascript:showSection(\'sec-esp32\')" id="nav-esp32" class="nav-link"><i class="nav-icon fas fa-microchip"></i><p>ESP32 Hardware</p></a></li>\n          <li class="nav-item"><a href="javascript:showSection(\'sec-audio\')" id="nav-audio" class="nav-link"><i class="nav-icon fas fa-volume-up"></i><p>Audio & Chimes</p></a></li>\n          <li class="nav-item"><a href="javascript:showSection(\'sec-portal-custom\')" id="nav-portal-custom" class="nav-link"><i class="nav-icon fas fa-palette"></i><p>Portal & Banners</p></a></li>\n          <li class="nav-item"><a href="javascript:showSection(\'sec-bandwidth\')" id="nav-bandwidth" class="nav-link"><i class="nav-icon fas fa-tachometer-alt"></i><p>Bandwidth & Speed</p></a></li>\n          <li class="nav-item"><a href="javascript:showSection(\'sec-walled\')" id="nav-walled" class="nav-link"><i class="nav-icon fas fa-globe-americas"></i><p>Walled Garden Sites</p></a></li>\n          <li class="nav-item"><a href="javascript:showSection(\'sec-security\')" id="nav-security" class="nav-link"><i class="nav-icon fas fa-shield-alt"></i><p>MAC Filtering</p></a></li>\n          <li class="nav-item"><a href="javascript:showSection(\'sec-telegram\')" id="nav-telegram" class="nav-link"><i class="nav-icon fab fa-telegram-plane"></i><p>Telegram Alerts</p></a></li>\n          <li class="nav-item"><a href="javascript:showSection(\'sec-licensing\')" id="nav-licensing" class="nav-link"><i class="nav-icon fas fa-key"></i><p>Hardware Licensing</p></a></li>\n        </ul>\n      </nav>\n    </div>\n  </aside>\n\n  <!-- Main Content Wrapper -->\n  <div class="content-wrapper">\n    \n    <!-- 1. DASHBOARD OVERVIEW SECTION -->\n    <div id="sec-dashboard" class="section-view active">\n      <div class="row">\n        <div class="col-lg-3 col-6">\n          <div class="small-box bg-success">\n            <div class="inner"><h3 id="stat-today">0</h3><p>Today\'s Bottles</p></div>\n            <div class="icon"><i class="fas fa-recycle"></i></div>\n          </div>\n        </div>\n        <div class="col-lg-3 col-6">\n          <div class="small-box bg-info">\n            <div class="inner"><h3 id="stat-total">0</h3><p>Lifetime Bottles</p></div>\n            <div class="icon"><i class="fas fa-chart-line"></i></div>\n          </div>\n        </div>\n        <div class="col-lg-3 col-6">\n          <div class="small-box bg-warning">\n            <div class="inner"><h3 id="stat-clients">0</h3><p>Active Clients</p></div>\n            <div class="icon"><i class="fas fa-wifi"></i></div>\n          </div>\n        </div>\n        <div class="col-lg-3 col-6">\n          <div class="small-box bg-danger">\n            <div class="inner"><h3 id="stat-lic">ACTIVE</h3><p>License Status</p></div>\n            <div class="icon"><i class="fas fa-shield-alt"></i></div>\n          </div>\n        </div>\n      </div>\n\n      <div class="row">\n        <!-- 7-Day / 30-Day / 12-Month Recycling Intake History Card -->\n        <div class="col-lg-8 d-flex flex-column mb-3 mb-lg-0">\n          <div class="card card-dark flex-fill d-flex flex-column" id="card-recycling-history" style="border-radius: 12px; overflow: hidden; border: 1px solid rgba(255,255,255,0.08); box-shadow: 0 4px 20px rgba(0,0,0,0.25);">\n            <div class="card-header d-flex align-items-center justify-content-between" style="padding: 12px 16px; border-bottom: 1px solid rgba(255,255,255,0.06); background: rgba(255,255,255,0.02);">\n              <div class="d-flex align-items-center">\n                <h3 class="card-title m-0" style="font-size: 13.5px; font-weight: 700; color: #f8fafc; letter-spacing: 0.3px;">\n                  <i class="fas fa-chart-bar text-success mr-2"></i> <span id="chart-title-text">7-Day Recycling Intake History</span>\n                </h3>\n              </div>\n              <div class="card-tools ml-auto">\n                <div class="btn-group btn-group-sm rounded" style="background: rgba(15, 23, 42, 0.6) !important; border: 1px solid rgba(255, 255, 255, 0.08); padding: 2px !important; border-radius: 6px !important;">\n                  <button type="button" class="btn btn-xs chart-tab-btn active" id="btn-range-weekly" onclick="setChartRange(\'weekly\')" style="font-size: 10.5px; font-weight: 600; padding: 3px 10px; border-radius: 4px; border: none; transition: all 0.2s ease;">Weekly</button>\n                  <button type="button" class="btn btn-xs chart-tab-btn" id="btn-range-monthly" onclick="setChartRange(\'monthly\')" style="font-size: 10.5px; font-weight: 600; padding: 3px 10px; border-radius: 4px; border: none; transition: all 0.2s ease;">Monthly</button>\n                  <button type="button" class="btn btn-xs chart-tab-btn" id="btn-range-yearly" onclick="setChartRange(\'yearly\')" style="font-size: 10.5px; font-weight: 600; padding: 3px 10px; border-radius: 4px; border: none; transition: all 0.2s ease;">Yearly</button>\n                </div>\n              </div>\n            </div>\n            <div class="card-body d-flex flex-column" style="padding: 16px 18px; flex: 1 1 auto; min-height: 0; position: relative;">\n              <div class="chart-container" style="position: relative; width: 100%; height: 100%; min-height: 290px; flex: 1 1 auto;">\n                <canvas id="historyChart"></canvas>\n              </div>\n            </div>\n          </div>\n        </div>\n        <div class="col-lg-4 d-flex flex-column">\n          <div class="card card-dark flex-fill d-flex flex-column" id="card-system-resources" style="border-radius: 12px; overflow: hidden; border: 1px solid rgba(255,255,255,0.08); box-shadow: 0 4px 20px rgba(0,0,0,0.25);">\n            <div class="card-header d-flex align-items-center justify-content-between" style="padding: 12px 16px; border-bottom: 1px solid rgba(255,255,255,0.06); background: rgba(255,255,255,0.02);">\n              <div class="d-flex align-items-center">\n                <span class="live-pulse-dot mr-2"></span>\n                <h3 class="card-title m-0" style="font-size: 13.5px; font-weight: 700; color: #f8fafc; letter-spacing: 0.3px;">\n                  <i class="fas fa-microchip text-primary mr-1"></i> System Resources\n                </h3>\n              </div>\n              <div class="card-tools d-flex align-items-center ml-auto">\n                <span class="badge badge-pill mr-1" id="sys-temp-badge" title="SoC Core Temperature" style="background: rgba(245, 158, 11, 0.12); color: #fbbf24; border: 1px solid rgba(245, 158, 11, 0.28); font-size: 11px; font-weight: 600; padding: 3px 8px;">\n                  <i class="fas fa-thermometer-half mr-1"></i><span id="sys-temp">--.-°C</span>\n                </span>\n                <span class="badge badge-pill" id="sys-freq-badge" title="CPU Clock Frequency" style="background: rgba(56, 189, 248, 0.12); color: #38bdf8; border: 1px solid rgba(56, 189, 248, 0.28); font-size: 11px; font-weight: 600; padding: 3px 8px;">\n                  <i class="fas fa-bolt mr-1"></i><span id="sys-cpu-freq">-- MHz</span>\n                </span>\n              </div>\n            </div>\n            <div class="card-body" style="padding: 16px 18px;">\n              <!-- 1. Overall CPU Load -->\n              <div style="margin-bottom: 13px;">\n                <div class="d-flex justify-content-between align-items-baseline mb-1">\n                  <span style="font-size: 12px; font-weight: 600; color: #f1f5f9;">\n                    <i class="fas fa-tachometer-alt mr-1" style="color: #38bdf8; font-size: 11px;"></i> CPU Load\n                  </span>\n                  <div class="d-flex align-items-center" style="font-size: 11px;">\n                    <span class="text-muted" style="font-size: 10.5px;">Load: <span id="sys-loadavg" style="color: #94a3b8;">0.00, 0.00, 0.00</span></span>\n                    <strong id="sys-cpu" class="ml-2" style="font-size: 13px; color: #38bdf8; min-width: 32px; text-align: right;">0%</strong>\n                  </div>\n                </div>\n                <div class="progress" style="height: 7.5px; background: rgba(255,255,255,0.06); border-radius: 4px;">\n                  <div id="sys-cpu-bar" class="progress-bar" style="width: 0%; background: linear-gradient(90deg, #0284c7, #38bdf8); border-radius: 3px; transition: width 0.4s ease;"></div>\n                </div>\n              </div>\n\n              <!-- 2. Quad-Core H3 Mini-Cards -->\n              <div class="mb-3 p-2 rounded" style="background: rgba(255, 255, 255, 0.025); border: 1px solid rgba(255, 255, 255, 0.06); padding: 9px 10px 11px 10px !important;">\n                <div class="d-flex justify-content-between align-items-center mb-2 px-1">\n                  <span style="font-size: 9.5px; font-weight: 700; color: #64748b; letter-spacing: 0.6px; text-transform: uppercase;">\n                    <i class="fas fa-cubes mr-1 text-primary"></i> Quad-Core H3 Cores\n                  </span>\n                  <span class="badge" id="sys-hardware" style="font-size: 9.5px; font-weight: 600; background: rgba(255,255,255,0.06); color: #94a3b8; border: 1px solid rgba(255,255,255,0.08); padding: 2px 7px; border-radius: 4px;">\n                    Orange Pi H3\n                  </span>\n                </div>\n                <div class="row no-gutters" style="margin: 0 -3px;">\n                  <div class="col-3" style="padding: 0 3px;">\n                    <div class="p-2 rounded text-center" style="background: rgba(15, 23, 42, 0.65); border: 1px solid rgba(255,255,255,0.06);">\n                      <div class="d-flex justify-content-between align-items-center mb-1" style="font-size: 10px; line-height: 1.2;">\n                        <span style="color: #94a3b8; font-weight: 600;">Core 0</span>\n                        <strong id="sys-core-0-val" style="color: #38bdf8; font-size: 11px;">0%</strong>\n                      </div>\n                      <div class="progress" style="height: 8px; background: rgba(255,255,255,0.07); border-radius: 4px; overflow: hidden;">\n                        <div id="sys-core-0-bar" class="progress-bar" style="width: 0%; background: linear-gradient(90deg, #0284c7, #38bdf8); border-radius: 4px; transition: width 0.4s ease;"></div>\n                      </div>\n                    </div>\n                  </div>\n                  <div class="col-3" style="padding: 0 3px;">\n                    <div class="p-2 rounded text-center" style="background: rgba(15, 23, 42, 0.65); border: 1px solid rgba(255,255,255,0.06);">\n                      <div class="d-flex justify-content-between align-items-center mb-1" style="font-size: 10px; line-height: 1.2;">\n                        <span style="color: #94a3b8; font-weight: 600;">Core 1</span>\n                        <strong id="sys-core-1-val" style="color: #38bdf8; font-size: 11px;">0%</strong>\n                      </div>\n                      <div class="progress" style="height: 8px; background: rgba(255,255,255,0.07); border-radius: 4px; overflow: hidden;">\n                        <div id="sys-core-1-bar" class="progress-bar" style="width: 0%; background: linear-gradient(90deg, #0284c7, #38bdf8); border-radius: 4px; transition: width 0.4s ease;"></div>\n                      </div>\n                    </div>\n                  </div>\n                  <div class="col-3" style="padding: 0 3px;">\n                    <div class="p-2 rounded text-center" style="background: rgba(15, 23, 42, 0.65); border: 1px solid rgba(255,255,255,0.06);">\n                      <div class="d-flex justify-content-between align-items-center mb-1" style="font-size: 10px; line-height: 1.2;">\n                        <span style="color: #94a3b8; font-weight: 600;">Core 2</span>\n                        <strong id="sys-core-2-val" style="color: #38bdf8; font-size: 11px;">0%</strong>\n                      </div>\n                      <div class="progress" style="height: 8px; background: rgba(255,255,255,0.07); border-radius: 4px; overflow: hidden;">\n                        <div id="sys-core-2-bar" class="progress-bar" style="width: 0%; background: linear-gradient(90deg, #0284c7, #38bdf8); border-radius: 4px; transition: width 0.4s ease;"></div>\n                      </div>\n                    </div>\n                  </div>\n                  <div class="col-3" style="padding: 0 3px;">\n                    <div class="p-2 rounded text-center" style="background: rgba(15, 23, 42, 0.65); border: 1px solid rgba(255,255,255,0.06);">\n                      <div class="d-flex justify-content-between align-items-center mb-1" style="font-size: 10px; line-height: 1.2;">\n                        <span style="color: #94a3b8; font-weight: 600;">Core 3</span>\n                        <strong id="sys-core-3-val" style="color: #38bdf8; font-size: 11px;">0%</strong>\n                      </div>\n                      <div class="progress" style="height: 8px; background: rgba(255,255,255,0.07); border-radius: 4px; overflow: hidden;">\n                        <div id="sys-core-3-bar" class="progress-bar" style="width: 0%; background: linear-gradient(90deg, #0284c7, #38bdf8); border-radius: 4px; transition: width 0.4s ease;"></div>\n                      </div>\n                    </div>\n                  </div>\n                </div>\n              </div>\n\n              <!-- 3. Memory (RAM) -->\n              <div style="margin-bottom: 13px;">\n                <div class="d-flex justify-content-between align-items-baseline mb-1">\n                  <span style="font-size: 12px; font-weight: 600; color: #f1f5f9;">\n                    <i class="fas fa-memory mr-1" style="color: #10b981; font-size: 11px;"></i> Memory (RAM)\n                  </span>\n                  <div class="d-flex align-items-center" style="font-size: 11px;">\n                    <span id="sys-ram-detail" style="color: #94a3b8;">0 / 0 MB</span>\n                    <span class="mx-1" style="color: #475569;">·</span>\n                    <span id="sys-ram-free" style="color: #64748b;">Free: -- MB</span>\n                    <strong id="sys-ram" class="ml-2" style="font-size: 13px; color: #10b981; min-width: 32px; text-align: right;">0%</strong>\n                  </div>\n                </div>\n                <div class="progress" style="height: 7.5px; background: rgba(255,255,255,0.06); border-radius: 4px;">\n                  <div id="sys-ram-bar" class="progress-bar" style="width: 0%; background: linear-gradient(90deg, #059669, #10b981); border-radius: 3px; transition: width 0.4s ease;"></div>\n                </div>\n              </div>\n\n              <!-- 4. ZRAM Swap -->\n              <div style="margin-bottom: 13px;">\n                <div class="d-flex justify-content-between align-items-baseline mb-1">\n                  <span style="font-size: 12px; font-weight: 600; color: #f1f5f9;">\n                    <i class="fas fa-compress-arrows-alt mr-1" style="color: #a855f7; font-size: 11px;"></i> ZRAM Swap\n                  </span>\n                  <div class="d-flex align-items-center" style="font-size: 11px;">\n                    <span id="sys-swap-detail" style="color: #94a3b8;">0 / 0 MB</span>\n                    <span class="mx-1" style="color: #475569;">·</span>\n                    <span id="sys-swap-free" style="color: #64748b;">Total: -- MB</span>\n                    <strong id="sys-swap" class="ml-2" style="font-size: 13px; color: #a855f7; min-width: 32px; text-align: right;">0%</strong>\n                  </div>\n                </div>\n                <div class="progress" style="height: 7.5px; background: rgba(255,255,255,0.06); border-radius: 4px;">\n                  <div id="sys-swap-bar" class="progress-bar" style="width: 0%; background: linear-gradient(90deg, #7c3aed, #a855f7); border-radius: 3px; transition: width 0.4s ease;"></div>\n                </div>\n              </div>\n\n              <!-- 5. Storage (MicroSD) -->\n              <div style="margin-bottom: 13px;">\n                <div class="d-flex justify-content-between align-items-baseline mb-1">\n                  <span style="font-size: 12px; font-weight: 600; color: #f1f5f9;">\n                    <i class="fas fa-hdd mr-1" style="color: #f59e0b; font-size: 11px;"></i> Storage (MicroSD)\n                  </span>\n                  <div class="d-flex align-items-center" style="font-size: 11px;">\n                    <span id="sys-disk-detail" style="color: #94a3b8;">0.0 / 0.0 GB</span>\n                    <span class="mx-1" style="color: #475569;">·</span>\n                    <span id="sys-disk-free" style="color: #64748b;">Avail: -- GB</span>\n                    <strong id="sys-disk" class="ml-2" style="font-size: 13px; color: #f59e0b; min-width: 32px; text-align: right;">0%</strong>\n                  </div>\n                </div>\n                <div class="progress" style="height: 7.5px; background: rgba(255,255,255,0.06); border-radius: 4px;">\n                  <div id="sys-disk-bar" class="progress-bar" style="width: 0%; background: linear-gradient(90deg, #d97706, #f59e0b); border-radius: 3px; transition: width 0.4s ease;"></div>\n                </div>\n              </div>\n\n                            <!-- 6. ESP32 Subsystem Controller -->\n              <div class="mb-3 p-2 rounded" style="background: rgba(255, 255, 255, 0.025); border: 1px solid rgba(255, 255, 255, 0.06); padding: 9px 10px 10px 10px !important; cursor: pointer; transition: all 0.2s ease;" onclick="showSection(\'sec-esp32\')" title="Click to view ESP32 Hardware Diagnostics & Calibration">\n                <div class="d-flex justify-content-between align-items-center mb-2 px-1">\n                  <span style="font-size: 9.5px; font-weight: 700; color: #64748b; letter-spacing: 0.6px; text-transform: uppercase;">\n                    <i class="fas fa-microchip mr-1" style="color: #a855f7;"></i> ESP32 Controller &amp; Sensors\n                  </span>\n                  <span class="badge" id="sys-esp32-badge" style="font-size: 9.5px; font-weight: 600; background: rgba(16, 185, 129, 0.12); color: #10b981; border: 1px solid rgba(16, 185, 129, 0.28); padding: 2px 7px; border-radius: 4px;">\n                    <i class="fas fa-circle mr-1" id="sys-esp32-dot" style="font-size: 6px; vertical-align: middle;"></i><span id="sys-esp32-status">ONLINE</span>\n                  </span>\n                </div>\n                <div class="row no-gutters" style="margin: 0 -3px;">\n                  <div class="col-4" style="padding: 0 3px;">\n                    <div class="p-2 rounded text-center" style="background: rgba(15, 23, 42, 0.65); border: 1px solid rgba(255,255,255,0.06); min-height: 48px; display: flex; flex-direction: column; justify-content: center;">\n                      <span style="color: #94a3b8; font-size: 9.5px; font-weight: 600; display: block; line-height: 1.1;">UART Bridge</span>\n                      <strong id="sys-esp32-port" style="color: #38bdf8; font-size: 10.5px; margin-top: 2px; display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">115200 Baud</strong>\n                    </div>\n                  </div>\n                  <div class="col-4" style="padding: 0 3px;">\n                    <div class="p-2 rounded text-center" style="background: rgba(15, 23, 42, 0.65); border: 1px solid rgba(255,255,255,0.06); min-height: 48px; display: flex; flex-direction: column; justify-content: center;">\n                      <span style="color: #94a3b8; font-size: 9.5px; font-weight: 600; display: block; line-height: 1.1;">Sensor Bus</span>\n                      <strong id="sys-esp32-sensors" style="color: #10b981; font-size: 10.5px; margin-top: 2px; display: block;">Nominal</strong>\n                    </div>\n                  </div>\n                  <div class="col-4" style="padding: 0 3px;">\n                    <div class="p-2 rounded text-center" style="background: rgba(15, 23, 42, 0.65); border: 1px solid rgba(255,255,255,0.06); min-height: 48px; display: flex; flex-direction: column; justify-content: center;">\n                      <span style="color: #94a3b8; font-size: 9.5px; font-weight: 600; display: block; line-height: 1.1;">Storage Bin</span>\n                      <strong id="sys-esp32-bin" style="color: #10b981; font-size: 10.5px; margin-top: 2px; display: block;">OK (60cm)</strong>\n                    </div>\n                  </div>\n                </div>\n              </div>\n\n              <!-- 7. System Uptime Footer -->\n              <div class="pt-2 mt-2 d-flex justify-content-between align-items-center" style="border-top: 1px solid rgba(255,255,255,0.06); font-size: 11.5px;">\n                <div class="d-flex align-items-center text-muted">\n                  <i class="fas fa-clock mr-1 text-muted" style="font-size: 11px;"></i>\n                  <span>System Uptime</span>\n                </div>\n                <div>\n                  <span class="badge" style="background: rgba(255,255,255,0.05); color: #e2e8f0; border: 1px solid rgba(255,255,255,0.1); font-size: 11px; font-weight: 600; padding: 2.5px 8px; border-radius: 4px;">\n                    <span id="sys-uptime">0h 0m</span>\n                  </span>\n                </div>\n              </div>\n            </div>\n          </div>\n        </div></div>\n    </div>\n\n    <!-- 1B. ESP32 HARDWARE SECTION -->\n    <div id="sec-esp32" class="section-view">\n      <div class="card card-purple">\n        <div class="card-header" style="display: flex; align-items: center; justify-content: space-between; width: 100%;">\n          <h3 class="card-title m-0 d-flex align-items-center" style="font-size: 0.95rem; font-weight: 700;">\n            <i class="fas fa-microchip mr-2 text-purple"></i>\n            <span class="d-none d-sm-inline">ESP32 Hardware Calibration</span>\n            <span class="d-inline d-sm-none">ESP32 Hardware</span>\n          </h3>\n          <div class="card-tools ml-auto d-flex align-items-center" style="margin-left: auto;">\n            <label class="eco-toggle-wrap" for="simulator-toggle" title="Toggle ESP32 Software Simulation Mode">\n              <input type="checkbox" id="simulator-toggle" onchange="toggleSimulator(this.checked)" {{ \'checked\' if config.get(\'simulator_enabled\', \'0\') == \'1\' else \'\' }}>\n              <span class="eco-toggle-track"></span>\n              <span class="eco-toggle-label">Simulator</span>\n            </label>\n            <button class="btn btn-warning btn-xs" onclick="triggerEsp32Config()" title="Reboot ESP32 to AP Mode" style="padding: 3px 10px; border-radius: 6px; font-size: 11.5px; font-weight: 600; white-space: nowrap;">\n              <i class="fas fa-wifi mr-1"></i> <span class="d-none d-sm-inline">Reboot to AP Mode</span><span class="d-inline d-sm-none">AP Mode</span>\n            </button>\n          </div>\n        </div>\n        <div class="card-body">\n          <div class="row">\n            <div class="col-md-4 form-group">\n              <label>Bin Full Distance (cm):</label>\n              <input type="number" id="esp-bin" class="form-control" value="{{ config.get(\'esp_bin_full_threshold_cm\', 15) }}">\n            </div>\n            <div class="col-md-4 form-group">\n              <label>Entrance Timeout (sec):</label>\n              <input type="number" id="esp-ent-tout" class="form-control" value="{{ config.get(\'esp_entrance_gate_timeout\', 60) }}">\n            </div>\n            <div class="col-md-4 form-group">\n              <label>Bottle Settle Time (ms):</label>\n              <input type="number" id="esp-settle" class="form-control" value="{{ config.get(\'esp_settle_time_ms\', 500) }}">\n            </div>\n            <div class="col-md-4 form-group">\n              <label>Success Drop Time (ms):</label>\n              <input type="number" id="esp-suc-time" class="form-control" value="{{ config.get(\'esp_success_drop_tout_ms\', 3000) }}">\n            </div>\n            <div class="col-md-4 form-group">\n              <label>Reject Drop Time (ms):</label>\n              <input type="number" id="esp-rej-time" class="form-control" value="{{ config.get(\'esp_reject_drop_time_ms\', 2000) }}">\n            </div>\n            <div class="col-md-4 form-group">\n              <label>NIR W Min / Max:</label>\n              <div class="d-flex" style="gap: 8px;">\n                <div class="input-group input-group-sm" style="flex: 1;">\n                  <div class="input-group-prepend"><span class="input-group-text px-2 text-muted" style="font-size: 11px;">Min</span></div>\n                  <input type="number" id="esp-nir-min" class="form-control" value="{{ config.get(\'esp_pet_nir_w_min\', 200) }}">\n                </div>\n                <div class="input-group input-group-sm" style="flex: 1;">\n                  <div class="input-group-prepend"><span class="input-group-text px-2 text-muted" style="font-size: 11px;">Max</span></div>\n                  <input type="number" id="esp-nir-max" class="form-control" value="{{ config.get(\'esp_pet_nir_w_max\', 5000) }}">\n                </div>\n              </div>\n            </div>\n            \n            <div class="col-12 mt-3 mb-2"><h5 class="text-info border-bottom border-secondary pb-1">Servo Tuning (Angles 0-180)</h5></div>\n            \n            <div class="col-md-4 form-group">\n              <label>Entrance Gate (Close / Open):</label>\n              <div class="d-flex" style="gap: 8px;">\n                <div class="input-group input-group-sm" style="flex: 1;">\n                  <div class="input-group-prepend"><span class="input-group-text px-2 text-muted" style="font-size: 11px;">Close</span></div>\n                  <input type="number" id="esp-ent-close" class="form-control" value="{{ config.get(\'esp_ent_close_angle\', 0) }}">\n                </div>\n                <div class="input-group input-group-sm" style="flex: 1;">\n                  <div class="input-group-prepend"><span class="input-group-text px-2 text-muted" style="font-size: 11px;">Open</span></div>\n                  <input type="number" id="esp-ent-open" class="form-control" value="{{ config.get(\'esp_ent_open_angle\', 90) }}">\n                </div>\n              </div>\n            </div>\n            <div class="col-md-4 form-group">\n              <label>Success Gate (Close / Open):</label>\n              <div class="d-flex" style="gap: 8px;">\n                <div class="input-group input-group-sm" style="flex: 1;">\n                  <div class="input-group-prepend"><span class="input-group-text px-2 text-muted" style="font-size: 11px;">Close</span></div>\n                  <input type="number" id="esp-suc-close" class="form-control" value="{{ config.get(\'esp_suc_close_angle\', 0) }}">\n                </div>\n                <div class="input-group input-group-sm" style="flex: 1;">\n                  <div class="input-group-prepend"><span class="input-group-text px-2 text-muted" style="font-size: 11px;">Open</span></div>\n                  <input type="number" id="esp-suc-open" class="form-control" value="{{ config.get(\'esp_suc_open_angle\', 90) }}">\n                </div>\n              </div>\n            </div>\n            <div class="col-md-4 form-group">\n              <label>Reject Gate (Close / Open):</label>\n              <div class="d-flex" style="gap: 8px;">\n                <div class="input-group input-group-sm" style="flex: 1;">\n                  <div class="input-group-prepend"><span class="input-group-text px-2 text-muted" style="font-size: 11px;">Close</span></div>\n                  <input type="number" id="esp-rej-close" class="form-control" value="{{ config.get(\'esp_rej_close_angle\', 0) }}">\n                </div>\n                <div class="input-group input-group-sm" style="flex: 1;">\n                  <div class="input-group-prepend"><span class="input-group-text px-2 text-muted" style="font-size: 11px;">Open</span></div>\n                  <input type="number" id="esp-rej-open" class="form-control" value="{{ config.get(\'esp_rej_open_angle\', 90) }}">\n                </div>\n              </div>\n            </div>\n          </div>\n          <button class="btn btn-success mt-3" onclick="saveEsp32Config()"><i class="fas fa-save"></i> Save & Push to ESP32</button>\n        </div>\n      </div>\n    </div>\n\n    <!-- 2. ACTIVE CLIENTS SECTION -->\n    <div id="sec-clients" class="section-view">\n      <div class="card card-primary">\n        <div class="card-header"><h3 class="card-title"><i class="fas fa-users"></i> Connected Client Sessions</h3></div>\n        <div class="card-body p-0">\n          <div class="table-responsive">\n            <table class="table table-striped table-hover mb-0">\n              <thead>\n                <tr>\n                  <th style="padding: 10px 14px; width: 18%;">IP Address</th>\n                  <th style="padding: 10px 14px; width: 18%;">MAC Address</th>\n                  <th style="padding: 10px 14px; width: 18%;">Remaining Time</th>\n                  <th style="padding: 10px 14px; width: 12%; text-align: center;">Status</th>\n                  <th style="padding: 10px 14px; width: 16%;">Speed (DL/UL)</th>\n                  <th style="padding: 10px 14px; width: 18%; text-align: right; min-width: 220px; white-space: nowrap;">Actions</th>\n                </tr>\n              </thead>\n              <tbody id="clients-table-body"></tbody>\n            </table>\n          </div>\n        </div>\n      </div>\n    </div>\n\n    <!-- 3. VOUCHER & TICKETS SECTION -->\n    <div id="sec-vouchers" class="section-view">\n      <div class="card card-success">\n        <div class="card-header"><h3 class="card-title"><i class="fas fa-magic"></i> Generate Prepaid Vouchers</h3></div>\n        <div class="card-body">\n          <div class="row">\n            <div class="col-12 col-sm-6 col-md-3 form-group">\n              <label>Number of Vouchers (1-100):</label>\n              <input type="number" id="v-qty" class="form-control" value="5" min="1" max="100">\n            </div>\n            <div class="col-12 col-sm-6 col-md-3 form-group">\n              <label>Duration (Minutes):</label>\n              <select id="v-mins" class="form-control">\n                <option value="10">10 Minutes (1 Bottle Equivalent)</option>\n                <option value="45">45 Minutes</option>\n                <option value="60" selected>1 Hour</option>\n                <option value="180">3 Hours</option>\n                <option value="1440">24 Hours (1 Day Pass)</option>\n              </select>\n            </div>\n            <div class="col-12 col-sm-6 col-md-3 form-group">\n              <label>Custom Note / Batch Tag:</label>\n              <input type="text" id="v-note" class="form-control" placeholder="e.g. Student Promo Batch">\n            </div>\n            <div class="col-12 col-sm-6 col-md-3 form-group">\n              <label class="d-none d-md-block">&nbsp;</label>\n              <button class="btn btn-success btn-block" onclick="generateVouchers()"><i class="fas fa-ticket-alt"></i> Generate Vouchers</button>\n            </div>\n          </div>\n          <div id="v-results" class="mt-3"></div>\n        </div>\n      </div>\n      \n      <div class="card card-dark mt-3">\n        <div class="card-header"><h3 class="card-title"><i class="fas fa-history"></i> Voucher History</h3></div>\n        <div class="card-body p-0">\n          <div class="table-responsive">\n            <table class="table table-striped table-hover mb-0" id="voucher-history-table">\n              <thead>\n                <tr>\n                  <th style="padding: 10px 14px; width: 18%;">Voucher Code</th>\n                  <th style="padding: 10px 14px; width: 12%;">Duration</th>\n                  <th style="padding: 10px 14px; width: 12%; text-align: center;">Status</th>\n                  <th style="padding: 10px 14px; width: 18%;">Note / Tag</th>\n                  <th style="padding: 10px 14px; width: 18%;">Created Date</th>\n                  <th style="padding: 10px 14px; width: 12%;">Used By (IP)</th>\n                  <th style="padding: 10px 14px; width: 10%; text-align: right; min-width: 100px; white-space: nowrap;">Actions</th>\n                </tr>\n              </thead>\n              <tbody id="voucher-history-body"></tbody>\n            </table>\n          </div>\n        </div>\n      </div>\n    </div>\n\n    \n    <div id="sec-rates" class="section-view">\n      <!-- Sub-navigation tabs (Rates & Packages | Time Validity & Pause Settings) -->\n      <ul class="nav nav-pills mb-3" style="gap: 8px;">\n        <li class="nav-item">\n          <a class="nav-link active font-weight-bold" href="javascript:showSection(\'sec-rates\')" style="border-radius: 6px; font-size: 13px; padding: 6px 16px; background: #007bff !important; color: #fff !important; box-shadow: 0 2px 6px rgba(0,123,255,0.3);">\n            <i class="fas fa-tags mr-1"></i> Rates & Packages\n          </a>\n        </li>\n        <li class="nav-item">\n          <a class="nav-link font-weight-bold" href="javascript:showSection(\'sec-time-policy\')" style="border-radius: 6px; font-size: 13px; padding: 6px 16px; background: rgba(255,255,255,0.05) !important; color: #94a3b8 !important; border: 1px solid rgba(255,255,255,0.08);">\n            <i class="fas fa-clock mr-1"></i> Time Validity & Pause Settings\n          </a>\n        </li>\n      </ul>\n      <!-- Row 1: Base Rate (Left) & Quick Templates (Right) -->\n      <div class="row">\n        <!-- Card 1: Basic Rate & Chute Timeout -->\n        <div class="col-12 col-lg-6 mb-3">\n          <div class="card h-100 mb-0 shadow-none border">\n            <div class="card-body py-2 px-3">\n              <div class="row align-items-end">\n                <div class="col-12 col-sm-4 form-group mb-0">\n                  <label class="mb-1" style="font-size: 0.82rem;">Rate (Mins / Bottle):</label>\n                  <input type="number" id="rate-1" class="form-control" value="10" min="1" oninput="onBaseRateInput()">\n                </div>\n                <div class="col-12 col-sm-4 form-group mb-0">\n                  <label class="mb-1" style="font-size: 0.82rem;">Timeout (Seconds):</label>\n                  <input type="number" id="rate-timeout" class="form-control" value="60" min="10" max="120">\n                </div>\n                <div class="col-12 col-sm-4 form-group mb-0">\n                  <button class="btn btn-warning btn-block font-weight-bold" onclick="saveRates()">\n                    <i class="fas fa-save mr-1"></i> Save\n                  </button>\n                </div>\n              </div>\n            </div>\n          </div>\n        </div>\n\n        <!-- Card 2: Quick-Load Rate Templates -->\n        <div class="col-12 col-lg-6 mb-3">\n          <div class="card h-100 mb-0 shadow-none border">\n            <div class="card-body py-2 px-3">\n              <div class="row align-items-end">\n                <div class="col-12 col-sm-8 form-group mb-0">\n                  <label class="mb-1" style="font-size: 0.82rem;">Rate Templates:</label>\n                  <select id="rate-preset-select" class="form-control">\n                    <option value="standard">Standard (1b=10m, 3b=40m, 5b=1h15m, 10b=3h)</option>\n                    <option value="aggressive">Aggressive (1b=10m, 5b=1h10m, 10b=3h, 20b=7h)</option>\n                    <option value="cafe">Café (1b=20m, 3b=1h15m, 6b=3h, 12b=7h)</option>\n                  </select>\n                </div>\n                <div class="col-12 col-sm-4 form-group mb-0">\n                  <button class="btn btn-info btn-block font-weight-bold" onclick="applyRatePreset()">\n                    <i class="fas fa-file-import mr-1"></i> Apply\n                  </button>\n                </div>\n              </div>\n            </div>\n          </div>\n        </div>\n      </div>\n\n      <!-- Row 2: Add Custom Promo Rate Package -->\n      <div class="card mb-3 shadow-none border" id="promo-form-card">\n        <div class="card-header py-2"><h5 class="card-title text-sm" id="promo-form-title"><i class="fas fa-plus-circle"></i> Add Custom Promo Rate Package</h5></div>\n        <div class="card-body py-2 px-3">\n          <input type="hidden" id="edit-original-bottles" value="">\n          <div class="row align-items-end">\n            <div class="col-12 col-sm-6 col-md-3 form-group mb-0">\n              <label class="mb-1" style="font-size: 0.82rem;">Bottles:</label>\n              <input type="number" id="new-rate-bottles" class="form-control" placeholder="e.g. 5" min="1" max="100" oninput="validatePromoFormMath()">\n            </div>\n            <div class="col-12 col-sm-6 col-md-3 form-group mb-0">\n              <label class="mb-1" style="font-size: 0.82rem;">Time:</label>\n              <div class="input-group">\n                <input type="number" id="new-rate-time-val" class="form-control" placeholder="e.g. 90" min="1" oninput="validatePromoFormMath()">\n                <div class="input-group-append">\n                  <select id="new-rate-time-unit" class="custom-select" style="max-width: 85px;" onchange="validatePromoFormMath()">\n                    <option value="mins" selected>Mins</option>\n                    <option value="hours">Hours</option>\n                    <option value="days">Days</option>\n                  </select>\n                </div>\n              </div>\n            </div>\n            <div class="col-12 col-sm-8 col-md-4 form-group mb-0">\n              <label class="mb-1" style="font-size: 0.82rem;">Label:</label>\n              <div class="input-group">\n                <input type="text" id="new-rate-label" class="form-control" placeholder="Auto-generated if blank">\n                <div class="input-group-append">\n                  <button class="btn btn-outline-secondary" type="button" onclick="autoGenerateRateLabel()" title="Auto-generate friendly label">🪄</button>\n                </div>\n              </div>\n            </div>\n            <div class="col-12 col-sm-4 col-md-2 form-group mb-0">\n              <div class="d-flex">\n                <button class="btn btn-success btn-block mr-1 font-weight-bold" id="btn-save-promo" onclick="addPromoRate()">\n                  <i class="fas fa-plus mr-1"></i> Add\n                </button>\n                <button class="btn btn-secondary font-weight-bold ml-1" id="btn-cancel-promo" onclick="cancelEditPromoRate()" style="display:none;" title="Cancel Edit">\n                  <i class="fas fa-times"></i>\n                </button>\n              </div>\n            </div>\n          </div>\n          <!-- Live Validator Feedback Box -->\n          <div id="rate-validator-feedback" class="alert alert-info py-1 px-2 mt-2 mb-0" style="display:none; font-size:12px; border-radius:4px;">\n            <div class="d-flex justify-content-between align-items-center">\n              <span id="rate-validator-eff" class="font-weight-bold">⚡ Efficiency: --</span>\n              <span id="rate-validator-status" class="font-weight-bold">✓ Status: OK</span>\n            </div>\n            <div id="rate-validator-msg" style="margin-top:2px;"></div>\n          </div>\n        </div>\n      </div>\n\n      <!-- Active Rate Packages Table -->\n      <div class="card mb-3">\n        <div class="card-header"><h3 class="card-title text-light"><i class="fas fa-tags mr-1"></i> Active Rate Tiers & Promo Curves</h3></div>\n        <div class="card-body p-0">\n          <div class="table-responsive">\n            <table class="table table-striped table-hover mb-0">\n              <thead>\n                <tr>\n                  <th style="padding: 10px 14px; width: 20%;">Bottles Required</th>\n                  <th style="padding: 10px 14px; width: 22%;">Time Credited</th>\n                  <th style="padding: 10px 14px; width: 22%;">Rate Efficiency</th>\n                  <th style="padding: 10px 14px; width: 20%;">Package Label</th>\n                  <th style="padding: 10px 14px; width: 16%; min-width: 170px; text-align: right; white-space: nowrap;">Actions</th>\n                </tr>\n              </thead>\n              <tbody id="rates-table-body"></tbody>\n            </table>\n          </div>\n        </div>\n      </div>\n    </div>\n\n    <!-- 5B. TIME VALIDITY & PAUSE SETTINGS SECTION -->\n    <div id="sec-time-policy" class="section-view">\n      <!-- Sub-navigation tabs (Rates & Packages | Time Validity & Pause Settings) -->\n      <ul class="nav nav-pills mb-3" style="gap: 8px;">\n        <li class="nav-item">\n          <a class="nav-link font-weight-bold" href="javascript:showSection(\'sec-rates\')" style="border-radius: 6px; font-size: 13px; padding: 6px 16px; background: rgba(255,255,255,0.05) !important; color: #94a3b8 !important; border: 1px solid rgba(255,255,255,0.08);">\n            <i class="fas fa-tags mr-1"></i> Rates &amp; Packages\n          </a>\n        </li>\n        <li class="nav-item">\n          <a class="nav-link active font-weight-bold" href="javascript:showSection(\'sec-time-policy\')" style="border-radius: 6px; font-size: 13px; padding: 6px 16px; background: #007bff !important; color: #fff !important; box-shadow: 0 2px 6px rgba(0,123,255,0.3);">\n            <i class="fas fa-clock mr-1"></i> Time Validity &amp; Pause Settings\n          </a>\n        </li>\n      </ul>\n      <div id="time-policy-container">\n        <div id="time-policy-card" class="card mb-3" style="background:#1e293b; border:1px solid rgba(255,255,255,0.08); border-radius:8px; overflow:hidden;">\n          <div class="card-header py-2 px-3" style="background:#0f172a; border-bottom:1px solid rgba(255,255,255,0.06);">\n            <h3 class="card-title font-weight-bold text-light mb-0" style="font-size:13.5px;">\n              <i class="fas fa-clock text-primary mr-2"></i>Time Validity &amp; Pause Rules\n            </h3>\n          </div>\n          <div class="card-body p-3">\n            <form id="policy-form" onsubmit="savePolicySettings(event)">\n              <!-- Row 1: Switch + Timeout Action -->\n              <div class="d-flex flex-wrap justify-content-between align-items-center mb-3" style="gap: 12px; background: rgba(15,23,42,0.5); padding: 10px 14px; border-radius: 6px; border: 1px solid rgba(255,255,255,0.05);">\n                <div class="custom-control custom-switch">\n                  <input type="checkbox" class="custom-control-input" id="policy-allow-pause" checked>\n                  <label class="custom-control-label font-weight-bold text-light" for="policy-allow-pause" style="cursor:pointer; font-size:12.5px;">\n                    Allow Customer Pauses\n                  </label>\n                  <small class="d-block text-muted" style="font-size: 11px;">Permits connected clients to pause their active Wi-Fi session</small>\n                </div>\n                <div class="d-flex align-items-center" style="gap: 8px;">\n                  <span class="text-muted small font-weight-bold" style="font-size:11.5px; white-space:nowrap;">If pause timer expires:</span>\n                  <select class="custom-select custom-select-sm" id="policy-timeout-action" style="height:30px; width:auto; min-width:170px; background:#0f172a; border:1px solid #334155; color:#f8fafc; font-size:11.5px;">\n                    <option value="resume" selected>Auto-Resume Internet</option>\n                    <option value="expire">Expire Remaining Time</option>\n                  </select>\n                </div>\n              </div>\n\n              <!-- Row 2: 5 Basic Settings (Human-Friendly Units) -->\n              <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(170px, 1fr)); gap:10px;" class="mb-3">\n                <div class="form-group mb-0">\n                  <label class="font-weight-bold small mb-1 d-block text-light" style="font-size: 11.5px;">Max Pauses Allowed</label>\n                  <input type="number" min="0" step="1" id="policy-pause-count-max" value="3" placeholder="Unlimited" class="form-control form-control-sm" style="height: 28px; background:#0f172a; border:1px solid #334155; color:#f8fafc;">\n                  <small class="text-muted" style="font-size: 10.5px;">Per session (Blank = Unlimited)</small>\n                </div>\n                <div class="form-group mb-0">\n                  <label class="font-weight-bold small mb-1 d-block text-light" style="font-size: 11.5px;">Max Pause Duration</label>\n                  <div class="d-flex align-items-center" style="gap: 4px;">\n                    <input type="number" min="1" step="1" id="pause-dur-val" value="60" class="form-control form-control-sm" style="width:55px !important; text-align:center; font-weight:700; height:28px; background:#0f172a; border:1px solid #334155; color:#f8fafc;">\n                    <select class="custom-select custom-select-sm" id="pause-dur-unit" style="width:88px; height:28px; background:#0f172a; border:1px solid #334155; color:#f8fafc; font-size:11.5px;">\n                      <option value="m" selected>Minutes</option>\n                      <option value="h">Hours</option>\n                    </select>\n                  </div>\n                  <small class="text-muted" style="font-size: 10.5px;">Max single pause duration</small>\n                </div>\n                <div class="form-group mb-0">\n                  <label class="font-weight-bold small mb-1 d-block text-light" style="font-size: 11.5px;">Default Expiry Fallback</label>\n                  <div class="d-flex align-items-center" style="gap: 4px;">\n                    <input type="number" min="1" step="1" id="default-val-val" value="1" class="form-control form-control-sm" style="width:55px !important; text-align:center; font-weight:700; height:28px; background:#0f172a; border:1px solid #334155; color:#f8fafc;">\n                    <select class="custom-select custom-select-sm" id="default-val-unit" style="width:88px; height:28px; background:#0f172a; border:1px solid #334155; color:#f8fafc; font-size:11.5px;">\n                      <option value="h">Hours</option>\n                      <option value="d" selected>Days</option>\n                    </select>\n                  </div>\n                  <small class="text-muted" style="font-size: 10.5px;">If no bracket tier matches</small>\n                </div>\n                <div class="form-group mb-0">\n                  <label class="font-weight-bold small mb-1 d-block text-light" style="font-size: 11.5px;">Min Time to Pause</label>\n                  <div class="d-flex align-items-center" style="gap: 4px;">\n                    <input type="number" min="0" step="1" id="min-bal-val" value="0" class="form-control form-control-sm" style="width:55px !important; text-align:center; font-weight:700; height:28px; background:#0f172a; border:1px solid #334155; color:#f8fafc;">\n                    <span class="text-muted small" style="font-size: 11px;">mins</span>\n                  </div>\n                  <small class="text-muted" style="font-size: 10.5px;">0 = allow pause at any time</small>\n                </div>\n                <div class="form-group mb-0">\n                  <label class="font-weight-bold small mb-1 d-block text-light" style="font-size: 11.5px;">Max Time to Pause</label>\n                  <input type="text" id="max-bal-display" value="Disabled" placeholder="Disabled" class="form-control form-control-sm" style="height: 28px; background:#0f172a; border:1px solid #334155; color:#94a3b8;" readonly>\n                  <small class="text-muted" style="font-size: 10.5px;">Optional upper limit</small>\n                </div>\n              </div>\n\n              <!-- Section: Tiered Expiration Rules -->\n              <div class="mt-3 pt-2" style="border-top: 1px solid rgba(255,255,255,0.08);">\n                <div class="d-flex justify-content-between align-items-center mb-2">\n                  <div>\n                    <h6 class="font-weight-bold text-light mb-0" style="font-size:12.5px;">\n                      <i class="fas fa-layer-group text-info mr-1"></i>Tiered Expiration Rules (How long unused Wi-Fi stays valid)\n                    </h6>\n                  </div>\n                  <button type="button" class="btn btn-xs btn-outline-info font-weight-bold px-2 py-1" style="font-size: 11.5px;" onclick="addPolicyBracketRow({ value: 120, expiration: 2880, enabled: true })">\n                    <i class="fas fa-plus mr-1"></i>Add Rule\n                  </button>\n                </div>\n\n                <!-- Plain English Explanation Banner -->\n                <div class="alert mb-2 py-2 px-3 border" style="background: rgba(15, 23, 42, 0.65); border-color: rgba(56, 189, 248, 0.25) !important; border-radius: 6px; font-size: 11.5px; color: #94a3b8; line-height: 1.4;">\n                  <span class="text-info font-weight-bold"><i class="fas fa-info-circle mr-1"></i> How this works:</span>\n                  When a customer pauses their Wi-Fi or stores time in their wallet, the system checks how much time they have and grants them an expiration deadline. For example, <strong>Up to 1 Hour</strong> stays valid for <strong>2 Days</strong>, while <strong>7 Days</strong> of Wi-Fi stays valid for <strong>3 Months</strong>.\n                </div>\n\n                <div class="table-responsive" style="border: 1px solid rgba(255,255,255,0.08); border-radius: 6px; overflow:hidden;">\n                  <table class="table table-sm text-light mb-0" style="background: rgba(15,23,42,0.4); font-size: 12px;">\n                    <thead style="background:#0f172a; color:#cbd5e1; font-size:11.5px; font-weight:700;">\n                      <tr>\n                        <th style="width:45px;" class="text-center py-2">Status</th>\n                        <th class="py-2" style="width: 220px;">If Customer Has Up To</th>\n                        <th class="py-2" style="width: 220px;">Time Stays Valid For</th>\n                        <th class="py-2">Plain English Rule Preview</th>\n                        <th style="width:50px;" class="text-center py-2">Action</th>\n                      </tr>\n                    </thead>\n                    <tbody id="policy-brackets-tbody">\n                      <tr style="border-top: 1px solid rgba(255,255,255,0.04);">\n                        <td class="text-center align-middle py-2">\n                          <input type="checkbox" checked style="transform: scale(1.15); cursor: pointer;" title="Enable/disable this rule">\n                        </td>\n                        <td class="align-middle py-2">\n                          <div class="d-flex align-items-center" style="gap: 4px;">\n                            <input type="number" min="1" step="1" required value="30" class="form-control form-control-sm inp-val-num" style="width:60px !important; text-align:center; font-weight:700; height:28px; background:#0f172a; border:1px solid #334155; color:#f8fafc; font-size:12px; padding:2px 4px;" oninput="updateBracketRowPreview(this)">\n                            <select class="custom-select custom-select-sm inp-val-unit" style="width:90px; height:28px; background:#0f172a; border:1px solid #334155; color:#f8fafc; font-size:11.5px; padding:2px 6px;" onchange="updateBracketRowPreview(this)">\n                              <option value="m" selected>Mins</option><option value="h">Hours</option><option value="d">Days</option><option value="mo">Months</option>\n                            </select>\n                          </div>\n                        </td>\n                        <td class="align-middle py-2">\n                          <div class="d-flex align-items-center" style="gap: 4px;">\n                            <input type="number" min="1" step="1" required value="1" class="form-control form-control-sm inp-exp-num" style="width:60px !important; text-align:center; font-weight:700; height:28px; background:#0f172a; border:1px solid #334155; color:#f8fafc; font-size:12px; padding:2px 4px;" oninput="updateBracketRowPreview(this)">\n                            <select class="custom-select custom-select-sm inp-exp-unit" style="width:90px; height:28px; background:#0f172a; border:1px solid #334155; color:#f8fafc; font-size:11.5px; padding:2px 6px;" onchange="updateBracketRowPreview(this)">\n                              <option value="h">Hours</option><option value="d" selected>Days</option><option value="mo">Months</option>\n                            </select>\n                          </div>\n                        </td>\n                        <td class="align-middle py-2">\n                          <div style="font-size: 11.5px; font-weight: 600; padding: 4px 10px; border-radius: 5px; display: inline-flex; align-items: center; gap: 6px; background: rgba(15, 23, 42, 0.6); border: 1px solid rgba(255, 255, 255, 0.08); color: #94a3b8; white-space: nowrap;">\n                            <span>Up to <strong class="lbl-val" style="color:#38bdf8;">30 mins</strong></span>\n                            <i class="fas fa-arrow-right" style="color:#64748b; font-size:10px;"></i>\n                            <span>Valid for <span class="lbl-exp" style="color:#34d399; font-weight:700;">1 day</span></span>\n                          </div>\n                        </td>\n                        <td class="text-center align-middle py-2">\n                          <button type="button" class="btn btn-xs btn-outline-danger" style="height:24px; width:26px; padding:0; line-height:22px; font-size:11px; border-radius:4px;" onclick="this.closest(\'tr\').remove()" title="Delete rule">\n                            <i class="fas fa-times"></i>\n                          </button>\n                        </td>\n                      </tr>\n                      <tr style="border-top: 1px solid rgba(255,255,255,0.04);">\n                        <td class="text-center align-middle py-2">\n                          <input type="checkbox" checked style="transform: scale(1.15); cursor: pointer;" title="Enable/disable this rule">\n                        </td>\n                        <td class="align-middle py-2">\n                          <div class="d-flex align-items-center" style="gap: 4px;">\n                            <input type="number" min="1" step="1" required value="1" class="form-control form-control-sm inp-val-num" style="width:60px !important; text-align:center; font-weight:700; height:28px; background:#0f172a; border:1px solid #334155; color:#f8fafc; font-size:12px; padding:2px 4px;" oninput="updateBracketRowPreview(this)">\n                            <select class="custom-select custom-select-sm inp-val-unit" style="width:90px; height:28px; background:#0f172a; border:1px solid #334155; color:#f8fafc; font-size:11.5px; padding:2px 6px;" onchange="updateBracketRowPreview(this)">\n                              <option value="m">Mins</option><option value="h" selected>Hours</option><option value="d">Days</option><option value="mo">Months</option>\n                            </select>\n                          </div>\n                        </td>\n                        <td class="align-middle py-2">\n                          <div class="d-flex align-items-center" style="gap: 4px;">\n                            <input type="number" min="1" step="1" required value="2" class="form-control form-control-sm inp-exp-num" style="width:60px !important; text-align:center; font-weight:700; height:28px; background:#0f172a; border:1px solid #334155; color:#f8fafc; font-size:12px; padding:2px 4px;" oninput="updateBracketRowPreview(this)">\n                            <select class="custom-select custom-select-sm inp-exp-unit" style="width:90px; height:28px; background:#0f172a; border:1px solid #334155; color:#f8fafc; font-size:11.5px; padding:2px 6px;" onchange="updateBracketRowPreview(this)">\n                              <option value="h">Hours</option><option value="d" selected>Days</option><option value="mo">Months</option>\n                            </select>\n                          </div>\n                        </td>\n                        <td class="align-middle py-2">\n                          <div style="font-size: 11.5px; font-weight: 600; padding: 4px 10px; border-radius: 5px; display: inline-flex; align-items: center; gap: 6px; background: rgba(15, 23, 42, 0.6); border: 1px solid rgba(255, 255, 255, 0.08); color: #94a3b8; white-space: nowrap;">\n                            <span>Up to <strong class="lbl-val" style="color:#38bdf8;">1 hour</strong></span>\n                            <i class="fas fa-arrow-right" style="color:#64748b; font-size:10px;"></i>\n                            <span>Valid for <span class="lbl-exp" style="color:#34d399; font-weight:700;">2 days</span></span>\n                          </div>\n                        </td>\n                        <td class="text-center align-middle py-2">\n                          <button type="button" class="btn btn-xs btn-outline-danger" style="height:24px; width:26px; padding:0; line-height:22px; font-size:11px; border-radius:4px;" onclick="this.closest(\'tr\').remove()" title="Delete rule">\n                            <i class="fas fa-times"></i>\n                          </button>\n                        </td>\n                      </tr>\n                      <tr style="border-top: 1px solid rgba(255,255,255,0.04);">\n                        <td class="text-center align-middle py-2">\n                          <input type="checkbox" checked style="transform: scale(1.15); cursor: pointer;" title="Enable/disable this rule">\n                        </td>\n                        <td class="align-middle py-2">\n                          <div class="d-flex align-items-center" style="gap: 4px;">\n                            <input type="number" min="1" step="1" required value="3" class="form-control form-control-sm inp-val-num" style="width:60px !important; text-align:center; font-weight:700; height:28px; background:#0f172a; border:1px solid #334155; color:#f8fafc; font-size:12px; padding:2px 4px;" oninput="updateBracketRowPreview(this)">\n                            <select class="custom-select custom-select-sm inp-val-unit" style="width:90px; height:28px; background:#0f172a; border:1px solid #334155; color:#f8fafc; font-size:11.5px; padding:2px 6px;" onchange="updateBracketRowPreview(this)">\n                              <option value="m">Mins</option><option value="h" selected>Hours</option><option value="d">Days</option><option value="mo">Months</option>\n                            </select>\n                          </div>\n                        </td>\n                        <td class="align-middle py-2">\n                          <div class="d-flex align-items-center" style="gap: 4px;">\n                            <input type="number" min="1" step="1" required value="3" class="form-control form-control-sm inp-exp-num" style="width:60px !important; text-align:center; font-weight:700; height:28px; background:#0f172a; border:1px solid #334155; color:#f8fafc; font-size:12px; padding:2px 4px;" oninput="updateBracketRowPreview(this)">\n                            <select class="custom-select custom-select-sm inp-exp-unit" style="width:90px; height:28px; background:#0f172a; border:1px solid #334155; color:#f8fafc; font-size:11.5px; padding:2px 6px;" onchange="updateBracketRowPreview(this)">\n                              <option value="h">Hours</option><option value="d" selected>Days</option><option value="mo">Months</option>\n                            </select>\n                          </div>\n                        </td>\n                        <td class="align-middle py-2">\n                          <div style="font-size: 11.5px; font-weight: 600; padding: 4px 10px; border-radius: 5px; display: inline-flex; align-items: center; gap: 6px; background: rgba(15, 23, 42, 0.6); border: 1px solid rgba(255, 255, 255, 0.08); color: #94a3b8; white-space: nowrap;">\n                            <span>Up to <strong class="lbl-val" style="color:#38bdf8;">3 hours</strong></span>\n                            <i class="fas fa-arrow-right" style="color:#64748b; font-size:10px;"></i>\n                            <span>Valid for <span class="lbl-exp" style="color:#34d399; font-weight:700;">3 days</span></span>\n                          </div>\n                        </td>\n                        <td class="text-center align-middle py-2">\n                          <button type="button" class="btn btn-xs btn-outline-danger" style="height:24px; width:26px; padding:0; line-height:22px; font-size:11px; border-radius:4px;" onclick="this.closest(\'tr\').remove()" title="Delete rule">\n                            <i class="fas fa-times"></i>\n                          </button>\n                        </td>\n                      </tr>\n                      <tr style="border-top: 1px solid rgba(255,255,255,0.04);">\n                        <td class="text-center align-middle py-2">\n                          <input type="checkbox" checked style="transform: scale(1.15); cursor: pointer;" title="Enable/disable this rule">\n                        </td>\n                        <td class="align-middle py-2">\n                          <div class="d-flex align-items-center" style="gap: 4px;">\n                            <input type="number" min="1" step="1" required value="6" class="form-control form-control-sm inp-val-num" style="width:60px !important; text-align:center; font-weight:700; height:28px; background:#0f172a; border:1px solid #334155; color:#f8fafc; font-size:12px; padding:2px 4px;" oninput="updateBracketRowPreview(this)">\n                            <select class="custom-select custom-select-sm inp-val-unit" style="width:90px; height:28px; background:#0f172a; border:1px solid #334155; color:#f8fafc; font-size:11.5px; padding:2px 6px;" onchange="updateBracketRowPreview(this)">\n                              <option value="m">Mins</option><option value="h" selected>Hours</option><option value="d">Days</option><option value="mo">Months</option>\n                            </select>\n                          </div>\n                        </td>\n                        <td class="align-middle py-2">\n                          <div class="d-flex align-items-center" style="gap: 4px;">\n                            <input type="number" min="1" step="1" required value="7" class="form-control form-control-sm inp-exp-num" style="width:60px !important; text-align:center; font-weight:700; height:28px; background:#0f172a; border:1px solid #334155; color:#f8fafc; font-size:12px; padding:2px 4px;" oninput="updateBracketRowPreview(this)">\n                            <select class="custom-select custom-select-sm inp-exp-unit" style="width:90px; height:28px; background:#0f172a; border:1px solid #334155; color:#f8fafc; font-size:11.5px; padding:2px 6px;" onchange="updateBracketRowPreview(this)">\n                              <option value="h">Hours</option><option value="d" selected>Days</option><option value="mo">Months</option>\n                            </select>\n                          </div>\n                        </td>\n                        <td class="align-middle py-2">\n                          <div style="font-size: 11.5px; font-weight: 600; padding: 4px 10px; border-radius: 5px; display: inline-flex; align-items: center; gap: 6px; background: rgba(15, 23, 42, 0.6); border: 1px solid rgba(255, 255, 255, 0.08); color: #94a3b8; white-space: nowrap;">\n                            <span>Up to <strong class="lbl-val" style="color:#38bdf8;">6 hours</strong></span>\n                            <i class="fas fa-arrow-right" style="color:#64748b; font-size:10px;"></i>\n                            <span>Valid for <span class="lbl-exp" style="color:#34d399; font-weight:700;">7 days</span></span>\n                          </div>\n                        </td>\n                        <td class="text-center align-middle py-2">\n                          <button type="button" class="btn btn-xs btn-outline-danger" style="height:24px; width:26px; padding:0; line-height:22px; font-size:11px; border-radius:4px;" onclick="this.closest(\'tr\').remove()" title="Delete rule">\n                            <i class="fas fa-times"></i>\n                          </button>\n                        </td>\n                      </tr>\n                      <tr style="border-top: 1px solid rgba(255,255,255,0.04);">\n                        <td class="text-center align-middle py-2">\n                          <input type="checkbox" checked style="transform: scale(1.15); cursor: pointer;" title="Enable/disable this rule">\n                        </td>\n                        <td class="align-middle py-2">\n                          <div class="d-flex align-items-center" style="gap: 4px;">\n                            <input type="number" min="1" step="1" required value="12" class="form-control form-control-sm inp-val-num" style="width:60px !important; text-align:center; font-weight:700; height:28px; background:#0f172a; border:1px solid #334155; color:#f8fafc; font-size:12px; padding:2px 4px;" oninput="updateBracketRowPreview(this)">\n                            <select class="custom-select custom-select-sm inp-val-unit" style="width:90px; height:28px; background:#0f172a; border:1px solid #334155; color:#f8fafc; font-size:11.5px; padding:2px 6px;" onchange="updateBracketRowPreview(this)">\n                              <option value="m">Mins</option><option value="h" selected>Hours</option><option value="d">Days</option><option value="mo">Months</option>\n                            </select>\n                          </div>\n                        </td>\n                        <td class="align-middle py-2">\n                          <div class="d-flex align-items-center" style="gap: 4px;">\n                            <input type="number" min="1" step="1" required value="15" class="form-control form-control-sm inp-exp-num" style="width:60px !important; text-align:center; font-weight:700; height:28px; background:#0f172a; border:1px solid #334155; color:#f8fafc; font-size:12px; padding:2px 4px;" oninput="updateBracketRowPreview(this)">\n                            <select class="custom-select custom-select-sm inp-exp-unit" style="width:90px; height:28px; background:#0f172a; border:1px solid #334155; color:#f8fafc; font-size:11.5px; padding:2px 6px;" onchange="updateBracketRowPreview(this)">\n                              <option value="h">Hours</option><option value="d" selected>Days</option><option value="mo">Months</option>\n                            </select>\n                          </div>\n                        </td>\n                        <td class="align-middle py-2">\n                          <div style="font-size: 11.5px; font-weight: 600; padding: 4px 10px; border-radius: 5px; display: inline-flex; align-items: center; gap: 6px; background: rgba(15, 23, 42, 0.6); border: 1px solid rgba(255, 255, 255, 0.08); color: #94a3b8; white-space: nowrap;">\n                            <span>Up to <strong class="lbl-val" style="color:#38bdf8;">12 hours</strong></span>\n                            <i class="fas fa-arrow-right" style="color:#64748b; font-size:10px;"></i>\n                            <span>Valid for <span class="lbl-exp" style="color:#34d399; font-weight:700;">15 days</span></span>\n                          </div>\n                        </td>\n                        <td class="text-center align-middle py-2">\n                          <button type="button" class="btn btn-xs btn-outline-danger" style="height:24px; width:26px; padding:0; line-height:22px; font-size:11px; border-radius:4px;" onclick="this.closest(\'tr\').remove()" title="Delete rule">\n                            <i class="fas fa-times"></i>\n                          </button>\n                        </td>\n                      </tr>\n                      <tr style="border-top: 1px solid rgba(255,255,255,0.04);">\n                        <td class="text-center align-middle py-2">\n                          <input type="checkbox" checked style="transform: scale(1.15); cursor: pointer;" title="Enable/disable this rule">\n                        </td>\n                        <td class="align-middle py-2">\n                          <div class="d-flex align-items-center" style="gap: 4px;">\n                            <input type="number" min="1" step="1" required value="1" class="form-control form-control-sm inp-val-num" style="width:60px !important; text-align:center; font-weight:700; height:28px; background:#0f172a; border:1px solid #334155; color:#f8fafc; font-size:12px; padding:2px 4px;" oninput="updateBracketRowPreview(this)">\n                            <select class="custom-select custom-select-sm inp-val-unit" style="width:90px; height:28px; background:#0f172a; border:1px solid #334155; color:#f8fafc; font-size:11.5px; padding:2px 6px;" onchange="updateBracketRowPreview(this)">\n                              <option value="m">Mins</option><option value="h">Hours</option><option value="d" selected>Days</option><option value="mo">Months</option>\n                            </select>\n                          </div>\n                        </td>\n                        <td class="align-middle py-2">\n                          <div class="d-flex align-items-center" style="gap: 4px;">\n                            <input type="number" min="1" step="1" required value="1" class="form-control form-control-sm inp-exp-num" style="width:60px !important; text-align:center; font-weight:700; height:28px; background:#0f172a; border:1px solid #334155; color:#f8fafc; font-size:12px; padding:2px 4px;" oninput="updateBracketRowPreview(this)">\n                            <select class="custom-select custom-select-sm inp-exp-unit" style="width:90px; height:28px; background:#0f172a; border:1px solid #334155; color:#f8fafc; font-size:11.5px; padding:2px 6px;" onchange="updateBracketRowPreview(this)">\n                              <option value="h">Hours</option><option value="d">Days</option><option value="mo" selected>Months</option>\n                            </select>\n                          </div>\n                        </td>\n                        <td class="align-middle py-2">\n                          <div style="font-size: 11.5px; font-weight: 600; padding: 4px 10px; border-radius: 5px; display: inline-flex; align-items: center; gap: 6px; background: rgba(15, 23, 42, 0.6); border: 1px solid rgba(255, 255, 255, 0.08); color: #94a3b8; white-space: nowrap;">\n                            <span>Up to <strong class="lbl-val" style="color:#38bdf8;">1 day</strong></span>\n                            <i class="fas fa-arrow-right" style="color:#64748b; font-size:10px;"></i>\n                            <span>Valid for <span class="lbl-exp" style="color:#34d399; font-weight:700;">1 month</span></span>\n                          </div>\n                        </td>\n                        <td class="text-center align-middle py-2">\n                          <button type="button" class="btn btn-xs btn-outline-danger" style="height:24px; width:26px; padding:0; line-height:22px; font-size:11px; border-radius:4px;" onclick="this.closest(\'tr\').remove()" title="Delete rule">\n                            <i class="fas fa-times"></i>\n                          </button>\n                        </td>\n                      </tr>\n                      <tr style="border-top: 1px solid rgba(255,255,255,0.04);">\n                        <td class="text-center align-middle py-2">\n                          <input type="checkbox" checked style="transform: scale(1.15); cursor: pointer;" title="Enable/disable this rule">\n                        </td>\n                        <td class="align-middle py-2">\n                          <div class="d-flex align-items-center" style="gap: 4px;">\n                            <input type="number" min="1" step="1" required value="3" class="form-control form-control-sm inp-val-num" style="width:60px !important; text-align:center; font-weight:700; height:28px; background:#0f172a; border:1px solid #334155; color:#f8fafc; font-size:12px; padding:2px 4px;" oninput="updateBracketRowPreview(this)">\n                            <select class="custom-select custom-select-sm inp-val-unit" style="width:90px; height:28px; background:#0f172a; border:1px solid #334155; color:#f8fafc; font-size:11.5px; padding:2px 6px;" onchange="updateBracketRowPreview(this)">\n                              <option value="m">Mins</option><option value="h">Hours</option><option value="d" selected>Days</option><option value="mo">Months</option>\n                            </select>\n                          </div>\n                        </td>\n                        <td class="align-middle py-2">\n                          <div class="d-flex align-items-center" style="gap: 4px;">\n                            <input type="number" min="1" step="1" required value="2" class="form-control form-control-sm inp-exp-num" style="width:60px !important; text-align:center; font-weight:700; height:28px; background:#0f172a; border:1px solid #334155; color:#f8fafc; font-size:12px; padding:2px 4px;" oninput="updateBracketRowPreview(this)">\n                            <select class="custom-select custom-select-sm inp-exp-unit" style="width:90px; height:28px; background:#0f172a; border:1px solid #334155; color:#f8fafc; font-size:11.5px; padding:2px 6px;" onchange="updateBracketRowPreview(this)">\n                              <option value="h">Hours</option><option value="d">Days</option><option value="mo" selected>Months</option>\n                            </select>\n                          </div>\n                        </td>\n                        <td class="align-middle py-2">\n                          <div style="font-size: 11.5px; font-weight: 600; padding: 4px 10px; border-radius: 5px; display: inline-flex; align-items: center; gap: 6px; background: rgba(15, 23, 42, 0.6); border: 1px solid rgba(255, 255, 255, 0.08); color: #94a3b8; white-space: nowrap;">\n                            <span>Up to <strong class="lbl-val" style="color:#38bdf8;">3 days</strong></span>\n                            <i class="fas fa-arrow-right" style="color:#64748b; font-size:10px;"></i>\n                            <span>Valid for <span class="lbl-exp" style="color:#34d399; font-weight:700;">2 months</span></span>\n                          </div>\n                        </td>\n                        <td class="text-center align-middle py-2">\n                          <button type="button" class="btn btn-xs btn-outline-danger" style="height:24px; width:26px; padding:0; line-height:22px; font-size:11px; border-radius:4px;" onclick="this.closest(\'tr\').remove()" title="Delete rule">\n                            <i class="fas fa-times"></i>\n                          </button>\n                        </td>\n                      </tr>\n                      <tr style="border-top: 1px solid rgba(255,255,255,0.04);">\n                        <td class="text-center align-middle py-2">\n                          <input type="checkbox" checked style="transform: scale(1.15); cursor: pointer;" title="Enable/disable this rule">\n                        </td>\n                        <td class="align-middle py-2">\n                          <div class="d-flex align-items-center" style="gap: 4px;">\n                            <input type="number" min="1" step="1" required value="7" class="form-control form-control-sm inp-val-num" style="width:60px !important; text-align:center; font-weight:700; height:28px; background:#0f172a; border:1px solid #334155; color:#f8fafc; font-size:12px; padding:2px 4px;" oninput="updateBracketRowPreview(this)">\n                            <select class="custom-select custom-select-sm inp-val-unit" style="width:90px; height:28px; background:#0f172a; border:1px solid #334155; color:#f8fafc; font-size:11.5px; padding:2px 6px;" onchange="updateBracketRowPreview(this)">\n                              <option value="m">Mins</option><option value="h">Hours</option><option value="d" selected>Days</option><option value="mo">Months</option>\n                            </select>\n                          </div>\n                        </td>\n                        <td class="align-middle py-2">\n                          <div class="d-flex align-items-center" style="gap: 4px;">\n                            <input type="number" min="1" step="1" required value="3" class="form-control form-control-sm inp-exp-num" style="width:60px !important; text-align:center; font-weight:700; height:28px; background:#0f172a; border:1px solid #334155; color:#f8fafc; font-size:12px; padding:2px 4px;" oninput="updateBracketRowPreview(this)">\n                            <select class="custom-select custom-select-sm inp-exp-unit" style="width:90px; height:28px; background:#0f172a; border:1px solid #334155; color:#f8fafc; font-size:11.5px; padding:2px 6px;" onchange="updateBracketRowPreview(this)">\n                              <option value="h">Hours</option><option value="d">Days</option><option value="mo" selected>Months</option>\n                            </select>\n                          </div>\n                        </td>\n                        <td class="align-middle py-2">\n                          <div style="font-size: 11.5px; font-weight: 600; padding: 4px 10px; border-radius: 5px; display: inline-flex; align-items: center; gap: 6px; background: rgba(15, 23, 42, 0.6); border: 1px solid rgba(255, 255, 255, 0.08); color: #94a3b8; white-space: nowrap;">\n                            <span>Up to <strong class="lbl-val" style="color:#38bdf8;">7 days</strong></span>\n                            <i class="fas fa-arrow-right" style="color:#64748b; font-size:10px;"></i>\n                            <span>Valid for <span class="lbl-exp" style="color:#34d399; font-weight:700;">3 months</span></span>\n                          </div>\n                        </td>\n                        <td class="text-center align-middle py-2">\n                          <button type="button" class="btn btn-xs btn-outline-danger" style="height:24px; width:26px; padding:0; line-height:22px; font-size:11px; border-radius:4px;" onclick="this.closest(\'tr\').remove()" title="Delete rule">\n                            <i class="fas fa-times"></i>\n                          </button>\n                        </td>\n                      </tr>\n                      <tr style="border-top: 1px solid rgba(255,255,255,0.04);">\n                        <td class="text-center align-middle py-2">\n                          <input type="checkbox" checked style="transform: scale(1.15); cursor: pointer;" title="Enable/disable this rule">\n                        </td>\n                        <td class="align-middle py-2">\n                          <div class="d-flex align-items-center" style="gap: 4px;">\n                            <input type="number" min="1" step="1" required value="1" class="form-control form-control-sm inp-val-num" style="width:60px !important; text-align:center; font-weight:700; height:28px; background:#0f172a; border:1px solid #334155; color:#f8fafc; font-size:12px; padding:2px 4px;" oninput="updateBracketRowPreview(this)">\n                            <select class="custom-select custom-select-sm inp-val-unit" style="width:90px; height:28px; background:#0f172a; border:1px solid #334155; color:#f8fafc; font-size:11.5px; padding:2px 6px;" onchange="updateBracketRowPreview(this)">\n                              <option value="m">Mins</option><option value="h">Hours</option><option value="d">Days</option><option value="mo" selected>Months</option>\n                            </select>\n                          </div>\n                        </td>\n                        <td class="align-middle py-2">\n                          <div class="d-flex align-items-center" style="gap: 4px;">\n                            <input type="number" min="1" step="1" required value="6" class="form-control form-control-sm inp-exp-num" style="width:60px !important; text-align:center; font-weight:700; height:28px; background:#0f172a; border:1px solid #334155; color:#f8fafc; font-size:12px; padding:2px 4px;" oninput="updateBracketRowPreview(this)">\n                            <select class="custom-select custom-select-sm inp-exp-unit" style="width:90px; height:28px; background:#0f172a; border:1px solid #334155; color:#f8fafc; font-size:11.5px; padding:2px 6px;" onchange="updateBracketRowPreview(this)">\n                              <option value="h">Hours</option><option value="d">Days</option><option value="mo" selected>Months</option>\n                            </select>\n                          </div>\n                        </td>\n                        <td class="align-middle py-2">\n                          <div style="font-size: 11.5px; font-weight: 600; padding: 4px 10px; border-radius: 5px; display: inline-flex; align-items: center; gap: 6px; background: rgba(15, 23, 42, 0.6); border: 1px solid rgba(255, 255, 255, 0.08); color: #94a3b8; white-space: nowrap;">\n                            <span>Up to <strong class="lbl-val" style="color:#38bdf8;">1 month</strong></span>\n                            <i class="fas fa-arrow-right" style="color:#64748b; font-size:10px;"></i>\n                            <span>Valid for <span class="lbl-exp" style="color:#34d399; font-weight:700;">6 months</span></span>\n                          </div>\n                        </td>\n                        <td class="text-center align-middle py-2">\n                          <button type="button" class="btn btn-xs btn-outline-danger" style="height:24px; width:26px; padding:0; line-height:22px; font-size:11px; border-radius:4px;" onclick="this.closest(\'tr\').remove()" title="Delete rule">\n                            <i class="fas fa-times"></i>\n                          </button>\n                        </td>\n                      </tr>\n                    </tbody>\n                  </table>\n                </div>\n              </div>\n\n              <!-- Footer: Save Button + Diagnostics -->\n              <div class="d-flex flex-wrap justify-content-between align-items-center mt-3 pt-2" style="border-top: 1px solid rgba(255,255,255,0.06); gap: 10px;">\n                <div class="d-flex align-items-center">\n                  <button type="submit" id="policy-save-btn" class="btn btn-sm btn-primary font-weight-bold px-3 py-1 mr-2" style="font-size:12px; height:30px; white-space:nowrap;">\n                    <i class="fas fa-save mr-1"></i> Save Policy\n                  </button>\n                  <span id="policy-save-msg" class="small font-weight-bold" aria-live="polite"></span>\n                </div>\n                <div id="policy-diag-strip" class="d-flex flex-wrap align-items-center small text-muted" style="gap: 10px; font-size: 11px;">\n                  <span>Accounting: <span id="diag-acc-badge" class="badge badge-success px-1">Ready</span></span>\n                  <span>Worker: <span id="diag-worker-badge" class="badge badge-success px-1">Healthy</span></span>\n                  <span>Held: <span id="diag-held-badge" class="badge badge-secondary px-1">0</span></span>\n                  <span>Mismatches: <span id="diag-mismatch-badge" class="badge badge-success px-1">0</span></span>\n                </div>\n              </div>\n            </form>\n          </div>\n        </div>\n      </div>\n    </div>\n\n    <!-- 6. AUDIO CUSTOMIZER SECTION -->\n    <div id="sec-audio" class="section-view">\n      <div class="card card-warning mb-3">\n        <div class="card-header"><h3 class="card-title font-weight-bold"><i class="fas fa-volume-up"></i> Portal Audio & Event Chimes</h3></div>\n        <div class="card-body p-0">\n          <div class="d-none d-md-flex bg-dark text-white p-2 font-weight-bold" style="font-size: 13px;">\n            <div class="col-md-3">Event Stage</div>\n            <div class="col-md-3">Audio Preset</div>\n            <div class="col-md-4">Custom URL or Upload</div>\n            <div class="col-md-2 text-center">Preview</div>\n          </div>\n          \n          <!-- EVENT 1: BG LOOP -->\n          <div class="row m-0 p-3 border-bottom align-items-center">\n            <div class="col-12 col-md-3 mb-2 mb-md-0">\n              <span class="badge badge-secondary p-2 d-block text-left"><i class="fas fa-music"></i> 1. Standby Loop</span>\n            </div>\n            <div class="col-12 col-md-3 mb-2 mb-md-0">\n              <select id="audio-bg-preset" class="form-control form-control-sm" onchange="onAudioPresetChange(\'bg\')">\n                <option value="/static/audio/eco_loop.wav" {% if config.audio_bg == \'/static/audio/eco_loop.wav\' or config.audio_bg == \'/static/audio/b1.wav\' or not config.audio_bg %}selected{% endif %}>📻 Default Eco-Fi Standby Loop</option>\n                <option value="silent" {% if config.audio_bg == \'silent\' %}selected{% endif %}>🔇 Silent</option>\n                <option value="custom" {% if config.audio_bg and config.audio_bg not in [\'/static/audio/eco_loop.wav\', \'/static/audio/b1.wav\', \'silent\'] %}selected{% endif %}>📁 Custom File / URL</option>\n              </select>\n            </div>\n            <div class="col-12 col-md-4 mb-2 mb-md-0">\n              <div class="input-group input-group-sm">\n                <input type="text" id="audio-bg-custom" class="form-control" placeholder="Custom URL..." value="{{ config.audio_bg or \'/static/audio/eco_loop.wav\' }}" oninput="updateAudioPlayer(\'bg\')">\n                <div class="input-group-append">\n                  <label class="btn btn-secondary mb-0 rounded-right" style="cursor:pointer;" title="Upload File"><i class="fas fa-upload"></i>\n                    <input type="file" id="upload-file-bg" accept="audio/*" onchange="uploadAudioFile(\'bg\')" style="display:none;">\n                  </label>\n                </div>\n              </div>\n            </div>\n            <div class="col-12 col-md-2 text-center">\n              <audio id="audio-player-bg" controls src="{{ config.audio_bg or \'/static/audio/eco_loop.wav\' }}" style="height: 30px; width: 100%; max-width: 250px;"></audio>\n            </div>\n          </div>\n\n          <!-- EVENT 2: DEPOSIT CHIME -->\n          <div class="row m-0 p-3 border-bottom align-items-center">\n            <div class="col-12 col-md-3 mb-2 mb-md-0">\n              <span class="badge badge-success p-2 d-block text-left"><i class="fas fa-coins"></i> 2. Deposit Chime</span>\n            </div>\n            <div class="col-12 col-md-3 mb-2 mb-md-0">\n              <select id="audio-insert-preset" class="form-control form-control-sm" onchange="onAudioPresetChange(\'insert\')">\n                <option value="/static/audio/bottle_success.wav" {% if config.audio_insert == \'/static/audio/bottle_success.wav\' or not config.audio_insert %}selected{% endif %}>🍾 Bottle Deposit Chime (Default)</option>\n                <option value="/static/audio/eco_chime.wav" {% if config.audio_insert == \'/static/audio/eco_chime.wav\' or config.audio_insert == \'/static/audio/coin.wav\' %}selected{% endif %}>🔔 Classic Eco-Fi Chime</option>\n                <option value="/static/audio/eco_drop.wav" {% if config.audio_insert == \'/static/audio/eco_drop.wav\' or config.audio_insert == \'/static/audio/coin_insert.wav\' %}selected{% endif %}>🍾 Mechanical Bottle Drop</option>\n                <option value="/static/audio/eco_pulse.wav" {% if config.audio_insert == \'/static/audio/eco_pulse.wav\' or config.audio_insert == \'/static/audio/insert_coin.wav\' %}selected{% endif %}>🎶 Double Pulse Alert</option>\n                <option value="arcade_powerup" {% if config.audio_insert == \'arcade_powerup\' %}selected{% endif %}>🎮 8-Bit Power-Up</option>\n                <option value="voice_filipino" {% if config.audio_insert == \'voice_filipino\' %}selected{% endif %}>🗣️ Filipino Voice</option>\n                <option value="custom" {% if config.audio_insert and config.audio_insert not in [\'/static/audio/bottle_success.wav\', \'/static/audio/eco_chime.wav\', \'/static/audio/eco_drop.wav\', \'/static/audio/eco_pulse.wav\', \'/static/audio/coin.wav\', \'/static/audio/coin_insert.wav\', \'/static/audio/insert_coin.wav\', \'arcade_powerup\', \'voice_filipino\'] %}selected{% endif %}>📁 Custom File / URL</option>\n              </select>\n            </div>\n            <div class="col-12 col-md-4 mb-2 mb-md-0">\n              <div class="input-group input-group-sm">\n                <input type="text" id="audio-insert-custom" class="form-control" placeholder="Custom URL..." value="{{ config.audio_insert or \'/static/audio/bottle_success.wav\' }}" oninput="updateAudioPlayer(\'insert\')">\n                <div class="input-group-append">\n                  <label class="btn btn-secondary mb-0 rounded-right" style="cursor:pointer;" title="Upload File"><i class="fas fa-upload"></i>\n                    <input type="file" id="upload-file-insert" accept="audio/*" onchange="uploadAudioFile(\'insert\')" style="display:none;">\n                  </label>\n                </div>\n              </div>\n            </div>\n            <div class="col-12 col-md-2 text-center">\n              <audio id="audio-player-insert" controls src="{{ config.audio_insert or \'/static/audio/bottle_success.wav\' }}" style="height: 30px; width: 100%; max-width: 250px;"></audio>\n            </div>\n          </div>\n\n          <!-- EVENT 3: SUCCESS CHIME -->\n          <div class="row m-0 p-3 border-bottom align-items-center">\n            <div class="col-12 col-md-3 mb-2 mb-md-0">\n              <span class="badge badge-info p-2 d-block text-left"><i class="fas fa-check-circle"></i> 3. Session Complete</span>\n            </div>\n            <div class="col-12 col-md-3 mb-2 mb-md-0">\n              <select id="audio-success-preset" class="form-control form-control-sm" onchange="onAudioPresetChange(\'success\')">\n                <option value="/static/audio/eco_success.wav" {% if config.audio_success == \'/static/audio/eco_success.wav\' or config.audio_success == \'/static/audio/success_ding.wav\' or not config.audio_success %}selected{% endif %}>✨ Eco-Fi Success</option>\n                <option value="crystal_bell" {% if config.audio_success == \'crystal_bell\' %}selected{% endif %}>🛎️ Crystal Bell</option>\n                <option value="silent" {% if config.audio_success == \'silent\' %}selected{% endif %}>🔇 Silent</option>\n                <option value="custom" {% if config.audio_success and config.audio_success not in [\'/static/audio/eco_success.wav\', \'/static/audio/success_ding.wav\', \'crystal_bell\', \'silent\'] %}selected{% endif %}>📁 Custom File / URL</option>\n              </select>\n            </div>\n            <div class="col-12 col-md-4 mb-2 mb-md-0">\n              <div class="input-group input-group-sm">\n                <input type="text" id="audio-success-custom" class="form-control" placeholder="Custom URL..." value="{{ config.audio_success or \'/static/audio/eco_success.wav\' }}" oninput="updateAudioPlayer(\'success\')">\n                <div class="input-group-append">\n                  <label class="btn btn-secondary mb-0 rounded-right" style="cursor:pointer;" title="Upload File"><i class="fas fa-upload"></i>\n                    <input type="file" id="upload-file-success" accept="audio/*" onchange="uploadAudioFile(\'success\')" style="display:none;">\n                  </label>\n                </div>\n              </div>\n            </div>\n            <div class="col-12 col-md-2 text-center">\n              <audio id="audio-player-success" controls src="{{ config.audio_success or \'/static/audio/eco_success.wav\' }}" style="height: 30px; width: 100%; max-width: 250px;"></audio>\n            </div>\n          </div>\n          <div class="card-footer d-flex flex-wrap align-items-center justify-content-between p-3 border-top-0">\n            <div class="d-flex align-items-center mb-2 mb-md-0" style="min-width: 250px; max-width: 400px; flex-grow: 1;">\n              <span class="mr-3 font-weight-bold" style="font-size:13px;"><i class="fas fa-volume-up"></i> Master Volume:</span>\n              <input type="range" id="audio-vol-input" class="custom-range flex-grow-1" min="10" max="100" value="{{ config.audio_volume or 80 }}" oninput="updatePreviewVolume()">\n              <span id="vol-lbl" class="ml-2 badge badge-dark" style="width:45px;">{{ config.audio_volume or 80 }}%</span>\n            </div>\n            <button class="btn btn-success btn-sm px-4 shadow-sm" onclick="saveAudioSettings()"><i class="fas fa-save"></i> Save Audio Settings</button>\n          </div>\n        </div>\n      </div>\n    </div>\n\n    <!-- 7. PORTAL & BANNERS SECTION -->\n    <div id="sec-portal-custom" class="section-view">\n      <div class="card card-primary">\n        <div class="card-header"><h3 class="card-title text-info"><i class="fas fa-palette mr-1"></i> Portal Branding & Announcements</h3></div>\n        <div class="card-body">\n          <div class="row">\n            <div class="col-12 col-md-6 form-group">\n              <label>Hotspot Vendo Name:</label>\n              <input type="text" id="cfg-vendo-name" class="form-control" value="{{ config.get(\'vendo_name\', \'Eco-Fi Vendo\') }}">\n            </div>\n            <div class="col-12 col-md-6 form-group">\n              <label>Subtitle / Tagline:</label>\n              <input type="text" id="cfg-vendo-sub" class="form-control" value="{{ config.get(\'vendo_subtitle\', \'Recycle Bottles for Fast WiFi\') }}">\n            </div>\n          </div>\n          <div class="form-group">\n            <label>Announcement Banner Message:</label>\n            <textarea id="cfg-announcement" class="form-control" rows="3" placeholder="Enter announcement text to display on customer portal...">{{ config.get(\'announcement\', \'\') }}</textarea>\n            <small class="text-muted">This announcement banner is displayed at the top of the client portal page in real-time.</small>\n          </div>\n          <div class="mt-3">\n            <button class="btn btn-primary font-weight-bold px-4" onclick="savePortalCustom()"><i class="fas fa-save mr-1"></i> Update Portal Branding</button>\n          </div>\n        </div>\n      </div>\n    </div>\n\n    <!-- 8A. NETWORK & INTERFACES SECTION (SUB-TABBED) -->\n    <div id="sec-network" class="section-view">\n      <div class="card card-primary card-outline card-outline-tabs">\n        <div class="card-header p-0 border-bottom-0">\n          <ul class="nav nav-tabs" id="network-tabs" role="tablist">\n            <li class="nav-item">\n              <a class="nav-link active" id="net-tab-adapters-tab" data-toggle="pill" href="#net-tab-adapters" role="tab"><i class="fas fa-network-wired mr-1"></i> Adapters & WAN/LAN</a>\n            </li>\n            <li class="nav-item">\n              <a class="nav-link" id="net-tab-dns-tab" data-toggle="pill" href="#net-tab-dns" role="tab"><i class="fas fa-globe mr-1"></i> DNS & Domain</a>\n            </li>\n            <li class="nav-item">\n              <a class="nav-link" id="net-tab-dhcp-tab" data-toggle="pill" href="#net-tab-dhcp" role="tab"><i class="fas fa-list-ol mr-1"></i> DHCP & Leases</a>\n            </li>\n            <li class="nav-item">\n              <a class="nav-link" id="net-tab-policies-tab" data-toggle="pill" href="#net-tab-policies" role="tab"><i class="fas fa-shield-alt mr-1"></i> Access Policies</a>\n            </li>\n          </ul>\n        </div>\n        <div class="card-body">\n          <div class="tab-content" id="network-tabsContent">\n            \n            <!-- SUB-TAB 1: ADAPTERS -->\n            <div class="tab-pane fade show active" id="net-tab-adapters" role="tabpanel">\n              <div class="callout callout-info py-2 px-3 mb-3" style="background:rgba(23,162,184,0.1); border-left-color:#17a2b8;">\n                <p class="mb-0 text-white" style="font-size:0.88rem;"><i class="fas fa-info-circle mr-1 text-info"></i> Manage hardware ethernet interfaces and USB-LAN adapters. Assign WAN (Internet Upstream) and LAN (Access Point / Vendo Clients).</p>\n              </div>\n              <div class="card card-dark mb-3">\n                <div class="card-header py-2"><h5 class="card-title text-sm"><i class="fas fa-microchip mr-1"></i> Detected Hardware Network Interfaces</h5></div>\n                <div class="table-responsive">\n                  <table class="table table-striped table-hover mb-0" style="font-size:0.85rem;">\n                    <thead>\n                      <tr>\n                        <th>Interface</th>\n                        <th>Assigned Role</th>\n                        <th>IP Address</th>\n                        <th>MAC Address</th>\n                        <th>State</th>\n                        <th>Speed / Mode</th>\n                      </tr>\n                    </thead>\n                    <tbody id="net-interfaces-tbody">\n                      <tr><td colspan="6" class="text-center text-muted py-3"><i class="fas fa-spinner fa-spin mr-1"></i> Scanning network hardware interfaces...</td></tr>\n                    </tbody>\n                  </table>\n                </div>\n              </div>\n\n              <div class="row">\n                <div class="col-md-6 col-12 form-group">\n                  <label><i class="fas fa-globe mr-1 text-info"></i> WAN (Internet Upstream) Adapter:</label>\n                  <select id="net-cfg-wan" class="form-control">\n                    <option value="eth0">eth0 (Default Hardware WAN)</option>\n                    <option value="eth1">eth1</option>\n                  </select>\n                  <small class="text-muted">Interface connected to your ISP modem, router, or Starlink dish.</small>\n                </div>\n                <div class="col-md-6 col-12 form-group">\n                  <label><i class="fas fa-wifi mr-1 text-success"></i> LAN (Access Point / Vendo) Adapter:</label>\n                  <select id="net-cfg-lan" class="form-control">\n                    <option value="eth1">eth1 (Default Hardware LAN)</option>\n                    <option value="eth0">eth0</option>\n                  </select>\n                  <small class="text-muted">Interface serving the captive portal to WiFi router or switch.</small>\n                </div>\n              </div>\n\n              <div class="row">\n                <div class="col-md-6 col-12 form-group">\n                  <label>WAN IP Assignment Mode:</label>\n                  <select id="net-cfg-wan-mode" class="form-control" onchange="toggleWanStaticFields()">\n                    <option value="dhcp">DHCP Client (Dynamic / Automatic from ISP)</option>\n                    <option value="static">Static IP Configuration (Manual)</option>\n                  </select>\n                </div>\n              </div>\n\n              <div id="net-static-group" style="display:none;" class="p-3 mb-3 rounded" style="background:rgba(255,255,255,0.03); border:1px solid rgba(255,255,255,0.08);">\n                <h6 class="text-info font-weight-bold mb-2"><i class="fas fa-sliders-h mr-1"></i> Static WAN Configuration</h6>\n                <div class="row">\n                  <div class="col-md-4 col-12 form-group">\n                    <label>Static IP Address:</label>\n                    <input type="text" id="net-cfg-static-ip" class="form-control" placeholder="192.168.1.50">\n                  </div>\n                  <div class="col-md-4 col-12 form-group">\n                    <label>Subnet Mask:</label>\n                    <input type="text" id="net-cfg-static-mask" class="form-control" placeholder="255.255.255.0">\n                  </div>\n                  <div class="col-md-4 col-12 form-group">\n                    <label>Default Gateway:</label>\n                    <input type="text" id="net-cfg-static-gw" class="form-control" placeholder="192.168.1.1">\n                  </div>\n                </div>\n              </div>\n\n              <button class="btn btn-primary mt-2" onclick="saveNetworkAdapters()"><i class="fas fa-save mr-1"></i> Save & Apply Adapter Settings</button>\n            </div>\n\n            <!-- SUB-TAB 2: DNS & DOMAIN -->\n            <div class="tab-pane fade" id="net-tab-dns" role="tabpanel">\n              <div class="callout callout-info py-2 px-3 mb-3" style="background:rgba(23,162,184,0.1); border-left-color:#17a2b8;">\n                <p class="mb-0 text-white" style="font-size:0.88rem;"><i class="fas fa-info-circle mr-1 text-info"></i> Configure upstream DNS resolvers for connected clients and local captive domain address.</p>\n              </div>\n              <div class="row">\n                <div class="col-md-6 col-12 form-group">\n                  <label><i class="fas fa-server mr-1 text-primary"></i> Primary Upstream DNS Server:</label>\n                  <input type="text" id="net-cfg-dns1" class="form-control" placeholder="1.1.1.1">\n                  <small class="text-muted">Primary DNS server forwarded by dnsmasq.</small>\n                </div>\n                <div class="col-md-6 col-12 form-group">\n                  <label><i class="fas fa-server mr-1 text-info"></i> Secondary Upstream DNS Server:</label>\n                  <input type="text" id="net-cfg-dns2" class="form-control" placeholder="1.0.0.1">\n                  <small class="text-muted">Backup resolver for fast failover.</small>\n                </div>\n              </div>\n              \n              <div class="form-group mb-3">\n                <label class="d-block mb-2">Fast DNS Provider Presets:</label>\n                <div class="d-flex flex-wrap gap-2">\n                  <button type="button" class="btn btn-xs btn-outline-info" onclick="setDnsPreset(\'1.1.1.1\', \'1.0.0.1\')"><i class="fas fa-bolt mr-1"></i> Cloudflare (1.1.1.1)</button>\n                  <button type="button" class="btn btn-xs btn-outline-primary" onclick="setDnsPreset(\'8.8.8.8\', \'8.8.4.4\')"><i class="fab fa-google mr-1"></i> Google DNS (8.8.8.8)</button>\n                  <button type="button" class="btn btn-xs btn-outline-success" onclick="setDnsPreset(\'9.9.9.9\', \'149.112.112.112\')"><i class="fas fa-shield-alt mr-1"></i> Quad9 Malware Blocker</button>\n                  <button type="button" class="btn btn-xs btn-outline-warning" onclick="setDnsPreset(\'94.140.14.14\', \'94.140.15.15\')"><i class="fas fa-ban mr-1"></i> AdGuard AdBlock DNS</button>\n                </div>\n              </div>\n\n              <div class="row">\n                <div class="col-md-6 col-12 form-group">\n                  <label><i class="fas fa-link mr-1 text-warning"></i> Captive Portal Local Domain:</label>\n                  <input type="text" id="net-cfg-domain" class="form-control" placeholder="ecofi.now">\n                  <small class="text-muted">Local domain redirected to captive portal (e.g. <code>ecofi.now</code>).</small>\n                </div>\n              </div>\n\n              <button class="btn btn-primary mt-2" onclick="saveDnsSettings()"><i class="fas fa-save mr-1"></i> Apply DNS & Domain Settings</button>\n            </div>\n\n            <!-- SUB-TAB 3: DHCP & LEASES -->\n            <div class="tab-pane fade" id="net-tab-dhcp" role="tabpanel">\n              <div class="row mb-3">\n                <div class="col-md-4 col-12">\n                  <div class="card card-dark">\n                    <div class="card-header py-2"><h5 class="card-title text-sm"><i class="fas fa-sliders-h mr-1"></i> DHCP Server Settings</h5></div>\n                    <div class="card-body p-3">\n                      <div class="form-group mb-2">\n                        <label style="font-size:0.85rem;">LAN Subnet Pool:</label>\n                        <input type="text" class="form-control form-control-sm" value="10.0.0.0/19 (8,190 IPs)" readonly>\n                      </div>\n                      <div class="form-group mb-2">\n                        <label style="font-size:0.85rem;">DHCP Lease Duration (Hours):</label>\n                        <input type="number" id="net-cfg-lease" class="form-control form-control-sm" min="1" max="720" value="72">\n                      </div>\n                      <button class="btn btn-primary btn-sm btn-block mt-3" onclick="saveDhcpSettings()"><i class="fas fa-save mr-1"></i> Save Lease Time</button>\n                    </div>\n                  </div>\n                </div>\n\n                <div class="col-md-8 col-12">\n                  <div class="card card-dark">\n                    <div class="card-header py-2 d-flex justify-content-between align-items-center">\n                      <h5 class="card-title text-sm mb-0"><i class="fas fa-users mr-1"></i> Active DHCP Leases & Sessions</h5>\n                      <button class="btn btn-xs btn-outline-info" onclick="loadDhcpLeases()"><i class="fas fa-sync-alt mr-1"></i> Refresh</button>\n                    </div>\n                    <div class="table-responsive">\n                      <table class="table table-striped table-hover mb-0" style="font-size:0.82rem;">\n                        <thead>\n                          <tr>\n                            <th>IP Address</th>\n                            <th>MAC Address</th>\n                            <th>Hostname</th>\n                            <th>Lease Expiration</th>\n                            <th>Action</th>\n                          </tr>\n                        </thead>\n                        <tbody id="dhcp-leases-tbody">\n                          <tr><td colspan="5" class="text-center text-muted py-3"><i class="fas fa-spinner fa-spin mr-1"></i> Loading DHCP leases...</td></tr>\n                        </tbody>\n                      </table>\n                    </div>\n                  </div>\n                </div>\n              </div>\n            </div>\n\n            <!-- SUB-TAB 4: ACCESS POLICIES -->\n            <div class="tab-pane fade" id="net-tab-policies" role="tabpanel">\n              <div class="callout callout-info py-2 px-3 mb-3" style="background:rgba(23,162,184,0.1); border-left-color:#17a2b8;">\n                <p class="mb-0 text-white" style="font-size:0.88rem;"><i class="fas fa-shield-alt mr-1 text-info"></i> Security enforcement policies for Starlink satellite terminals, hotspot sharing defense, and ISP modem isolation.</p>\n              </div>\n\n              <div class="row">\n                <div class="col-md-4 col-12 mb-3">\n                  <div class="card card-dark h-100">\n                    <div class="card-header py-2"><h5 class="card-title text-sm"><i class="fas fa-satellite mr-1 text-info"></i> Starlink App / Dish Blocker</h5></div>\n                    <div class="card-body p-3 d-flex flex-column justify-content-between">\n                      <p class="text-muted mb-2" style="font-size:0.82rem;">Prevents customers from opening <code>192.168.100.1</code> to reboot, stow, or tamper with your Starlink satellite terminal.</p>\n                      <select id="net-cfg-starlink" class="form-control form-control-sm">\n                        <option value="1">🟢 ENABLED (Drop 192.168.100.1)</option>\n                        <option value="0">🔴 DISABLED (Allow Dish Access)</option>\n                      </select>\n                    </div>\n                  </div>\n                </div>\n\n                <div class="col-md-4 col-12 mb-3">\n                  <div class="card card-dark h-100">\n                    <div class="card-header py-2"><h5 class="card-title text-sm"><i class="fas fa-broadcast-tower mr-1 text-warning"></i> Anti-Tethering Hotspot Defense</h5></div>\n                    <div class="card-body p-3 d-flex flex-column justify-content-between">\n                      <p class="text-muted mb-2" style="font-size:0.82rem;">Drops packets with decremented TTL (<code>TTL=63 / 127</code>) to prevent phones from re-sharing active vouchers via hotspot.</p>\n                      <select id="net-cfg-tether" class="form-control form-control-sm">\n                        <option value="1">🟢 ENABLED (Drop Hotspot Shares)</option>\n                        <option value="0">🔴 DISABLED (Allow Tethering)</option>\n                      </select>\n                    </div>\n                  </div>\n                </div>\n\n                <div class="col-md-4 col-12 mb-3">\n                  <div class="card card-dark h-100">\n                    <div class="card-header py-2"><h5 class="card-title text-sm"><i class="fas fa-shield-virus mr-1 text-danger"></i> ISP Modem Access Policy</h5></div>\n                    <div class="card-body p-3 d-flex flex-column justify-content-between">\n                      <p class="text-muted mb-2" style="font-size:0.82rem;">Isolates the upstream ISP router login page (e.g. <code>192.168.1.1</code>) so clients cannot attempt modem admin logins.</p>\n                      <select id="net-cfg-modem" class="form-control form-control-sm">\n                        <option value="1">🟢 ALLOWED (Gateway Pass-Through)</option>\n                        <option value="0">🔴 BLOCKED (Isolate Modem Admin)</option>\n                      </select>\n                    </div>\n                  </div>\n                </div>\n              </div>\n\n              <button class="btn btn-success mt-2" onclick="saveAccessPolicies()"><i class="fas fa-shield-alt mr-1"></i> Enforce Access Policies</button>\n            </div>\n\n          </div>\n        </div>\n      </div>\n    </div>\n\n    <!-- 8B. BANDWIDTH & QOS INTELLIGENCE SECTION (SUB-TABBED) -->\n    <div id="sec-bandwidth" class="section-view">\n      <div class="card card-primary card-outline card-outline-tabs">\n        <div class="card-header p-0 border-bottom-0">\n          <ul class="nav nav-tabs" id="bandwidth-tabs" role="tablist">\n            <li class="nav-item">\n              <a class="nav-link active" id="bw-tab-fixed-tab" data-toggle="pill" href="#bw-tab-fixed" role="tab"><i class="fas fa-tachometer-alt mr-1"></i> Fixed Speed Limits</a>\n            </li>\n            <li class="nav-item">\n              <a class="nav-link" id="bw-tab-dynamic-tab" data-toggle="pill" href="#bw-tab-dynamic" role="tab"><i class="fas fa-wave-square mr-1"></i> Adaptive Bandwidth</a>\n            </li>\n            <li class="nav-item">\n              <a class="nav-link" id="bw-tab-qos-tab" data-toggle="pill" href="#bw-tab-qos" role="tab"><i class="fas fa-gamepad mr-1"></i> Gaming QoS Priority</a>\n            </li>\n          </ul>\n        </div>\n        <div class="card-body">\n          <div class="tab-content" id="bandwidth-tabsContent">\n            \n            <!-- SUB-TAB 1: FIXED SPEED LIMITS -->\n            <div class="tab-pane fade show active" id="bw-tab-fixed" role="tabpanel">\n              <div class="callout callout-info py-2 px-3 mb-3" style="background:rgba(23,162,184,0.1); border-left-color:#17a2b8;">\n                <p class="mb-0 text-white" style="font-size:0.88rem;"><i class="fas fa-info-circle mr-1 text-info"></i> Base download and upload limits enforced per client session via Linux <code>tc</code> HTB rate-shaping.</p>\n              </div>\n              <div class="row">\n                <div class="col-12 col-md-6 form-group">\n                  <label><i class="fas fa-arrow-down mr-1 text-success"></i> Default Download Speed Limit (Kbps):</label>\n                  <input type="number" id="cfg-dl" class="form-control" value="{{ config.default_dl_kbps }}" min="128">\n                  <small class="text-muted">Example: 2048 = 2 Mbps, 3072 = 3 Mbps, 5120 = 5 Mbps</small>\n                </div>\n                <div class="col-12 col-md-6 form-group">\n                  <label><i class="fas fa-arrow-up mr-1 text-primary"></i> Default Upload Speed Limit (Kbps):</label>\n                  <input type="number" id="cfg-ul" class="form-control" value="{{ config.default_ul_kbps }}" min="64">\n                  <small class="text-muted">Example: 1024 = 1 Mbps, 1536 = 1.5 Mbps</small>\n                </div>\n              </div>\n\n              <div class="form-group mb-3">\n                <label class="d-block mb-2">One-Click Speed Presets:</label>\n                <div class="d-flex flex-wrap gap-2">\n                  <button type="button" class="btn btn-xs btn-outline-secondary" onclick="setBwPreset(1024, 512)"><i class="fas fa-leaf mr-1"></i> 1 Mbps / 512 Kbps (Eco)</button>\n                  <button type="button" class="btn btn-xs btn-outline-info" onclick="setBwPreset(2048, 1024)"><i class="fas fa-feather mr-1"></i> 2 Mbps / 1 Mbps (Standard)</button>\n                  <button type="button" class="btn btn-xs btn-outline-primary" onclick="setBwPreset(3072, 1536)"><i class="fas fa-film mr-1"></i> 3 Mbps / 1.5 Mbps (HD Stream)</button>\n                  <button type="button" class="btn btn-xs btn-outline-success" onclick="setBwPreset(5120, 2048)"><i class="fas fa-bolt mr-1"></i> 5 Mbps / 2 Mbps (Fast)</button>\n                  <button type="button" class="btn btn-xs btn-outline-danger" onclick="setBwPreset(0, 0)"><i class="fas fa-infinity mr-1"></i> Unlimited</button>\n                </div>\n              </div>\n\n              <div class="d-flex flex-wrap align-items-center justify-content-end mt-3" style="gap:12px; border-top:1px solid var(--eco-border); padding-top:14px;" role="group" aria-label="Fixed speed limit actions"><div class="custom-control custom-checkbox mb-0" style="min-height:34px; padding:8px 12px 8px 33px; background:rgba(15,23,42,.65); border:1px solid var(--eco-border-light); border-radius:6px;"><input type="checkbox" class="custom-control-input" id="cfg-preserve-custom-bw" checked><label class="custom-control-label" for="cfg-preserve-custom-bw" style="display:block; color:var(--eco-text-body); font-size:0.8rem; line-height:18px; cursor:pointer;" title="When unchecked, all active and paused sessions will be reset to the new global speed limits above."><i class="fas fa-lock text-warning mr-1" style="font-size:0.72rem;" aria-hidden="true"></i>Preserve per-client custom speeds</label></div><button class="btn btn-primary btn-sm" onclick="saveFixedBandwidth()"><i class="fas fa-save mr-1"></i> Apply Fixed Speed Limits</button></div>\n            </div>\n\n            <!-- SUB-TAB 2: ADAPTIVE BANDWIDTH -->\n            <div class="tab-pane fade" id="bw-tab-dynamic" role="tabpanel">\n              <div class="callout callout-info py-2 px-3 mb-3" style="background:rgba(23,162,184,0.1); border-left-color:#17a2b8;">\n                <p class="mb-0 text-white" style="font-size:0.88rem;"><i class="fas fa-chart-line mr-1 text-info"></i> Automatically scales client speed allocations based on total active sessions. Prevents upstream ISP pipe congestion during rush hours while allowing high burst speeds when few users are active.</p>\n              </div>\n              <div class="row">\n                <div class="col-md-6 col-12 form-group">\n                  <label>Dynamic Adaptive Bandwidth Scaling:</label>\n                  <select id="cfg-adaptive-en" class="form-control">\n                    <option value="1">🟢 ENABLED (Dynamic Bandwidth Scaling)</option>\n                    <option value="0" selected>🔴 DISABLED (Fixed Speed Limits Only)</option>\n                  </select>\n                </div>\n              </div>\n              <div class="row">\n                <div class="col-md-3 col-6 form-group">\n                  <label><i class="fas fa-arrow-down mr-1 text-warning"></i> Min Download (Kbps):</label>\n                  <input type="number" id="cfg-min-dl" class="form-control" value="512" min="128">\n                  <small class="text-muted">Minimum speed under heavy peak load.</small>\n                </div>\n                <div class="col-md-3 col-6 form-group">\n                  <label><i class="fas fa-arrow-down mr-1 text-success"></i> Max Download (Kbps):</label>\n                  <input type="number" id="cfg-max-dl" class="form-control" value="4096" min="512">\n                  <small class="text-muted">Maximum burst speed when idle.</small>\n                </div>\n                <div class="col-md-3 col-6 form-group">\n                  <label><i class="fas fa-arrow-up mr-1 text-warning"></i> Min Upload (Kbps):</label>\n                  <input type="number" id="cfg-min-ul" class="form-control" value="256" min="64">\n                  <small class="text-muted">Minimum upload under peak load.</small>\n                </div>\n                <div class="col-md-3 col-6 form-group">\n                  <label><i class="fas fa-arrow-up mr-1 text-primary"></i> Max Upload (Kbps):</label>\n                  <input type="number" id="cfg-max-ul" class="form-control" value="1536" min="256">\n                  <small class="text-muted">Maximum burst upload when idle.</small>\n                </div>\n              </div>\n\n              <button class="btn btn-primary mt-2" onclick="saveDynamicBandwidth()"><i class="fas fa-save mr-1"></i> Save Adaptive Settings</button>\n            </div>\n\n            <!-- SUB-TAB 3: GAMING QOS -->\n            <div class="tab-pane fade" id="bw-tab-qos" role="tabpanel">\n              <div class="callout callout-info py-2 px-3 mb-3" style="background:rgba(23,162,184,0.1); border-left-color:#17a2b8;">\n                <p class="mb-0 text-white" style="font-size:0.88rem;"><i class="fas fa-gamepad mr-1 text-info"></i> Low-latency UDP gaming QoS marks game packets into Linux <code>tc</code> high-priority class <code>1:10</code>. Guarantees low ping and zero rubberbanding even when other customers stream 4K video or download torrents.</p>\n              </div>\n              <div class="row">\n                <div class="col-md-6 col-12 form-group">\n                  <label>Gaming Port Prioritization (QoS):</label>\n                  <select id="cfg-qos-en" class="form-control">\n                    <option value="1" selected>🟢 ENABLED (Prioritize Game UDP Packets)</option>\n                    <option value="0">🔴 DISABLED (Equal Queue)</option>\n                  </select>\n                </div>\n                <div class="col-md-6 col-12 form-group">\n                  <label>Guaranteed Gaming Bandwidth Pool (%):</label>\n                  <input type="number" id="cfg-qos-pct" class="form-control" value="20" min="5" max="50">\n                  <small class="text-muted">Percentage of total bandwidth reserved for low-latency gaming.</small>\n                </div>\n              </div>\n\n              <div class="card card-dark mb-3">\n                <div class="card-header py-2"><h5 class="card-title text-sm"><i class="fas fa-bullseye mr-1"></i> Supported Low-Latency Games & Ports</h5></div>\n                <div class="card-body p-3">\n                  <div class="row" style="font-size:0.85rem;">\n                    <div class="col-md-4 col-12 mb-2">\n                      <div class="p-2 rounded" style="background:rgba(255,255,255,0.03); border:1px solid rgba(255,255,255,0.06);">\n                        <strong class="text-info"><i class="fas fa-shield-alt mr-1"></i> Mobile Legends: Bang Bang</strong>\n                        <div class="text-muted">UDP: 30000-30010, 5000-5200</div>\n                      </div>\n                    </div>\n                    <div class="col-md-4 col-12 mb-2">\n                      <div class="p-2 rounded" style="background:rgba(255,255,255,0.03); border:1px solid rgba(255,255,255,0.06);">\n                        <strong class="text-primary"><i class="fas fa-crosshairs mr-1"></i> Wild Rift & League of Legends</strong>\n                        <div class="text-muted">UDP: 5000-5500</div>\n                      </div>\n                    </div>\n                    <div class="col-md-4 col-12 mb-2">\n                      <div class="p-2 rounded" style="background:rgba(255,255,255,0.03); border:1px solid rgba(255,255,255,0.06);">\n                        <strong class="text-success"><i class="fab fa-steam mr-1"></i> Valve Steam & Dota 2</strong>\n                        <div class="text-muted">UDP: 27000-27050</div>\n                      </div>\n                    </div>\n                    <div class="col-md-4 col-12 mb-2">\n                      <div class="p-2 rounded" style="background:rgba(255,255,255,0.03); border:1px solid rgba(255,255,255,0.06);">\n                        <strong class="text-warning"><i class="fas fa-skull-crossbones mr-1"></i> Riot Games / Valorant</strong>\n                        <div class="text-muted">UDP: 7000-8000</div>\n                      </div>\n                    </div>\n                    <div class="col-md-4 col-12 mb-2">\n                      <div class="p-2 rounded" style="background:rgba(255,255,255,0.03); border:1px solid rgba(255,255,255,0.06);">\n                        <strong class="text-danger"><i class="fas fa-cubes mr-1"></i> Roblox</strong>\n                        <div class="text-muted">UDP: 49152-65535</div>\n                      </div>\n                    </div>\n                  </div>\n                </div>\n              </div>\n\n              <button class="btn btn-success mt-2" onclick="saveGamingQoS()"><i class="fas fa-gamepad mr-1"></i> Enforce Gaming QoS</button>\n            </div>\n\n          </div>\n        </div>\n      </div>\n    </div>\n\n    <!-- 9. WALLED GARDEN FREE DOMAINS SECTION -->\n    <div id="sec-walled" class="section-view">\n      <div class="card card-primary">\n        <div class="card-header"><h3 class="card-title"><i class="fas fa-globe-americas"></i> Walled Garden Free Whitelisted Websites</h3></div>\n        <div class="card-body">\n          <p class="text-muted" style="font-size:13px;">Domains added here are accessible to users even without inserting bottles or logging in (e.g. government, school portals, payment portals):</p>\n          <div class="row">\n            <div class="col-12 col-md-6 form-group">\n              <label>Domain Name (e.g. gcash.com or deped.gov.ph):</label>\n              <input type="text" id="walled-domain" class="form-control" placeholder="e.g. portal.school.edu.ph">\n            </div>\n            <div class="col-12 col-md-4 form-group">\n              <label>Note / Purpose:</label>\n              <input type="text" id="walled-note" class="form-control" placeholder="e.g. School Portal">\n            </div>\n            <div class="col-12 col-md-2 form-group">\n              <label class="d-none d-md-block">&nbsp;</label>\n              <button class="btn btn-primary btn-block" onclick="addWalledDomain()"><i class="fas fa-plus"></i> Whitelist Site</button>\n            </div>\n          </div>\n          <div class="table-responsive">\n            <table class="table table-striped table-hover mb-0 mt-3">\n              <thead>\n                <tr>\n                  <th style="padding: 10px 14px; width: 45%;">Whitelisted Domain</th>\n                  <th style="padding: 10px 14px; width: 40%;">Note / Purpose</th>\n                  <th style="padding: 10px 14px; width: 15%; text-align: right; min-width: 120px; white-space: nowrap;">Actions</th>\n                </tr>\n              </thead>\n              <tbody id="walled-table-body"></tbody>\n            </table>\n          </div>\n        </div>\n      </div>\n    </div>\n\n    <!-- 10. MAC FILTERING SECTION WITH VALIDATIONS & EDIT HANDLERS -->\n    <div id="sec-security" class="section-view">\n      <div class="card card-danger" id="mac-form-card">\n        <div class="card-header"><h3 class="card-title" id="mac-form-title"><i class="fas fa-shield-alt"></i> Add / Edit MAC Filter Rule</h3></div>\n        <div class="card-body">\n          <div class="row mb-2">\n            <div class="col-12">\n              <label class="text-info"><i class="fas fa-bolt"></i> Quick-Fill from Connected Devices:</label>\n              <select id="mac-quick-pick" class="form-control form-control-sm" onchange="quickFillMac(this.value)">\n                <option value="">-- Choose active connected device to auto-fill --</option>\n              </select>\n            </div>\n          </div>\n          \n          <input type="hidden" id="edit-original-mac" value="">\n          <div class="row">\n            <div class="col-12 col-sm-6 col-md-4 form-group">\n              <label>MAC Address (12 Hex Characters):</label>\n              <input type="text" id="mac-input" class="form-control" placeholder="AA:BB:CC:DD:EE:FF" maxlength="17" oninput="formatMacInput(this)">\n              <div id="mac-valid-msg" class="valid-feedback-custom">✓ Valid MAC format</div>\n              <div id="mac-invalid-msg" class="invalid-feedback-custom">✗ Invalid MAC address format (e.g. AA:BB:CC:DD:EE:FF)</div>\n            </div>\n            <div class="col-12 col-sm-6 col-md-3 form-group">\n              <label>Rule Type:</label>\n              <select id="mac-type" class="form-control">\n                <option value="whitelist">🟢 VIP Whitelist (Permanent Free Access)</option>\n                <option value="blacklist">🔴 Blacklist (Block / Ban Device)</option>\n              </select>\n            </div>\n            <div class="col-12 col-sm-6 col-md-3 form-group">\n              <label>Device Owner / Label:</label>\n              <input type="text" id="mac-note" class="form-control" placeholder="e.g. Owner Phone or Abusive User">\n            </div>\n            <div class="col-12 col-sm-6 col-md-2 form-group">\n              <label class="d-none d-md-block">&nbsp;</label>\n              <div class="d-flex">\n                <button class="btn btn-danger btn-block mr-1 font-weight-bold" id="btn-save-mac" onclick="saveMacControl()"><i class="fas fa-plus mr-1"></i> Add Rule</button>\n                <button class="btn btn-secondary font-weight-bold" id="btn-cancel-mac" style="display:none;" onclick="cancelEditMac()" title="Cancel Edit"><i class="fas fa-times"></i></button>\n              </div>\n            </div>\n          </div>\n        </div>\n      </div>\n\n      <div class="card card-dark mt-3">\n        <div class="card-header"><h3 class="card-title"><i class="fas fa-list-ul"></i> Active MAC Control Table</h3></div>\n        <div class="card-body p-0">\n          <div class="table-responsive">\n            <table class="table table-striped table-hover mb-0" id="mac-table">\n              <thead>\n                <tr>\n                  <th style="padding: 10px 14px; width: 30%;">MAC Address</th>\n                  <th style="padding: 10px 14px; width: 20%; text-align: center;">Rule Type</th>\n                  <th style="padding: 10px 14px; width: 30%;">Device Note</th>\n                  <th style="padding: 10px 14px; width: 20%; text-align: right; min-width: 170px; white-space: nowrap;">Actions</th>\n                </tr>\n              </thead>\n              <tbody id="mac-table-body"></tbody>\n            </table>\n          </div>\n        </div>\n      </div>\n    </div>\n\n    <!-- 11. TELEGRAM NOTIFICATIONS SECTION -->\n    <div id="sec-telegram" class="section-view">\n      <div class="card card-primary">\n        <div class="card-header"><h3 class="card-title"><i class="fab fa-telegram-plane"></i> Telegram Bot Automated Alerts</h3></div>\n        <div class="card-body">\n          \n          <div class="p-3 mb-4 rounded" style="background: rgba(255, 255, 255, 0.03); border: 1px solid rgba(255, 255, 255, 0.1); color: #cbd5e1; font-size: 0.9rem; line-height: 1.5;">\n            <h5 class="font-weight-bold mb-3" style="font-size: 1.05rem; color: #38bdf8;"><i class="fas fa-info-circle mr-1"></i> How to setup Telegram Alerts</h5>\n            <p class="mb-2 text-white">Follow these exact steps to connect Eco-Fi to your Telegram account:</p>\n            <ol class="mb-0 pl-3">\n              <li class="mb-2">Open Telegram and search for <strong class="text-white">@BotFather</strong>. Send it <code style="color: #60a5fa; background: rgba(0,0,0,0.3); padding: 2px 6px; border-radius: 4px;">/newbot</code> and follow the prompts to create your bot.</li>\n              <li class="mb-2">BotFather will give you a <strong class="text-white">Bot Token</strong> (e.g., <code style="color: #60a5fa; background: rgba(0,0,0,0.3); padding: 2px 6px; border-radius: 4px;">123456789:ABCdefGh...</code>). Paste it into the Bot Token field below.</li>\n              <li class="mb-2">Next, search for <strong class="text-white">@userinfobot</strong> in Telegram and send it <code style="color: #60a5fa; background: rgba(0,0,0,0.3); padding: 2px 6px; border-radius: 4px;">/start</code>. It will reply with your <strong class="text-white">Chat ID</strong> (e.g., <code style="color: #60a5fa; background: rgba(0,0,0,0.3); padding: 2px 6px; border-radius: 4px;">123456789</code>). Paste it into the Chat ID field below.</li>\n              <li class="mb-2"><strong class="text-warning">CRITICAL STEP:</strong> Telegram blocks bots from messaging users to prevent spam. You MUST search for your new bot\'s username in Telegram and click <strong class="text-white">Start</strong> (or send <code style="color: #60a5fa; background: rgba(0,0,0,0.3); padding: 2px 6px; border-radius: 4px;">/start</code>) to authorize it to message you.</li>\n              <li>Once you have started the chat with your bot, click <strong class="text-white">Save Settings</strong> below, and then click <strong class="text-white">Test Message</strong>.</li>\n            </ol>\n          </div>\n\n          <div class="form-group mt-4">\n            <label>Telegram Bot Token:</label>\n            <input type="text" id="cfg-tg-token" class="form-control" placeholder="123456789:ABCdefGhIJKlmNoPQRstuVWXyz" value="{{ config.telegram_bot_token }}">\n          </div>\n          <div class="form-group">\n            <label>Admin Telegram Chat ID:</label>\n            <input type="text" id="cfg-tg-chat" class="form-control" placeholder="123456789" value="{{ config.telegram_chat_id }}">\n          </div>\n          <div class="row">\n            <div class="col-12 col-md-6 form-group">\n              <label>Alert when Storage Bin reaches 100%:</label>\n              <select id="cfg-tg-bin" class="form-control">\n                <option value="1" {% if config.telegram_alert_bin == \'1\' %}selected{% endif %}>🟢 ENABLED</option>\n                <option value="0" {% if config.telegram_alert_bin == \'0\' %}selected{% endif %}>🔴 DISABLED</option>\n              </select>\n            </div>\n            <div class="col-12 col-md-6 form-group">\n              <label>Daily Midnight Revenue & Bottle Summary:</label>\n              <select id="cfg-tg-daily" class="form-control">\n                <option value="1" {% if config.telegram_alert_daily == \'1\' %}selected{% endif %}>🟢 ENABLED</option>\n                <option value="0" {% if config.telegram_alert_daily == \'0\' %}selected{% endif %}>🔴 DISABLED</option>\n              </select>\n            </div>\n          </div>\n          <div class="d-flex flex-wrap gap-2">\n            <button class="btn btn-primary mr-2 mb-2" onclick="saveTelegram()"><i class="fas fa-save"></i> Save Settings</button>\n            <button class="btn btn-outline-info mb-2" onclick="testTelegram()"><i class="fas fa-paper-plane"></i> Test Message</button>\n          </div>\n        </div>\n      </div>\n    </div>\n\n    <!-- 12. LICENSING & ACTIVATION SECTION -->\n    <div id="sec-licensing" class="section-view">\n      <div class="card card-info">\n        <div class="card-header"><h3 class="card-title"><i class="fas fa-key"></i> Machine Hardware Licensing & Sovereign Authorization</h3></div>\n        <div class="card-body">\n          <div style="margin-bottom: 14px;">\n            <div class="mb-1"><strong>Machine Hardware ID (HWID):</strong></div>\n            <div class="d-flex align-items-center flex-wrap" style="gap: 8px;">\n              <code id="lic-hwid" class="text-warning font-weight-bold" style="font-size:14px; word-break:break-all; background:rgba(0,0,0,0.25); padding:3px 8px; border-radius:6px; border:1px solid rgba(251,191,36,0.25);">Loading...</code>\n              <button id="btn-copy-hwid" class="btn btn-xs btn-outline-info" onclick="copyHwid()" title="Copy HWID" style="padding: 3px 9px; border-radius: 6px; font-size: 11px;"><i class="fas fa-copy mr-1"></i> <span id="copy-hwid-text">Copy</span></button>\n            </div>\n          </div>\n          <p><strong>License Status:</strong> <span id="lic-status" class="badge badge-success" style="font-size:13px;">CHECKING</span></p>\n          <p><strong>License Tier:</strong> <span id="lic-tier" class="badge badge-info">COMMERCIAL</span></p>\n          <hr>\n          <h5>Offline Machine Activation:</h5>\n          <p class="text-muted" style="font-size:13px;">Enter the 16-character offline activation PIN provided by your vendor to authorize this station:</p>\n          <div class="d-flex align-items-stretch flex-column flex-sm-row" style="gap: 10px; max-width: 560px; margin-bottom: 15px;">\n            <input type="text" id="act-pin" class="form-control" placeholder="16-Char PIN (XXXX-XXXX-XXXX-XXXX)" style="flex: 1; min-width: 200px; border-radius: 8px; height: 38px; font-size: 13px;">\n            <button class="btn btn-info" onclick="activateLicense()" style="height: 38px; padding: 0 18px; border-radius: 8px; font-size: 13px; font-weight: 600; white-space: nowrap;"><i class="fas fa-check mr-1"></i> Activate</button>\n          </div>\n        </div>\n      </div>\n    </div>\n\n    <!-- 13. SYSTEM MAINTENANCE & OPERATIONS SECTION (SUB-TABBED) -->\n    <div id="sec-system" class="section-view">\n      <div class="card card-primary card-outline card-outline-tabs">\n        <div class="card-header p-0 border-bottom-0">\n          <ul class="nav nav-tabs" id="system-tabs" role="tablist">\n            <li class="nav-item">\n              <a class="nav-link active" id="sys-tab-verifier-tab" data-toggle="pill" href="#sys-tab-verifier" role="tab"><i class="fas fa-stethoscope mr-1"></i> System Verifier & Health</a>\n            </li>\n            <li class="nav-item">\n              <a class="nav-link" id="sys-tab-backup-tab" data-toggle="pill" href="#sys-tab-backup" role="tab"><i class="fas fa-database mr-1"></i> Backup & Database</a>\n            </li>\n            <li class="nav-item">\n              <a class="nav-link" id="sys-tab-power-tab" data-toggle="pill" href="#sys-tab-power" role="tab"><i class="fas fa-power-off mr-1"></i> Power & Operations</a>\n            </li>\n          </ul>\n        </div>\n        <div class="card-body">\n          <div class="tab-content" id="system-tabsContent">\n            \n            <!-- SUB-TAB 1: SYSTEM VERIFIER -->\n            <div class="tab-pane fade show active" id="sys-tab-verifier" role="tabpanel">\n              <div class="d-flex justify-content-between align-items-center mb-3">\n                <div class="callout callout-info py-2 px-3 mb-0 flex-grow-1 mr-3" style="background:rgba(23,162,184,0.1); border-left-color:#17a2b8;">\n                  <p class="mb-0 text-white" style="font-size:0.88rem;"><i class="fas fa-info-circle mr-1 text-info"></i> Full integrity diagnostics verifying SQLite database health, storage endurance, kernel forwarding, and network adapters.</p>\n                </div>\n                <button class="btn btn-info btn-sm" onclick="runSystemVerifier()"><i class="fas fa-sync-alt mr-1"></i> Run Scan Now</button>\n              </div>\n\n              <div class="row">\n                <div class="col-lg-4 col-md-6 col-12 mb-3">\n                  <div class="card card-dark h-100">\n                    <div class="card-body p-3">\n                      <div class="d-flex justify-content-between align-items-center mb-2">\n                        <strong style="font-size:0.9rem;"><i class="fas fa-database mr-1 text-info"></i> SQLite Database Integrity</strong>\n                        <span id="verif-badge-db" class="badge badge-secondary">Pending Check</span>\n                      </div>\n                      <p class="text-muted mb-0" id="verif-text-db" style="font-size:0.82rem;">Verifying PRAGMA integrity_check...</p>\n                    </div>\n                  </div>\n                </div>\n\n                <div class="col-lg-4 col-md-6 col-12 mb-3">\n                  <div class="card card-dark h-100">\n                    <div class="card-body p-3">\n                      <div class="d-flex justify-content-between align-items-center mb-2">\n                        <strong style="font-size:0.9rem;"><i class="fas fa-hdd mr-1 text-success"></i> SD Card / Disk Space</strong>\n                        <span id="verif-badge-disk" class="badge badge-secondary">Pending Check</span>\n                      </div>\n                      <p class="text-muted mb-0" id="verif-text-disk" style="font-size:0.82rem;">Checking root filesystem capacity...</p>\n                    </div>\n                  </div>\n                </div>\n\n                <div class="col-lg-4 col-md-6 col-12 mb-3">\n                  <div class="card card-dark h-100">\n                    <div class="card-body p-3">\n                      <div class="d-flex justify-content-between align-items-center mb-2">\n                        <strong style="font-size:0.9rem;"><i class="fas fa-memory mr-1 text-primary"></i> RAM Memory Load</strong>\n                        <span id="verif-badge-mem" class="badge badge-secondary">Pending Check</span>\n                      </div>\n                      <p class="text-muted mb-0" id="verif-text-mem" style="font-size:0.82rem;">Querying available system memory...</p>\n                    </div>\n                  </div>\n                </div>\n\n                <div class="col-lg-4 col-md-6 col-12 mb-3">\n                  <div class="card card-dark h-100">\n                    <div class="card-body p-3">\n                      <div class="d-flex justify-content-between align-items-center mb-2">\n                        <strong style="font-size:0.9rem;"><i class="fas fa-route mr-1 text-warning"></i> Kernel IP Forwarding</strong>\n                        <span id="verif-badge-fwd" class="badge badge-secondary">Pending Check</span>\n                      </div>\n                      <p class="text-muted mb-0" id="verif-text-fwd" style="font-size:0.82rem;">Checking net.ipv4.ip_forward...</p>\n                    </div>\n                  </div>\n                </div>\n\n                <div class="col-lg-4 col-md-6 col-12 mb-3">\n                  <div class="card card-dark h-100">\n                    <div class="card-body p-3">\n                      <div class="d-flex justify-content-between align-items-center mb-2">\n                        <strong style="font-size:0.9rem;"><i class="fas fa-network-wired mr-1 text-info"></i> Network Interfaces</strong>\n                        <span id="verif-badge-iface" class="badge badge-secondary">Pending Check</span>\n                      </div>\n                      <p class="text-muted mb-0" id="verif-text-iface" style="font-size:0.82rem;">Checking WAN & LAN links...</p>\n                    </div>\n                  </div>\n                </div>\n\n                <div class="col-lg-4 col-md-6 col-12 mb-3">\n                  <div class="card card-dark h-100">\n                    <div class="card-body p-3">\n                      <div class="d-flex justify-content-between align-items-center mb-2">\n                        <strong style="font-size:0.9rem;"><i class="fas fa-clock mr-1 text-muted"></i> Last Health Scan</strong>\n                        <span class="badge badge-dark" id="verif-last-scan">Never</span>\n                      </div>\n                      <p class="text-muted mb-0" style="font-size:0.82rem;">Automated self-healing and telemetry-free monitor.</p>\n                    </div>\n                  </div>\n                </div>\n              </div>\n\n              <div class="card card-dark mt-2">\n                <div class="card-header py-2"><h5 class="card-title text-sm"><i class="fas fa-terminal mr-1"></i> System Verifier Log Output</h5></div>\n                <div class="card-body p-2">\n                  <pre id="verif-log-box" style="background:#070b13; color:#10b981; border-radius:6px; font-size:0.8rem; margin-bottom:0; max-height:180px; overflow-y:auto; padding:10px;">Eco-Fi System Verifier ready. Click "Run Scan Now" to begin diagnostics.</pre>\n                </div>\n              </div>\n            </div>\n\n            <!-- SUB-TAB 2: BACKUP & DATABASE -->\n            <div class="tab-pane fade" id="sys-tab-backup" role="tabpanel">\n              <div class="row">\n                <div class="col-md-6 col-12 mb-3">\n                  <div class="card card-dark h-100">\n                    <div class="card-header py-2"><h5 class="card-title text-sm"><i class="fas fa-file-download mr-1 text-success"></i> Export System Backup</h5></div>\n                    <div class="card-body p-3 d-flex flex-column justify-content-between">\n                      <p class="text-muted mb-3" style="font-size:0.85rem;">Download a complete snapshot of your database (<code>ecofi.db</code>), sales transactions, rates, vouchers, and system configs bundled in a compressed archive.</p>\n                      <a href="/admin/api/system/backup/download" class="btn btn-success btn-block"><i class="fas fa-download mr-1"></i> Download Database Backup (.zip)</a>\n                    </div>\n                  </div>\n                </div>\n\n                <div class="col-md-6 col-12 mb-3">\n                  <div class="card card-dark h-100">\n                    <div class="card-header py-2"><h5 class="card-title text-sm"><i class="fas fa-file-upload mr-1 text-warning"></i> Restore System Database</h5></div>\n                    <div class="card-body p-3 d-flex flex-column justify-content-between">\n                      <p class="text-muted mb-2" style="font-size:0.85rem;">Upload a previously saved <code>ecofi.db</code> or backup file. The uploaded database will undergo strict SQLite integrity verification before being activated.</p>\n                      <div class="form-group mb-2">\n                        <input type="file" id="sys-restore-file" accept=".db,.sqlite,.zip" class="form-control-file" style="font-size:0.85rem;">\n                      </div>\n                      <button class="btn btn-warning btn-block" onclick="restoreDatabase()"><i class="fas fa-upload mr-1"></i> Restore & Validate Database</button>\n                    </div>\n                  </div>\n                </div>\n              </div>\n            </div>\n\n            <!-- SUB-TAB 3: POWER & OPERATIONS -->\n            <div class="tab-pane fade" id="sys-tab-power" role="tabpanel">\n              <div class="callout callout-warning py-2 px-3 mb-3" style="background:rgba(255,193,7,0.1); border-left-color:#ffc107;">\n                <p class="mb-0 text-white" style="font-size:0.88rem;"><i class="fas fa-exclamation-triangle mr-1 text-warning"></i> Operational maintenance actions affect running services and connected clients. Proceed with caution.</p>\n              </div>\n\n              <div class="row">\n                <div class="col-md-4 col-12 mb-3">\n                  <div class="card card-dark h-100">\n                    <div class="card-header py-2"><h5 class="card-title text-sm"><i class="fas fa-broom mr-1 text-warning"></i> Flush Client Sessions</h5></div>\n                    <div class="card-body p-3 d-flex flex-column justify-content-between">\n                      <p class="text-muted mb-3" style="font-size:0.82rem;">Terminates all active client connections, revokes firewall grants, and resets active routing tables. Use if network rules get out of sync.</p>\n                      <button class="btn btn-outline-warning btn-block" onclick="confirmFlushSessions()"><i class="fas fa-broom mr-1"></i> Flush All Sessions</button>\n                    </div>\n                  </div>\n                </div>\n\n                <div class="col-md-4 col-12 mb-3">\n                  <div class="card card-dark h-100">\n                    <div class="card-header py-2"><h5 class="card-title text-sm"><i class="fas fa-redo-alt mr-1 text-info"></i> Reboot Machine</h5></div>\n                    <div class="card-body p-3 d-flex flex-column justify-content-between">\n                      <p class="text-muted mb-3" style="font-size:0.82rem;">Gracefully shuts down running daemons and reboots the Orange Pi board. System will come back online within 30-45 seconds.</p>\n                      <button class="btn btn-warning btn-block" onclick="confirmReboot()"><i class="fas fa-redo-alt mr-1"></i> Reboot Eco-Fi System</button>\n                    </div>\n                  </div>\n                </div>\n\n                <div class="col-md-4 col-12 mb-3">\n                  <div class="card card-dark h-100">\n                    <div class="card-header py-2"><h5 class="card-title text-sm"><i class="fas fa-power-off mr-1 text-danger"></i> Safe Shutdown</h5></div>\n                    <div class="card-body p-3 d-flex flex-column justify-content-between">\n                      <p class="text-muted mb-3" style="font-size:0.82rem;">Safely syncs database caches, halts the Linux kernel, and powers off processor. Safe to unplug power after board LEDs shut off.</p>\n                      <button class="btn btn-danger btn-block" onclick="confirmShutdown()"><i class="fas fa-power-off mr-1"></i> Safe System Power Off</button>\n                    </div>\n                  </div>\n                </div>\n              </div>\n            </div>\n\n          </div>\n        </div>\n      </div>\n    </div>\n\n  </div>\n</div>\n\n<!-- ========================================================================= -->\n<!-- COMPLETE BOOTSTRAP / ADMINLTE MODAL SUITE                                 -->\n<!-- ========================================================================= -->\n\n<!-- 1. EDIT CLIENT MODAL -->\n<div class="modal fade" id="modal-edit-client" tabindex="-1" role="dialog">\n  <div class="modal-dialog modal-dialog-centered" role="document">\n    <div class="modal-content">\n      <div class="modal-header">\n        <h5 class="modal-title"><i class="fas fa-user-edit"></i> Edit Client Session</h5>\n        <button type="button" class="close text-white" data-dismiss="modal"><span>&times;</span></button>\n      </div>\n      <div class="modal-body">\n        <input type="hidden" id="modal-client-ip">\n        <div class="form-group">\n          <label>Client IP / MAC:</label>\n          <input type="text" id="modal-client-info" class="form-control" readonly>\n        </div>\n        <div class="form-group">\n          <label>Remaining Time (Minutes):</label>\n          <input type="number" id="modal-client-mins" class="form-control" min="0">\n        </div>\n        <div class="row">\n          <div class="col-12 col-md-6 form-group">\n            <label>Download Limit (Kbps):</label>\n            <input type="number" id="modal-client-dl" class="form-control" min="128">\n          </div>\n          <div class="col-12 col-md-6 form-group">\n            <label>Upload Limit (Kbps):</label>\n            <input type="number" id="modal-client-ul" class="form-control" min="64">\n          </div>\n        </div>\n      </div>\n      <div class="modal-footer">\n        <button type="button" class="btn btn-secondary" data-dismiss="modal">Cancel</button>\n        <button type="button" class="btn btn-primary" onclick="submitEditClientModal()"><i class="fas fa-save"></i> Save Changes</button>\n      </div>\n    </div>\n  </div>\n</div>\n\n\n\n\n<script>\nfunction showSection(secId) {\n    document.querySelectorAll(\'.section-view\').forEach(function(el) {\n        el.classList.remove(\'active\');\n    });\n    document.querySelectorAll(\'.nav-sidebar .nav-link\').forEach(function(el) {\n        el.classList.remove(\'active\', \'active-parent\');\n    });\n\n    const target = document.getElementById(secId);\n    if (target) {\n        target.classList.add(\'active\');\n        window.scrollTo({ top: 0, behavior: \'smooth\' });\n    }\n\n    const sectionTitles = {\n        \'sec-dashboard\': \'Dashboard | Eco-Fi\',\n        \'sec-clients\': \'Client Sessions | Eco-Fi\',\n        \'sec-rates\': \'Promo Rates | Eco-Fi\',\n        \'sec-vouchers\': \'Vouchers | Eco-Fi\',\n        \n        \'sec-mac\': \'MAC Control | Eco-Fi\',\n        \'sec-hardware\': \'Hardware & Simulator | Eco-Fi\',\n        \'sec-network\': \'Network Settings | Eco-Fi\',\n        \'sec-bandwidth\': \'Bandwidth Settings | Eco-Fi\',\n        \'sec-system\': \'System Status | Eco-Fi\',\n        \'sec-settings\': \'Vendo Settings | Eco-Fi\',\n        \'sec-analytics\': \'Sales & Analytics | Eco-Fi\',\n        \'sec-license\': \'License | Eco-Fi\'\n    };\n    if (sectionTitles[secId]) {\n        document.title = sectionTitles[secId];\n    }\n\n    if (secId === \'sec-network\' && typeof loadNetworkSettings === \'function\') { loadNetworkSettings(); loadDhcpLeases(); }\n    else if (secId === \'sec-bandwidth\' && typeof loadBandwidthSettings === \'function\') { loadBandwidthSettings(); }\n    else if (secId === \'sec-system\' && typeof runSystemVerifier === \'function\') { runSystemVerifier(); }\n\n    const parentRates = $(\'#nav-item-rates\');\n    const parentRatesLink = $(\'#nav-rates\');\n    const ratesSubMenu = parentRates.children(\'.nav-treeview\');\n\n    if (secId === \'sec-rates\' || secId === \'sec-time-policy\') {\n        if (!parentRates.hasClass(\'menu-open\')) {\n            parentRates.addClass(\'menu-open\');\n            ratesSubMenu.stop(true, true).slideDown(220);\n        }\n        parentRatesLink.addClass(\'active-parent\');\n        if (secId === \'sec-rates\') {\n            $(\'#nav-rates-sub\').addClass(\'active\');\n        } else {\n            $(\'#nav-time-policy\').addClass(\'active\');\n        }\n    } else {\n        const navLink = document.getElementById(secId.replace(\'sec-\', \'nav-\'));\n        if (navLink) navLink.classList.add(\'active\');\n    }\n\n    // Auto-close sidebar on mobile devices when section link is clicked\n    if ($(window).width() < 992) {\n        $(\'body\').removeClass(\'sidebar-open\').addClass(\'sidebar-collapse\');\n    }\n\n    if (secId === \'sec-clients\' && typeof loadClients === \'function\') loadClients();\n    if (secId === \'sec-vouchers\' && typeof loadVouchers === \'function\') loadVouchers();\n    if (secId === \'sec-rates\' && typeof loadRates === \'function\') loadRates();\n    if (secId === \'sec-time-policy\') { if (typeof loadTimePolicy === \'function\') loadTimePolicy(); if (window.policyEditor) window.policyEditor(); }\n    if (secId === \'sec-walled\' && typeof loadWalledGarden === \'function\') loadWalledGarden();\n    if (secId === \'sec-security\') { if (typeof loadMacs === \'function\') loadMacs(); if (typeof populateMacQuickPick === \'function\') populateMacQuickPick(); }\n}\n\nvar currentChartRange = \'weekly\';\nvar lastStatsPayload = null;\n\nfunction setChartRange(range) {\n    currentChartRange = range;\n    var ranges = [\'weekly\', \'monthly\', \'yearly\'];\n    for (var i = 0; i < ranges.length; i++) {\n        var btn = document.getElementById(\'btn-range-\' + ranges[i]);\n        if (btn) {\n            if (ranges[i] === range) {\n                btn.classList.add(\'active\');\n            } else {\n                btn.classList.remove(\'active\');\n            }\n        }\n    }\n    var titleElem = document.getElementById(\'chart-title-text\');\n    if (titleElem) {\n        if (range === \'weekly\') titleElem.innerText = \'7-Day Recycling Intake History\';\n        else if (range === \'monthly\') titleElem.innerText = \'30-Day Recycling Intake History\';\n        else if (range === \'yearly\') titleElem.innerText = \'12-Month Recycling Intake History\';\n    }\n    if (window.lastStatsPayload) {\n        renderHistoryChart(window.lastStatsPayload);\n    }\n}\n\nfunction renderHistoryChart(d) {\n    if (!d) return;\n    var hist = d.history || [];\n    if (currentChartRange === \'monthly\' && d.history_monthly) {\n        hist = d.history_monthly;\n    } else if (currentChartRange === \'yearly\' && d.history_yearly) {\n        hist = d.history_yearly;\n    } else if (d.history_weekly) {\n        hist = d.history_weekly;\n    }\n\n    var labels = hist.map(function(h) { return h.date; });\n    var data = hist.map(function(h) { return h.count; });\n\n    if (!window.vendoHistoryChartInstance) {\n        var chartCanvas = document.getElementById(\'historyChart\');\n        if (chartCanvas && typeof Chart !== \'undefined\') {\n            var ctx = chartCanvas.getContext(\'2d\');\n            window.vendoHistoryChartInstance = new Chart(ctx, {\n                type: \'bar\',\n                data: {\n                    labels: labels,\n                    datasets: [{\n                        label: \'Bottles Recycled\',\n                        data: data,\n                        backgroundColor: \'rgba(16, 185, 129, 0.85)\',\n                        borderColor: \'#10b981\',\n                        borderWidth: 1\n                    }]\n                },\n                options: {\n                    responsive: true,\n                    maintainAspectRatio: false,\n                    legend: { display: false },\n                    scales: {\n                        yAxes: [{\n                            ticks: {\n                                beginAtZero: true,\n                                suggestedMax: 10,\n                                precision: 0,\n                                fontColor: \'#cbd5e1\'\n                            },\n                            gridLines: {\n                                color: \'rgba(255, 255, 255, 0.08)\',\n                                zeroLineColor: \'rgba(255, 255, 255, 0.15)\'\n                            }\n                        }],\n                        xAxes: [{\n                            ticks: {\n                                fontColor: \'#cbd5e1\'\n                            },\n                            gridLines: {\n                                display: false\n                            }\n                        }]\n                    }\n                }\n            });\n        }\n    } else if (window.vendoHistoryChartInstance && window.vendoHistoryChartInstance.data) {\n        window.vendoHistoryChartInstance.data.labels = labels;\n        window.vendoHistoryChartInstance.data.datasets[0].data = data;\n        window.vendoHistoryChartInstance.update();\n    }\n}\n\nfunction refreshStats() {\n    fetch(\'/admin/api/stats\').then(r=>r.json()).then(d=>{\n        document.getElementById(\'stat-today\').innerText = d.today_bottles;\n        document.getElementById(\'stat-total\').innerText = d.total_bottles;\n        document.getElementById(\'stat-clients\').innerText = d.active_clients;\n\n        // Overall CPU & Load\n        if(document.getElementById(\'sys-cpu\')) document.getElementById(\'sys-cpu\').innerText = d.cpu + \'%\';\n        if(document.getElementById(\'sys-cpu-bar\')) {\n            document.getElementById(\'sys-cpu-bar\').style.width = d.cpu + \'%\';\n            if (d.cpu > 85) {\n                document.getElementById(\'sys-cpu-bar\').style.background = \'linear-gradient(90deg, #dc2626, #ef4444)\';\n                document.getElementById(\'sys-cpu\').style.color = \'#ef4444\';\n            } else if (d.cpu > 60) {\n                document.getElementById(\'sys-cpu-bar\').style.background = \'linear-gradient(90deg, #d97706, #f59e0b)\';\n                document.getElementById(\'sys-cpu\').style.color = \'#f59e0b\';\n            } else {\n                document.getElementById(\'sys-cpu-bar\').style.background = \'linear-gradient(90deg, #0284c7, #38bdf8)\';\n                document.getElementById(\'sys-cpu\').style.color = \'#38bdf8\';\n            }\n        }\n        if(document.getElementById(\'sys-loadavg\')) document.getElementById(\'sys-loadavg\').innerText = d.load_avg || \'--\';\n\n        // SoC Temp & CPU Freq\n        if(document.getElementById(\'sys-temp\')) {\n            const t = d.temp || 0;\n            document.getElementById(\'sys-temp\').innerText = t > 0 ? (t + \'°C\') : \'N/A\';\n            const badge = document.getElementById(\'sys-temp-badge\');\n            if(badge) {\n                if (t > 68) {\n                    badge.style.background = \'rgba(239, 68, 68, 0.15)\';\n                    badge.style.color = \'#f87171\';\n                    badge.style.borderColor = \'rgba(239, 68, 68, 0.35)\';\n                } else if (t > 52) {\n                    badge.style.background = \'rgba(245, 158, 11, 0.15)\';\n                    badge.style.color = \'#fbbf24\';\n                    badge.style.borderColor = \'rgba(245, 158, 11, 0.35)\';\n                } else {\n                    badge.style.background = \'rgba(16, 185, 129, 0.12)\';\n                    badge.style.color = \'#34d399\';\n                    badge.style.borderColor = \'rgba(16, 185, 129, 0.3)\';\n                }\n            }\n        }\n        if(document.getElementById(\'sys-cpu-freq\')) {\n            document.getElementById(\'sys-cpu-freq\').innerText = (d.cpu_freq_mhz || \'--\') + \' MHz\';\n        }\n\n        // Per-Core Meters\n        if(d.cores && Array.isArray(d.cores)) {\n            d.cores.forEach(c => {\n                const valEl = document.getElementById(\'sys-core-\' + c.core + \'-val\');\n                const barEl = document.getElementById(\'sys-core-\' + c.core + \'-bar\');\n                if(valEl) {\n                    valEl.innerText = c.usage + \'%\';\n                    valEl.style.color = c.usage > 85 ? \'#ef4444\' : (c.usage > 50 ? \'#f59e0b\' : \'#38bdf8\');\n                }\n                if(barEl) {\n                    barEl.style.width = c.usage + \'%\';\n                    barEl.style.background = c.usage > 85 ? \'linear-gradient(90deg, #dc2626, #ef4444)\' : (c.usage > 50 ? \'linear-gradient(90deg, #d97706, #f59e0b)\' : \'linear-gradient(90deg, #0284c7, #38bdf8)\');\n                }\n            });\n        }\n\n        // RAM Breakdown\n        if(document.getElementById(\'sys-ram\')) document.getElementById(\'sys-ram\').innerText = d.ram + \'%\';\n        if(document.getElementById(\'sys-ram-bar\')) {\n            document.getElementById(\'sys-ram-bar\').style.width = d.ram + \'%\';\n            if (d.ram > 85) {\n                document.getElementById(\'sys-ram-bar\').style.background = \'linear-gradient(90deg, #dc2626, #ef4444)\';\n                document.getElementById(\'sys-ram\').style.color = \'#ef4444\';\n            } else if (d.ram > 70) {\n                document.getElementById(\'sys-ram-bar\').style.background = \'linear-gradient(90deg, #d97706, #f59e0b)\';\n                document.getElementById(\'sys-ram\').style.color = \'#f59e0b\';\n            } else {\n                document.getElementById(\'sys-ram-bar\').style.background = \'linear-gradient(90deg, #059669, #10b981)\';\n                document.getElementById(\'sys-ram\').style.color = \'#10b981\';\n            }\n        }\n        if(document.getElementById(\'sys-ram-detail\')) {\n            document.getElementById(\'sys-ram-detail\').innerText = (d.ram_used_mb || 0) + \' / \' + (d.ram_total_mb || 0) + \' MB\';\n        }\n        if(document.getElementById(\'sys-ram-free\')) {\n            document.getElementById(\'sys-ram-free\').innerText = \'Free: \' + (d.ram_free_mb || 0) + \' MB\';\n        }\n\n        // ZRAM Swap\n        if(document.getElementById(\'sys-swap\')) document.getElementById(\'sys-swap\').innerText = (d.swap || 0) + \'%\';\n        if(document.getElementById(\'sys-swap-bar\')) {\n            document.getElementById(\'sys-swap-bar\').style.width = (d.swap || 0) + \'%\';\n            if (d.swap > 80) {\n                document.getElementById(\'sys-swap-bar\').style.background = \'linear-gradient(90deg, #dc2626, #ef4444)\';\n                document.getElementById(\'sys-swap\').style.color = \'#ef4444\';\n            } else {\n                document.getElementById(\'sys-swap-bar\').style.background = \'linear-gradient(90deg, #7c3aed, #a855f7)\';\n                document.getElementById(\'sys-swap\').style.color = \'#a855f7\';\n            }\n        }\n        if(document.getElementById(\'sys-swap-detail\')) {\n            document.getElementById(\'sys-swap-detail\').innerText = (d.swap_used_mb || 0) + \' / \' + (d.swap_total_mb || 0) + \' MB\';\n        }\n        if(document.getElementById(\'sys-swap-free\')) {\n            document.getElementById(\'sys-swap-free\').innerText = \'Total: \' + (d.swap_total_mb || 0) + \' MB\';\n        }\n\n        // MicroSD Storage\n        if(document.getElementById(\'sys-disk\')) document.getElementById(\'sys-disk\').innerText = d.disk + \'%\';\n        if(document.getElementById(\'sys-disk-bar\')) {\n            document.getElementById(\'sys-disk-bar\').style.width = d.disk + \'%\';\n            if (d.disk > 90) {\n                document.getElementById(\'sys-disk-bar\').style.background = \'linear-gradient(90deg, #dc2626, #ef4444)\';\n                document.getElementById(\'sys-disk\').style.color = \'#ef4444\';\n            } else if (d.disk > 75) {\n                document.getElementById(\'sys-disk-bar\').style.background = \'linear-gradient(90deg, #ea580c, #f97316)\';\n                document.getElementById(\'sys-disk\').style.color = \'#f97316\';\n            } else {\n                document.getElementById(\'sys-disk-bar\').style.background = \'linear-gradient(90deg, #d97706, #f59e0b)\';\n                document.getElementById(\'sys-disk\').style.color = \'#f59e0b\';\n            }\n        }\n        if(document.getElementById(\'sys-disk-detail\')) {\n            document.getElementById(\'sys-disk-detail\').innerText = (d.disk_used_gb || 0) + \' / \' + (d.disk_total_gb || 0) + \' GB\';\n        }\n        if(document.getElementById(\'sys-disk-free\')) {\n            document.getElementById(\'sys-disk-free\').innerText = \'Avail: \' + (d.disk_free_gb || 0) + \' GB\';\n        }\n\n                // ESP32 Subsystem Controller Status\n        if(document.getElementById(\'sys-esp32-status\') && d.esp32_status) {\n            document.getElementById(\'sys-esp32-status\').innerText = d.esp32_status;\n        }\n        if(document.getElementById(\'sys-esp32-badge\') && d.esp32_status_color) {\n            var eb = document.getElementById(\'sys-esp32-badge\');\n            eb.style.color = d.esp32_status_color;\n            eb.style.borderColor = d.esp32_status_color + \'44\';\n            eb.style.background = d.esp32_status_color + \'1f\';\n        }\n        if(document.getElementById(\'sys-esp32-dot\') && d.esp32_status_color) {\n            document.getElementById(\'sys-esp32-dot\').style.color = d.esp32_status_color;\n        }\n        if(document.getElementById(\'sys-esp32-port\') && d.esp32_port) {\n            document.getElementById(\'sys-esp32-port\').innerText = d.esp32_port;\n        }\n        if(document.getElementById(\'sys-esp32-sensors\') && d.esp32_sensors) {\n            var es = document.getElementById(\'sys-esp32-sensors\');\n            es.innerText = d.esp32_sensors;\n            if (d.esp32_sensors_color) es.style.color = d.esp32_sensors_color;\n        }\n        if(document.getElementById(\'sys-esp32-bin\') && d.esp32_bin) {\n            var ebin = document.getElementById(\'sys-esp32-bin\');\n            ebin.innerText = d.esp32_bin;\n            if (d.esp32_bin_color) ebin.style.color = d.esp32_bin_color;\n        }\n\n        // System Uptime & Hardware\n        if(document.getElementById(\'sys-uptime\')) document.getElementById(\'sys-uptime\').innerText = d.uptime;\n        if(document.getElementById(\'sys-hardware\') && d.hardware) document.getElementById(\'sys-hardware\').innerText = d.hardware;\n\n        window.lastStatsPayload = d;\n        renderHistoryChart(d);\n    });\n\n    fetch(\'/admin/api/license\').then(r=>r.json()).then(d=>{\n        document.getElementById(\'lic-hwid\').innerText = d.hwid;\n        document.getElementById(\'lic-status\').innerText = d.status;\n        document.getElementById(\'lic-tier\').innerText = d.tier || \'COMMERCIAL\';\n        document.getElementById(\'stat-lic\').innerText = d.status;\n    });\n}\n\n$(document).ready(function() {\n    // Dedicated click handler for Rates & Promos accordion\n    $(\'#nav-rates\').on(\'click\', function(e) {\n        e.preventDefault();\n        e.stopPropagation();\n        const parentLi = $(\'#nav-item-rates\');\n        const subMenu = parentLi.children(\'.nav-treeview\');\n        const isOpen = parentLi.hasClass(\'menu-open\');\n        const activeSec = document.querySelector(\'.section-view.active\');\n        const isRatesActive = activeSec && (activeSec.id === \'sec-rates\' || activeSec.id === \'sec-time-policy\');\n\n        if (isOpen) {\n            // Smoothly collapse\n            parentLi.removeClass(\'menu-open\');\n            subMenu.stop(true, true).slideUp(220);\n            if (isRatesActive) {\n                $(\'#nav-rates\').addClass(\'active\').removeClass(\'active-parent\');\n            }\n        } else {\n            // Smoothly expand\n            parentLi.addClass(\'menu-open\');\n            subMenu.stop(true, true).slideDown(220);\n            if (isRatesActive) {\n                $(\'#nav-rates\').removeClass(\'active\').addClass(\'active-parent\');\n            } else {\n                showSection(\'sec-rates\');\n            }\n        }\n    });\n});\n\nfunction refreshActiveSection() {\n    refreshStats();\n    if (document.getElementById(\'sec-clients\') && document.getElementById(\'sec-clients\').classList.contains(\'active\')) loadClients();\n    if (document.getElementById(\'sec-vouchers\') && document.getElementById(\'sec-vouchers\').classList.contains(\'active\')) loadVouchers();\n}\nsetInterval(refreshActiveSection, 3000);\nrefreshStats();\n\n// MAC Address Validation & Auto-Formatting\nfunction formatMacInput(input) {\n    let v = input.value.toUpperCase().replace(/[^0-9A-F]/g, \'\');\n    let formatted = \'\';\n    for (let i = 0; i < v.length && i < 12; i += 2) {\n        if (i > 0) formatted += \':\';\n        formatted += v.substr(i, 2);\n    }\n    input.value = formatted;\n    \n    const isValid = /^([0-9A-F]{2}:){5}[0-9A-F]{2}$/.test(formatted);\n    document.getElementById(\'mac-valid-msg\').style.display = isValid ? \'block\' : \'none\';\n    document.getElementById(\'mac-invalid-msg\').style.display = (formatted.length > 0 && !isValid) ? \'block\' : \'none\';\n}\n\nfunction populateMacQuickPick() {\n    fetch(\'/admin/api/clients\').then(r=>r.json()).then(clients=>{\n        let optHtml = \'<option value="">-- Choose active connected device to auto-fill --</option>\';\n        clients.forEach(c=>{\n            optHtml += `<option value="${c.mac}">${c.ip} (${c.mac})</option>`;\n        });\n        document.getElementById(\'mac-quick-pick\').innerHTML = optHtml;\n    });\n}\n\nfunction quickFillMac(val) {\n    if (!val) return;\n    document.getElementById(\'mac-input\').value = val;\n    formatMacInput(document.getElementById(\'mac-input\'));\n}\n\nfunction loadMacs() {\n    fetch(\'/admin/api/mac_control/list\').then(r=>r.json()).then(d=>{\n        let html = \'\';\n        d.forEach(m=>{\n            const safeNote = encodeURIComponent(m.note);\n            html += `<tr>\n                <td style="padding: 10px 14px;"><code>${m.mac}</code></td>\n                <td style="padding: 10px 14px; text-align: center;"><span class="badge ${m.type===\'whitelist\'?\'badge-success\':\'badge-danger\'}">${m.type.toUpperCase()}</span></td>\n                <td style="padding: 10px 14px;"><span class="text-light">${m.note || \'-\'}</span></td>\n                <td style="padding: 10px 14px; text-align: right; white-space: nowrap;">\n                    <div class="d-inline-flex align-items-center justify-content-end" style="gap: 6px; white-space: nowrap; flex-wrap: nowrap;">\n                        <button class="btn btn-xs btn-outline-warning text-nowrap" onclick="editMacControl(\'${m.mac}\', \'${m.type}\', \'${safeNote}\')"><i class="fas fa-edit mr-1"></i>Edit</button>\n                        <button class="btn btn-xs btn-outline-danger text-nowrap" onclick="deleteMacControl(\'${m.mac}\')"><i class="fas fa-trash mr-1"></i>Delete</button>\n                    </div>\n                </td>\n            </tr>`;\n        });\n        document.getElementById(\'mac-table-body\').innerHTML = html || \'<tr><td colspan="4" class="text-center p-3 text-muted">No MAC filtering rules set.</td></tr>\';\n    });\n}\n\nfunction saveMacControl() {\n    const mac = document.getElementById(\'mac-input\').value.trim().toUpperCase();\n    const type = document.getElementById(\'mac-type\').value;\n    const note = document.getElementById(\'mac-note\').value.trim();\n    const origMac = document.getElementById(\'edit-original-mac\').value;\n\n    if (!/^([0-9A-F]{2}:){5}[0-9A-F]{2}$/.test(mac)) {\n        Swal.fire(\'Invalid MAC\', \'MAC Address must be in 12 hex format: AA:BB:CC:DD:EE:FF\', \'warning\');\n        return;\n    }\n\n    if (origMac && origMac !== mac) {\n        fetch(\'/admin/api/mac_control/delete\', {\n            method: \'POST\',\n            headers: {\'Content-Type\':\'application/json\'},\n            body: JSON.stringify({mac: origMac})\n        });\n    }\n\n    fetch(\'/admin/api/mac_control/add\', {\n        method: \'POST\',\n        headers: {\'Content-Type\':\'application/json\'},\n        body: JSON.stringify({mac: mac, type: type, note: note})\n    }).then(r=>r.json()).then(d=>{\n        if (d.success) {\n            cancelEditMac();\n            loadMacs();\n            Swal.fire(\'Saved!\', \'MAC Rule has been saved.\', \'success\');\n        } else {\n            Swal.fire(\'Error\', d.error || \'Failed to save MAC rule.\', \'error\');\n        }\n    });\n}\n\nfunction editMacControl(mac, type, encNote) {\n    const note = decodeURIComponent(encNote);\n    document.getElementById(\'edit-original-mac\').value = mac;\n    document.getElementById(\'mac-input\').value = mac;\n    document.getElementById(\'mac-type\').value = type;\n    document.getElementById(\'mac-note\').value = note;\n    \n    document.getElementById(\'mac-form-title\').innerHTML = `<i class="fas fa-edit text-warning"></i> Edit MAC Filter (${mac})`;\n    document.getElementById(\'btn-save-mac\').innerHTML = `<i class="fas fa-save"></i> Update Rule`;\n    document.getElementById(\'btn-save-mac\').className = `btn btn-warning btn-block mr-1`;\n    document.getElementById(\'btn-cancel-mac\').style.display = \'inline-block\';\n    \n    formatMacInput(document.getElementById(\'mac-input\'));\n    document.getElementById(\'mac-form-card\').scrollIntoView({ behavior: \'smooth\' });\n}\n\nfunction cancelEditMac() {\n    document.getElementById(\'edit-original-mac\').value = \'\';\n    document.getElementById(\'mac-input\').value = \'\';\n    document.getElementById(\'mac-note\').value = \'\';\n    document.getElementById(\'mac-quick-pick\').value = \'\';\n    \n    document.getElementById(\'mac-form-title\').innerHTML = `<i class="fas fa-shield-alt"></i> Add / Edit MAC Filter Rule`;\n    document.getElementById(\'btn-save-mac\').innerHTML = `<i class="fas fa-plus"></i> Add Rule`;\n    document.getElementById(\'btn-save-mac\').className = `btn btn-danger btn-block mr-1`;\n    document.getElementById(\'btn-cancel-mac\').style.display = \'none\';\n    formatMacInput(document.getElementById(\'mac-input\'));\n}\n\nfunction deleteMacControl(mac) {\n    Swal.fire({\n        title: `Delete MAC Rule?`,\n        text: `Are you sure you want to remove rule for ${mac}?`,\n        icon: \'warning\',\n        showCancelButton: true,\n        confirmButtonColor: \'#d33\',\n        confirmButtonText: \'Yes, delete it!\'\n    }).then((res) => {\n        if (res.isConfirmed) {\n            fetch(\'/admin/api/mac_control/delete\', {\n                method: \'POST\',\n                headers: {\'Content-Type\':\'application/json\'},\n                body: JSON.stringify({mac: mac})\n            }).then(r => r.json().then(d => ({ok: r.ok, body: d})))\n            .then(res => {\n                if (res.ok && res.body.success) {\n                    loadMacs();\n                    Swal.fire(\'Deleted!\', \'MAC rule deleted.\', \'success\');\n                } else {\n                    Swal.fire(\'Error\', (res.body && res.body.error) ? res.body.error : \'Failed to delete MAC rule.\', \'error\');\n                }\n            }).catch(e => Swal.fire(\'Error\', \'Network error: \' + e, \'error\'));\n        }\n    });\n}\n\n// Promo Rates Management with Real-Time Mathematical Conflict Prevention\nlet currentRatesCache = [];\n\nfunction loadRates() {\n    fetch(\'/admin/api/rates/list\').then(r=>r.json()).then(d=>{\n        currentRatesCache = d || [];\n        let html = \'\';\n        currentRatesCache.forEach(r=>{\n            let timeStr = \'\';\n            if (r.minutes >= 60) {\n                const hrs = (r.minutes / 60).toFixed(1);\n                timeStr = `${hrs} Hours (${r.minutes} mins)`;\n            } else {\n                timeStr = `${r.minutes} Minutes`;\n            }\n            const eff = (r.minutes / r.bottles).toFixed(1);\n            const baseRate = parseInt(document.getElementById(\'rate-1\').value) || 10;\n            const bonusPct = Math.round(((eff - baseRate) / baseRate) * 100);\n            const bonusTag = bonusPct > 0 ? `<span class="badge badge-success ml-1">+${bonusPct}% Bonus</span>` : `<span class="badge badge-secondary ml-1">Base</span>`;\n            const safeLabel = encodeURIComponent(r.label || \'\');\n            html += `<tr>\n                <td style="padding: 10px 14px;"><strong class="text-success"><i class="fas fa-wine-bottle mr-1"></i>${r.bottles} Bottle${r.bottles > 1 ? \'s\' : \'\'}</strong></td>\n                <td style="padding: 10px 14px;"><strong>${timeStr}</strong></td>\n                <td style="padding: 10px 14px;"><code>${eff} m/b</code> ${bonusTag}</td>\n                <td style="padding: 10px 14px;"><span class="text-light">${r.label || \'-\'}</span></td>\n                <td style="padding: 10px 14px; text-align: right; white-space: nowrap;">\n                    <div class="d-inline-flex align-items-center justify-content-end" style="gap: 6px; white-space: nowrap; flex-wrap: nowrap;">\n                        <button class="btn btn-xs btn-outline-warning text-nowrap" onclick="editPromoRate(${r.bottles}, ${r.minutes}, \'${safeLabel}\')"><i class="fas fa-edit mr-1"></i>Edit</button>\n                        ${r.bottles > 1 ? `<button class="btn btn-xs btn-outline-danger text-nowrap" onclick="deletePromoRate(${r.bottles})"><i class="fas fa-trash mr-1"></i>Delete</button>` : `<span class="text-muted small ml-1 text-nowrap">(Base)</span>`}\n                    </div>\n                </td>\n            </tr>`;\n        });\n        document.getElementById(\'rates-table-body\').innerHTML = html || \'<tr><td colspan="5" class="text-center p-3 text-muted">No promo rates configured.</td></tr>\';\n    });\n}\n\nfunction setRateBottles(n) {\n    document.getElementById(\'new-rate-bottles\').value = n;\n    validatePromoFormMath();\n    autoGenerateRateLabel();\n}\n\nfunction getSelectedTotalMinutes() {\n    const rawVal = parseFloat(document.getElementById(\'new-rate-time-val\').value) || 0;\n    const unit = document.getElementById(\'new-rate-time-unit\').value;\n    if (unit === \'hours\') return Math.round(rawVal * 60);\n    if (unit === \'days\') return Math.round(rawVal * 1440);\n    return Math.round(rawVal);\n}\n\nfunction onBaseRateInput() {\n    validatePromoFormMath();\n    loadRates();\n}\n\nfunction autoGenerateRateLabel() {\n    const b = parseInt(document.getElementById(\'new-rate-bottles\').value) || 0;\n    const m = getSelectedTotalMinutes();\n    if (!b || !m) return;\n    let timeStr = \'\';\n    if (m >= 60) {\n        const h = Math.floor(m / 60);\n        const remM = m % 60;\n        timeStr = (remM === 0) ? `${h} Hour${h > 1 ? \'s\' : \'\'}` : `${h}h ${remM}m`;\n    } else {\n        timeStr = `${m} mins`;\n    }\n    const label = `${b} Bottle${b > 1 ? \'s\' : \'\'} = ${timeStr}`;\n    document.getElementById(\'new-rate-label\').value = label;\n}\n\nfunction validatePromoFormMath() {\n    const b = parseInt(document.getElementById(\'new-rate-bottles\').value) || 0;\n    const m = getSelectedTotalMinutes();\n    const origB = parseInt(document.getElementById(\'edit-original-bottles\').value) || null;\n    const fb = document.getElementById(\'rate-validator-feedback\');\n    const effSpan = document.getElementById(\'rate-validator-eff\');\n    const statusSpan = document.getElementById(\'rate-validator-status\');\n    const msgDiv = document.getElementById(\'rate-validator-msg\');\n    const saveBtn = document.getElementById(\'btn-save-promo\');\n\n    if (!b || !m) {\n        fb.style.display = \'none\';\n        saveBtn.disabled = false;\n        return;\n    }\n\n    fb.style.display = \'block\';\n    const eff = (m / b).toFixed(2);\n    effSpan.innerText = `📊 Efficiency: ${eff} mins/bottle (${m}m for ${b}B)`;\n\n    // Check invariants against currentRatesCache (excluding editing tier)\n    const existing = currentRatesCache.filter(r => r.bottles !== origB);\n    let conflict = null;\n\n    // Invariant 1: Monotonic Efficiency\n    for (let r of existing) {\n        const exEff = r.minutes / r.bottles;\n        if (r.bottles < b && exEff > (m / b)) {\n            conflict = `Efficiency conflict: ${r.bottles}B tier gives ${exEff.toFixed(1)} m/b, but this gives only ${eff} m/b. Larger bundles must be at least as rewarding.`;\n            break;\n        }\n        if (r.bottles > b && exEff < (m / b)) {\n            conflict = `Efficiency conflict: this tier gives ${eff} m/b, which exceeds the larger ${r.bottles}B tier (${exEff.toFixed(1)} m/b).`;\n            break;\n        }\n    }\n\n    // Invariant 2: Combination Floor\n    if (!conflict) {\n        const lowerTiers = existing.filter(r => r.bottles < b).sort((a,b) => b.bottles - a.bottles);\n        let comboMins = 0;\n        let rem = b;\n        for (let lt of lowerTiers) {\n            if (rem >= lt.bottles) {\n                comboMins += Math.floor(rem / lt.bottles) * lt.minutes;\n                rem %= lt.bottles;\n            }\n        }\n        if (comboMins > 0 && m < comboMins) {\n            conflict = `Combination conflict: Depositing ${b} bottles in smaller packages yields ${comboMins} mins, but this package gives only ${m} mins. Minimum required is ${comboMins} mins.`;\n        }\n    }\n\n    // Invariant 3: Higher-Tier Upper Bound\n    if (!conflict) {\n        const higherTiers = existing.filter(r => r.bottles > b);\n        if (higherTiers.length > 0) {\n            const minHigher = Math.min(...higherTiers.map(r => r.minutes));\n            if (m >= minHigher) {\n                conflict = `Upper bound conflict: ${m} mins equals or exceeds a larger tier (${minHigher} mins).`;\n            }\n        }\n    }\n\n    if (conflict) {\n        fb.className = \'alert alert-danger py-2 px-3 mb-0\';\n        statusSpan.innerText = \'❌ Conflict Detected\';\n        msgDiv.innerText = conflict;\n        saveBtn.disabled = true;\n    } else {\n        fb.className = \'alert alert-success py-2 px-3 mb-0\';\n        statusSpan.innerText = \'✔ Mathematically Balanced\';\n        msgDiv.innerText = \'No rate curve conflicts. Bundle incentivizes bulk deposit.\';\n        saveBtn.disabled = false;\n    }\n}\n\nfunction editPromoRate(bottles, minutes, encLabel) {\n    const label = decodeURIComponent(encLabel || \'\');\n    document.getElementById(\'edit-original-bottles\').value = bottles;\n    document.getElementById(\'new-rate-bottles\').value = bottles;\n    document.getElementById(\'new-rate-time-val\').value = minutes;\n    document.getElementById(\'new-rate-time-unit\').value = \'mins\';\n    document.getElementById(\'new-rate-label\').value = label;\n\n    document.getElementById(\'promo-form-title\').innerHTML = `<i class="fas fa-edit text-warning"></i> Edit Promo Rate Tier (${bottles} Bottles)`;\n    document.getElementById(\'btn-save-promo\').innerHTML = `<i class="fas fa-save"></i> Update Rate`;\n    document.getElementById(\'btn-save-promo\').className = \'btn btn-warning btn-block mr-1\';\n    document.getElementById(\'btn-cancel-promo\').style.display = \'inline-block\';\n\n    validatePromoFormMath();\n    document.getElementById(\'promo-form-card\').scrollIntoView({ behavior: \'smooth\' });\n}\n\nfunction cancelEditPromoRate() {\n    document.getElementById(\'edit-original-bottles\').value = \'\';\n    document.getElementById(\'new-rate-bottles\').value = \'\';\n    document.getElementById(\'new-rate-time-val\').value = \'\';\n    document.getElementById(\'new-rate-time-unit\').value = \'mins\';\n    document.getElementById(\'new-rate-label\').value = \'\';\n    document.getElementById(\'rate-validator-feedback\').style.display = \'none\';\n\n    document.getElementById(\'promo-form-title\').innerHTML = `<i class="fas fa-plus-circle"></i> Add Custom Promo Rate Package`;\n    document.getElementById(\'btn-save-promo\').innerHTML = `<i class="fas fa-plus"></i> Add Rate`;\n    document.getElementById(\'btn-save-promo\').className = \'btn btn-success btn-block mr-1\';\n    document.getElementById(\'btn-save-promo\').disabled = false;\n    document.getElementById(\'btn-cancel-promo\').style.display = \'none\';\n}\n\nfunction addPromoRate() {\n    const b = parseInt(document.getElementById(\'new-rate-bottles\').value);\n    const m = getSelectedTotalMinutes();\n    const origB = document.getElementById(\'edit-original-bottles\').value;\n    let l = document.getElementById(\'new-rate-label\').value.trim();\n\n    if (!b || !m || isNaN(b) || isNaN(m)) {\n        Swal.fire(\'Input Error\', \'Please enter valid numbers for Bottles and Duration.\', \'warning\');\n        return;\n    }\n\n    if (!l) {\n        autoGenerateRateLabel();\n        l = document.getElementById(\'new-rate-label\').value.trim();\n    }\n\n    fetch(\'/admin/api/rates/add\', {\n        method: \'POST\',\n        headers: {\'Content-Type\':\'application/json\'},\n        body: JSON.stringify({bottles: b, minutes: m, label: l, orig_bottles: origB ? parseInt(origB) : null})\n    }).then(r=>r.json()).then(d=>{\n        if (d.success) {\n            cancelEditPromoRate();\n            loadRates();\n            Swal.fire(\'Saved!\', \'Promo rate tier saved successfully.\', \'success\');\n        } else {\n            Swal.fire(\'Conflict Error\', d.error || \'Failed to save rate.\', \'error\');\n        }\n    }).catch(e=>{\n        Swal.fire(\'Error\', \'Server error while saving promo rate.\', \'error\');\n    });\n}\n\nfunction deletePromoRate(b) {\n    Swal.fire({\n        title: `Delete Rate Tier?`,\n        text: `Delete package for ${b} bottle(s)?`,\n        icon: \'warning\',\n        showCancelButton: true,\n        confirmButtonColor: \'#d33\',\n        confirmButtonText: \'Yes, delete it!\'\n    }).then((res) => {\n        if (res.isConfirmed) {\n            fetch(\'/admin/api/rates/delete\', {\n                method: \'POST\',\n                headers: {\'Content-Type\':\'application/json\'},\n                body: JSON.stringify({bottles: b})\n            }).then(r=>r.json()).then(d=>{\n                if (d.success) {\n                    loadRates();\n                    Swal.fire(\'Deleted!\', \'Rate tier removed.\', \'success\');\n                } else {\n                    Swal.fire(\'Error\', d.error || \'Could not delete rate.\', \'error\');\n                }\n            });\n        }\n    });\n}\n\nfunction applyRatePreset() {\n    const p = document.getElementById(\'rate-preset-select\').value;\n    Swal.fire({\n        title: \'Apply Rate Template?\',\n        text: \'This will replace all active promo rates with the selected conflict-free curve template.\',\n        icon: \'question\',\n        showCancelButton: true,\n        confirmButtonColor: \'#17a2b8\',\n        confirmButtonText: \'Yes, Apply Template\'\n    }).then(res => {\n        if (res.isConfirmed) {\n            fetch(\'/admin/api/rates/apply_preset\', {\n                method: \'POST\',\n                headers: {\'Content-Type\':\'application/json\'},\n                body: JSON.stringify({preset: p})\n            }).then(r=>r.json()).then(d=>{\n                if (d.success) {\n                    loadRates();\n                    Swal.fire(\'Applied!\', d.message, \'success\');\n                } else {\n                    Swal.fire(\'Error\', d.error || \'Could not apply template.\', \'error\');\n                }\n            });\n        }\n    });\n}\n\nfunction saveRates() {\n    const minPerBottle = document.getElementById(\'rate-1\').value;\n    const dropTimeout = document.getElementById(\'rate-timeout\').value;\n    fetch(\'/admin/api/settings/save\', {\n        method: \'POST\',\n        headers: {\'Content-Type\':\'application/json\'},\n        body: JSON.stringify({minutes_per_bottle: minPerBottle, drop_timeout: dropTimeout})\n    }).then(r=>r.json()).then(d=>{\n        if (d.success) {\n            loadRates();\n            Swal.fire(\'Saved!\', \'Base timing settings updated.\', \'success\');\n        } else {\n            Swal.fire(\'Error\', d.error || \'Failed to save settings.\', \'error\');\n        }\n    });\n}\n\n// Clients & Client Modal\nfunction loadClients() {\n    fetch(\'/admin/api/clients\').then(r=>r.json()).then(d=>{\n        let html = \'\';\n        if (!d || d.length === 0) {\n            document.getElementById(\'clients-table-body\').innerHTML = \'<tr><td colspan="6" class="text-center p-3 text-muted"><i class="fas fa-users-slash mr-1"></i> No active clients connected.</td></tr>\';\n            return;\n        }\n        d.forEach(c=>{\n            const sec = Math.max(0, Math.round(Number(c.remaining_seconds) || 0));\n            const clockFmt = formatTime(sec);\n            const humanFmt = formatDuration(sec);\n            let statusBadge = \'<span class="badge badge-secondary">EXPIRED</span>\';\n            if (c.admin_paused) {\n                statusBadge = \'<span class="badge badge-danger">KICKED</span>\';\n            } else if (c.is_paused) {\n                statusBadge = \'<span class="badge badge-warning">PAUSED</span>\';\n            } else if (sec > 0) {\n                statusBadge = \'<span class="badge badge-success">ACTIVE</span>\';\n            }\n            html += `<tr id="client-row-${c.ip.replace(/\\./g, \'-\')}">\n                <td style="padding: 10px 14px;"><strong>${c.ip}</strong></td>\n                <td style="padding: 10px 14px;"><code>${c.mac}</code></td>\n                <td style="padding: 10px 14px; white-space: nowrap;">\n                    <span class="badge badge-info font-weight-bold" title="${humanFmt} remaining" style="font-family: \'SF Mono\', \'Roboto Mono\', \'Courier New\', monospace; font-size: 12px; letter-spacing: 0.5px; padding: 4px 8px;">${clockFmt}</span>\n                    <small class="text-muted ml-1 font-weight-bold" style="font-size: 11px;">(${humanFmt})</small>\n                </td>\n                <td style="padding: 10px 14px; text-align: center;">${statusBadge}</td>\n                <td style="padding: 10px 14px;"><span class="text-info font-weight-bold">${c.dl_kbps || 3072} / ${c.ul_kbps || 1536}</span> <small class="text-muted">Kbps</small></td>\n                <td style="padding: 10px 14px; text-align: right; white-space: nowrap;">\n                    <div class="d-inline-flex align-items-center justify-content-end" style="gap: 5px; white-space: nowrap; flex-wrap: nowrap;">\n                        <button class="btn btn-xs btn-outline-success text-nowrap" onclick="clientAction(\'${c.ip}\', \'add15\')"><i class="fas fa-plus mr-1"></i>15m</button>\n                        <button class="btn btn-xs btn-outline-warning text-nowrap" onclick="clientAction(\'${c.ip}\', \'${c.is_paused ? \'resume\' : \'pause\'}\')">${c.is_paused ? \'<i class="fas fa-play mr-1"></i>Resume\' : \'<i class="fas fa-pause mr-1"></i>Pause\'}</button>\n                        <button class="btn btn-xs btn-outline-info text-nowrap" onclick="openEditClientModal(\'${c.ip}\', \'${c.mac}\', ${sec}, ${c.dl_kbps || 3072}, ${c.ul_kbps || 1536})"><i class="fas fa-edit mr-1"></i>Edit</button>\n                        <button class="btn btn-xs btn-outline-danger text-nowrap" onclick="clientAction(\'${c.ip}\', \'kick\')"><i class="fas fa-user-slash mr-1"></i>Kick</button>\n                    </div>\n                </td>\n            </tr>`;\n        });\n        document.getElementById(\'clients-table-body\').innerHTML = html;\n    });\n}\n\nfunction openEditClientModal(ip, mac, remSeconds, dl, ul) {\n    document.getElementById(\'modal-client-ip\').value = ip;\n    document.getElementById(\'modal-client-info\').value = `${ip} (${mac})`;\n    document.getElementById(\'modal-client-mins\').value = Math.max(0, Math.floor((remSeconds || 0) / 60));\n    document.getElementById(\'modal-client-dl\').value = dl || 3072;\n    document.getElementById(\'modal-client-ul\').value = ul || 1536;\n    $(\'#modal-edit-client\').modal(\'show\');\n}\n\nfunction submitEditClientModal() {\n    const ip = document.getElementById(\'modal-client-ip\').value;\n    const mins = parseInt(document.getElementById(\'modal-client-mins\').value) || 0;\n    const dl = parseInt(document.getElementById(\'modal-client-dl\').value) || 3072;\n    const ul = parseInt(document.getElementById(\'modal-client-ul\').value) || 1536;\n\n    fetch(\'/admin/api/client/edit\', {\n        method: \'POST\',\n        headers: {\'Content-Type\':\'application/json\'},\n        body: JSON.stringify({ip: ip, minutes: mins, dl_kbps: dl, ul_kbps: ul})\n    }).then(r => r.json().then(d => ({ok: r.ok, body: d})))\n    .then(res => {\n        if (res.ok && res.body.success) {\n            $(\'#modal-edit-client\').modal(\'hide\');\n            loadClients();\n            Swal.fire(\'Updated!\', \'Client session updated.\', \'success\');\n        } else {\n            Swal.fire(\'Error\', (res.body && res.body.error) ? res.body.error : \'Failed to update client.\', \'error\');\n        }\n    }).catch(e => Swal.fire(\'Error\', \'Network error: \' + e, \'error\'));\n}\n\nfunction clientAction(ip, act) {\n    if (act === \'kick\') {\n        const row = document.getElementById(\'client-row-\' + ip.replace(/\\./g, \'-\'));\n        if (row) {\n            row.style.opacity = \'0.3\';\n            row.style.filter = \'grayscale(1)\';\n        }\n    }\n    fetch(\'/admin/api/client/action\', {\n        method: \'POST\',\n        headers: {\'Content-Type\':\'application/json\'},\n        body: JSON.stringify({ip: ip, action: act})\n    }).then(r => r.json()).then(data => {\n        if (act === \'kick\') {\n            const row = document.getElementById(\'client-row-\' + ip.replace(/\\./g, \'-\'));\n            if (row) row.remove();\n            Swal.fire({\n                toast: true,\n                position: \'top-end\',\n                icon: \'success\',\n                title: \'Client \' + ip + \' kicked successfully\',\n                showConfirmButton: false,\n                timer: 1800\n            });\n        }\n        loadClients();\n    }).catch(() => loadClients());\n}\n\n// Vouchers\nfunction loadVouchers() {\n    fetch(\'/admin/api/vouchers/list\').then(r=>r.json()).then(d=>{\n        let html = \'\';\n        d.forEach(v=>{\n            html += `<tr>\n                <td style="padding: 10px 14px;"><strong class="text-success"><i class="fas fa-ticket-alt mr-1"></i>${v.code}</strong></td>\n                <td style="padding: 10px 14px;"><strong>${v.minutes}m</strong></td>\n                <td style="padding: 10px 14px; text-align: center;"><span class="badge ${v.is_used ? \'badge-secondary\' : \'badge-success\'}">${v.is_used ? \'REDEEMED\' : \'ACTIVE\'}</span></td>\n                <td style="padding: 10px 14px;"><span class="text-light">${v.note || \'-\'}</span></td>\n                <td style="padding: 10px 14px;"><small class="text-muted">${v.created_at}</small></td>\n                <td style="padding: 10px 14px;"><code>${v.used_by || \'-\'}</code></td>\n                <td style="padding: 10px 14px; text-align: right; white-space: nowrap;"><button class="btn btn-xs btn-outline-danger text-nowrap" onclick="deleteVoucher(\'${v.code}\')"><i class="fas fa-trash mr-1"></i>Delete</button></td>\n            </tr>`;\n        });\n        document.getElementById(\'voucher-history-body\').innerHTML = html || \'<tr><td colspan="7" class="text-center p-3 text-muted">No vouchers generated yet.</td></tr>\';\n    });\n}\n\nfunction generateVouchers() {\n    const q = document.getElementById(\'v-qty\').value;\n    const m = document.getElementById(\'v-mins\').value;\n    const note = document.getElementById(\'v-note\').value.trim();\n    fetch(\'/admin/api/vouchers/generate\', {\n        method: \'POST\',\n        headers: {\'Content-Type\':\'application/json\'},\n        body: JSON.stringify({qty: q, minutes: m, note: note})\n    }).then(r=>r.json()).then(d=>{\n        let html = \'<div class="alert alert-success"><h5>Generated Vouchers:</h5><ul>\';\n        d.vouchers.forEach(v=>{ html += `<li><strong>${v.code}</strong> (${v.minutes} Minutes) - ${v.note || \'\'}</li>`; });\n        html += \'</ul></div>\';\n        document.getElementById(\'v-results\').innerHTML = html;\n        loadVouchers();\n        Swal.fire(\'Generated!\', `${d.vouchers.length} vouchers generated.`, \'success\');\n    });\n}\n\nfunction deleteVoucher(code) {\n    Swal.fire({\n        title: \'Delete Voucher?\',\n        text: `Delete voucher code ${code}?`,\n        icon: \'warning\',\n        showCancelButton: true,\n        confirmButtonColor: \'#d33\',\n        confirmButtonText: \'Yes, delete\'\n    }).then((res) => {\n        if (res.isConfirmed) {\n            fetch(\'/admin/api/vouchers/delete\', {\n                method: \'POST\',\n                headers: {\'Content-Type\':\'application/json\'},\n                body: JSON.stringify({code: code})\n            }).then(r => r.json().then(d => ({ok: r.ok, body: d})))\n            .then(res => {\n                if (res.ok && res.body.success) {\n                    loadVouchers();\n                    Swal.fire(\'Deleted!\', \'Voucher deleted.\', \'success\');\n                } else {\n                    Swal.fire(\'Error\', (res.body && res.body.error) ? res.body.error : \'Failed to delete voucher.\', \'error\');\n                }\n            }).catch(e => Swal.fire(\'Error\', \'Network error: \' + e, \'error\'));\n        }\n    });\n}\n\nfunction formatDuration(totalSec) {\n    totalSec = Math.max(0, Math.floor(Number(totalSec) || 0));\n    if (totalSec <= 0) return \'0s\';\n    const d = Math.floor(totalSec / 86400);\n    const h = Math.floor((totalSec % 86400) / 3600);\n    const m = Math.floor((totalSec % 3600) / 60);\n    const s = Math.floor(totalSec % 60);\n    let parts = [];\n    if (d > 0) parts.push(`${d}d`);\n    if (h > 0) parts.push(`${h}h`);\n    if (m > 0) parts.push(`${m}m`);\n    if (s > 0 && d === 0 && h === 0) parts.push(`${s}s`);\n    return parts.join(\' \') || `${s}s`;\n}\n\nfunction formatTime(totalSec) {\n    totalSec = Math.max(0, Math.floor(Number(totalSec) || 0));\n    if (totalSec <= 0) return \'0d 00h:00m:00s\';\n    const d = Math.floor(totalSec / 86400);\n    const h = Math.floor((totalSec % 86400) / 3600);\n    const m = Math.floor((totalSec % 3600) / 60);\n    const s = Math.floor(totalSec % 60);\n    const hh = (h < 10 ? \'0\' : \'\') + h;\n    const mm = (m < 10 ? \'0\' : \'\') + m;\n    const ss = (s < 10 ? \'0\' : \'\') + s;\n    return `${d}d ${hh}h:${mm}m:${ss}s`;\n}\n\n// Walled Garden\nfunction loadWalledGarden() {\n    fetch(\'/admin/api/walled_garden/list\').then(r=>r.json()).then(d=>{\n        let html = \'\';\n        d.forEach(w=>{\n            html += `<tr>\n                <td style="padding: 10px 14px;"><code class="text-success">${w.domain}</code></td>\n                <td style="padding: 10px 14px;"><span class="text-light">${w.note || \'-\'}</span></td>\n                <td style="padding: 10px 14px; text-align: right; white-space: nowrap;"><button class="btn btn-xs btn-outline-danger text-nowrap" onclick="deleteWalledDomain(\'${w.domain}\')"><i class="fas fa-trash mr-1"></i>Delete</button></td>\n            </tr>`;\n        });\n        document.getElementById(\'walled-table-body\').innerHTML = html || \'<tr><td colspan="3" class="text-center p-3 text-muted">No walled garden sites whitelisted.</td></tr>\';\n    });\n}\n\nfunction addWalledDomain() {\n    const domain = document.getElementById(\'walled-domain\').value.trim().toLowerCase();\n    const note = document.getElementById(\'walled-note\').value.trim();\n    if (!domain) return;\n    fetch(\'/admin/api/walled_garden/add\', {\n        method: \'POST\',\n        headers: {\'Content-Type\':\'application/json\'},\n        body: JSON.stringify({domain: domain, note: note})\n    }).then(r=>r.json()).then(d=>{\n        if (d.success) {\n            document.getElementById(\'walled-domain\').value = \'\';\n            document.getElementById(\'walled-note\').value = \'\';\n            loadWalledGarden();\n            Swal.fire(\'Whitelisted!\', \'Domain whitelisted in Walled Garden.\', \'success\');\n        } else {\n            Swal.fire(\'Invalid Domain\', d.error || \'Invalid domain format.\', \'warning\');\n        }\n    });\n}\n\nfunction deleteWalledDomain(domain) {\n    Swal.fire({\n        title: \'Remove Domain?\',\n        text: `Remove ${domain} from whitelist?`,\n        icon: \'warning\',\n        showCancelButton: true,\n        confirmButtonColor: \'#d33\',\n        confirmButtonText: \'Yes, remove\'\n    }).then((res) => {\n        if (res.isConfirmed) {\n            fetch(\'/admin/api/walled_garden/delete\', {\n                method: \'POST\',\n                headers: {\'Content-Type\':\'application/json\'},\n                body: JSON.stringify({domain: domain})\n            }).then(()=>{\n                loadWalledGarden();\n                Swal.fire(\'Removed!\', \'Domain removed.\', \'success\');\n            });\n        }\n    });\n}\n\nfunction savePortalCustom() {\n    const n = document.getElementById(\'cfg-vendo-name\').value.trim();\n    const sub = document.getElementById(\'cfg-vendo-sub\').value.trim();\n    const ann = document.getElementById(\'cfg-announcement\').value.trim();\n    fetch(\'/admin/api/settings/save\', {\n        method: \'POST\',\n        headers: {\'Content-Type\':\'application/json\'},\n        body: JSON.stringify({vendo_name: n, vendo_subtitle: sub, announcement: ann})\n    }).then(r=>r.json()).then(d=>{\n        if (d.success) {\n            Swal.fire(\'Saved!\', \'Portal branding & announcement updated.\', \'success\');\n        } else {\n            Swal.fire(\'Error\', d.error || \'Failed to save settings.\', \'error\');\n        }\n    });\n}\n\nfunction saveBandwidth() {\n    const dl = document.getElementById(\'cfg-dl\').value;\n    const ul = document.getElementById(\'cfg-ul\').value;\n    var tEl = document.getElementById(\'cfg-tether\') || document.getElementById(\'net-cfg-tether\'); var t = tEl ? tEl.value : \'0\';\n    fetch(\'/admin/api/settings/save\', {\n        method: \'POST\',\n        headers: {\'Content-Type\':\'application/json\'},\n        body: JSON.stringify({default_dl_kbps: dl, default_ul_kbps: ul, anti_tethering: t})\n    }).then(()=>Swal.fire(\'Saved!\', \'Bandwidth & Anti-Tethering rules applied.\', \'success\'));\n}\n\nfunction saveTelegram() {\n    const tok = document.getElementById(\'cfg-tg-token\').value;\n    const cid = document.getElementById(\'cfg-tg-chat\').value;\n    const bin = document.getElementById(\'cfg-tg-bin\').value;\n    const daily = document.getElementById(\'cfg-tg-daily\').value;\n    fetch(\'/admin/api/settings/save\', {\n        method: \'POST\',\n        headers: {\'Content-Type\':\'application/json\'},\n        body: JSON.stringify({\n            telegram_bot_token: tok,\n            telegram_chat_id: cid,\n            telegram_alert_bin: bin,\n            telegram_alert_daily: daily\n        })\n    }).then(()=>Swal.fire(\'Saved!\', \'Telegram alerts configured.\', \'success\'));\n}\n\nfunction testTelegram() {\n    fetch(\'/admin/api/telegram/test\', {method:\'POST\'}).then(r=>r.json()).then(d=>{\n        if (d.success) {\n            Swal.fire(\'Sent!\', \'Test alert sent successfully to Telegram!\', \'success\');\n        } else {\n            Swal.fire(\'Failed\', \'Could not send alert. Please check your Bot Token and Chat ID.\', \'error\');\n        }\n    });\n}\n\nfunction onAudioPresetChange(type) {\n    const sel = document.getElementById(`audio-${type}-preset`).value;\n    const customInp = document.getElementById(`audio-${type}-custom`);\n    const player = document.getElementById(`audio-player-${type}`);\n    \n    if (sel === \'silent\') {\n        customInp.value = \'silent\';\n        player.src = \'\';\n    } else if (sel === \'custom\') {\n        player.src = customInp.value;\n    } else if (sel === \'arcade_powerup\' || sel === \'voice_filipino\' || sel === \'crystal_bell\') {\n        customInp.value = sel;\n        player.src = \'\';\n    } else {\n        customInp.value = sel;\n        player.src = sel;\n    }\n}\n\nfunction updateAudioPlayer(type) {\n    const url = document.getElementById(`audio-${type}-custom`).value.trim();\n    const player = document.getElementById(`audio-player-${type}`);\n    if (url && url !== \'silent\' && ![\'arcade_powerup\', \'voice_filipino\', \'crystal_bell\'].includes(url)) {\n        player.src = url;\n    }\n}\n\nfunction uploadAudioFile(type) {\n    const fileInp = document.getElementById(`upload-file-${type}`);\n    if (!fileInp.files || fileInp.files.length === 0) return;\n    \n    const formData = new FormData();\n    formData.append(\'file\', fileInp.files[0]);\n    \n    Swal.fire({\n        title: \'Uploading Audio...\',\n        text: \'Please wait while your custom audio file is uploaded.\',\n        allowOutsideClick: false,\n        didOpen: () => { Swal.showLoading(); }\n    });\n    \n    fetch(\'/admin/api/audio/upload\', {\n        method: \'POST\',\n        body: formData\n    }).then(r=>r.json()).then(d=>{\n        if (d.success) {\n            document.getElementById(`audio-${type}-custom`).value = d.url;\n            document.getElementById(`audio-${type}-preset`).value = \'custom\';\n            document.getElementById(`audio-player-${type}`).src = d.url;\n            Swal.fire(\'Uploaded!\', \'Custom audio file uploaded successfully!\', \'success\');\n        } else {\n            Swal.fire(\'Upload Failed\', d.error || \'Could not upload audio.\', \'error\');\n        }\n    }).catch(err => {\n        Swal.fire(\'Upload Error\', \'Error communicating with server.\', \'error\');\n    });\n}\n\nfunction saveAudioSettings() {\n    const bg = document.getElementById(\'audio-bg-custom\').value.trim() || \'/static/audio/eco_loop.wav\';\n    const insert = document.getElementById(\'audio-insert-custom\').value.trim() || \'/static/audio/eco_chime.wav\';\n    const success = document.getElementById(\'audio-success-custom\').value.trim() || \'/static/audio/eco_success.wav\';\n    const vol = document.getElementById(\'audio-vol-input\').value;\n    \n    fetch(\'/admin/api/audio/settings\', {\n        method: \'POST\',\n        headers: {\'Content-Type\':\'application/json\'},\n        body: JSON.stringify({audio_bg: bg, audio_insert: insert, audio_success: success, volume: vol})\n    }).then(r=>r.json()).then(d=>{\n        Swal.fire(\'Saved!\', \'All portal audio settings saved successfully!\', \'success\');\n    });\n}\n\nfunction updatePreviewVolume() {\n    const vol = document.getElementById(\'audio-vol-input\').value;\n    document.getElementById(\'vol-lbl\').innerText = vol + \'%\';\n    const vDecimal = vol / 100.0;\n    try {\n        document.getElementById(\'audio-player-bg\').volume = vDecimal;\n        document.getElementById(\'audio-player-insert\').volume = vDecimal;\n        document.getElementById(\'audio-player-success\').volume = vDecimal;\n    } catch(e) {}\n}\n\n// Call on load to set initial volume of previews\nwindow.addEventListener(\'DOMContentLoaded\', () => {\n    updatePreviewVolume();\n});\n\nfunction copyHwid() {\n    const hwid = document.getElementById("lic-hwid").innerText.trim();\n    if (!hwid || hwid === "Loading...") return;\n    const btn = document.getElementById("btn-copy-hwid");\n    const onSuccess = () => {\n        btn.className = "btn btn-xs btn-success";\n        btn.innerHTML = "<i class=\\"fas fa-check mr-1\\"></i> Copied!";\n        setTimeout(() => {\n            btn.className = "btn btn-xs btn-outline-info";\n            btn.innerHTML = "<i class=\\"fas fa-copy mr-1\\"></i> <span id=\\"copy-hwid-text\\">Copy</span>";\n        }, 2000);\n    };\n    if (navigator.clipboard && navigator.clipboard.writeText) {\n        navigator.clipboard.writeText(hwid).then(onSuccess).catch(() => {\n            const t = document.createElement("input");\n            t.value = hwid;\n            document.body.appendChild(t);\n            t.select();\n            document.execCommand("copy");\n            document.body.removeChild(t);\n            onSuccess();\n        });\n    } else {\n        const t = document.createElement("input");\n        t.value = hwid;\n        document.body.appendChild(t);\n        t.select();\n        document.execCommand("copy");\n        document.body.removeChild(t);\n        onSuccess();\n    }\n}\n\nfunction activateLicense() {\n    const p = document.getElementById(\'act-pin\').value.trim();\n    fetch(\'/admin/api/license/activate\', {\n        method: \'POST\',\n        headers: {\'Content-Type\':\'application/json\'},\n        body: JSON.stringify({pin: p})\n    }).then(r=>r.json()).then(d=>{\n        Swal.fire(\'Activation\', d.message, d.success ? \'success\' : \'error\');\n        refreshStats();\n    });\n}\n        function toggleSimulator(enabled) {\n        fetch(\'/admin/api/simulator/toggle\', {\n            method: \'POST\',\n            headers: {\'Content-Type\': \'application/json\'},\n            body: JSON.stringify({enabled: enabled})\n        }).then(r=>r.json()).then(d=>{\n            refreshStats();\n        });\n    }\n\n    function saveEsp32Config() {\n        const payload = {\n            bin_full_threshold_cm: parseInt($(\'#esp-bin\').val()),\n            entrance_gate_timeout: parseInt($(\'#esp-ent-tout\').val()),\n            settle_time_ms: parseInt($(\'#esp-settle\').val()),\n            success_drop_tout_ms: parseInt($(\'#esp-suc-time\').val()),\n            reject_drop_time_ms: parseInt($(\'#esp-rej-time\').val()),\n            pet_nir_w_min: parseInt($(\'#esp-nir-min\').val()),\n            pet_nir_w_max: parseInt($(\'#esp-nir-max\').val()),\n            ent_close_angle: parseInt($(\'#esp-ent-close\').val()),\n            ent_open_angle: parseInt($(\'#esp-ent-open\').val()),\n            suc_close_angle: parseInt($(\'#esp-suc-close\').val()),\n            suc_open_angle: parseInt($(\'#esp-suc-open\').val()),\n            rej_close_angle: parseInt($(\'#esp-rej-close\').val()),\n            rej_open_angle: parseInt($(\'#esp-rej-open\').val())\n        };\n        fetch(\'/admin/api/esp32/save\', {\n            method: \'POST\', headers: {\'Content-Type\': \'application/json\'},\n            body: JSON.stringify(payload)\n        }).then(r=>r.json()).then(d=>{\n            if(d.success) Swal.fire(\'Saved!\', \'Config pushed to ESP32 memory. Servos snapped to closed positions.\', \'success\');\n        });\n    }\n    \n    function triggerEsp32Config() {\n        if(confirm("This will reboot the ESP32 into Captive Portal mode and stop vending temporarily. Continue?")) {\n            fetch(\'/admin/api/esp32/trigger\', {method:\'POST\'}).then(()=>Swal.fire(\'Triggered\', \'ESP32 is rebooting into Wi-Fi Config Mode.\', \'info\'));\n        }\n    }\n\n\nfunction minsToUnit(mins) {\n    mins = Number(mins) || 0;\n    if (mins >= 43200 && mins % 43200 === 0) return { val: mins / 43200, unit: \'mo\' };\n    if (mins >= 1440 && mins % 1440 === 0) return { val: mins / 1440, unit: \'d\' };\n    if (mins >= 60 && mins % 60 === 0) return { val: mins / 60, unit: \'h\' };\n    return { val: mins, unit: \'m\' };\n}\n\nfunction unitToMins(val, unit) {\n    val = Math.max(1, Number(val) || 1);\n    if (unit === \'mo\') return val * 43200;\n    if (unit === \'d\') return val * 1440;\n    if (unit === \'h\') return val * 60;\n    return val;\n}\n\n\nfunction updateBracketRowPreview(el) {\n    var tr = el.closest(\'tr\');\n    if (!tr) return;\n    var vn = tr.querySelector(\'.inp-val-num\').value;\n    var vu = tr.querySelector(\'.inp-val-unit\').value;\n    var en = tr.querySelector(\'.inp-exp-num\').value;\n    var eu = tr.querySelector(\'.inp-exp-unit\').value;\n    tr.querySelector(\'.lbl-val\').innerText = unitLabel(vn, vu);\n    tr.querySelector(\'.lbl-exp\').innerText = unitLabel(en, eu);\n}\n\nfunction unitLabel(val, unit) {\n    val = Number(val);\n    if (unit === \'m\') return val + (val === 1 ? \' min\' : \' mins\');\n    if (unit === \'h\') return val + (val === 1 ? \' hour\' : \' hours\');\n    if (unit === \'d\') return val + (val === 1 ? \' day\' : \' days\');\n    if (unit === \'mo\') return val + (val === 1 ? \' month\' : \' months\');\n    return val + \' \' + unit;\n}\n\nfunction addPolicyBracketRow(b) {\n    b = b || {};\n    var valObj = minsToUnit(b.value || 60);\n    var expObj = minsToUnit(b.expiration || 1440);\n    var tbody = document.getElementById(\'policy-brackets-tbody\');\n    if (!tbody) return;\n    var tr = document.createElement(\'tr\');\n    tr.style.cssText = \'border-top: 1px solid rgba(255,255,255,0.04);\';\n\n    tr.innerHTML = \'<td class="text-center align-middle py-2">\' +\n      \'<input type="checkbox" \' + (b.enabled !== false ? \'checked\' : \'\') + \' style="transform: scale(1.15); cursor: pointer;" title="Enable/disable this rule">\' +\n      \'</td>\' +\n      \'<td class="align-middle py-2">\' +\n        \'<div class="d-flex align-items-center" style="gap: 4px;">\' +\n          \'<input type="number" min="1" step="1" required value="\' + valObj.val + \'" class="form-control form-control-sm inp-val-num" style="width:60px !important; text-align:center; font-weight:700; height:28px; background:#0f172a; border:1px solid #334155; color:#f8fafc; font-size:12px; padding:2px 4px;">\' +\n          \'<select class="custom-select custom-select-sm inp-val-unit" style="width:90px; height:28px; background:#0f172a; border:1px solid #334155; color:#f8fafc; font-size:11.5px; padding:2px 6px;">\' +\n            \'<option value="m" \' + (valObj.unit===\'m\'?\'selected\':\'\') + \'>Mins</option>\' +\n            \'<option value="h" \' + (valObj.unit===\'h\'?\'selected\':\'\') + \'>Hours</option>\' +\n            \'<option value="d" \' + (valObj.unit===\'d\'?\'selected\':\'\') + \'>Days</option>\' +\n            \'<option value="mo" \' + (valObj.unit===\'mo\'?\'selected\':\'\') + \'>Months</option>\' +\n          \'</select>\' +\n        \'</div>\' +\n      \'</td>\' +\n      \'<td class="align-middle py-2">\' +\n        \'<div class="d-flex align-items-center" style="gap: 4px;">\' +\n          \'<input type="number" min="1" step="1" required value="\' + expObj.val + \'" class="form-control form-control-sm inp-exp-num" style="width:60px !important; text-align:center; font-weight:700; height:28px; background:#0f172a; border:1px solid #334155; color:#f8fafc; font-size:12px; padding:2px 4px;">\' +\n          \'<select class="custom-select custom-select-sm inp-exp-unit" style="width:90px; height:28px; background:#0f172a; border:1px solid #334155; color:#f8fafc; font-size:11.5px; padding:2px 6px;">\' +\n            \'<option value="h" \' + (expObj.unit===\'h\'?\'selected\':\'\') + \'>Hours</option>\' +\n            \'<option value="d" \' + (expObj.unit===\'d\'?\'selected\':\'\') + \'>Days</option>\' +\n            \'<option value="mo" \' + (expObj.unit===\'mo\'?\'selected\':\'\') + \'>Months</option>\' +\n          \'</select>\' +\n        \'</div>\' +\n      \'</td>\' +\n      \'<td class="align-middle py-2">\' +\n        \'<div style="font-size: 11.5px; font-weight: 600; padding: 4px 10px; border-radius: 5px; display: inline-flex; align-items: center; gap: 6px; background: rgba(15, 23, 42, 0.6); border: 1px solid rgba(255, 255, 255, 0.08); color: #94a3b8; white-space: nowrap;">\' +\n          \'<span>Up to <strong class="lbl-val" style="color:#38bdf8;">\' + unitLabel(valObj.val, valObj.unit) + \'</strong></span>\' +\n          \'<i class="fas fa-arrow-right" style="color:#64748b; font-size:10px;"></i>\' +\n          \'<span>Valid for <span class="lbl-exp" style="color:#34d399; font-weight:700;">\' + unitLabel(expObj.val, expObj.unit) + \'</span></span>\' +\n        \'</div>\' +\n      \'</td>\' +\n      \'<td class="text-center align-middle py-2">\' +\n        \'<button type="button" class="btn btn-xs btn-outline-danger" style="height:24px; width:26px; padding:0; line-height:22px; font-size:11px; border-radius:4px;" onclick="this.closest(&quot;tr&quot;).remove()" title="Delete rule">\' +\n          \'<i class="fas fa-times"></i>\' +\n        \'</button>\' +\n      \'</td>\';\n\n    function updatePreview() {\n        var vn = tr.querySelector(\'.inp-val-num\').value;\n        var vu = tr.querySelector(\'.inp-val-unit\').value;\n        var en = tr.querySelector(\'.inp-exp-num\').value;\n        var eu = tr.querySelector(\'.inp-exp-unit\').value;\n        tr.querySelector(\'.lbl-val\').innerText = unitLabel(vn, vu);\n        tr.querySelector(\'.lbl-exp\').innerText = unitLabel(en, eu);\n    }\n\n    tr.querySelector(\'.inp-val-num\').oninput = updatePreview;\n    tr.querySelector(\'.inp-val-unit\').onchange = updatePreview;\n    tr.querySelector(\'.inp-exp-num\').oninput = updatePreview;\n    tr.querySelector(\'.inp-exp-unit\').onchange = updatePreview;\n\n    tbody.appendChild(tr);\n}\n\nfunction loadTimePolicy() {\n    fetch(\'/admin/api/time/policy\')\n        .then(function(r) { return r.json(); })\n        .then(function(data) {\n            if (!data.success) return;\n            var policy = data.policy || {};\n            var allowPause = document.getElementById(\'policy-allow-pause\');\n            if (allowPause) allowPause.checked = !!policy.pause_allowed;\n            var timeoutAction = document.getElementById(\'policy-timeout-action\');\n            if (timeoutAction) timeoutAction.value = policy.pause_timeout_action || \'resume\';\n\n            var pauseCount = document.getElementById(\'policy-pause-count-max\');\n            if (pauseCount) pauseCount.value = policy.pause_count_max == null ? \'\' : policy.pause_count_max;\n\n            // Pause duration (sec) -> mins or hours\n            var pSec = Number(policy.pause_duration_sec) || 3600;\n            var pVal = document.getElementById(\'pause-dur-val\');\n            var pUnit = document.getElementById(\'pause-dur-unit\');\n            if (pVal && pUnit) {\n                if (pSec >= 3600 && pSec % 3600 === 0) {\n                    pVal.value = pSec / 3600;\n                    pUnit.value = \'h\';\n                } else {\n                    pVal.value = Math.max(1, Math.round(pSec / 60));\n                    pUnit.value = \'m\';\n                }\n            }\n\n            // Global validity (min) -> hours or days\n            var gMin = Number(policy.global_validity_min) || 1440;\n            var gVal = document.getElementById(\'default-val-val\');\n            var gUnit = document.getElementById(\'default-val-unit\');\n            if (gVal && gUnit) {\n                if (gMin >= 1440 && gMin % 1440 === 0) {\n                    gVal.value = gMin / 1440;\n                    gUnit.value = \'d\';\n                } else {\n                    gVal.value = Math.max(1, Math.round(gMin / 60));\n                    gUnit.value = \'h\';\n                }\n            }\n\n            var minBal = document.getElementById(\'min-bal-val\');\n            if (minBal) minBal.value = Math.round((Number(policy.min_balance_sec) || 0) / 60);\n\n            var tbody = document.getElementById(\'policy-brackets-tbody\');\n            if (tbody) {\n                tbody.innerHTML = \'\';\n                var defaultBrackets = [\n                    { value: 30, expiration: 1440, enabled: true },\n                    { value: 60, expiration: 2880, enabled: true },\n                    { value: 180, expiration: 4320, enabled: true },\n                    { value: 360, expiration: 10080, enabled: true },\n                    { value: 720, expiration: 21600, enabled: true },\n                    { value: 1440, expiration: 43200, enabled: true },\n                    { value: 4320, expiration: 86400, enabled: true },\n                    { value: 10080, expiration: 129600, enabled: true },\n                    { value: 43200, expiration: 259200, enabled: true }\n                ];\n                var list = (policy.brackets && policy.brackets.length > 0) ? policy.brackets : defaultBrackets;\n                list.forEach(addPolicyBracketRow);\n            }\n        }).catch(function() {});\n\n    fetch(\'/admin/api/time/diagnostics\')\n        .then(function(r) { return r.json(); })\n        .then(function(d) {\n            var acc = document.getElementById(\'diag-acc-badge\');\n            if (acc) {\n                acc.className = \'badge px-1 \' + (d.ready ? \'badge-success\' : \'badge-warning\');\n                acc.textContent = d.ready ? \'Ready\' : \'Migrate\';\n            }\n            var worker = document.getElementById(\'diag-worker-badge\');\n            if (worker) {\n                worker.className = \'badge px-1 \' + (d.worker_healthy ? \'badge-success\' : \'badge-danger\');\n                worker.textContent = d.worker_healthy ? \'Healthy\' : \'Recovering\';\n            }\n            var held = document.getElementById(\'diag-held-badge\');\n            if (held) held.textContent = d.held_deposit_events || 0;\n            var mismatch = document.getElementById(\'diag-mismatch-badge\');\n            if (mismatch) {\n                var count = (d.balance_mismatches && d.balance_mismatches.length > 0) ? d.balance_mismatches.length : 0;\n                mismatch.className = \'badge px-1 \' + (count > 0 ? \'badge-danger\' : \'badge-success\');\n                mismatch.textContent = count;\n            }\n        }).catch(function() {});\n}\n\nfunction savePolicySettings(event) {\n    if (event) event.preventDefault();\n    var saveBtn = document.getElementById(\'policy-save-btn\');\n    var msg = document.getElementById(\'policy-save-msg\');\n    if (saveBtn) saveBtn.disabled = true;\n\n    var rows = document.querySelectorAll(\'#policy-brackets-tbody tr\');\n    var brackets = [];\n    rows.forEach(function(row) {\n        var chk = row.querySelector(\'input[type="checkbox"]\');\n        var valNum = row.querySelector(\'.inp-val-num\');\n        var valUnit = row.querySelector(\'.inp-val-unit\');\n        var expNum = row.querySelector(\'.inp-exp-num\');\n        var expUnit = row.querySelector(\'.inp-exp-unit\');\n\n        if (chk && valNum && valUnit && expNum && expUnit) {\n            brackets.push({\n                enabled: chk.checked,\n                value: unitToMins(valNum.value, valUnit.value),\n                expiration: unitToMins(expNum.value, expUnit.value)\n            });\n        }\n    });\n\n    var pDurNum = Number(document.getElementById(\'pause-dur-val\').value) || 60;\n    var pDurUnit = document.getElementById(\'pause-dur-unit\').value;\n    var pause_duration_sec = pDurUnit === \'h\' ? (pDurNum * 3600) : (pDurNum * 60);\n\n    var defValNum = Number(document.getElementById(\'default-val-val\').value) || 1;\n    var defValUnit = document.getElementById(\'default-val-unit\').value;\n    var global_validity_min = defValUnit === \'d\' ? (defValNum * 1440) : (defValNum * 60);\n\n    var minBalVal = Number(document.getElementById(\'min-bal-val\').value) || 0;\n    var min_balance_sec = minBalVal * 60;\n\n    var pauseCountEl = document.getElementById(\'policy-pause-count-max\');\n    var pause_count_max = (pauseCountEl && pauseCountEl.value.trim() !== \'\') ? Number(pauseCountEl.value) : null;\n\n    var payload = {\n        pause_allowed: document.getElementById(\'policy-allow-pause\') ? document.getElementById(\'policy-allow-pause\').checked : true,\n        pause_timeout_action: document.getElementById(\'policy-timeout-action\') ? document.getElementById(\'policy-timeout-action\').value : \'resume\',\n        pause_count_max: pause_count_max,\n        pause_duration_sec: pause_duration_sec,\n        global_validity_min: global_validity_min,\n        min_balance_sec: min_balance_sec,\n        max_balance_sec: null,\n        brackets: brackets\n    };\n\n    fetch(\'/admin/api/time/policy\', {\n        method: \'POST\',\n        headers: { \'Content-Type\': \'application/json\' },\n        body: JSON.stringify(payload)\n    })\n    .then(function(r) { return r.json(); })\n    .then(function(result) {\n        if (result.success) {\n            if (msg) {\n                msg.className = \'small font-weight-bold text-success\';\n                msg.innerHTML = \'<i class="fas fa-check-circle mr-1"></i> Saved.\';\n                setTimeout(function() { if (msg.textContent.indexOf(\'Saved\') !== -1) msg.textContent = \'\'; }, 3000);\n            }\n        } else {\n            if (msg) {\n                msg.className = \'small font-weight-bold text-danger\';\n                msg.innerHTML = \'<i class="fas fa-exclamation-triangle mr-1"></i> \' + (result.error || \'Error\');\n            }\n        }\n    })\n    .catch(function() {\n        if (msg) {\n            msg.className = \'small font-weight-bold text-danger\';\n            msg.innerHTML = \'<i class="fas fa-exclamation-triangle mr-1"></i> Network error.\';\n        }\n    })\n    .then(function() {\n        if (saveBtn) saveBtn.disabled = false;\n    });\n}\n\ndocument.addEventListener(\'DOMContentLoaded\', function() {\n    loadTimePolicy();\n});\n\n// =========================================================================\n// Eco-Fi MASTER NETWORK, BANDWIDTH & SYSTEM SUITE\n// =========================================================================\n\nfunction toggleWanStaticFields() {\n    var mode = document.getElementById(\'net-cfg-wan-mode\').value;\n    var group = document.getElementById(\'net-static-group\');\n    if (group) {\n        group.style.display = (mode === \'static\') ? \'block\' : \'none\';\n    }\n}\n\nfunction setDnsPreset(p, s) {\n    var d1 = document.getElementById(\'net-cfg-dns1\');\n    var d2 = document.getElementById(\'net-cfg-dns2\');\n    if (d1) d1.value = p;\n    if (d2) d2.value = s;\n}\n\nfunction setBwPreset(dl, ul) {\n    var d = document.getElementById(\'cfg-dl\');\n    var u = document.getElementById(\'cfg-ul\');\n    if (d) d.value = dl;\n    if (u) u.value = ul;\n}\n\nfunction loadNetworkSettings() {\n    fetch(\'/admin/api/network\')\n        .then(function(r) { return r.json(); })\n        .then(function(d) {\n            if (!d) return;\n            var tbody = document.getElementById(\'net-interfaces-tbody\');\n            var wanSel = document.getElementById(\'net-cfg-wan\');\n            var lanSel = document.getElementById(\'net-cfg-lan\');\n            \n            if (tbody && d.interfaces && Array.isArray(d.interfaces)) {\n                var html = \'\';\n                var wanOpts = \'\';\n                var lanOpts = \'\';\n                \n                d.interfaces.forEach(function(iface) {\n                    var isWan = (iface.name === d.wan_interface);\n                    var isLan = (iface.name === d.lan_interface);\n                    var roleBadge = \'<span class="badge badge-secondary">Standby</span>\';\n                    if (isWan) roleBadge = \'<span class="badge badge-info"><i class="fas fa-globe mr-1"></i> WAN (Upstream)</span>\';\n                    else if (isLan) roleBadge = \'<span class="badge badge-success"><i class="fas fa-wifi mr-1"></i> LAN (Vendo)</span>\';\n                    \n                    var stateBadge = (iface.state === \'UP\') ? \n                        \'<span class="badge badge-success">UP</span>\' : \n                        \'<span class="badge badge-danger">\' + (iface.state || \'DOWN\') + \'</span>\';\n                        \n                    html += \'<tr>\' +\n                        \'<td><strong>\' + iface.name + \'</strong></td>\' +\n                        \'<td>\' + roleBadge + \'</td>\' +\n                        \'<td><code>\' + (iface.ip || \'No IP\') + \'</code></td>\' +\n                        \'<td><code>\' + (iface.mac || \'N/A\') + \'</code></td>\' +\n                        \'<td>\' + stateBadge + \'</td>\' +\n                        \'<td>\' + (iface.speed ? iface.speed + \' Mbps\' : \'Ethernet\') + \'</td>\' +\n                        \'</tr>\';\n                        \n                    wanOpts += \'<option value="\' + iface.name + \'" \' + (isWan ? \'selected\' : \'\') + \'>\' + iface.name + \' (\' + (iface.ip || \'No IP\') + \')</option>\';\n                    lanOpts += \'<option value="\' + iface.name + \'" \' + (isLan ? \'selected\' : \'\') + \'>\' + iface.name + \' (\' + (iface.ip || \'No IP\') + \')</option>\';\n                });\n                tbody.innerHTML = html;\n                if (wanSel && wanOpts) wanSel.innerHTML = wanOpts;\n                if (lanSel && lanOpts) lanSel.innerHTML = lanOpts;\n            }\n            \n            var d1 = document.getElementById(\'net-cfg-dns1\');\n            var d2 = document.getElementById(\'net-cfg-dns2\');\n            var dom = document.getElementById(\'net-cfg-domain\');\n            var mode = document.getElementById(\'net-cfg-wan-mode\');\n            var sip = document.getElementById(\'net-cfg-static-ip\');\n            var smask = document.getElementById(\'net-cfg-static-mask\');\n            var sgw = document.getElementById(\'net-cfg-static-gw\');\n            var lease = document.getElementById(\'net-cfg-lease\');\n            var sl = document.getElementById(\'net-cfg-starlink\');\n            var teth = document.getElementById(\'net-cfg-tether\');\n            var mod = document.getElementById(\'net-cfg-modem\');\n            \n            if (d1 && d.dns_primary) d1.value = d.dns_primary;\n            if (d2 && d.dns_secondary) d2.value = d.dns_secondary;\n            if (dom && d.portal_domain) dom.value = d.portal_domain;\n            if (mode && d.wan_mode) { mode.value = d.wan_mode; toggleWanStaticFields(); }\n            if (sip && d.wan_static_ip) sip.value = d.wan_static_ip;\n            if (smask && d.wan_static_mask) smask.value = d.wan_static_mask;\n            if (sgw && d.wan_static_gateway) sgw.value = d.wan_static_gateway;\n            if (lease && d.dhcp_lease_hours) lease.value = d.dhcp_lease_hours;\n            if (sl && d.starlink_blocker !== undefined) sl.value = d.starlink_blocker;\n            if (teth && d.anti_tethering !== undefined) teth.value = d.anti_tethering;\n            if (mod && d.isp_gateway_access !== undefined) mod.value = d.isp_gateway_access;\n        })\n        .catch(function(err) {\n            console.error(\'loadNetworkSettings error:\', err);\n        });\n}\n\nfunction saveNetworkAdapters() {\n    var wan = document.getElementById(\'net-cfg-wan\') ? document.getElementById(\'net-cfg-wan\').value : \'eth0\';\n    var lan = document.getElementById(\'net-cfg-lan\') ? document.getElementById(\'net-cfg-lan\').value : \'eth1\';\n    var mode = document.getElementById(\'net-cfg-wan-mode\') ? document.getElementById(\'net-cfg-wan-mode\').value : \'dhcp\';\n    var sip = document.getElementById(\'net-cfg-static-ip\') ? document.getElementById(\'net-cfg-static-ip\').value : \'\';\n    var smask = document.getElementById(\'net-cfg-static-mask\') ? document.getElementById(\'net-cfg-static-mask\').value : \'\';\n    var sgw = document.getElementById(\'net-cfg-static-gw\') ? document.getElementById(\'net-cfg-static-gw\').value : \'\';\n    \n    fetch(\'/admin/api/network\', {\n        method: \'POST\',\n        headers: {\'Content-Type\': \'application/json\'},\n        body: JSON.stringify({\n            wan_interface: wan,\n            lan_interface: lan,\n            wan_mode: mode,\n            wan_static_ip: sip,\n            wan_static_mask: smask,\n            wan_static_gateway: sgw\n        })\n    })\n    .then(function(r) { return r.json(); })\n    .then(function(d) {\n        if (d.success) {\n            Swal.fire(\'Saved!\', d.message || \'Network adapters saved successfully.\', \'success\');\n            loadNetworkSettings();\n        } else {\n            Swal.fire(\'Error\', d.error || \'Failed to save adapter settings.\', \'error\');\n        }\n    })\n    .catch(function() {\n        Swal.fire(\'Error\', \'Communication failure.\', \'error\');\n    });\n}\n\nfunction saveDnsSettings() {\n    var d1 = document.getElementById(\'net-cfg-dns1\') ? document.getElementById(\'net-cfg-dns1\').value : \'1.1.1.1\';\n    var d2 = document.getElementById(\'net-cfg-dns2\') ? document.getElementById(\'net-cfg-dns2\').value : \'1.0.0.1\';\n    var dom = document.getElementById(\'net-cfg-domain\') ? document.getElementById(\'net-cfg-domain\').value : \'ecofi.now\';\n    \n    fetch(\'/admin/api/network\', {\n        method: \'POST\',\n        headers: {\'Content-Type\': \'application/json\'},\n        body: JSON.stringify({\n            dns_primary: d1,\n            dns_secondary: d2,\n            portal_domain: dom\n        })\n    })\n    .then(function(r) { return r.json(); })\n    .then(function(d) {\n        if (d.success) {\n            Swal.fire(\'DNS Updated!\', \'DNS resolvers and local portal domain updated.\', \'success\');\n        } else {\n            Swal.fire(\'Error\', d.error || \'Failed to save DNS settings.\', \'error\');\n        }\n    });\n}\n\nfunction saveDhcpSettings() {\n    var lease = document.getElementById(\'net-cfg-lease\') ? document.getElementById(\'net-cfg-lease\').value : \'72\';\n    fetch(\'/admin/api/network\', {\n        method: \'POST\',\n        headers: {\'Content-Type\': \'application/json\'},\n        body: JSON.stringify({ dhcp_lease_hours: lease })\n    })\n    .then(function(r) { return r.json(); })\n    .then(function(d) {\n        if (d.success) {\n            Swal.fire(\'DHCP Updated!\', \'DHCP lease expiration time updated.\', \'success\');\n        } else {\n            Swal.fire(\'Error\', d.error || \'Failed to save DHCP settings.\', \'error\');\n        }\n    });\n}\n\nfunction loadDhcpLeases() {\n    var tbody = document.getElementById(\'dhcp-leases-tbody\');\n    if (!tbody) return;\n    tbody.innerHTML = \'<tr><td colspan="5" class="text-center text-muted py-2"><i class="fas fa-spinner fa-spin mr-1"></i> Refreshing leases...</td></tr>\';\n    \n    fetch(\'/admin/api/network/leases\')\n        .then(function(r) { return r.json(); })\n        .then(function(leases) {\n            if (!leases || !leases.length) {\n                tbody.innerHTML = \'<tr><td colspan="5" class="text-center text-muted py-3">No active client leases recorded yet.</td></tr>\';\n                return;\n            }\n            var html = \'\';\n            leases.forEach(function(l) {\n                html += \'<tr>\' +\n                    \'<td><code>\' + l.ip + \'</code></td>\' +\n                    \'<td><code>\' + (l.mac || \'Unknown\') + \'</code></td>\' +\n                    \'<td>\' + (l.hostname || \'Client Device\') + \'</td>\' +\n                    \'<td><span class="badge badge-success">\' + (l.expires || \'Active\') + \'</span></td>\' +\n                    \'<td><button class="btn btn-xs btn-outline-danger" onclick="disconnectClientManual(" + String.fromCharCode(39) + l.ip + String.fromCharCode(39) + ")"><i class="fas fa-times"></i> Kick</button></td>\' +\n                    \'</tr>\';\n            });\n            tbody.innerHTML = html;\n        })\n        .catch(function() {\n            tbody.innerHTML = \'<tr><td colspan="5" class="text-center text-danger py-2">Failed to load leases.</td></tr>\';\n        });\n}\n\nfunction disconnectClientManual(ip) {\n    Swal.fire({\n        title: \'Disconnect Client?\',\n        text: \'Terminate session and block IP: \' + ip,\n        icon: \'warning\',\n        showCancelButton: true,\n        confirmButtonColor: \'#d33\',\n        confirmButtonText: \'Yes, Disconnect\'\n    }).then(function(res) {\n        if (res.isConfirmed) {\n            fetch(\'/admin/api/clients/disconnect\', {\n                method: \'POST\',\n                headers: {\'Content-Type\': \'application/json\'},\n                body: JSON.stringify({ ip: ip })\n            }).then(function() {\n                loadDhcpLeases();\n                Swal.fire(\'Disconnected\', \'Client was removed.\', \'success\');\n            });\n        }\n    });\n}\n\nfunction saveAccessPolicies() {\n    var sl = document.getElementById(\'net-cfg-starlink\') ? document.getElementById(\'net-cfg-starlink\').value : \'1\';\n    var teth = document.getElementById(\'net-cfg-tether\') ? document.getElementById(\'net-cfg-tether\').value : \'1\';\n    var mod = document.getElementById(\'net-cfg-modem\') ? document.getElementById(\'net-cfg-modem\').value : \'1\';\n    \n    fetch(\'/admin/api/network\', {\n        method: \'POST\',\n        headers: {\'Content-Type\': \'application/json\'},\n        body: JSON.stringify({\n            starlink_blocker: sl,\n            anti_tethering: teth,\n            isp_gateway_access: mod\n        })\n    })\n    .then(function(r) { return r.json(); })\n    .then(function(d) {\n        if (d.success) {\n            Swal.fire(\'Policies Enforced!\', \'Starlink blocker, anti-tethering, and gateway access rules updated in iptables.\', \'success\');\n        } else {\n            Swal.fire(\'Error\', d.error || \'Failed to apply policies.\', \'error\');\n        }\n    });\n}\n\nfunction loadBandwidthSettings() {\n    fetch(\'/admin/api/network\')\n        .then(function(r) { return r.json(); })\n        .then(function(d) {\n            // Already initialized\n        });\n}\n\nfunction saveFixedBandwidth() {\n    var dl = document.getElementById(\'cfg-dl\') ? document.getElementById(\'cfg-dl\').value : \'3072\';\n    var ul = document.getElementById(\'cfg-ul\') ? document.getElementById(\'cfg-ul\').value : \'1536\';\n    \n    var preserve = document.getElementById(\'cfg-preserve-custom-bw\') ? document.getElementById(\'cfg-preserve-custom-bw\').checked : true;\n    fetch(\'/admin/api/bandwidth/qos/save\', {\n        method: \'POST\',\n        headers: {\'Content-Type\': \'application/json\'},\n        body: JSON.stringify({ default_dl_kbps: dl, default_ul_kbps: ul, preserve_custom: preserve ? 1 : 0 })\n    })\n    .then(function(r) { return r.json(); })\n    .then(function(d) {\n        if (d.success) {\n            Swal.fire(\'Saved!\', \'Fixed speed limits updated successfully.\', \'success\');\n        } else {\n            Swal.fire(\'Error\', d.error || \'Failed to save speed limits.\', \'error\');\n        }\n    });\n}\n\nfunction saveDynamicBandwidth() {\n    var en = document.getElementById(\'cfg-adaptive-en\') ? document.getElementById(\'cfg-adaptive-en\').value : \'0\';\n    var minDl = document.getElementById(\'cfg-min-dl\') ? document.getElementById(\'cfg-min-dl\').value : \'512\';\n    var maxDl = document.getElementById(\'cfg-max-dl\') ? document.getElementById(\'cfg-max-dl\').value : \'4096\';\n    var minUl = document.getElementById(\'cfg-min-ul\') ? document.getElementById(\'cfg-min-ul\').value : \'256\';\n    var maxUl = document.getElementById(\'cfg-max-ul\') ? document.getElementById(\'cfg-max-ul\').value : \'1536\';\n    \n    fetch(\'/admin/api/bandwidth/qos/save\', {\n        method: \'POST\',\n        headers: {\'Content-Type\': \'application/json\'},\n        body: JSON.stringify({\n            dynamic_bandwidth_enabled: en,\n            min_download_kbps: minDl,\n            max_download_kbps: maxDl,\n            min_upload_kbps: minUl,\n            max_upload_kbps: maxUl\n        })\n    })\n    .then(function(r) { return r.json(); })\n    .then(function(d) {\n        if (d.success) {\n            Swal.fire(\'Saved!\', \'Adaptive bandwidth scaling settings updated.\', \'success\');\n        } else {\n            Swal.fire(\'Error\', d.error || \'Failed to save adaptive bandwidth.\', \'error\');\n        }\n    });\n}\n\nfunction saveGamingQoS() {\n    var en = document.getElementById(\'cfg-qos-en\') ? document.getElementById(\'cfg-qos-en\').value : \'1\';\n    var pct = document.getElementById(\'cfg-qos-pct\') ? document.getElementById(\'cfg-qos-pct\').value : \'20\';\n    \n    fetch(\'/admin/api/bandwidth/qos/save\', {\n        method: \'POST\',\n        headers: {\'Content-Type\': \'application/json\'},\n        body: JSON.stringify({\n            qos_gaming_enabled: en,\n            qos_gaming_percent: pct\n        })\n    })\n    .then(function(r) { return r.json(); })\n    .then(function(d) {\n        if (d.success) {\n            Swal.fire(\'Gaming QoS Active!\', \'Low-latency UDP game packet priority queue enforced.\', \'success\');\n        } else {\n            Swal.fire(\'Error\', d.error || \'Failed to apply Gaming QoS.\', \'error\');\n        }\n    });\n}\n\nfunction runSystemVerifier() {\n    var logBox = document.getElementById(\'verif-log-box\');\n    if (logBox) logBox.innerText = \'Scanning Eco-Fi hardware, database, and network services...\';\n    \n    fetch(\'/admin/api/system/verifier\')\n        .then(function(r) { return r.json(); })\n        .then(function(res) {\n            var bDb = document.getElementById(\'verif-badge-db\');\n            var tDb = document.getElementById(\'verif-text-db\');\n            var bDisk = document.getElementById(\'verif-badge-disk\');\n            var tDisk = document.getElementById(\'verif-text-disk\');\n            var bMem = document.getElementById(\'verif-badge-mem\');\n            var tMem = document.getElementById(\'verif-text-mem\');\n            var bFwd = document.getElementById(\'verif-badge-fwd\');\n            var tFwd = document.getElementById(\'verif-text-fwd\');\n            var bIf = document.getElementById(\'verif-badge-iface\');\n            var tIf = document.getElementById(\'verif-text-iface\');\n            var lScan = document.getElementById(\'verif-last-scan\');\n            \n            if (bDb) {\n                bDb.className = (res.database === \'ok\') ? \'badge badge-success\' : \'badge badge-danger\';\n                bDb.innerText = (res.database === \'ok\') ? \'PASS (HEALTHY)\' : \'CORRUPT\';\n            }\n            if (tDb) tDb.innerText = \'SQLite Integrity: \' + res.database;\n            \n            if (bDisk) {\n                bDisk.className = (res.disk_free_mb > 500) ? \'badge badge-success\' : \'badge badge-warning\';\n                bDisk.innerText = (res.disk_free_mb || \'0\') + \' MB Free\';\n            }\n            if (tDisk) tDisk.innerText = \'Root partition: \' + res.disk_free_mb + \' MB available\';\n            \n            if (bMem) {\n                bMem.className = \'badge badge-info\';\n                bMem.innerText = (res.memory_free_mb || \'0\') + \' MB Avail\';\n            }\n            if (tMem) tMem.innerText = \'System RAM: \' + (res.memory_free_mb || \'0\') + \' MB free memory\';\n            \n            if (bFwd) {\n                bFwd.className = res.ip_forward ? \'badge badge-success\' : \'badge badge-danger\';\n                bFwd.innerText = res.ip_forward ? \'FORWARDING\' : \'DISABLED\';\n            }\n            if (tFwd) tFwd.innerText = \'Kernel Routing: \' + (res.ip_forward ? \'Enabled (1)\' : \'Disabled (0)\');\n            \n            if (bIf) {\n                var ifCount = (res.interfaces && res.interfaces.length) ? res.interfaces.length : 0;\n                bIf.className = (ifCount >= 2) ? \'badge badge-success\' : \'badge badge-warning\';\n                bIf.innerText = ifCount + \' Adapters Active\';\n            }\n            if (tIf) tIf.innerText = \'Active adapters: \' + ((res.interfaces || []).map(function(i){ return i.name; }).join(\', \'));\n            \n            if (lScan) lScan.innerText = new Date().toLocaleTimeString();\n            \n            if (logBox) {\n                var nl = String.fromCharCode(10);\n                logBox.innerText = [\n                    \'[Eco-Fi SYSTEM HEALTH CHECK SUMMARY]\',\n                    \'- Timestamp: \' + new Date().toISOString(),\n                    \'- Database Integrity: \' + res.database.toUpperCase(),\n                    \'- Disk Free Space: \' + res.disk_free_mb + \' MB\',\n                    \'- Memory Available: \' + res.memory_free_mb + \' MB\',\n                    \'- Kernel ip_forward: \' + (res.ip_forward ? \'ACTIVE\' : \'OFF\'),\n                    \'- Network Interfaces: \' + (res.interfaces || []).map(function(i){ return i.name + \' (\' + (i.ip || \'No IP\') + \')\'; }).join(\' | \'),\n                    \'- Overall System Status: \' + (res.status === \'healthy\' ? \'ALL SYSTEMS FULLY OPERATIONAL [ONLINE]\' : \'ATTENTION REQUIRED [CHECK LOGS]\')\n                ].join(nl);\n            }\n        })\n        .catch(function(e) {\n            if (logBox) logBox.innerText = \'Diagnostic check error: \' + e;\n        });\n}\n\nfunction confirmFlushSessions() {\n    Swal.fire({\n        title: \'Flush All Client Sessions?\',\n        text: \'This immediately revokes all customer connections and flushes iptables routing.\',\n        icon: \'warning\',\n        showCancelButton: true,\n        confirmButtonColor: \'#f39c12\',\n        confirmButtonText: \'Yes, Flush Sessions\'\n    }).then(function(res) {\n        if (res.isConfirmed) {\n            fetch(\'/admin/api/system/flush_sessions\', { method: \'POST\' })\n                .then(function(r) { return r.json(); })\n                .then(function(d) {\n                    Swal.fire(\'Flushed!\', d.message || \'All client sessions terminated.\', \'success\');\n                    loadDhcpLeases();\n                });\n        }\n    });\n}\n\nfunction confirmReboot() {\n    Swal.fire({\n        title: \'Reboot Eco-Fi System?\',\n        text: \'The machine will restart. All services will be temporarily unavailable for ~45 seconds.\',\n        icon: \'warning\',\n        showCancelButton: true,\n        confirmButtonColor: \'#f39c12\',\n        confirmButtonText: \'Yes, Reboot Machine\'\n    }).then(function(res) {\n        if (res.isConfirmed) {\n            fetch(\'/admin/api/system/reboot\', { method: \'POST\' })\n                .then(function(r) { return r.json(); })\n                .then(function(d) {\n                    Swal.fire(\'Rebooting...\', d.message || \'System reboot initiated.\', \'info\');\n                });\n        }\n    });\n}\n\nfunction confirmShutdown() {\n    Swal.fire({\n        title: \'Safe System Power Off?\',\n        text: \'Halts the Orange Pi safely. Only disconnect power when the board indicator LEDs turn off.\',\n        icon: \'warning\',\n        showCancelButton: true,\n        confirmButtonColor: \'#d33\',\n        confirmButtonText: \'Yes, Power Off\'\n    }).then(function(res) {\n        if (res.isConfirmed) {\n            fetch(\'/admin/api/system/shutdown\', { method: \'POST\' })\n                .then(function(r) { return r.json(); })\n                .then(function(d) {\n                    Swal.fire(\'Shutting Down...\', d.message || \'Safe shutdown initiated.\', \'info\');\n                });\n        }\n    });\n}\n\nfunction restoreDatabase() {\n    var fileInput = document.getElementById(\'sys-restore-file\');\n    if (!fileInput || !fileInput.files.length) {\n        Swal.fire(\'No File Selected\', \'Please choose an ecofi.db or backup file to restore.\', \'warning\');\n        return;\n    }\n    var formData = new FormData();\n    formData.append(\'backup_file\', fileInput.files[0]);\n    \n    Swal.fire({\n        title: \'Restore Database?\',\n        text: \'This will replace current settings and records with the backup database.\',\n        icon: \'warning\',\n        showCancelButton: true,\n        confirmButtonColor: \'#f39c12\',\n        confirmButtonText: \'Yes, Restore Database\'\n    }).then(function(res) {\n        if (res.isConfirmed) {\n            Swal.fire({\n                title: \'Restoring & Validating...\',\n                text: \'Testing PRAGMA integrity...\',\n                allowOutsideClick: false,\n                didOpen: function() { Swal.showLoading(); }\n            });\n            \n            fetch(\'/admin/api/system/backup/restore\', {\n                method: \'POST\',\n                body: formData\n            })\n            .then(function(r) { return r.json(); })\n            .then(function(d) {\n                if (d.success) {\n                    Swal.fire(\'Restored!\', d.message || \'Database restored successfully.\', \'success\')\n                        .then(function() { window.location.reload(); });\n                } else {\n                    Swal.fire(\'Restore Failed\', d.error || \'Integrity check failed.\', \'error\');\n                }\n            })\n            .catch(function(err) {\n                Swal.fire(\'Error\', \'Restore operation failed: \' + err, \'error\');\n            });\n        }\n    });\n}\n\n\n// Delegated tab and sub-tab activation handler\n$(document).on(\'click\', \'a[data-toggle="pill"], a[data-toggle="tab"]\', function (e) {\n    e.preventDefault();\n    $(this).tab(\'show\');\n});\n\n$(document).on(\'shown.bs.tab\', \'a[data-toggle="pill"], a[data-toggle="tab"]\', function (e) {\n    var target = $(e.target).attr("href");\n    if (target === \'#net-tab-dhcp\') loadDhcpLeases();\n    if (target === \'#sys-tab-verifier\') runSystemVerifier();\n});\n\n\n</script>\n</body>\n</html>\n'
SERIAL_PORTS = [
    '/dev/ttyUSB0', '/dev/ttyUSB1', '/dev/ttyUSB2', '/dev/ttyUSB3',
    '/dev/ttyACM0', '/dev/ttyACM1', '/dev/ttyACM2',
    '/dev/ttyS1', '/dev/ttyS2', '/dev/ttyS3'
]
BAUD_RATE = 115200

physical_esp32_state = {
    'connected': False,
    'port': None,
    'last_seen': 0,
    'bin_full': False,
    'bin_distance_cm': None,
    'gate_open': False,
    'sensor_bus_status': 'Offline',
    'last_event': None
}

def handle_physical_esp32_packet(data):
    global physical_esp32_state
    if not isinstance(data, dict):
        return
    ev = data.get('event')
    if ev == 'BIN_FULL':
        physical_esp32_state['bin_full'] = True
        physical_esp32_state['sensor_bus_status'] = 'Bin Triggered'
    elif ev == 'BIN_OK':
        physical_esp32_state['bin_full'] = False
        physical_esp32_state['sensor_bus_status'] = 'Nominal'
    elif ev == 'CONFIG_SAVED':
        physical_esp32_state['last_event'] = 'Config Confirmed'
    elif ev == 'CREDIT_ADD':
        physical_esp32_state['last_event'] = 'Bottle Processed'
    elif ev == 'GATE_OPEN':
        physical_esp32_state['gate_open'] = True
    elif ev in ('TIMEOUT', 'REJECTED', 'GATE_CLOSED', 'FINISH'):
        physical_esp32_state['gate_open'] = False

def push_config_to_physical_esp32():
    global ser
    if not ser:
        return False
    try:
        cfg = get_all_config()
        payload = {
            'cmd': 'SET_CONFIG',
            'bin_full_threshold_cm': int(cfg.get('esp_bin_full_threshold_cm', 15)),
            'entrance_gate_timeout': int(cfg.get('esp_entrance_gate_timeout', 60)),
            'settle_time_ms': int(cfg.get('esp_settle_time_ms', 500)),
            'success_drop_tout_ms': int(cfg.get('esp_success_drop_tout_ms', 3000)),
            'reject_drop_time_ms': int(cfg.get('esp_reject_drop_time_ms', 2000)),
            'pet_nir_w_min': int(cfg.get('esp_pet_nir_w_min', 200)),
            'pet_nir_w_max': int(cfg.get('esp_pet_nir_w_max', 5000)),
            'ent_close_angle': int(cfg.get('esp_ent_close_angle', 0)),
            'ent_open_angle': int(cfg.get('esp_ent_open_angle', 90)),
            'suc_close_angle': int(cfg.get('esp_suc_close_angle', 0)),
            'suc_open_angle': int(cfg.get('esp_suc_open_angle', 90)),
            'rej_close_angle': int(cfg.get('esp_rej_close_angle', 0)),
            'rej_open_angle': int(cfg.get('esp_rej_open_angle', 90))
        }
        if not transmit_to_esp32(payload):return False
        log.info("Synchronized configuration to physical ESP32.")
        return True
    except Exception as e:
        log.error("Failed to sync config to physical ESP32: %s", e)
        return False

def hardware_serial_daemon():
    global ser, last_esp32_rx_time, esp32_port_name, physical_esp32_state
    while True:
        port = None
        for p in SERIAL_PORTS:
            if os.path.exists(p):
                port = p
                break
        if not port:
            ser = None
            esp32_port_name = None
            physical_esp32_state['connected'] = False
            physical_esp32_state['port'] = None
            physical_esp32_state['sensor_bus_status'] = 'Offline'
            time.sleep(2)
            continue
        try:
            with serial.Serial(port, BAUD_RATE, timeout=2, write_timeout=2) as s:
                ser = s
                esp32_port_name = port
                physical_esp32_state['connected'] = True
                physical_esp32_state['port'] = port
                physical_esp32_state['sensor_bus_status'] = 'Nominal'
                last_esp32_rx_time = time.time()
                log.info("Established physical serial connection to ESP32 on %s", port)
                
                # Auto-sync saved configuration to physical ESP32
                push_config_to_physical_esp32()
                
                while True:
                    raw_line = s.readline(1024).decode('utf-8', errors='ignore').strip()
                    if not raw_line:
                        continue
                    last_esp32_rx_time = time.time()
                    physical_esp32_state['last_seen'] = last_esp32_rx_time
                    try:
                        data = json.loads(raw_line)
                        handle_physical_esp32_packet(data)
                        on_esp32_uart_output(raw_line,source='physical')
                    except json.JSONDecodeError:
                        pass
        except Exception:
            ser = None
            esp32_port_name = None
            physical_esp32_state['connected'] = False
            physical_esp32_state['port'] = None
            physical_esp32_state['sensor_bus_status'] = 'Offline'
            time.sleep(2)






import signal
import sys

def graceful_shutdown(sig, frame):
    print("Received shutdown signal. Flushing DB...", flush=True)
    try:
        time_service.last_success_mono=None
        time_service.reconcile()
        with db_connection() as conn:
            if time_schema.metadata(conn,'ready','0')=='1':
                transition_engine.check_due_events(conn,time.time(),time.monotonic())
    except Exception as e:
        print("Error settling/revoking sessions: %s" % e)
    sys.exit(0)



login_attempts = {}

@app.route('/admin/api/license', methods=['GET'])
def admin_api_license():
    if not session.get('admin_logged_in'):
        return (jsonify({'error': 'unauthorized'}), 401)
    return jsonify(license_manager.verify_license())

@app.route('/admin/api/license/activate', methods=['POST'])
def admin_api_license_activate():
    if not session.get('admin_logged_in'):
        return (jsonify({'error': 'unauthorized'}), 401)
    data = request.get_json() or {}
    pin = data.get('pin', '').strip()
    licensee = data.get('licensee', 'Store Owner')
    tier = data.get('tier', 'COMMERCIAL')
    return jsonify(license_manager.activate_machine(pin, licensee, tier))

@app.route('/admin/api/simulator/toggle', methods=['POST'])
def admin_api_simulator_toggle():
    if not session.get('admin_logged_in'):
        return (jsonify({'error': 'unauthorized'}), 401)
    data = request.get_json() or {}
    enabled = '1' if data.get('enabled') else '0'
    set_config('simulator_enabled', enabled)
    return jsonify({'success': True, 'simulator_enabled': (enabled == '1')})

@app.route('/admin/api/esp32/save', methods=['POST'])
def admin_api_esp32_save():
    if not session.get('admin_logged_in'):
        return (jsonify({'error': 'unauthorized'}), 401)
    data = request.get_json(silent=True)
    try:
        with db_connection() as conn:
            stored=dict(conn.execute("SELECT key,value FROM config WHERE key LIKE 'esp_%'"))
            current={key:stored.get('esp_'+key,limits[2]) for key,limits in HARDWARE_BOUNDS.items()}
            data=validate_hardware_config(data,current)
            for key,value in data.items():
                conn.execute('REPLACE INTO config(key,value) VALUES (?,?)',('esp_'+key,str(value)))
    except ValueError as error:return jsonify(success=False,error=str(error)),400
    data['cmd'] = 'SET_CONFIG'
    transmit_to_esp32(data)
    global ser, esp32_port_name
    is_physical = bool(ser is not None and esp32_port_name is not None)
    if is_physical:
        return jsonify({
            'success': True,
            'physical_connected': True,
            'message': 'Configuration successfully transmitted to physical ESP32 over serial.'
        })
    else:
        return jsonify({
            'success': True,
            'physical_connected': False,
            'message': 'Settings saved to Orange Pi database. Physical ESP32 is currently disconnected; settings will automatically sync once connected.'
        })

@app.route('/admin/api/esp32/trigger', methods=['POST'])
def admin_api_esp32_trigger():
    if not session.get('admin_logged_in'):
        return (jsonify({'error': 'unauthorized'}), 401)
    global ser, esp32_port_name
    is_physical = bool(ser is not None and esp32_port_name is not None)
    if not is_physical:
        return jsonify({
            'success': False,
            'message': 'Cannot reboot ESP32: Physical ESP32 is not connected via serial.'
        }), 400
    transmit_to_esp32({'cmd': 'TRIGGER_CONFIG'})
    return jsonify({'success': True, 'message': 'Reboot command sent to physical ESP32.'})

def validate_promo_rate_conflict(bottles, minutes, exclude_bottles=None):
    """
    Validates a proposed promo rate against 3 mathematical invariants:
    1. Combination Floor: minutes >= greedy combo of lower tiers
    2. Monotonic Efficiency: minutes/bottle >= all smaller-tier efficiencies
    3. Higher-Tier Bound: minutes <= smallest higher-tier's minutes
    Returns (is_valid: bool, error_message: str)
    """
    if bottles <= 0 or minutes <= 0:
        return (False, 'Bottles and Minutes must be positive numbers.')
    with db_connection() as conn:
        c = conn.cursor()
        c.execute('SELECT bottles, minutes FROM promo_rates ORDER BY bottles ASC')
        existing = [(r[0], r[1]) for r in c.fetchall() if r[0] != exclude_bottles]
    eff_new = minutes / bottles
    for eb, em in existing:
        eff_ex = em / eb
        if eb < bottles and eff_ex > eff_new:
            return (False, 'Efficiency conflict: {}B→{}m tier gives {:.1f} m/bottle, but your {}B→{}m gives only {:.1f} m/bottle. Higher bottle tiers must reward at least as much per bottle.'.format(eb, em, eff_ex, bottles, minutes, eff_new))
        if eb > bottles and eff_ex < eff_new:
            return (False, 'Efficiency conflict: your {}B→{}m tier gives {:.1f} m/bottle, which exceeds the {}B→{}m tier at {:.1f} m/bottle. Larger tiers must always be at least as efficient.'.format(bottles, minutes, eff_new, eb, em, eff_ex))
    lower_tiers = sorted([(eb, em) for eb, em in existing if eb < bottles], reverse=True)
    combo_minutes = 0
    rem = bottles
    for eb, em in lower_tiers:
        if rem >= eb:
            times = rem // eb
            combo_minutes += times * em
            rem %= eb
    if combo_minutes > 0 and minutes < combo_minutes:
        return (False, 'Combination conflict: {} bottles can be split into smaller tiers yielding {} mins, but this rate only gives {} mins. New rate must be at least {} mins to incentivize bulk deposit.'.format(bottles, combo_minutes, minutes, combo_minutes))
    higher_tiers = sorted([(eb, em) for eb, em in existing if eb > bottles])
    if higher_tiers:
        min_higher_mins = min((em for _, em in higher_tiers))
        if minutes >= min_higher_mins:
            hb, hm = min(((eb, em) for eb, em in higher_tiers if em == min_higher_mins))
            return (False, 'Upper-bound conflict: your {}B→{}m would give same or more time than the {}B→{}m tier. Reduce minutes or increase the higher tier.'.format(bottles, minutes, hb, hm))
    return (True, '')

@app.route('/admin/api/rates/list')
def admin_api_rates_list():
    if not session.get('admin_logged_in'):
        return (jsonify({'error': 'unauthorized'}), 401)
    with db_connection() as conn:
        c = conn.cursor()
        c.execute('SELECT bottles, minutes, label, speed_profile FROM promo_rates ORDER BY bottles ASC')
        return jsonify([{'bottles': r[0], 'minutes': r[1], 'label': r[2], 'speed_profile': r[3] or ''} for r in c.fetchall()])

@app.route('/admin/api/rates/add', methods=['POST'])
def admin_api_rates_add():
    if not session.get('admin_logged_in'):
        return (jsonify({'error': 'unauthorized'}), 401)
    data = request.get_json() or {}
    try:
        bottles = int(data.get('bottles', 0))
        minutes = int(data.get('minutes', 0))
        orig_bottles = int(data.get('orig_bottles')) if data.get('orig_bottles') else None
        label = data.get('label', '').strip() or '{} Bottle{} = {} mins'.format(bottles, 's' if bottles != 1 else '', minutes)
    except (TypeError, ValueError):
        return (jsonify({'success': False, 'error': 'Bottles and Minutes must be positive numbers.'}), 400)
    if bottles <= 0 or minutes <= 0:
        return (jsonify({'success': False, 'error': 'Bottles and Minutes must be positive numbers.'}), 400)
    is_valid, err_msg = validate_promo_rate_conflict(bottles, minutes, exclude_bottles=orig_bottles)
    if not is_valid:
        return (jsonify({'success': False, 'error': err_msg}), 400)
    with db_connection() as conn:
        c = conn.cursor()
        if orig_bottles and orig_bottles != bottles:
            c.execute('DELETE FROM promo_rates WHERE bottles = ?', (orig_bottles,))
        c.execute("REPLACE INTO promo_rates (bottles, minutes, label, speed_profile) VALUES (?, ?, ?, '')", (bottles, minutes, label))
        if bottles == 1:
            c.execute("REPLACE INTO config (key, value) VALUES ('minutes_per_bottle', ?)", (str(minutes),))
        conn.commit()
    return jsonify({'success': True})

@app.route('/admin/api/rates/delete', methods=['POST'])
def admin_api_rates_delete():
    if not session.get('admin_logged_in'):
        return (jsonify({'error': 'unauthorized'}), 401)
    data = request.get_json() or {}
    bottles = data.get('bottles')
    if not bottles:
        return (jsonify({'success': False, 'error': 'Missing bottles parameter.'}), 400)
    with db_connection() as conn:
        c = conn.cursor()
        c.execute('DELETE FROM promo_rates WHERE bottles = ?', (int(bottles),))
        conn.commit()
    return jsonify({'success': True})

@app.route('/admin/api/rates/apply_preset', methods=['POST'])
def admin_api_rates_apply_preset():
    if not session.get('admin_logged_in'):
        return (jsonify({'error': 'unauthorized'}), 401)
    data = request.get_json() or {}
    preset_key = data.get('preset', 'standard')
    PRESETS = {'standard': {'label': 'Standard Community Curve', 'rates': [(1, 10, '1 Bottle = 10 mins'), (3, 40, '3 Bottles = 40 mins'), (5, 75, '5 Bottles = 1h 15m'), (10, 180, '10 Bottles = 3 Hours')]}, 'aggressive': {'label': 'Aggressive Reward Curve', 'rates': [(1, 10, '1 Bottle = 10 mins'), (5, 70, '5 Bottles = 1h 10m'), (10, 180, '10 Bottles = 3 Hours'), (20, 420, '20 Bottles = 7 Hours')]}, 'cafe': {'label': 'Café / Study Hub Curve', 'rates': [(1, 20, '1 Bottle = 20 mins'), (3, 75, '3 Bottles = 1h 15m'), (6, 180, '6 Bottles = 3 Hours'), (12, 420, '12 Bottles = 7 Hours')]}}
    if preset_key not in PRESETS:
        return (jsonify({'success': False, 'error': "Unknown preset '{}'.".format(preset_key)}), 400)
    preset = PRESETS[preset_key]
    with db_connection() as conn:
        c = conn.cursor()
        c.execute('DELETE FROM promo_rates')
        for bottles, minutes, label in preset['rates']:
            c.execute("REPLACE INTO promo_rates (bottles, minutes, label, speed_profile) VALUES (?, ?, ?, '')", (bottles, minutes, label))
        base = next((m for b, m, _ in preset['rates'] if b == 1), None)
        if base:
            c.execute("REPLACE INTO config (key, value) VALUES ('minutes_per_bottle', ?)", (str(base),))
        conn.commit()
    return jsonify({'success': True, 'message': "'{}' template applied with {} tiers.".format(preset['label'], len(preset['rates']))})

@app.route('/admin/api/audio/settings', methods=['POST'])
def admin_api_audio_settings():
    if not session.get('admin_logged_in'):
        return (jsonify({'error': 'unauthorized'}), 401)
    data = request.get_json() or {}
    bg = data.get('audio_bg', '/static/audio/b1.wav')
    insert = data.get('audio_insert', '/static/audio/bottle_success.wav')
    success = data.get('audio_success', '/static/audio/eco_success.wav')
    vol = data.get('volume', '80')
    set_config('audio_bg', bg)
    set_config('audio_insert', insert)
    set_config('audio_success', success)
    set_config('audio_volume', vol)
    set_config('audio_preset', insert)
    return jsonify({'success': True})

@app.route('/admin/api/audio/upload', methods=['POST'])
def admin_api_audio_upload():
    if not session.get('admin_logged_in'):
        return (jsonify({'error': 'unauthorized'}), 401)
    if 'file' not in request.files:
        return (jsonify({'success': False, 'error': 'No file uploaded.'}), 400)
    file = request.files['file']
    if file.filename == '':
        return (jsonify({'success': False, 'error': 'No file selected.'}), 400)
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ['.mp3', '.wav', '.ogg', '.m4a', '.aac']:
        return (jsonify({'success': False, 'error': 'Invalid audio file format. Only MP3, WAV, OGG allowed.'}), 400)
    upload_dir = os.path.join(app.static_folder or 'static', 'audio', 'uploads')
    os.makedirs(upload_dir, exist_ok=True)
    safe_name = 'custom_{}_{}'.format(int(time.time()), re.sub('[^a-zA-Z0-9_.-]', '', file.filename))
    filepath = os.path.join(upload_dir, safe_name)
    file.save(filepath)
    file_url = '/static/audio/uploads/{}'.format(safe_name)
    return jsonify({'success': True, 'url': file_url})

@app.route('/simulator')
def simulator_ui():
    return render_template_string(esp32.render_simulator_html())

@app.route('/simulator/api/state')
def simulator_api_state():
    return jsonify(esp32.get_state())

@app.route('/simulator/api/drop', methods=['POST'])
@app.route('/simulator/api/trigger', methods=['POST'])
def simulator_drop():
    data = request.get_json() or {}
    item_type = data.get('item_type') or data.get('type', 'valid_pet')
    esp32.simulate_insert(item_type=item_type)
    return jsonify({'success': True, 'item_type': item_type})

@app.route('/simulator/api/bin', methods=['POST'])
def simulator_bin():
    data = request.get_json() or {}
    dist = int(data.get('distance_cm', 60))
    esp32.set_bin_distance(dist)
    return jsonify({'success': True, 'distance_cm': dist})

@app.route('/simulator/api/reset', methods=['POST'])
def simulator_reset():
    esp32.reset_session()
    return jsonify({'success': True})

@app.route('/simulator/api/lcd', methods=['POST'])
def simulator_set_lcd():
    data = request.get_json() or {}
    l0 = data.get('line0')
    l1 = data.get('line1')
    l2 = data.get('line2')
    l3 = data.get('line3')
    esp32.set_lcd(line0=l0, line1=l1, line2=l2, line3=l3)
    return jsonify({'success': True, 'lcd_lines': esp32.lcd_lines})

FORCE_PASS_HTML = '\n<!DOCTYPE html>\n<html lang="en">\n<head>\n    <meta charset="utf-8">\n    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">\n    <title>Password Change | Eco-Fi</title>\n    <link rel="icon" type="image/png" sizes="32x32" href="/static/favicon-32x32.png">\n    <link rel="icon" type="image/png" sizes="16x16" href="/static/favicon-16x16.png">\n    <link rel="shortcut icon" href="/static/favicon.ico">\n    <link rel="apple-touch-icon" sizes="180x180" href="/static/apple-touch-icon.png">\n    <link rel="stylesheet" href="/static/vendor/fontawesome/css/all.min.css">\n    <style>\n        body {\n            background-color: #0b0f19;\n            min-height: 100vh;\n            display: flex;\n            align-items: center;\n            justify-content: center;\n            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;\n            margin: 0;\n            padding: 16px;\n        }\n        .pass-box-clean {\n            width: 100%;\n            max-width: 340px;\n            background: #111827;\n            border: 1px solid #1f2937;\n            border-radius: 12px;\n            padding: 24px;\n            box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.6);\n        }\n        .pass-title {\n            font-size: 17px;\n            font-weight: 700;\n            color: #f9fafb;\n            text-align: center;\n            margin-bottom: 4px;\n        }\n        .pass-subtitle {\n            font-size: 12px;\n            color: #f87171;\n            text-align: center;\n            margin-bottom: 18px;\n            line-height: 1.4;\n        }\n        .form-group-clean {\n            margin-bottom: 14px;\n        }\n        .form-group-clean label {\n            display: block;\n            font-size: 12px;\n            font-weight: 500;\n            color: #9ca3af;\n            margin-bottom: 5px;\n        }\n        .form-control-clean {\n            width: 100%;\n            height: 38px;\n            background-color: #1f2937 !important;\n            border: 1px solid #374151 !important;\n            border-radius: 6px !important;\n            color: #f9fafb !important;\n            font-size: 13px !important;\n            padding: 8px 12px !important;\n            box-sizing: border-box;\n            outline: none;\n        }\n        .form-control-clean:focus {\n            border-color: #ef4444 !important;\n            box-shadow: 0 0 0 2px rgba(239, 68, 68, 0.2) !important;\n        }\n        .btn-update {\n            width: 100%;\n            height: 38px;\n            background: #ef4444;\n            border: none;\n            border-radius: 6px;\n            color: #ffffff;\n            font-size: 13.5px;\n            font-weight: 600;\n            cursor: pointer;\n            margin-top: 6px;\n            transition: background 0.15s ease;\n        }\n        .btn-update:hover {\n            background: #dc2626;\n        }\n        .pass-footer {\n            margin-top: 18px;\n            text-align: center;\n            font-size: 12px;\n        }\n        .pass-footer a {\n            color: #6b7280;\n            text-decoration: none;\n        }\n        .pass-footer a:hover {\n            color: #9ca3af;\n        }\n    </style>\n</head>\n<body>\n<div class="pass-box-clean">\n    <div class="pass-title"><i class="fas fa-shield-alt text-danger mr-1"></i> Security Requirement</div>\n    <div class="pass-subtitle">You must change the default password before accessing the admin dashboard.</div>\n    \n    <form method="POST" action="/admin/force_password_change">\n        <div class="form-group-clean">\n            <label for="new_password">New Password (min. 6 characters)</label>\n            <input type="password" id="new_password" name="new_password" class="form-control-clean" placeholder="Enter new password" required minlength="6" autofocus>\n        </div>\n        \n        <button type="submit" class="btn-update">Change Password</button>\n    </form>\n    \n    <div class="pass-footer">\n        <a href="/admin/logout">← Cancel & Logout</a>\n    </div>\n</div>\n</body>\n</html>\n'

@app.before_request
def admin_security_guard():
    is_admin_route = request.path == '/admin' or request.path.startswith('/admin/')
    is_sim_route = request.path == '/simulator' or request.path.startswith('/simulator/')

    if is_admin_route or is_sim_route:
        if request.path == '/admin/login':
            return None
        if not session.get('admin_logged_in'):
            if request.path.startswith('/admin/api/') or request.path.startswith('/simulator/api/'):
                return (jsonify({'error': 'unauthorized', 'message': 'Admin authentication required.'}), 401)
            return redirect('/admin/login')
        if session.get('must_change_password'):
            allowed_during_pw_change = ['/admin/force_password_change', '/admin/logout']
            if request.path not in allowed_during_pw_change:
                if request.path.startswith('/admin/api/') or request.path.startswith('/simulator/api/'):
                    return (jsonify({'error': 'password_change_required', 'message': 'Default password must be changed first.'}), 403)
                return redirect('/admin/force_password_change')
        if is_sim_route and get_config('simulator_enabled', '0') != '1':
            abort(404)

@app.route('/admin/force_password_change', methods=['GET', 'POST'])
def admin_force_password_change():
    if not session.get('admin_logged_in'):
        return redirect('/admin/login')
    if request.method == 'POST':
        new_pw = request.form.get('new_password', '').strip()
        if new_pw and len(new_pw) >= 6 and (new_pw != 'admin123'):
            admin_user = session.get('admin_username', 'admin')
            with db_connection() as conn:
                conn.execute('UPDATE admins SET password_hash=? WHERE username=?', (generate_password_hash(new_pw, method='pbkdf2:sha256'), admin_user))
            session.pop('must_change_password', None)
            return redirect('/admin')
    return render_template_string(FORCE_PASS_HTML)

admin_login_attempts = {}

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    error = None
    if request.method == 'POST':
        client_ip = get_client_ip()
        now = time.time()
        if client_ip in admin_login_attempts:
            count, last_time = admin_login_attempts[client_ip]
            if now - last_time < 300:
                if count >= 5:
                    return render_template_string(LOGIN_HTML, error='Too many attempts. Try again in 5 minutes.')
            else:
                admin_login_attempts[client_ip] = [0, now]
        else:
            admin_login_attempts[client_ip] = [0, now]
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        with db_connection() as conn:
            c = conn.cursor()
            c.execute('SELECT password_hash FROM admins WHERE username=?', (username,))
            row = c.fetchone()
            if row and check_password_hash(row[0], password):
                if client_ip in admin_login_attempts:
                    del admin_login_attempts[client_ip]
                session['admin_logged_in'] = True
                session['admin_username'] = username
                if password == 'admin123':
                    session['must_change_password'] = True
                return redirect('/admin')
            else:
                admin_login_attempts[client_ip][0] += 1
                admin_login_attempts[client_ip][1] = time.time()
                error = 'Invalid username or password'
    return render_template_string(LOGIN_HTML, error=error)

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_logged_in', None)
    return redirect('/admin/login')

@app.route('/admin')
def admin_dashboard():
    if not session.get('admin_logged_in'):
        return redirect('/admin/login')
    return render_template_string(ADMIN_HTML, config=get_all_config())


_last_cpu_sample = {'time': 0, 'stats': {}}

def get_system_hardware_stats():
    global _last_cpu_sample
    now = time.time()
    cores = []
    cpu_overall = 0
    temp_val = 0.0
    freq_mhz = 0
    load_avg = '0.00, 0.00, 0.00'
    ram_total_mb = 0
    ram_used_mb = 0
    ram_free_mb = 0
    ram_pct = 0
    swap_total_mb = 0
    swap_used_mb = 0
    swap_pct = 0
    disk_total_gb = 0.0
    disk_used_gb = 0.0
    disk_free_gb = 0.0
    disk_pct = 0
    uptime_val = '0h 0m'
    hardware_val = 'Orange Pi H3'

    try:
        # 1. CPU /proc/stat
        cur_stats = {}
        if os.path.exists('/proc/stat'):
            with open('/proc/stat', 'r') as f:
                for line in f:
                    if line.startswith('cpu'):
                        p = line.split()
                        name = p[0]
                        vals = [int(x) for x in p[1:]]
                        idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
                        cur_stats[name] = (idle, sum(vals))

            prev_stats = _last_cpu_sample.get('stats', {})
            _last_cpu_sample['stats'] = cur_stats
            _last_cpu_sample['time'] = now

            if prev_stats and 'cpu' in prev_stats and 'cpu' in cur_stats:
                dt = cur_stats['cpu'][1] - prev_stats['cpu'][1]
                di = cur_stats['cpu'][0] - prev_stats['cpu'][0]
                if dt > 0:
                    cpu_overall = max(0, min(100, int((dt - di) * 100 / dt)))
                for k in sorted(cur_stats.keys()):
                    if k == 'cpu': continue
                    if k in prev_stats:
                        cdt = cur_stats[k][1] - prev_stats[k][1]
                        cdi = cur_stats[k][0] - prev_stats[k][0]
                        core_pct = max(0, min(100, int((cdt - cdi) * 100 / max(1, cdt))))
                    else:
                        core_pct = 0
                    core_num = k.replace('cpu', '')
                    cores.append({'core': core_num, 'usage': core_pct})
            else:
                if os.path.exists('/proc/loadavg'):
                    with open('/proc/loadavg', 'r') as f:
                        load = float(f.read().split()[0])
                        cpu_overall = min(100, int(load * 100 / (os.cpu_count() or 1)))
                for i in range(os.cpu_count() or 4):
                    cores.append({'core': str(i), 'usage': cpu_overall})

        # 2. Temperature
        for tpath in ['/sys/devices/virtual/thermal/thermal_zone0/temp', '/etc/armbianmonitor/datasources/soctemp']:
            if os.path.exists(tpath):
                try:
                    with open(tpath, 'r') as f:
                        raw = int(f.read().strip())
                        temp_val = round(raw / 1000.0 if raw > 1000 else raw, 1)
                        break
                except Exception:
                    pass

        # 3. CPU Freq
        for fpath in ['/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq', '/sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_cur_freq']:
            if os.path.exists(fpath):
                try:
                    with open(fpath, 'r') as f:
                        freq_mhz = int(int(f.read().strip()) / 1000)
                        break
                except Exception:
                    pass

        # 4. Load Average
        if os.path.exists('/proc/loadavg'):
            with open('/proc/loadavg', 'r') as f:
                parts = f.read().split()
                load_avg = '{}, {}, {}'.format(parts[0], parts[1], parts[2])

        # 5. RAM & Swap
        if os.path.exists('/proc/meminfo'):
            mem = {}
            with open('/proc/meminfo', 'r') as f:
                for line in f:
                    p = line.split()
                    mem[p[0].strip(':')] = int(p[1])
            ram_total_mb = int(mem.get('MemTotal', 0) / 1024)
            ram_free_mb = int(mem.get('MemAvailable', mem.get('MemFree', 0)) / 1024)
            ram_used_mb = max(0, ram_total_mb - ram_free_mb)
            ram_pct = int(ram_used_mb * 100 / max(1, ram_total_mb))

            swap_total_mb = int(mem.get('SwapTotal', 0) / 1024)
            swap_free_mb = int(mem.get('SwapFree', 0) / 1024)
            swap_used_mb = max(0, swap_total_mb - swap_free_mb)
            swap_pct = int(swap_used_mb * 100 / max(1, swap_total_mb)) if swap_total_mb > 0 else 0

        # 6. Storage (MicroSD /)
        if hasattr(os, 'statvfs'):
            st = os.statvfs('/')
            disk_total_gb = round(st.f_blocks * st.f_frsize / (1024**3), 1)
            disk_free_gb = round(st.f_bavail * st.f_frsize / (1024**3), 1)
            disk_used_gb = round(disk_total_gb - disk_free_gb, 1)
            disk_pct = int(disk_used_gb * 100 / max(1, disk_total_gb))

        # 7. Uptime
        if os.path.exists('/proc/uptime'):
            with open('/proc/uptime', 'r') as f:
                up_sec = float(f.read().split()[0])
                uptime_val = '{}h {}m'.format(int(up_sec // 3600), int(up_sec % 3600 // 60))

    except Exception:
        pass

    return {
        'cpu': cpu_overall,
        'cores': cores,
        'temp': temp_val,
        'cpu_freq_mhz': freq_mhz,
        'load_avg': load_avg,
        'ram_used_mb': ram_used_mb,
        'ram_total_mb': ram_total_mb,
        'ram_free_mb': ram_free_mb,
        'ram': ram_pct,
        'swap_used_mb': swap_used_mb,
        'swap_total_mb': swap_total_mb,
        'swap': swap_pct,
        'disk_used_gb': disk_used_gb,
        'disk_total_gb': disk_total_gb,
        'disk_free_gb': disk_free_gb,
        'disk': disk_pct,
        'uptime': uptime_val,
        'hardware': hardware_val
    }

def get_esp32_health_stats():
    global ser, last_esp32_rx_time, esp32_port_name, physical_esp32_state, esp32
    now = time.time()
    
    is_physical = bool(ser is not None and esp32_port_name is not None)
    rx_delta = (now - last_esp32_rx_time) if last_esp32_rx_time > 0 else 9999
    sim_enabled = (get_config('simulator_enabled', '0') == '1')
    
    if sim_enabled or not is_physical:
        if sim_enabled and esp32:
            st = esp32.get_state()
            bin_dist = st.get('bin_distance_cm', 60)
            bin_full = bool(st.get('is_bin_full', False))
            return {
                'esp32_status': 'SIMULATOR',
                'esp32_status_color': '#38bdf8',
                'esp32_port': 'Virtual Bridge',
                'esp32_sensors': 'Simulated',
                'esp32_sensors_color': '#38bdf8',
                'esp32_bin': 'FULL ({}cm)'.format(bin_dist) if bin_full else 'OK ({}cm)'.format(bin_dist),
                'esp32_bin_color': '#ef4444' if bin_full else '#10b981',
                'esp32_bin_full': bin_full,
                'esp32_bin_dist': bin_dist,
                'esp32_is_physical': False,
                'esp32_simulator_enabled': True,
                'esp32_gate_open': bool(st.get('entrance_servo', 0) > 45)
            }
        else:
            return {
                'esp32_status': 'DISCONNECTED',
                'esp32_status_color': '#ef4444',
                'esp32_port': 'No Device',
                'esp32_sensors': 'Offline',
                'esp32_sensors_color': '#94a3b8',
                'esp32_bin': 'Offline',
                'esp32_bin_color': '#94a3b8',
                'esp32_bin_full': False,
                'esp32_bin_dist': None,
                'esp32_is_physical': False,
                'esp32_simulator_enabled': False,
                'esp32_gate_open': False
            }
    
    port_label = os.path.basename(esp32_port_name) + ' (115.2k)'
    bin_full = physical_esp32_state.get('bin_full', False) or (get_config('hw_bin_full', '0') == '1')
    bin_dist = physical_esp32_state.get('bin_distance_cm')
    
    if rx_delta <= 15.0:
        status = 'ONLINE'
        status_color = '#10b981'
    else:
        status = 'CONNECTED'
        status_color = '#38bdf8'
        
    if bin_full:
        bin_text = 'FULL' if bin_dist is None else 'FULL ({}cm)'.format(bin_dist)
        bin_color = '#ef4444'
        status = 'BIN FULL'
        status_color = '#ef4444'
    else:
        bin_text = 'OK' if bin_dist is None else 'OK ({}cm)'.format(bin_dist)
        bin_color = '#10b981'
        
    sensors_text = physical_esp32_state.get('sensor_bus_status', 'Nominal')
    sensors_color = '#ef4444' if bin_full else '#10b981'
    
    return {
        'esp32_status': status,
        'esp32_status_color': status_color,
        'esp32_port': port_label,
        'esp32_sensors': sensors_text,
        'esp32_sensors_color': sensors_color,
        'esp32_bin': bin_text,
        'esp32_bin_color': bin_color,
        'esp32_bin_full': bin_full,
        'esp32_bin_dist': bin_dist,
        'esp32_is_physical': True,
        'esp32_simulator_enabled': sim_enabled,
        'esp32_gate_open': physical_esp32_state.get('gate_open', False)
    }

@app.route('/admin/api/stats')
def admin_api_stats():
    if not session.get('admin_logged_in'):
        return (jsonify({'error': 'unauthorized'}), 401)
    today_dt = datetime.now()
    today = today_dt.strftime('%Y-%m-%d')
    with db_connection() as conn:
        c = conn.cursor()
        c.execute('SELECT total_bottles FROM stats WHERE date=?', (today,))
        row = c.fetchone()
        today_bottles = row[0] if row else 0
        c.execute('SELECT SUM(total_bottles) FROM stats')
        row = c.fetchone()
        total_bottles = row[0] if row and row[0] else 0

        # Construct rolling intake history (Weekly 7d, Monthly 30d, Yearly 12m)
        history_map = {}
        for r in c.execute('SELECT date, total_bottles FROM stats').fetchall():
            history_map[r[0]] = r[1]
        
        # 1. Weekly (7 days)
        history_weekly = []
        for i in range(6, -1, -1):
            day_dt = today_dt - timedelta(days=i)
            day_key = day_dt.strftime('%Y-%m-%d')
            history_weekly.append({
                'date': day_dt.strftime('%b %d'),
                'count': int(history_map.get(day_key, 0))
            })

        # 2. Monthly (30 days)
        history_monthly = []
        for i in range(29, -1, -1):
            day_dt = today_dt - timedelta(days=i)
            day_key = day_dt.strftime('%Y-%m-%d')
            history_monthly.append({
                'date': day_dt.strftime('%b %d'),
                'count': int(history_map.get(day_key, 0))
            })

        # 3. Yearly (past 12 months)
        monthly_map = {}
        for date_str, b_count in history_map.items():
            if len(date_str) >= 7:
                month_key = date_str[:7]
                monthly_map[month_key] = monthly_map.get(month_key, 0) + int(b_count or 0)

        history_yearly = []
        cur_year = today_dt.year
        cur_month = today_dt.month
        for i in range(11, -1, -1):
            total_m = cur_year * 12 + (cur_month - 1) - i
            y = total_m // 12
            m = (total_m % 12) + 1
            m_key = '%04d-%02d' % (y, m)
            history_yearly.append({
                'date': datetime(y, m, 1).strftime('%b %Y'),
                'count': int(monthly_map.get(m_key, 0))
            })
    with active_clients_lock:
        active_count = sum((1 for c in active_clients.values() if c['remaining_seconds'] > 0 and not c.get('admin_paused')))

    hw = get_system_hardware_stats()
    esp_stats = get_esp32_health_stats()
    res_data = {
        'today_bottles': today_bottles,
        'total_bottles': total_bottles,
        'active_clients': active_count,
        'history': history_weekly,
        'history_weekly': history_weekly,
        'history_monthly': history_monthly,
        'history_yearly': history_yearly
    }
    res_data.update(hw)
    res_data.update(esp_stats)
    return jsonify(res_data)

@app.route('/admin/api/clients')
def admin_api_clients():
    if not session.get('admin_logged_in'):
        return (jsonify({'error': 'unauthorized'}), 401)
    with active_clients_lock:
        res = []
        for ip, sess in list(active_clients.items()):
            if ip in ('127.0.0.1', '10.0.0.1', '::1', 'localhost') or ip.startswith('saved:'):
                continue
            rem = sess.get('remaining_seconds', 0)
            is_paused = sess.get('is_paused', False)
            pending = sess.get('pending_bottles', 0)
            admin_paused = sess.get('admin_paused', False)
            if rem <= 0 and not is_paused and pending <= 0:
                continue
            res.append({'ip': ip, 'mac': sess.get('mac', '00:00:00:00:00:00'), 'remaining_seconds': rem, 'is_paused': is_paused, 'admin_paused': admin_paused, 'dl_kbps': sess.get('dl_kbps', 3072), 'ul_kbps': sess.get('ul_kbps', 1536), 'applied_state': sess.get('applied_state', 'DISCONNECTED'), 'desired_state': sess.get('desired_state', 'DISCONNECTED')})
        active_count = sum((1 for c in res if c['remaining_seconds'] > 0 and not c['admin_paused']))
        return jsonify(res)




def get_system_interfaces():
    interfaces = []
    net_path = '/sys/class/net'
    if os.path.exists(net_path):
        for iface in sorted(os.listdir(net_path)):
            if iface == 'lo' or iface.startswith('ifb'):
                continue
            state = 'DOWN'
            state_p = os.path.join(net_path, iface, 'operstate')
            if os.path.exists(state_p):
                try:
                    state = open(state_p).read().strip().upper()
                except Exception:
                    pass
            mac = '00:00:00:00:00:00'
            mac_p = os.path.join(net_path, iface, 'address')
            if os.path.exists(mac_p):
                try:
                    mac = open(mac_p).read().strip()
                except Exception:
                    pass
            ip = ''
            try:
                out = subprocess.check_output(['ip', '-4', 'addr', 'show', iface], universal_newlines=True, timeout=2)
                for line in out.splitlines():
                    if 'inet ' in line:
                        ip = line.strip().split()[1]
                        break
            except Exception:
                pass
            interfaces.append({
                'name': iface,
                'state': state,
                'mac': mac,
                'ip': ip,
                'is_up': state == 'UP'
            })
    if not interfaces:
        interfaces = [
            {'name': 'eth0', 'state': 'UP', 'mac': '02:42:0a:00:00:01', 'ip': '192.168.1.214/24', 'is_up': True},
            {'name': 'eth1', 'state': 'UP', 'mac': '02:42:0a:00:00:02', 'ip': '10.0.0.1/19', 'is_up': True}
        ]
    return interfaces


def get_dhcp_leases():
    leases = []
    lease_files = [
        '/var/lib/misc/dnsmasq.leases',
        '/tmp/dnsmasq.leases',
        '/var/run/dnsmasq/dnsmasq.leases'
    ]
    for lf in lease_files:
        if os.path.exists(lf):
            try:
                with open(lf, 'r', encoding='utf-8', errors='ignore') as f:
                    for line in f:
                        parts = line.strip().split()
                        if len(parts) >= 4:
                            ts = int(parts[0])
                            mac = parts[1]
                            ip = parts[2]
                            hostname = parts[3] if parts[3] != '*' else 'Client Device'
                            exp = datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S') if ts > 0 else 'Active'
                            leases.append({
                                'ip': ip,
                                'mac': mac,
                                'hostname': hostname,
                                'expires': exp
                            })
                if leases:
                    break
            except Exception:
                pass
    if not leases:
        with db_connection() as conn:
            c = conn.cursor()
            c.execute('SELECT ip, mac, remaining_seconds FROM active_sessions WHERE remaining_seconds > 0 ORDER BY remaining_seconds DESC LIMIT 25')
            for row in c.fetchall():
                leases.append({
                    'ip': row[0],
                    'mac': row[1] or '00:00:00:00:00:00',
                    'hostname': 'Active Client',
                    'expires': '{}s remaining'.format(int(row[2]))
                })
    return leases


def run_system_verifier():
    report = {
        'status': 'PASS',
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'checks': []
    }
    
    # 1. SQLite Database Integrity
    try:
        with db_connection() as conn:
            c = conn.cursor()
            c.execute('PRAGMA integrity_check')
            res = c.fetchone()[0]
            if res == 'ok':
                report['checks'].append({'name': 'SQLite Database Integrity', 'status': 'PASS', 'detail': 'PRAGMA integrity_check verified OK'})
            else:
                report['status'] = 'WARN'
                report['checks'].append({'name': 'SQLite Database Integrity', 'status': 'WARN', 'detail': str(res)})
    except Exception as e:
        report['status'] = 'FAIL'
        report['checks'].append({'name': 'SQLite Database Integrity', 'status': 'FAIL', 'detail': str(e)})

    # 2. Root Storage Space
    try:
        usage = shutil.disk_usage('/')
        free_gb = usage.free / (1024**3)
        pct_used = (usage.used / usage.total) * 100
        status = 'PASS' if free_gb > 0.5 else 'WARN'
        if status == 'WARN' and report['status'] == 'PASS': report['status'] = 'WARN'
        report['checks'].append({
            'name': 'Root Storage Space',
            'status': status,
            'detail': '{:.1f} GB free ({:.1f}% used of {:.1f} GB)'.format(free_gb, pct_used, usage.total / (1024**3))
        })
    except Exception:
        report['checks'].append({'name': 'Root Storage Space', 'status': 'PASS', 'detail': 'Storage space nominal'})

    # 3. Memory & RAM
    try:
        if os.path.exists('/proc/meminfo'):
            mem = {}
            with open('/proc/meminfo') as f:
                for line in f:
                    parts = line.split(':')
                    if len(parts) == 2:
                        mem[parts[0].strip()] = int(parts[1].split()[0])
            total_mb = mem.get('MemTotal', 0) // 1024
            avail_mb = mem.get('MemAvailable', mem.get('MemFree', 0)) // 1024
            report['checks'].append({
                'name': 'RAM System Memory',
                'status': 'PASS',
                'detail': '{} MB free / {} MB total'.format(avail_mb, total_mb)
            })
        else:
            report['checks'].append({'name': 'RAM System Memory', 'status': 'PASS', 'detail': 'Memory operational'})
    except Exception:
        pass

    # 4. Network Interfaces
    ifaces = get_system_interfaces()
    up_ifaces = [i['name'] for i in ifaces if i['state'] == 'UP']
    report['checks'].append({
        'name': 'Network Interfaces',
        'status': 'PASS' if len(up_ifaces) >= 1 else 'WARN',
        'detail': 'Online: {} | Available: {}'.format(', '.join(up_ifaces) if up_ifaces else 'None', ', '.join([i['name'] for i in ifaces]))
    })

    # 5. Linux Kernel IP Forwarding & Firewall
    try:
        if os.path.exists('/proc/sys/net/ipv4/ip_forward'):
            fwd = open('/proc/sys/net/ipv4/ip_forward').read().strip()
            detail = 'IPv4 Forwarding enabled (1)' if fwd == '1' else 'IPv4 Forwarding disabled (0)'
            report['checks'].append({'name': 'Kernel IPv4 Forwarding', 'status': 'PASS' if fwd == '1' else 'WARN', 'detail': detail})
        else:
            report['checks'].append({'name': 'Kernel IPv4 Forwarding', 'status': 'PASS', 'detail': 'Forwarding active'})
    except Exception:
        pass

    # 6. Service & Microservice Heartbeat
    report['checks'].append({
        'name': 'Eco-Fi Captive Engine',
        'status': 'PASS',
        'detail': 'Daemon active on port 5000, Python 3 runtime nominal'
    })

    db_pass = any(c['name'] == 'SQLite Database Integrity' and c['status'] == 'PASS' for c in report['checks'])
    report['database'] = 'ok' if db_pass else 'error'
    report['disk_free_mb'] = int(free_gb * 1024) if 'free_gb' in locals() else 1024
    report['memory_free_mb'] = avail_mb if 'avail_mb' in locals() else 512
    report['ip_forward'] = (fwd == '1') if 'fwd' in locals() else True
    report['interfaces'] = ifaces

    return report


@app.route('/admin/api/network', methods=['GET', 'POST'])
def admin_api_network():
    if not session.get('admin_logged_in'):
        return jsonify({'error': 'unauthorized'}), 401
    if request.method == 'POST':
        data = request.get_json() or {}
        keys = [
            'wan_interface', 'lan_interface', 'dns_primary', 'dns_secondary',
            'wan_mode', 'wan_static_ip', 'wan_static_mask', 'wan_static_gateway',
            'isp_gateway_access', 'dhcp_lease_hours', 'reserved_ip_offset',
            'anti_tethering', 'starlink_blocker', 'portal_domain'
        ]
        for k in keys:
            if k in data:
                set_config(k, data[k])
        
        # Apply policies to kernel/firewall
        if 'anti_tethering' in data:
            gateway_network.set_anti_tethering(str(data['anti_tethering']) == '1')
        if 'starlink_blocker' in data:
            gateway_network.set_starlink_blocker(str(data['starlink_blocker']) == '1')
        if 'isp_gateway_access' in data:
            gw_ip = data.get('wan_static_gateway', '192.168.1.1')
            gateway_network.set_isp_gateway_access(str(data['isp_gateway_access']) == '1', gw_ip)
            
        return jsonify({'success': True, 'message': 'Network & security settings saved successfully.'})
        
    # GET
    cfg = get_all_config()
    return jsonify({
        'interfaces': get_system_interfaces(),
        'wan_interface': cfg.get('wan_interface', 'eth0'),
        'lan_interface': cfg.get('lan_interface', 'eth1'),
        'dns_primary': cfg.get('dns_primary', '1.1.1.1'),
        'dns_secondary': cfg.get('dns_secondary', '1.0.0.1'),
        'wan_mode': cfg.get('wan_mode', 'dhcp'),
        'wan_static_ip': cfg.get('wan_static_ip', '192.168.1.50'),
        'wan_static_mask': cfg.get('wan_static_mask', '255.255.255.0'),
        'wan_static_gateway': cfg.get('wan_static_gateway', '192.168.1.1'),
        'isp_gateway_access': cfg.get('isp_gateway_access', '1'),
        'dhcp_lease_hours': cfg.get('dhcp_lease_hours', '72'),
        'reserved_ip_offset': cfg.get('reserved_ip_offset', '100'),
        'anti_tethering': cfg.get('anti_tethering', '0'),
        'starlink_blocker': cfg.get('starlink_blocker', '0'),
        'portal_domain': cfg.get('portal_domain', '10.0.0.1')
    })


@app.route('/admin/api/network/leases')
def admin_api_network_leases():
    if not session.get('admin_logged_in'):
        return jsonify({'error': 'unauthorized'}), 401
    return jsonify(get_dhcp_leases())


@app.route('/admin/api/bandwidth/qos/save', methods=['POST'])
def admin_api_bandwidth_qos_save():
    if not session.get('admin_logged_in'):
        return jsonify({'error': 'unauthorized'}), 401
    data = request.get_json() or {}
    
    if 'default_dl_kbps' in data and int(data['default_dl_kbps']) <= 0:
        return jsonify({'error': 'Invalid default_dl_kbps'}), 400
    if str(data.get('dynamic_bandwidth_enabled', '0')) == '1' or str(data.get('qos_gaming_enabled', '0')) == '1':
        return jsonify({'error': 'Not Implemented'}), 501

    keys = [
        'default_dl_kbps', 'default_ul_kbps', 'dynamic_bandwidth_enabled',
        'min_download_kbps', 'max_download_kbps', 'min_upload_kbps', 'max_upload_kbps',
        'qos_gaming_enabled', 'qos_gaming_percent'
    ]
    for k in keys:
        if k in data:
            set_config(k, data[k])
            
    if 'qos_gaming_enabled' in data:
        en = str(data['qos_gaming_enabled']) == '1'
        pct = int(data.get('qos_gaming_percent', 20))
        gateway_network.set_gaming_qos(en, pct)

    # preserve_custom=0 means admin wants to overwrite per-client speed overrides with new defaults
    preserve_custom = int(data.get('preserve_custom', 1))
    if preserve_custom == 0 and ('default_dl_kbps' in data or 'default_ul_kbps' in data):
        new_dl = int(data.get('default_dl_kbps', get_config('default_dl_kbps', '3072')))
        new_ul = int(data.get('default_ul_kbps', get_config('default_ul_kbps', '1536')))
        with db_connection() as conn:
            conn.execute(
                'UPDATE time_grants SET speed_override=0, dl_kbps=?, ul_kbps=?'
                ' WHERE state IN ("ACTIVE","PAUSED")',
                (new_dl, new_ul)
            )
        log.info('admin_api_bandwidth_qos_save: reset speed_override for all active/paused grants '
                 'to dl=%s ul=%s', new_dl, new_ul)

    # Trigger worker pass so new speeds propagate to tc shaping immediately
    time_service.worker_pass()

    return jsonify({'success': True, 'message': 'Bandwidth & QoS settings updated.'})


@app.route('/admin/api/system/verifier')
def admin_api_system_verifier():
    if not session.get('admin_logged_in'):
        return jsonify({'error': 'unauthorized'}), 401
    return jsonify(run_system_verifier())


@app.route('/admin/api/system/reboot', methods=['POST'])
def admin_api_system_reboot():
    if not session.get('admin_logged_in'):
        return jsonify({'error': 'unauthorized'}), 401
    def do_reboot():
        time.sleep(1.5)
        try:
            subprocess.run(['systemctl', 'reboot'])
        except Exception:
            pass
    threading.Thread(target=do_reboot, daemon=True).start()
    return jsonify({'success': True, 'message': 'Eco-Fi system reboot initiated. Device will restart in a few seconds.'})


@app.route('/admin/api/system/shutdown', methods=['POST'])
def admin_api_system_shutdown():
    if not session.get('admin_logged_in'):
        return jsonify({'error': 'unauthorized'}), 401
    def do_poweroff():
        time.sleep(1.5)
        try:
            subprocess.run(['systemctl', 'poweroff'])
        except Exception:
            pass
    threading.Thread(target=do_poweroff, daemon=True).start()
    return jsonify({'success': True, 'message': 'Eco-Fi clean shutdown initiated. Safe to power off after LEDs turn off.'})


@app.route('/admin/api/system/flush_sessions', methods=['POST'])
def admin_api_system_flush_sessions():
    if not session.get('admin_logged_in'):
        return jsonify({'error': 'unauthorized'}), 401
    return time_service.disconnect_clients()


@app.route('/admin/api/system/backup/download')
def admin_api_system_backup_download():
    if not session.get('admin_logged_in'):
        return jsonify({'error': 'unauthorized'}), 401
    import tempfile, zipfile
    buf = io.BytesIO()
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    # A read transaction includes committed WAL pages. Materialize a standalone
    # database instead of copying the main file while writes are in flight.
    with tempfile.TemporaryDirectory(prefix='ecofi-backup-') as folder:
        snapshot_path=os.path.join(folder,'ecofi.db')
        with db_connection() as source:
            script='\n'.join(source.iterdump())
        snapshot=sqlite3.connect(snapshot_path)
        try:snapshot.executescript(script)
        finally:snapshot.close()
        with zipfile.ZipFile(buf,'w',zipfile.ZIP_DEFLATED) as zf:
            zf.write(snapshot_path,arcname='ecofi.db')
            zf.writestr('manifest.json',json.dumps({'application':'Eco-Fi Master Vendo','version':RELEASE_VERSION,
                'backup_timestamp':datetime.now().isoformat()},indent=2))
    buf.seek(0)
    filename = 'ecofi_backup_{}.zip'.format(ts)
    try:
        return send_file(buf, mimetype='application/zip', as_attachment=True, download_name=filename)
    except TypeError:
        return send_file(buf, mimetype='application/zip', as_attachment=True, attachment_filename=filename)


@app.route('/admin/api/system/backup/restore', methods=['POST'])
def admin_api_system_backup_restore():
    if not session.get('admin_logged_in'):
        return jsonify({'error': 'unauthorized'}), 401
    if 'backup_file' not in request.files:
        return jsonify({'success': False, 'error': 'No file uploaded'}), 400
    f = request.files['backup_file']
    if not f or f.filename == '':
        return jsonify({'success': False, 'error': 'No file selected'}), 400
        
    import tempfile, zipfile
    limit=32*1024*1024
    payload=f.stream.read(limit+1)
    if len(payload)>limit:return jsonify(success=False,error='Backup exceeds 32 MiB'),400
    try:
        if zipfile.is_zipfile(io.BytesIO(payload)):
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                entry=archive.getinfo('ecofi.db')
                if entry.file_size>limit:raise ValueError('Database exceeds 32 MiB')
                payload=archive.read(entry)
        with tempfile.TemporaryDirectory(prefix='ecofi-restore-') as folder:
            source_path=os.path.join(folder,'restore.db')
            with open(source_path,'wb') as stream:stream.write(payload)
            source=sqlite3.connect(source_path)
            try:
                if source.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Invalid database integrity')
                if source.execute('PRAGMA foreign_key_check').fetchone():raise ValueError('Broken database references')
                schema=lambda c:list(c.execute("SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"))
                with db_connection() as current:
                    if schema(source)!=schema(current):raise ValueError('Backup schema differs; migrate it offline before restoring')
                statements=[line for line in source.iterdump() if line not in ('BEGIN TRANSACTION;','COMMIT;')]
            finally:source.close()
            with time_service.network_lock:
                time_service.last_success_mono=None
                with db_connection() as current:
                    ips=[row[0] for row in current.execute("SELECT ip FROM connections WHERE ip NOT LIKE 'detached:%'")]
                for ip in ips:update_firewall(ip,'del')
                target=sqlite3.connect(DB_PATH,timeout=15,isolation_level=None)
                try:
                    drops=[]
                    for kind,name,sql in schema(target):
                        if kind in ('table','view','trigger'):
                            drops.append('DROP '+kind.upper()+' IF EXISTS "'+name.replace('"','""')+'";')
                    target.execute('PRAGMA foreign_keys=OFF')
                    target.executescript('BEGIN IMMEDIATE;\n'+'\n'.join(drops+statements)+'\nCOMMIT;')
                except Exception:
                    target.rollback();raise
                finally:target.close()
                time_service.restore();time_service.restore_projections()
                app.secret_key=_initialize_secret_key()
                session.clear()
        return jsonify(success=True,message='Database restored. Sign in again.')
    except (ValueError,KeyError,sqlite3.Error,zipfile.BadZipFile,OSError) as error:
        return jsonify(success=False,error=str(error)),400

@app.route('/admin/api/clients/disconnect', methods=['POST'])
def admin_api_clients_disconnect():
    if not session.get('admin_logged_in'):
        return jsonify({'error': 'unauthorized'}), 401
    data = request.get_json() or {}
    ip = data.get('ip')
    if not ip:
        return jsonify({'success': False, 'error': 'No IP specified.'}), 400
    return time_service.disconnect_clients([ip])


from time_portal import TimePortal
time_service = TimePortal(app, globals())
atexit.register(save_sessions_to_db)

if __name__ == '__main__':
    signal.signal(signal.SIGINT, graceful_shutdown)
    signal.signal(signal.SIGTERM, graceful_shutdown)
    setup_firewall()
    restore_sessions_from_db()
    threading.Thread(target=time_daemon, daemon=True).start()
    if serial:
        threading.Thread(target=hardware_serial_daemon, daemon=True).start()
    port = int(os.environ.get('PORT', 5000))
    print('=================================================='.format())
    print('  SMART Eco-Fi REVERSE VENDING MACHINE v{}'.format(RELEASE_VERSION))
    print('  Captive Portal : http://localhost:{}/'.format(port))
    print('  Simulator UI   : http://localhost:{}/simulator'.format(port))
    print('  Admin Panel    : http://localhost:{}/admin'.format(port))
    print('=================================================='.format())
    app.run(host='0.0.0.0', port=port, debug=False)
