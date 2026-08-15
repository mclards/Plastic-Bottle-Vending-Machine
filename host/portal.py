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
        default_hash = generate_password_hash("admin123")
        c.execute("INSERT OR IGNORE INTO admins (username, password_hash) VALUES ('admin', ?)", (default_hash,))
        conn.commit()

init_db()

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
    <meta charset="UTF-8"><title>Eco-Fi Vendo</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css" rel="stylesheet">
    <style>
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #f3f4f6; margin: 0; color: #374151; }
        .header { background: white; padding: 15px 20px; display: flex; align-items: center; justify-content: space-between; border-bottom: 1px solid #e5e7eb; box-shadow: 0 1px 3px rgba(0,0,0,0.05); }
        .header-left { display: flex; align-items: center; color: #10B981; font-weight: 700; font-size: 1.1rem; }
        .header-left i { margin-right: 8px; font-size: 1.2rem; }
        .banner { width: 100%; max-width: 600px; margin: 0 auto; height: auto; display: block; border-bottom: 3px solid #10B981; }
        .content { background: white; padding: 20px; text-align: center; margin-top: -5px; }
        .status-connected { color: #10B981; font-size: 36px; margin: 10px 0; display: flex; align-items: center; justify-content: center; gap: 10px; font-weight: 700; }
        .status-disconnected { color: #EF4444; font-size: 36px; margin: 10px 0; display: flex; align-items: center; justify-content: center; gap: 10px; font-weight: 700; }
        .ip-mac { color: #3b82f6; font-size: 14px; margin-bottom: 15px; font-weight: 600; }
        .unclaimed { color: #3b82f6; font-size: 18px; font-weight: 700; margin-bottom: 15px; padding: 10px; background: #eff6ff; border-radius: 8px; display: inline-block; }
        .timer-label { font-size: 12px; color: #6b7280; font-weight: 700; letter-spacing: 1px; margin-bottom: 5px; text-transform: uppercase; }
        .time-display { color: #2563eb; font-size: 36px; font-weight: 800; margin-bottom: 30px; text-shadow: 1px 1px 2px rgba(0,0,0,0.1); }
        .timer span { font-size: 14px; color: #3b82f6; font-weight: 600; margin-left: 2px; }
        .btn { display: block; width: 100%; max-width: 350px; margin: 10px auto; padding: 14px; text-align: center; font-size: 16px; font-weight: bold; border-radius: 8px; border: none; cursor: pointer; color: white; transition: all 0.2s; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }
        .btn:active { transform: translateY(2px); box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        .btn-green { background: linear-gradient(135deg, #10B981 0%, #059669 100%); }
        .btn-red { background: linear-gradient(135deg, #ef4444 0%, #b91c1c 100%); }
        .btn-blue { background: linear-gradient(135deg, #3b82f6 0%, #2563eb 100%); }
        .btn-orange { background: linear-gradient(135deg, #f59e0b 0%, #d97706 100%); color: white; }
        .footer-note { margin-top: 30px; font-size: 12px; color: #9ca3af; }
    </style>
</head>
<body>
    <img src="/static/banner-main.jpg" class="banner" alt="Eco-Fi Banner" style="margin-top: 0;">
    
    <div class="content">
        <div class="status-connected" id="wifi-status" style="display: none;"><i class="fa-solid fa-wifi"></i> Connected</div>
        <div class="status-disconnected" id="wifi-status-dc" style="display: none;"><i class="fa-solid fa-wifi-slash"></i> Disconnected</div>
        
        <div class="ip-mac">IP: {{ client_ip }} | Rate: {{ rate }} min/bottle</div>
        
        <div class="unclaimed">UNCLAIMED BOTTLES: <span id="unclaimed">0</span></div>
        
        <div class="timer-label">REMAINING TIME:</div>
        <div class="time-display timer">
            <span id="td" style="font-size:36px;color:#2563eb;">0</span><span>D.</span>
            <span id="th" style="font-size:36px;color:#2563eb;">0</span><span>HR.</span>
            <span id="tm" style="font-size:36px;color:#2563eb;">0</span><span>MIN.</span>
            <span id="ts" style="font-size:36px;color:#2563eb;">0</span><span>SEC.</span>
        </div>
        
        <button class="btn btn-blue" id="btn-open-gate" onclick="openGate()">Insert Plastic Bottle</button>
        <div id="drop-progress-container" style="display: none; width: 100%; max-width: 350px; background: #e5e7eb; border-radius: 10px; margin: 10px auto; height: 20px; overflow: hidden;">
            <div id="drop-progress-bar" style="width: 100%; height: 100%; background: #3b82f6; transition: width 1s linear;"></div>
        </div>
        <button class="btn btn-green" id="btn-insert" onclick="insertBottles()" style="display: none;">Connect / Claim Bottles</button>
        <button class="btn btn-red" id="btn-pause" onclick="togglePause()" style="display: none;">Pause Time</button>
    </div>

    <script>
        const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
        function playTone(freq, type, duration) {
            if (audioCtx.state === 'suspended') audioCtx.resume();
            let osc = audioCtx.createOscillator();
            let gain = audioCtx.createGain();
            osc.type = type;
            osc.frequency.setValueAtTime(freq, audioCtx.currentTime);
            gain.gain.setValueAtTime(0.1, audioCtx.currentTime);
            gain.gain.exponentialRampToValueAtTime(0.001, audioCtx.currentTime + duration);
            osc.connect(gain);
            gain.connect(audioCtx.destination);
            osc.start();
            osc.stop(audioCtx.currentTime + duration);
        }
        function playTick() { playTone(800, 'sine', 0.1); }
        function playTimeout() { playTone(200, 'sawtooth', 0.5); }
        function playReject() { playTone(150, 'square', 0.2); setTimeout(() => playTone(150, 'square', 0.4), 250); }
        function playSuccess() { playTone(523.25, 'sine', 0.2); setTimeout(() => playTone(659.25, 'sine', 0.4), 150); }

        let dropInterval = null;
        let lastEventTs = 0;
        window.currentDropTimeout = 30;
        window.currentTimeLeft = 0;

        function fetchStatus() {
            fetch('/api/status')
                .then(r => r.json())
                .then(data => {
                    document.getElementById('unclaimed').innerText = data.pending_bottles;
                    
                    let total = data.remaining_seconds;
                    let d = Math.floor(total / (3600*24));
                    let h = Math.floor(total % (3600*24) / 3600);
                    let m = Math.floor(total % 3600 / 60);
                    let s = Math.floor(total % 60);
                    
                    document.getElementById('td').innerText = d;
                    document.getElementById('th').innerText = h;
                    document.getElementById('tm').innerText = m;
                    document.getElementById('ts').innerText = s;

                    if(data.event_timestamp > lastEventTs) {
                        if(lastEventTs !== 0) {
                            if(data.last_event === "SUCCESS") {
                                playSuccess();
                                if(dropInterval) {
                                    window.currentTimeLeft = window.currentDropTimeout;
                                }
                            } else if(data.last_event === "REJECT") {
                                playReject();
                            }
                        }
                        lastEventTs = data.event_timestamp;
                    }

                    let pauseBtn = document.getElementById('btn-pause');
                    let claimBtn = document.getElementById('btn-insert');
                    let openGateBtn = document.getElementById('btn-open-gate');
                    let connectedStatus = document.getElementById('wifi-status');
                    let dcStatus = document.getElementById('wifi-status-dc');

                    if(total > 0 && !data.is_paused) {
                        connectedStatus.style.display = "flex";
                        dcStatus.style.display = "none";
                    } else {
                        connectedStatus.style.display = "none";
                        dcStatus.style.display = "flex";
                    }

                    if(data.is_paused) {
                        pauseBtn.innerText = "Resume Time";
                        pauseBtn.className = "btn btn-orange";
                        pauseBtn.style.display = "block";
                    } else if (total > 0) {
                        pauseBtn.innerText = "Pause Time";
                        pauseBtn.className = "btn btn-red";
                        pauseBtn.style.display = "block";
                    } else {
                        pauseBtn.style.display = "none";
                    }
                    
                    if(data.pending_bottles > 0) {
                        claimBtn.style.display = "block";
                    } else {
                        claimBtn.style.display = "none";
                    }
                    
                    if(!dropInterval) {
                        openGateBtn.style.display = "block";
                    } else {
                        openGateBtn.style.display = "none";
                    }
                });
        }
        
        function openGate() {
            fetch('/api/open_gate', {method: 'POST'})
                .then(r => r.json())
                .then(data => {
                    document.getElementById('btn-open-gate').style.display = 'none';
                    let container = document.getElementById('drop-progress-container');
                    let bar = document.getElementById('drop-progress-bar');
                    container.style.display = 'block';
                    
                    window.currentDropTimeout = data.timeout;
                    window.currentTimeLeft = data.timeout;
                    bar.style.width = '100%';
                    
                    if(dropInterval) clearInterval(dropInterval);
                    dropInterval = setInterval(() => {
                        window.currentTimeLeft--;
                        playTick();
                        bar.style.width = (window.currentTimeLeft / window.currentDropTimeout * 100) + '%';
                        if(window.currentTimeLeft <= 0) {
                            playTimeout();
                            clearInterval(dropInterval);
                            dropInterval = null;
                            container.style.display = 'none';
                            fetchStatus();
                        }
                    }, 1000);
                });
        }

        function insertBottles() {
            fetch('/api/connect', {method: 'POST'}).then(res => res.json()).then(data => {
                if(data.success) {
                    if(dropInterval) {
                        clearInterval(dropInterval);
                        dropInterval = null;
                        document.getElementById('drop-progress-container').style.display = 'none';
                    }
                    fetchStatus();
                }
            });
        }

        function togglePause() {
            let btn = document.getElementById('btn-pause');
            let endpoint = btn.innerText.includes("Pause") ? '/api/pause' : '/api/resume';
            fetch(endpoint, {method: 'POST'}).then(res => res.json()).then(data => {
                if(data.success) fetchStatus();
            });
        }
        
        setInterval(fetchStatus, 1500);
        fetchStatus();
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
<html lang="en">
<head>
    <meta charset="UTF-8"><title>Eco-Fi Admin Dashboard</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #f1f5f9; margin: 0; display: flex; color: #334155; }
        .sidebar { width: 260px; background: #0f172a; color: white; height: 100vh; position: fixed; display: flex; flex-direction: column; }
        .sidebar h2 { text-align: center; color: #10B981; margin: 30px 0; font-size: 24px; letter-spacing: 1px; }
        .sidebar a { display: block; color: #94a3b8; padding: 16px 25px; text-decoration: none; font-size: 16px; font-weight: 500; transition: all 0.2s; border-left: 4px solid transparent; }
        .sidebar a:hover, .sidebar a.active-tab { background: #1e293b; color: white; border-left-color: #10B981; }
        .main { margin-left: 260px; padding: 40px; flex: 1; }
        .main h1 { font-size: 28px; margin-top: 0; margin-bottom: 30px; color: #0f172a; }
        
        .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 20px; margin-bottom: 30px; }
        .stat-card { background: white; padding: 25px; border-radius: 12px; box-shadow: 0 4px 6px rgba(0,0,0,0.05); display: flex; flex-direction: column; }
        .stat-card h3 { margin: 0; font-size: 15px; color: #64748b; text-transform: uppercase; letter-spacing: 0.5px; }
        .stat-card .value { font-size: 42px; font-weight: 800; margin: 10px 0 0 0; color: #0f172a; }
        
        .card { background: white; padding: 30px; border-radius: 12px; box-shadow: 0 4px 6px rgba(0,0,0,0.05); margin-bottom: 30px; }
        .card h2 { margin-top: 0; font-size: 20px; color: #0f172a; border-bottom: 1px solid #e2e8f0; padding-bottom: 15px; margin-bottom: 20px; }
        
        input[type=number] { padding: 12px; border: 1px solid #cbd5e1; border-radius: 6px; width: 120px; font-size: 16px; outline: none; }
        input[type=number]:focus { border-color: #10B981; }
        button { background: #10B981; color: white; border: none; padding: 12px 20px; border-radius: 6px; cursor: pointer; font-size: 15px; font-weight: 600; transition: background 0.2s; }
        button:hover { background: #059669; }
        
        table { width: 100%; border-collapse: collapse; }
        th { text-align: left; padding: 15px; border-bottom: 2px solid #e2e8f0; color: #64748b; font-weight: 600; }
        td { padding: 15px; border-bottom: 1px solid #e2e8f0; color: #334155; }
        tr:hover td { background: #f8fafc; }
        .badge { padding: 6px 12px; border-radius: 20px; font-size: 12px; font-weight: 700; text-transform: uppercase; }
        .badge-active { background: #d1fae5; color: #065f46; }
        .badge-paused { background: #fef3c7; color: #92400e; }
        
        /* Mobile Navbar Styles */
        .mobile-header { display: none; background: #0f172a; color: white; padding: 15px 20px; align-items: center; justify-content: space-between; }
        .mobile-header h2 { margin: 0; font-size: 20px; color: #10B981; }
        .hamburger { background: none; border: none; color: white; font-size: 24px; cursor: pointer; padding: 0; }
        .overlay { display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.5); z-index: 99; }
        .overlay.active { display: block; }

        @media (max-width: 768px) {
            body { flex-direction: column; }
            .mobile-header { display: flex; }
            .sidebar { position: fixed; top: 0; left: 0; height: 100vh; width: 260px; z-index: 100; transform: translateX(-100%); transition: transform 0.3s ease; }
            .sidebar.active { transform: translateX(0); }
            .sidebar h2 { display: none; }
            .main { margin-left: 0; padding: 20px; }
            .stats-grid { grid-template-columns: 1fr; }
            table { display: block; overflow-x: auto; white-space: nowrap; }
            input[type=number] { width: 100%; box-sizing: border-box; }
        }
    </style>
</head>
<body>
    <div class="mobile-header">
        <div style="display: flex; align-items: center;">
            <img src="/static/logo.jpg" style="height: 24px; margin-right: 10px; border-radius: 4px;" alt="Logo">
            <h2>ECO-FI ADMIN</h2>
        </div>
        <button class="hamburger" onclick="toggleNav()">&equiv;</button>
    </div>
    <div class="overlay" id="overlay" onclick="toggleNav()"></div>
    <div class="sidebar" id="sidebar">
        <div style="text-align: center; margin-top: 30px;">
            <img src="/static/logo.jpg" style="max-width: 120px; border-radius: 8px;" alt="Logo">
            <h2 style="margin-top: 10px;">ECO-FI ADMIN</h2>
        </div>
        <a href="#" onclick="showTab('dashboard'); return false;" id="nav-dashboard" class="active-tab">Dashboard</a>
        <a href="#" onclick="showTab('settings'); return false;" id="nav-settings">Settings</a>
        <a href="/">View Portal</a>
        <a href="/admin/logout">Logout</a>
    </div>
    
    <div class="main" id="dashboard-view">
        <h1>Dashboard Overview</h1>
        
        <div class="stats-grid">
            <div class="stat-card">
                <h3>Today's Bottles</h3>
                <div class="value">{{ today_bottles }}</div>
            </div>
            <div class="stat-card">
                <h3>Total Bottles (All Time)</h3>
                <div class="value">{{ total_bottles }}</div>
            </div>
            <div class="stat-card" style="border-top: 4px solid #f59e0b;">
                <h3>Active Clients</h3>
                <div class="value">{{ active_count }}</div>
            </div>
        </div>
        
        <div class="card">
            <h2>Active Clients & Sessions</h2>
            <table>
                <thead>
                    <tr>
                        <th>IP Address</th>
                        <th>Time Remaining</th>
                        <th>Status</th>
                    </tr>
                </thead>
                <tbody>
                    {% for ip, client in clients.items() %}
                    {% if client.remaining_seconds > 0 %}
                    <tr>
                        <td>{{ ip }}</td>
                        <td>{{ client.remaining_seconds // 60 }} min {{ client.remaining_seconds % 60 }} sec</td>
                        <td>
                            {% if client.is_paused %}
                            <span class="badge badge-paused">Paused</span>
                            {% else %}
                            <span class="badge badge-active">Active</span>
                            {% endif %}
                        </td>
                    </tr>
                    {% endif %}
                    {% endfor %}
                    {% if active_count == 0 %}
                    <tr><td colspan="3" style="text-align:center; color:#94a3b8;">No active sessions right now.</td></tr>
                    {% endif %}
                </tbody>
            </table>
        </div>
    </div>
    
    <div class="main" id="settings-view" style="display: none;">
        <h1>System Settings</h1>
        
        <div class="card">
            <h2>System Configuration</h2>
            <form action="/admin/config" method="POST" class="mt-3">
                <label style="display:block; margin-bottom:5px; font-weight:600; color:#475569;">Minutes per Bottle:</label>
                <input type="number" name="rate" value="{{ rate }}" required style="margin-bottom:15px;">
                <label style="display:block; margin-bottom:5px; font-weight:600; color:#475569;">Drop Timeout (seconds):</label>
                <input type="number" name="drop_timeout" value="{{ drop_timeout }}" required style="margin-bottom:15px;">
                <br>
                <button type="submit">Save Config</button>
            </form>
        </div>
    </div>
    
    <script>
        function toggleNav() {
            document.getElementById('sidebar').classList.toggle('active');
            document.getElementById('overlay').classList.toggle('active');
        }
        
        function showTab(tabName) {
            // Update nav items
            document.getElementById('nav-dashboard').classList.remove('active-tab');
            document.getElementById('nav-settings').classList.remove('active-tab');
            document.getElementById('nav-' + tabName).classList.add('active-tab');
            
            // Update views
            document.getElementById('dashboard-view').style.display = 'none';
            document.getElementById('settings-view').style.display = 'none';
            document.getElementById(tabName + '-view').style.display = 'block';
            
            // Close mobile menu if open
            if(document.getElementById('sidebar').classList.contains('active')) {
                toggleNav();
            }
        }
        
        // Show correct tab after config save (if URL has #settings)
        if(window.location.hash === '#settings') {
            showTab('settings');
        }
    </script>
</body>
</html>
"""

@app.route("/")
def index():
    client_ip = request.remote_addr
    if client_ip not in active_clients:
        active_clients[client_ip] = {"pending_bottles": 0, "remaining_seconds": 0, "is_paused": False, "last_event": "", "event_timestamp": 0.0}
    rate = get_config("minutes_per_bottle")
    return render_template_string(USER_HTML, client_ip=client_ip, rate=rate)

@app.route("/api/status")
def status():
    client_ip = request.remote_addr
    return jsonify(active_clients.get(client_ip, {"pending_bottles": 0, "remaining_seconds": 0, "is_paused": False, "last_event": "", "event_timestamp": 0.0}))

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
    return redirect("/admin#settings")

# Mock endpoint to trigger a bottle drop
@app.route("/mock_drop")
def mock_drop():
    client_ip = request.remote_addr
    if client_ip in active_clients:
        active_clients[client_ip]["pending_bottles"] += 1
        active_clients[client_ip]["last_event"] = "SUCCESS"
        active_clients[client_ip]["event_timestamp"] = time.time()
    return jsonify({"success": True})

@app.route("/mock_reject")
def mock_reject():
    client_ip = request.remote_addr
    if client_ip in active_clients:
        active_clients[client_ip]["last_event"] = "REJECT"
        active_clients[client_ip]["event_timestamp"] = time.time()
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
                        if data.get("event") == "BOTTLE_SAVED" or data.get("event") == "CREDIT_ADD":
                            with app.app_context():
                                client_ip = list(active_clients.keys())[0] if active_clients else None
                                if client_ip:
                                    active_clients[client_ip]["pending_bottles"] += 1
                                    active_clients[client_ip]["last_event"] = "SUCCESS"
                                    active_clients[client_ip]["event_timestamp"] = time.time()
                        elif data.get("event") == "REJECTED":
                            with app.app_context():
                                client_ip = list(active_clients.keys())[0] if active_clients else None
                                if client_ip:
                                    active_clients[client_ip]["last_event"] = "REJECT"
                                    active_clients[client_ip]["event_timestamp"] = time.time()
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
