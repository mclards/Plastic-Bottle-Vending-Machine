import os
import sqlite3
import threading
import json
import time
import serial
from datetime import datetime
from flask import Flask, request, render_template_string, jsonify, session, redirect, url_for
from werkzeug.security import generate_password_hash, check_password_hash
import logging

log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

app = Flask(__name__, static_folder="static")
app.secret_key = "eco_fi_super_secret_key_change_in_production"

DB_PATH = "vendo_sessions.db"

active_clients = {}
ser = None

def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS stats (date TEXT PRIMARY KEY, total_bottles INTEGER)''')
        c.execute('''CREATE TABLE IF NOT EXISTS admins (username TEXT PRIMARY KEY, password_hash TEXT)''')
        
        c.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('minutes_per_bottle', '15')")
        c.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('drop_timeout', '30')")
        c.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('default_dl_kbps', '2048')")
        c.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('default_ul_kbps', '1024')")
        c.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('custom_css', '')")
        c.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('telegram_bot_token', '')")
        c.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('telegram_chat_id', '')")
        c.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('anti_tethering', '0')")
        default_hash = generate_password_hash("admin123")
        c.execute("INSERT OR IGNORE INTO admins (username, password_hash) VALUES ('admin', ?)", (default_hash,))
        conn.commit()

init_db()

import platform
import subprocess


import urllib.request
import urllib.parse
import base64

def apply_anti_tethering(enable):
    if platform.system() != "Linux":
        print(f"[MOCK] Anti-Tethering Enabled: {enable}")
        return
    if enable:
        print("[IPTABLES] Enforcing Anti-Tethering (TTL=64)")
        # os.system("iptables -t mangle -A POSTROUTING -j TTL --ttl-set 64")
    else:
        print("[IPTABLES] Disabling Anti-Tethering")
        # os.system("iptables -t mangle -D POSTROUTING -j TTL --ttl-set 64")

import json
def send_telegram_alert():
    bot_token = get_config('telegram_bot_token')
    chat_id = get_config('telegram_chat_id')
    
    if not bot_token or not chat_id:
        print("[Telegram] Credentials not configured. Skipping alert.")
        return
        
    try:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": "🚨 *Eco-Fi Alert*\n\nThe recycling bin has reached **100% capacity**! Please empty the bin to allow more users to recycle.",
            "parse_mode": "Markdown"
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5)
        print("[Telegram] Bin Full Alert sent successfully!")
    except Exception as e:
        print(f"[Telegram] Failed to send alert: {e}")

def apply_bandwidth_limit(ip, dl_kbps, ul_kbps):
    if platform.system() != "Linux":
        print(f"[MOCK] Applied bandwidth limit to {ip}: {dl_kbps}Kbps DL, {ul_kbps}Kbps UL")
        return
    print(f"[TC] Shaping {ip} to DL={dl_kbps} UL={ul_kbps}")

def remove_bandwidth_limit(ip):
    if platform.system() != "Linux":
        print(f"[MOCK] Removed bandwidth limit for {ip}")
        return
    print(f"[TC] Removed shaping for {ip}")

def get_config(key):
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("SELECT value FROM config WHERE key=?", (key,))
        row = c.fetchone()
        return row[0] if row else None

def set_config(key, value):
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("REPLACE INTO config (key, value) VALUES (?, ?)", (key, str(value)))
        conn.commit()

def record_bottle_drop(count):
    today = datetime.now().strftime('%Y-%m-%d')
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("INSERT OR IGNORE INTO stats (date, total_bottles) VALUES (?, 0)", (today,))
        c.execute("UPDATE stats SET total_bottles = total_bottles + ? WHERE date = ?", (count, today))
        conn.commit()

USER_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8"><title>Eco-Fi Portal</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/5.15.4/css/all.min.css">
    <style>
        * { box-sizing: border-box; }
        body { 
            font-family: 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; 
            margin: 0; padding: 0; min-height: 100vh;
            background: linear-gradient(rgba(15, 23, 42, 0.75), rgba(15, 23, 42, 0.9)), url('/static/banner.jpg') no-repeat center center fixed;
            background-size: cover;
            color: white; 
            display: flex; flex-direction: column; align-items: center; 
        }
        
        .portal-container {
            width: 100%; max-width: 450px;
            margin-top: 5vh; margin-bottom: 20px;
            background: rgba(30, 41, 59, 0.65);
            backdrop-filter: blur(16px);
            -webkit-backdrop-filter: blur(16px);
            border-radius: 24px;
            box-shadow: 0 25px 50px -12px rgba(0,0,0,0.6);
            border: 1px solid rgba(255, 255, 255, 0.15);
            padding: 30px 20px;
            text-align: center;
        }

        .brand-logo { max-width: 160px; margin-bottom: 15px; filter: drop-shadow(0 4px 6px rgba(0,0,0,0.4)); }
        .brand-title { color: #f8fafc; font-size: 24px; font-weight: 700; margin: 0 0 25px 0; letter-spacing: 1px; }

        .status-box {
            background: rgba(15, 23, 42, 0.5);
            border-radius: 16px; padding: 20px; margin-bottom: 25px;
            border: 1px solid rgba(16, 185, 129, 0.3);
            box-shadow: inset 0 2px 10px rgba(0,0,0,0.3);
        }
        .time-display { font-size: 48px; font-family: 'Courier New', monospace; font-weight: bold; color: #10B981; margin: 10px 0; text-shadow: 0 0 10px rgba(16, 185, 129, 0.4); }
        .status-text { font-size: 14px; text-transform: uppercase; letter-spacing: 1.5px; color: #94a3b8; font-weight: 600; }
        .status-badge { display: inline-block; padding: 4px 12px; border-radius: 20px; font-size: 12px; font-weight: bold; margin-top: 10px; }
        .bg-active { background: rgba(16, 185, 129, 0.2); color: #34d399; }
        .bg-paused { background: rgba(245, 158, 11, 0.2); color: #fbbf24; }
        .bg-inactive { background: rgba(239, 68, 68, 0.2); color: #f87171; }

        .action-btn {
            width: 100%; padding: 18px; border-radius: 14px; border: none; font-size: 18px; font-weight: 700;
            color: white; cursor: pointer; transition: all 0.2s ease; margin-bottom: 15px;
            display: flex; align-items: center; justify-content: center; gap: 10px;
            box-shadow: 0 4px 15px rgba(0,0,0,0.2);
        }
        .action-btn:active { transform: scale(0.98); }
        .btn-insert { background: linear-gradient(135deg, #10B981 0%, #059669 100%); border: 1px solid #34d399; }
        .btn-pause { background: linear-gradient(135deg, #f59e0b 0%, #d97706 100%); border: 1px solid #fbbf24; }
        .btn-resume { background: linear-gradient(135deg, #3b82f6 0%, #2563eb 100%); border: 1px solid #60a5fa; }

        .guidelines-box {
            width: 100%; max-width: 450px;
            background: rgba(15, 23, 42, 0.8);
            backdrop-filter: blur(8px);
            border-radius: 20px; padding: 25px 20px;
            border: 1px dashed rgba(148, 163, 184, 0.4);
            margin-bottom: 30px;
        }
        .guidelines-box h3 { color: #e2e8f0; margin-top: 0; font-size: 18px; text-align: center; margin-bottom: 20px; }
        .plastic-types { display: flex; justify-content: space-around; flex-wrap: wrap; gap: 15px; }
        .plastic-item { text-align: center; width: 30%; }
        .plastic-item i { font-size: 32px; color: #38bdf8; margin-bottom: 8px; display: block; }
        .plastic-item span { font-size: 12px; color: #cbd5e1; font-weight: 600; display: block; }
        .plastic-item .badge { background: #0f172a; border-radius: 4px; padding: 2px 6px; font-size: 10px; color: #10B981; border: 1px solid #10B981; margin-top: 4px; display: inline-block; }

        {{ custom_css }}
    </style>
</head>
<body>

    <div class="portal-container">
        <img src="/static/banner-main.jpg" alt="Eco-Fi Banner" style="width: 100%; border-radius: 12px; margin-bottom: 20px; box-shadow: 0 4px 10px rgba(0,0,0,0.3);">

        <div class="status-box">
            <div class="status-text">Remaining WiFi Time</div>
            <div class="time-display" id="time-display">00:00:00</div>
            <div id="status-badge" class="status-badge bg-inactive">DISCONNECTED</div>
        </div>

        <button class="action-btn btn-insert" onclick="mockInsertBottle()">
            <i class="fas fa-recycle"></i> Insert Plastic Bottle
        </button>

        <div style="display: flex; gap: 10px;">
            <button class="action-btn btn-pause" onclick="pauseTime()">
                <i class="fas fa-pause-circle"></i> Pause
            </button>
            <button class="action-btn btn-resume" onclick="resumeTime()">
                <i class="fas fa-play-circle"></i> Resume
            </button>
        </div>
        
        <p style="font-size: 12px; color: #94a3b8; margin-top: 15px;">
            Current Rates: 1 Bottle = {{ config.default_dl_kbps }} Minutes
        </p>
    </div>

    <!-- Guidelines Footer -->
    <div class="guidelines-box">
        <h3><i class="fas fa-info-circle mr-1"></i> Acceptable Plastics</h3>
        <div class="plastic-types">
            <div class="plastic-item">
                <i class="fas fa-wine-bottle"></i>
                <span>PET Bottles</span>
                <div class="badge">ACCEPTED</div>
            </div>
            <div class="plastic-item">
                <i class="fas fa-prescription-bottle"></i>
                <span>HDPE Jugs</span>
                <div class="badge">ACCEPTED</div>
            </div>
            <div class="plastic-item">
                <i class="fas fa-trash-alt" style="color: #f87171;"></i>
                <span>Trash/PVC</span>
                <div class="badge" style="color: #f87171; border-color: #f87171;">REJECTED</div>
            </div>
        </div>
        <p style="font-size: 11px; color: #64748b; text-align: center; margin: 15px 0 0 0; line-height: 1.4;">
            Please ensure bottles are empty and uncrushed. The built-in NIR spectrometer and load cell will automatically reject invalid items.
        </p>
    </div>

    <script>
        // Audio Context (Web Audio API)
        const AudioContext = window.AudioContext || window.webkitAudioContext;
        const audioCtx = new AudioContext();

        function playTick() {
            if (audioCtx.state === 'suspended') audioCtx.resume();
            const osc = audioCtx.createOscillator();
            const gainNode = audioCtx.createGain();
            osc.connect(gainNode);
            gainNode.connect(audioCtx.destination);
            
            osc.type = 'sine';
            osc.frequency.setValueAtTime(800, audioCtx.currentTime);
            osc.frequency.exponentialRampToValueAtTime(1200, audioCtx.currentTime + 0.1);
            
            gainNode.gain.setValueAtTime(0, audioCtx.currentTime);
            gainNode.gain.linearRampToValueAtTime(0.5, audioCtx.currentTime + 0.02);
            gainNode.gain.exponentialRampToValueAtTime(0.01, audioCtx.currentTime + 0.1);
            
            osc.start();
            osc.stop(audioCtx.currentTime + 0.1);
        }

        let timeRemaining = {{ time_remaining }};
        let isPaused = {{ "true" if is_paused else "false" }};

        function formatTime(seconds) {
            const h = Math.floor(seconds / 3600).toString().padStart(2, '0');
            const m = Math.floor((seconds % 3600) / 60).toString().padStart(2, '0');
            const s = (seconds % 60).toString().padStart(2, '0');
            return `${h}:${m}:${s}`;
        }

        function updateDisplay() {
            document.getElementById('time-display').innerText = formatTime(timeRemaining);
            let badge = document.getElementById('status-badge');
            
            if (timeRemaining > 0) {
                if (isPaused) {
                    badge.innerText = "TIME PAUSED";
                    badge.className = "status-badge bg-paused";
                } else {
                    badge.innerText = "CONNECTED";
                    badge.className = "status-badge bg-active";
                }
            } else {
                badge.innerText = "NO TIME LEFT";
                badge.className = "status-badge bg-inactive";
            }
        }

        setInterval(() => {
            if (timeRemaining > 0 && !isPaused) {
                timeRemaining--;
                updateDisplay();
            }
        }, 1000);

        function mockInsertBottle() {
            fetch('/mock_drop').then(res => res.json()).then(data => {
                if(data.status === 'ok') {
                    playTick();
                    timeRemaining = data.time_remaining;
                    isPaused = false;
                    updateDisplay();
                }
            });
        }

        function pauseTime() {
            fetch('/api/pause').then(res => res.json()).then(data => {
                if(data.status === 'ok') {
                    isPaused = true;
                    updateDisplay();
                }
            });
        }

        function resumeTime() {
            fetch('/api/resume').then(res => res.json()).then(data => {
                if(data.status === 'ok') {
                    isPaused = false;
                    updateDisplay();
                }
            });
        }

        updateDisplay();
    </script>
</body>
</html>
"""

LOGIN_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8"><title>Eco-Fi Secure Admin</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #0f172a; color: white; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; }
        .login-box { background: rgba(30, 41, 59, 0.8); backdrop-filter: blur(10px); padding: 40px 30px; border-radius: 16px; box-shadow: 0 10px 25px rgba(0,0,0,0.5); width: 100%; max-width: 320px; text-align: center; border: 1px solid #334155; }
        .login-box h2 { color: #10B981; margin-top: 0; font-size: 28px; }
        input { width: 90%; padding: 12px 15px; margin: 10px 0; border: 1px solid #475569; border-radius: 8px; background: #0f172a; color: white; outline: none; transition: border-color 0.2s; }
        input:focus { border-color: #10B981; }
        button { width: 100%; padding: 14px; background: linear-gradient(135deg, #10B981 0%, #059669 100%); border: none; color: white; border-radius: 8px; cursor: pointer; font-size: 16px; font-weight: 600; margin-top: 15px; transition: transform 0.1s; }
        button:active { transform: scale(0.98); }
        .error { color: #ef4444; font-size: 14px; margin-bottom: 10px; background: rgba(239, 68, 68, 0.1); padding: 10px; border-radius: 6px; }
    </style>
</head>
<body>
    <div class="login-box">
        <h2>Admin Portal</h2>
        {% if error %}<div class="error">{{ error }}</div>{% endif %}
        <form method="POST" action="/admin/login">
            <input type="text" name="username" placeholder="Username" required autofocus>
            <input type="password" name="password" placeholder="Password" required>
            <button type="submit">Secure Login</button>
        </form>
    </div>
</body>
</html>
"""

ADMIN_HTML = """
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta http-equiv="X-UA-Compatible" content="IE=edge">
  <title>PisoFi Admin</title>
  <meta content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no" name="viewport">
  
  <!-- Bootstrap 3.3.7 -->
  <link rel="stylesheet" href="https://maxcdn.bootstrapcdn.com/bootstrap/3.3.7/css/bootstrap.min.css">
  <!-- Font Awesome -->
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/4.7.0/css/font-awesome.min.css">
  <!-- AdminLTE 2 Theme -->
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/admin-lte/2.4.18/css/AdminLTE.min.css">
  <!-- AdminLTE Skins. We use skin-blue -->
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/admin-lte/2.4.18/css/skins/skin-blue.min.css">
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>

  <style>
    .content-wrapper { background-color: #ecf0f5; }
    .nav-tabs-custom > .nav-tabs > li.active { border-top-color: #00a65a; }
  </style>
</head>
<body class="hold-transition skin-blue sidebar-mini">
<div class="wrapper">

  <header class="main-header">
    <!-- Logo -->
    <a href="#" class="logo">
      <!-- mini logo for sidebar mini 50x50 pixels -->
      <span class="logo-mini"><img src="/static/logo.jpg" style="max-height: 40px;" onerror="this.style.display='none'"></span>
      <!-- logo for regular state and mobile devices -->
      <span class="logo-lg">
        <img src="/static/logo.jpg" style="max-height: 40px; margin-right: 10px;" onerror="this.style.display='none'"><b>Eco-Fi</b> Admin
      </span>
    </a>
    
    <!-- Header Navbar -->
    <nav class="navbar navbar-static-top">
      <!-- Sidebar toggle button-->
      <a href="#" class="sidebar-toggle" data-toggle="push-menu" role="button">
        <span class="sr-only">Toggle navigation</span>
      </a>

      <div class="navbar-custom-menu">
        <ul class="nav navbar-nav">
          <!-- View Live Portal -->
          <li>
            <a href="/" target="_blank">
              <i class="fa fa-external-link"></i> <span class="hidden-xs">Live Portal</span>
            </a>
          </li>
          
          <!-- User Account Menu -->
          <li class="dropdown user user-menu">
            <a href="#" class="dropdown-toggle" data-toggle="dropdown">
              <img src="/static/logo.jpg" class="user-image" alt="User Image" onerror="this.src='https://adminlte.io/themes/AdminLTE/dist/img/user2-160x160.jpg'">
              <span class="hidden-xs">Administrator</span>
            </a>
            <ul class="dropdown-menu">
              <!-- User image -->
              <li class="user-header">
                <img src="/static/logo.jpg" class="img-circle" alt="User Image" onerror="this.style.display='none'">
                <p>
                  Eco-Fi Administrator
                  <small>System Management</small>
                </p>
              </li>
              <!-- Menu Footer-->
              <li class="user-footer">
                <div class="pull-left">
                  <a href="#" class="btn btn-default btn-flat" onclick="switchTab('settings')">Settings</a>
                </div>
                <div class="pull-right">
                  <a href="/admin/logout" class="btn btn-default btn-flat">Sign out</a>
                </div>
              </li>
            </ul>
          </li>
        </ul>
      </div>
    </nav>
  </header>

  <!-- Left side column. contains the logo and sidebar -->
  <aside class="main-sidebar">
    <!-- sidebar: style can be found in sidebar.less -->
    <section class="sidebar">
      <!-- sidebar menu -->
      <ul class="sidebar-menu" data-widget="tree">
        <li class="header">MAIN NAVIGATION</li>
        <li class="active treeview" id="nav-dashboard">
          <a href="#" onclick="switchTab('dashboard')">
            <i class="fa fa-dashboard text-aqua"></i> <span>Dashboard</span>
          </a>
        </li>
        <li class="treeview" id="nav-settings">
          <a href="#" onclick="switchTab('settings')">
            <i class="fa fa-cogs"></i> <span>Configuration</span>
          </a>
        </li>
      </ul>
    </section>
    <!-- /.sidebar -->
  </aside>

  <!-- Content Wrapper. Contains page content -->
  <div class="content-wrapper">
    <!-- DASHBOARD TAB -->
    <div id="tab-dashboard">
      <section class="content-header">
        <h1>Dashboard <small>Overview</small></h1>
      </section>

      <section class="content">
        <div class="row">
          <div class="col-lg-3 col-xs-6">
            <div class="small-box bg-aqua">
              <div class="inner">
                <h3 id="stat-today">0</h3>
                <p>Today's Bottles</p>
              </div>
              <div class="icon"><i class="fa fa-recycle"></i></div>
            </div>
          </div>
          <div class="col-lg-3 col-xs-6">
            <div class="small-box bg-green">
              <div class="inner">
                <h3 id="stat-total">0</h3>
                <p>Total Bottles (All Time)</p>
              </div>
              <div class="icon"><i class="fa fa-leaf"></i></div>
            </div>
          </div>
          <div class="col-lg-3 col-xs-6">
            <div class="small-box bg-yellow">
              <div class="inner">
                <h3 id="stat-clients">0</h3>
                <p>Active Clients</p>
              </div>
              <div class="icon"><i class="fa fa-users"></i></div>
            </div>
          </div>
          <div class="col-lg-3 col-xs-6">
            <div class="small-box bg-red">
              <div class="inner">
                <h3 id="stat-cpu">0%</h3>
                <p>CPU / <span id="stat-ram">0%</span> RAM</p>
              </div>
              <div class="icon"><i class="fa fa-server"></i></div>
            </div>
          </div>
        </div>
        
        <div class="row">
          <div class="col-md-12">
            <div class="box box-info">
              <div class="box-header with-border">
                <h3 class="box-title">Weekly Analytics</h3>
              </div>
              <div class="box-body">
                <canvas id="bottlesChart" style="height:250px"></canvas>
              </div>
            </div>
          </div>
        </div>
        
        <div class="row">
          <div class="col-xs-12">
            <div class="box box-success">
              <div class="box-header">
                <h3 class="box-title">Active Clients</h3>
                <div class="box-tools">
                  <button type="button" class="btn btn-box-tool" onclick="refreshClients()"><i class="fa fa-refresh"></i></button>
                </div>
              </div>
              <div class="box-body table-responsive no-padding">
                <table class="table table-hover">
                  <thead>
                    <tr>
                      <th>IP / MAC Address</th>
                      <th>Time Remaining</th>
                      <th>Status</th>
                      <th>Actions</th>
                    </tr>
                  </thead>
                  <tbody id="clients-table">
                    <tr><td colspan="4" class="text-center text-muted">Loading clients...</td></tr>
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        </div>
      </section>
    </div>

    <!-- SETTINGS TAB -->
    <div id="tab-settings" style="display: none;">
      <section class="content-header">
        <h1>System Configuration <small>Adjust parameters</small></h1>
      </section>

      <section class="content">
        <div class="row">
          <div class="col-md-6">
            <div class="box box-primary">
              <div class="box-header with-border">
                <h3 class="box-title">Network & Vendo Limits</h3>
              </div>
              <div class="box-body">
                <div class="form-group">
                  <label>Minutes per Bottle (Default 15)</label>
                  <input type="number" id="cfg-dl" class="form-control" value="{{ config.default_dl_kbps }}" placeholder="15">
                </div>
                <div class="form-group">
                  <label>Drop Timeout (Seconds)</label>
                  <input type="number" id="cfg-ul" class="form-control" value="{{ config.default_ul_kbps }}" placeholder="30">
                </div>
                <div class="form-group">
                  <label>Anti-Tethering (TTL Blocking)</label>
                  <select id="cfg-tether" class="form-control">
                    <option value="0" {% if config.anti_tethering == '0' %}selected{% endif %}>Disabled (Allow Hotspot)</option>
                    <option value="1" {% if config.anti_tethering == '1' %}selected{% endif %}>Enabled (Block Hotspot)</option>
                  </select>
                </div>
              </div>
            </div>
          </div>

          <div class="col-md-6">
            <div class="box box-info">
              <div class="box-header with-border">
                <h3 class="box-title"><i class="fa fa-telegram"></i> Telegram Alerts</h3>
              </div>
              <div class="box-body">
                <div class="form-group">
                  <label>Bot Token</label>
                  <input type="password" id="cfg-tg-token" class="form-control" value="{{ config.telegram_bot_token }}">
                </div>
                <div class="form-group">
                  <label>Chat ID</label>
                  <input type="text" id="cfg-tg-chat" class="form-control" value="{{ config.telegram_chat_id }}">
                </div>
              </div>
            </div>
            
            <div class="box box-default">
              <div class="box-header with-border">
                <h3 class="box-title">Custom CSS</h3>
              </div>
              <div class="box-body">
                <textarea id="cfg-css" class="form-control" rows="3">{{ config.custom_css }}</textarea>
              </div>
              <div class="box-footer">
                <button onclick="savePisoFiSettings()" class="btn btn-primary pull-right">Save All</button>
              </div>
            </div>
          </div>
        </div>
      </section>
    </div>
  </div>

</div>

<!-- jQuery 3 -->
<script src="https://code.jquery.com/jquery-3.3.1.min.js"></script>
<!-- Bootstrap 3.3.7 -->
<script src="https://maxcdn.bootstrapcdn.com/bootstrap/3.3.7/js/bootstrap.min.js"></script>
<!-- AdminLTE App -->
<script src="https://cdnjs.cloudflare.com/ajax/libs/admin-lte/2.4.18/js/adminlte.min.js"></script>

<script>
    function switchTab(tabId) {
        document.getElementById('tab-dashboard').style.display = 'none';
        document.getElementById('tab-settings').style.display = 'none';
        document.getElementById('tab-' + tabId).style.display = 'block';
        
        document.getElementById('nav-dashboard').classList.remove('active');
        document.getElementById('nav-settings').classList.remove('active');
        document.getElementById('nav-' + tabId).classList.add('active');
        
        if(tabId === 'dashboard') {
            refreshStats();
            refreshClients();
        }
    }

    function savePisoFiSettings() {
        let css = document.getElementById('cfg-css').value;
        let dl = document.getElementById('cfg-dl').value;
        let ul = document.getElementById('cfg-ul').value;
        let at = document.getElementById('cfg-tether').value;
        let tg_token = document.getElementById('cfg-tg-token').value;
        let tg_chat = document.getElementById('cfg-tg-chat').value;
        
        fetch('/admin/api/settings', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                custom_css: css, default_dl_kbps: dl, default_ul_kbps: ul,
                anti_tethering: at, telegram_bot_token: tg_token, telegram_chat_id: tg_chat
            })
        }).then(res => res.json()).then(data => {
            alert('Settings saved!');
        });
    }

    let bottleChart;
    function refreshStats() {
        fetch('/admin/api/stats').then(res => res.json()).then(data => {
            document.getElementById('stat-today').innerText = data.today_bottles || 0;
            document.getElementById('stat-total').innerText = data.total_bottles || 0;
            document.getElementById('stat-cpu').innerText = (data.cpu || 0) + '%';
            document.getElementById('stat-ram').innerText = (data.ram || 0) + '%';
            document.getElementById('stat-clients').innerText = (data.active_clients || 0);

            if(data.history) {
                let labels = data.history.map(x => x.date);
                let vals = data.history.map(x => x.count);
                if(bottleChart) bottleChart.destroy();
                let ctx = document.getElementById('bottlesChart').getContext('2d');
                bottleChart = new Chart(ctx, {
                    type: 'line',
                    data: {
                        labels: labels,
                        datasets: [{
                            label: 'Bottles',
                            data: vals,
                            borderColor: '#3c8dbc',
                            backgroundColor: 'rgba(60, 141, 188, 0.2)',
                            fill: true
                        }]
                    },
                    options: { responsive: true, maintainAspectRatio: false }
                });
            }
        });
    }

    function refreshClients() {
        fetch('/admin/api/clients').then(res => res.json()).then(data => {
            let tbody = document.getElementById('clients-table');
            tbody.innerHTML = '';
            if(data.length === 0) {
                tbody.innerHTML = '<tr><td colspan="4" class="text-center text-muted">No active sessions.</td></tr>';
                return;
            }
            data.forEach(client => {
                let statusBadge = client.paused ? '<span class="label label-warning">Paused</span>' : '<span class="label label-success">Active</span>';
                let actionBtn = client.paused 
                    ? `<button class="btn btn-xs btn-success" style="margin:2px;" onclick="modifyClient('${client.mac}', 'resume')">Resume</button>`
                    : `<button class="btn btn-xs btn-warning" style="margin:2px;" onclick="modifyClient('${client.mac}', 'pause')">Pause</button>`;
                
                let tr = document.createElement('tr');
                tr.innerHTML = `
                    <td><strong>${client.ip}</strong><br><small class="text-muted">${client.mac}</small></td>
                    <td>${client.time_remaining}s</td>
                    <td>${statusBadge}</td>
                    <td>
                        <button class="btn btn-xs btn-info" style="margin:2px;" onclick="modifyClient('${client.mac}', 'add15')">+15m</button>
                        ${actionBtn}
                        <button class="btn btn-xs btn-danger" style="margin:2px;" onclick="modifyClient('${client.mac}', 'disconnect')">Kick</button>
                    </td>
                `;
                tbody.appendChild(tr);
            });
        });
    }

    function modifyClient(mac, action) {
        alert("Client modification requested: " + action + " for " + mac + " (Mock Execution)");
        refreshClients();
    }

    setInterval(() => {
        if(document.getElementById('tab-dashboard').style.display !== 'none') {
            refreshStats();
        }
    }, 10000);
    
    refreshStats();
    refreshClients();
</script>
</body>
</html>
"""

@app.route("/")
def index():
    client_ip = request.remote_addr
    if client_ip not in active_clients:
        active_clients[client_ip] = {"pending_bottles": 0, "remaining_seconds": 0, "is_paused": False}
    rate = get_config("minutes_per_bottle")
    return render_template_string(USER_HTML, client_ip=client_ip, rate=rate)

@app.route("/api/status")
def status():
    client_ip = request.remote_addr
    return jsonify(active_clients.get(client_ip, {"pending_bottles": 0, "remaining_seconds": 0, "is_paused": False}))

@app.route("/api/connect", methods=["POST"])
def connect():
    client_ip = request.remote_addr
    session_data = active_clients.get(client_ip)
    if session_data and session_data["pending_bottles"] > 0:
        rate = int(get_config("minutes_per_bottle"))
        added_minutes = session_data["pending_bottles"] * rate
        session_data["remaining_seconds"] += added_minutes * 60
        session_data["is_paused"] = False
        record_bottle_drop(session_data["pending_bottles"])
        session_data["pending_bottles"] = 0
        return jsonify({"success": True})
    return jsonify({"success": False})

@app.route("/api/open_gate", methods=["POST"])
def open_gate():
    timeout = int(get_config("drop_timeout") or 30)
    try:
        if ser:
            ser.write(f'{{"cmd":"OPEN_GATE", "timeout":{timeout}}}\n'.encode())
    except Exception as e:
        pass
    return jsonify({"success": True, "timeout": timeout})

@app.route("/api/pause", methods=["POST"])
def pause():
    client_ip = request.remote_addr
    session_data = active_clients.get(client_ip)
    if session_data and session_data["remaining_seconds"] > 0 and not session_data["is_paused"]:
        session_data["is_paused"] = True
        return jsonify({"success": True})
    return jsonify({"success": False})

@app.route("/api/resume", methods=["POST"])
def resume():
    client_ip = request.remote_addr
    session_data = active_clients.get(client_ip)
    if session_data and session_data["remaining_seconds"] > 0 and session_data["is_paused"]:
        session_data["is_paused"] = False
        return jsonify({"success": True})
    return jsonify({"success": False})

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    error = None
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            c.execute("SELECT password_hash FROM admins WHERE username=?", (username,))
            row = c.fetchone()
            if row and check_password_hash(row[0], password):
                session['admin_logged_in'] = True
                return redirect("/admin")
            else:
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
    rate = get_config("minutes_per_bottle")
    drop_timeout = get_config("drop_timeout") or "30"
    today = datetime.now().strftime('%Y-%m-%d')
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("SELECT total_bottles FROM stats WHERE date=?", (today,))
        row = c.fetchone()
        today_bottles = row[0] if row else 0
        c.execute("SELECT SUM(total_bottles) FROM stats")
        row = c.fetchone()
        total_bottles = row[0] if row and row[0] else 0
    active_count = sum(1 for c in active_clients.values() if c["remaining_seconds"] > 0)
    return render_template_string(ADMIN_HTML, rate=rate, drop_timeout=drop_timeout, today_bottles=today_bottles, total_bottles=total_bottles, active_count=active_count, clients=active_clients)

@app.route("/admin/config", methods=["POST"])
def update_config():
    if not session.get('admin_logged_in'): return redirect("/admin/login")
    new_rate = request.form.get("rate")
    new_timeout = request.form.get("drop_timeout")
    if new_rate and new_rate.isdigit():
        set_config("minutes_per_bottle", new_rate)
    if new_timeout and new_timeout.isdigit():
        set_config("drop_timeout", new_timeout)
    return redirect("/admin")

# Mock endpoint to trigger a bottle drop
@app.route("/mock_drop")
def mock_drop():
    client_ip = request.remote_addr
    if client_ip in active_clients:
        active_clients[client_ip]["pending_bottles"] += 1
    return jsonify({"success": True})

def serial_daemon():
    global ser
    try:
        ser = serial.Serial('/dev/ttyS1', 115200, timeout=1)
        print("Connected to ESP32 on /dev/ttyS1")
        while True:
            if ser.in_waiting > 0:
                line = ser.readline().decode().strip()
                if line:
                    try:
                        data = json.loads(line)
                        if data.get("event") == "BOTTLE_SAVED":
                            with app.app_context():
                                # In production, match active client by MAC. Mocking here.
                                client_ip = list(active_clients.keys())[0] if active_clients else None
                                if client_ip:
                                    active_clients[client_ip]["pending_bottles"] += 1
                    except Exception as e:
                        print("Serial decode error:", e)
            time.sleep(0.1)
    except Exception as e:
        print("Serial not available, mocking only.")

def time_daemon():
    while True:
        for ip, session_data in list(active_clients.items()):
            if session_data["remaining_seconds"] > 0 and not session_data["is_paused"]:
                session_data["remaining_seconds"] -= 1
        time.sleep(1)

if __name__ == "__main__":
    threading.Thread(target=serial_daemon, daemon=True).start()
    threading.Thread(target=time_daemon, daemon=True).start()
    app.run(host="0.0.0.0", port=80)
