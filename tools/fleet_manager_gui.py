#!/usr/bin/env python3
"""
VMC ECO-VENDO Fleet Command & Management Console (Windows Utility)
Authoritative developer utility to monitor, update, troubleshoot,
flash, and remotely manage sold Eco-Vendo machines over Tailscale / LAN.
"""

import os
import sys
import json
import time
import datetime
import threading
import subprocess
import webbrowser
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

# Add project root to sys.path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'host'))

try:
    import paramiko
except ImportError:
    paramiko = None

try:
    from license_manager import compute_activation_pin, normalize_hwid
except ImportError:
    compute_activation_pin = None
    normalize_hwid = None

FLEET_FILE = ROOT / 'tools' / 'fleet_machines.json'

DEFAULT_MACHINES = [
    {
        "id": "vendo-live-lab",
        "name": "Vendo #01 (Live OPi - Tailscale)",
        "host": "100.127.191.71",
        "port": 22,
        "username": "root",
        "password": "",
        "location": "Developer Lab (Tailscale)",
        "notes": "Primary development & test bench Orange Pi One"
    },
    {
        "id": "vendo-local-lan",
        "name": "Vendo #01 (Local Subnet LAN)",
        "host": "10.0.0.1",
        "port": 22,
        "username": "root",
        "password": "",
        "location": "Local AP Gateway (10.0.0.1)",
        "notes": "Direct connection via customer LAN interface"
    }
]


def get_default_ssh_password():
    """Extract default root password from build_ecofi_img.sh or fallback."""
    try:
        sh_path = ROOT / 'build_ecofi_img.sh'
        if sh_path.exists():
            import re
            m = re.search(r'passwords to "([^"]+)"', sh_path.read_text(encoding='utf-8', errors='ignore'))
            if m:
                return m.group(1)
    except Exception:
        pass
    return "admin1234"


class FleetManagerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("VMC ECO-VENDO — Fleet Command & Manager v1.2")
        self.root.geometry("1280x820")
        self.root.minsize(1050, 680)
        self.root.configure(bg="#080E1E")

        self.machines = []
        self.filtered_machines = []
        self.selected_machine = None
        self.ping_cache = {}  # {host: {"online": bool, "ms": int, "last_check": float}}
        self.log_stream_thread = None
        self.log_stream_running = False
        self.autoscroll_enabled = True

        self.load_fleet()
        self.setup_ui()
        self.start_background_ping_daemon()
        self.start_clock_timer()

    # -------------------------------------------------------------
    # Data Persistence
    # -------------------------------------------------------------
    def load_fleet(self):
        if FLEET_FILE.exists():
            try:
                with open(FLEET_FILE, 'r', encoding='utf-8') as f:
                    self.machines = json.load(f)
            except Exception as e:
                print("Error loading fleet file, using defaults:", e)
                self.machines = list(DEFAULT_MACHINES)
        else:
            self.machines = list(DEFAULT_MACHINES)
            self.save_fleet()
        self.filtered_machines = list(self.machines)

    def save_fleet(self):
        try:
            with open(FLEET_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.machines, f, indent=2)
        except Exception as e:
            messagebox.showerror("Save Error", "Could not save fleet roster: " + str(e))

    # -------------------------------------------------------------
    # SSH Client Builder
    # -------------------------------------------------------------
    def get_ssh_client(self, machine, timeout=8):
        if not paramiko:
            raise RuntimeError("paramiko is not installed in your Python environment.")
        host = machine.get('host', '127.0.0.1').strip()
        user = machine.get('username', 'root').strip()
        pw = machine.get('password', '').strip() or get_default_ssh_password()
        port = int(machine.get('port', 22))

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(host, port=port, username=user, password=pw,
                       timeout=timeout, auth_timeout=timeout,
                       look_for_keys=False, allow_agent=False)
        return client

    # -------------------------------------------------------------
    # UI Setup
    # -------------------------------------------------------------
    def setup_ui(self):
        # Configure TTK Styles
        style = ttk.Style()
        style.theme_use('clam')
        style.configure(".", background="#080E1E", foreground="#F8FAFC", font=("Segoe UI", 9))
        
        # Treeview styling
        style.configure("Treeview",
                        background="#0F172A",
                        foreground="#F1F5F9",
                        fieldbackground="#0F172A",
                        rowheight=34,
                        font=("Segoe UI", 9),
                        borderwidth=0)
        style.map("Treeview",
                  background=[('selected', '#1E3A8A')],
                  foreground=[('selected', '#FFFFFF')])
        style.configure("Treeview.Heading",
                        background="#1E293B",
                        foreground="#38BDF8",
                        font=("Segoe UI", 9, "bold"),
                        relief="flat",
                        padding=6)
        style.map("Treeview.Heading", background=[('active', '#334155')])

        # Scrollbar styling
        style.configure("Vertical.TScrollbar", background="#1E293B", troughcolor="#0F172A", borderwidth=0, arrowsize=12)

        # ---------------------------------------------------------
        # 1. TOP HEADER & TELEMETRY BAR
        # ---------------------------------------------------------
        header = tk.Frame(self.root, bg="#0F172A", height=70, padx=20, pady=10, relief="flat")
        header.pack(fill="x", side="top")

        # Left branding
        title_box = tk.Frame(header, bg="#0F172A")
        title_box.pack(side="left", fill="y")

        brand_row = tk.Frame(title_box, bg="#0F172A")
        brand_row.pack(anchor="w")

        tk.Label(brand_row, text="⚡", font=("Segoe UI Emoji", 14), fg="#10B981", bg="#0F172A").pack(side="left", padx=(0, 6))
        tk.Label(brand_row, text="ECO-VENDO", font=("Segoe UI", 13, "bold"), fg="#10B981", bg="#0F172A").pack(side="left")
        tk.Label(brand_row, text="FLEET COMMAND", font=("Segoe UI", 13, "bold"), fg="#38BDF8", bg="#0F172A").pack(side="left", padx=(6, 8))

        badge_ver = tk.Label(brand_row, text=" v1.2 PRO ", font=("Segoe UI", 8, "bold"), fg="#93C5FD", bg="#1E3A8A", padx=4, pady=1)
        badge_ver.pack(side="left")

        tk.Label(title_box, text="Remote OTA Updates • Hardware Flashing • Direct Diagnostics • Instant Dev Access",
                 font=("Segoe UI", 8), fg="#64748B", bg="#0F172A").pack(anchor="w", pady=(2, 0))

        # Center Fleet Telemetry Counters
        self.stats_box = tk.Frame(header, bg="#0F172A")
        self.stats_box.pack(side="left", padx=40)

        self.lbl_stats_total = tk.Label(self.stats_box, text="TOTAL: 0", font=("Segoe UI", 8, "bold"), fg="#94A3B8", bg="#1E293B", padx=8, pady=3)
        self.lbl_stats_total.pack(side="left", padx=3)
        self.lbl_stats_online = tk.Label(self.stats_box, text="ONLINE: 0", font=("Segoe UI", 8, "bold"), fg="#34D399", bg="#064E3B", padx=8, pady=3)
        self.lbl_stats_online.pack(side="left", padx=3)
        self.lbl_stats_offline = tk.Label(self.stats_box, text="OFFLINE: 0", font=("Segoe UI", 8, "bold"), fg="#F87171", bg="#4C0519", padx=8, pady=3)
        self.lbl_stats_offline.pack(side="left", padx=3)

        # Dynamic Dev Password Widget (Top Right)
        self.dev_cred_box = tk.Frame(header, bg="#1E293B", padx=12, pady=6, relief="flat", highlightbackground="#334155", highlightthickness=1)
        self.dev_cred_box.pack(side="right")

        cred_top_row = tk.Frame(self.dev_cred_box, bg="#1E293B")
        cred_top_row.pack(fill="x")
        tk.Label(cred_top_row, text="MASTER REMOTE BYPASS", font=("Segoe UI", 7, "bold"), fg="#38BDF8", bg="#1E293B").pack(side="left")
        self.lbl_sec_badge = tk.Label(cred_top_row, text="--s", font=("Segoe UI", 7, "bold"), fg="#FCD34D", bg="#1E293B")
        self.lbl_sec_badge.pack(side="right")

        cred_val_row = tk.Frame(self.dev_cred_box, bg="#1E293B")
        cred_val_row.pack(fill="x", pady=(2, 4))
        tk.Label(cred_val_row, text="User: ", font=("Segoe UI", 8), fg="#94A3B8", bg="#1E293B").pack(side="left")
        tk.Label(cred_val_row, text="devclard", font=("Consolas", 9, "bold"), fg="#38BDF8", bg="#1E293B").pack(side="left", padx=(0, 8))
        tk.Label(cred_val_row, text="Pass: ", font=("Segoe UI", 8), fg="#94A3B8", bg="#1E293B").pack(side="left")
        self.lbl_dyn_pass = tk.Label(cred_val_row, text="dev--", font=("Consolas", 10, "bold"), fg="#FCD34D", bg="#1E293B")
        self.lbl_dyn_pass.pack(side="left")

        cred_btn_row = tk.Frame(self.dev_cred_box, bg="#1E293B")
        cred_btn_row.pack(fill="x")
        self.btn_copy_pass = tk.Button(cred_btn_row, text="📋 Copy Pass", bg="#334155", fg="#FFFFFF",
                                       font=("Segoe UI", 7, "bold"), relief="flat", padx=6, pady=1, cursor="hand2",
                                       command=self.copy_current_password)
        self.btn_copy_pass.pack(side="left", padx=(0, 4))

        self.btn_copy_link = tk.Button(cred_btn_row, text="🔗 Copy Magic Link", bg="#0284C7", fg="#FFFFFF",
                                       font=("Segoe UI", 7, "bold"), relief="flat", padx=6, pady=1, cursor="hand2",
                                       command=self.copy_magic_auth_link)
        self.btn_copy_link.pack(side="left")

        # ---------------------------------------------------------
        # 2. MAIN WORKSPACE PANES (EXPANDS FULLY TO EDGES)
        # ---------------------------------------------------------
        workspace = tk.Frame(self.root, bg="#080E1E", padx=12, pady=10)
        workspace.pack(fill="both", expand=True)

        workspace.columnconfigure(0, weight=0, minsize=380)  # Left sidebar
        workspace.columnconfigure(1, weight=1)               # Right main panel
        workspace.rowconfigure(0, weight=1)

        # ---------------------------------------------------------
        # LEFT SIDEBAR: FLEET ROSTER & SEARCH
        # ---------------------------------------------------------
        left_card = tk.Frame(workspace, bg="#0F172A", relief="flat", padx=12, pady=12,
                             highlightbackground="#1E293B", highlightthickness=1)
        left_card.grid(row=0, column=0, sticky="nsew", padx=(0, 10))

        # Title & Action Buttons Row
        roster_header = tk.Frame(left_card, bg="#0F172A")
        roster_header.pack(fill="x", pady=(0, 8))

        tk.Label(roster_header, text="FLEET ROSTER", font=("Segoe UI", 10, "bold"), fg="#E2E8F0", bg="#0F172A").pack(side="left")

        roster_btns = tk.Frame(roster_header, bg="#0F172A")
        roster_btns.pack(side="right")

        self._create_btn(roster_btns, "+ Add", "#059669", "#10B981", self.add_machine_dialog, padx=6, pady=2, font=("Segoe UI", 8, "bold")).pack(side="left", padx=2)
        self._create_btn(roster_btns, "✏️ Edit", "#334155", "#475569", self.edit_machine_dialog, padx=6, pady=2, font=("Segoe UI", 8, "bold")).pack(side="left", padx=2)
        self._create_btn(roster_btns, "🗑️ Del", "#7F1D1D", "#991B1B", self.delete_machine_action, padx=6, pady=2, font=("Segoe UI", 8, "bold")).pack(side="left", padx=2)

        # Search Bar
        search_frame = tk.Frame(left_card, bg="#1E293B", padx=6, pady=4, relief="flat")
        search_frame.pack(fill="x", pady=(0, 8))

        tk.Label(search_frame, text="🔍", font=("Segoe UI Emoji", 8), fg="#94A3B8", bg="#1E293B").pack(side="left", padx=(2, 4))
        self.entry_search = tk.Entry(search_frame, bg="#1E293B", fg="#F8FAFC", font=("Segoe UI", 9),
                                     insertbackground="#FFFFFF", relief="flat")
        self.entry_search.pack(side="left", fill="x", expand=True)
        self.entry_search.insert(0, "")
        self.entry_search.bind("<KeyRelease>", self.filter_machines)

        # Treeview Roster Table
        tree_container = tk.Frame(left_card, bg="#0F172A")
        tree_container.pack(fill="both", expand=True)

        cols = ("status", "name", "host", "latency")
        self.tree = ttk.Treeview(tree_container, columns=cols, show="headings", selectmode="browse")
        self.tree.heading("status", text="Status")
        self.tree.heading("name", text="Machine Name")
        self.tree.heading("host", text="Host / IP")
        self.tree.heading("latency", text="Ping")

        self.tree.column("status", width=75, anchor="center")
        self.tree.column("name", width=145, anchor="w")
        self.tree.column("host", width=110, anchor="w")
        self.tree.column("latency", width=60, anchor="center")

        # Color tags for row status
        self.tree.tag_configure('online', foreground='#34D399')
        self.tree.tag_configure('offline', foreground='#FB7185')

        tree_scroll = ttk.Scrollbar(tree_container, orient="vertical", command=self.tree.yview, style="Vertical.TScrollbar")
        self.tree.configure(yscrollcommand=tree_scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        tree_scroll.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self.on_machine_selected)

        # Bottom Scan & Roster Meta Bar
        roster_bottom = tk.Frame(left_card, bg="#0F172A", pady=6)
        roster_bottom.pack(fill="x")

        self._create_btn(roster_bottom, "🔄 Scan All Pings Now", "#1E293B", "#334155", self.trigger_all_pings, padx=8, pady=3, font=("Segoe UI", 8)).pack(side="left")
        self.lbl_last_scan = tk.Label(roster_bottom, text="Auto-ping: 8s", font=("Segoe UI", 7), fg="#64748B", bg="#0F172A")
        self.lbl_last_scan.pack(side="right", pady=4)

        # ---------------------------------------------------------
        # RIGHT PANEL: MACHINE HERO CARD + OPERATIONS + TERMINAL
        # ---------------------------------------------------------
        right_panel = tk.Frame(workspace, bg="#080E1E")
        right_panel.grid(row=0, column=1, sticky="nsew")

        right_panel.columnconfigure(0, weight=1)
        right_panel.rowconfigure(0, weight=0)  # Hero card
        right_panel.rowconfigure(1, weight=0)  # Operations control center
        right_panel.rowconfigure(2, weight=1)  # Terminal console (expands 100%)

        # ---------------------------------------------------------
        # 2A. MACHINE HERO CARD
        # ---------------------------------------------------------
        self.hero_card = tk.Frame(right_panel, bg="#0F172A", padx=16, pady=12, relief="flat",
                                  highlightbackground="#1E293B", highlightthickness=1)
        self.hero_card.grid(row=0, column=0, sticky="ew", pady=(0, 10))

        hero_top = tk.Frame(self.hero_card, bg="#0F172A")
        hero_top.pack(fill="x")

        self.lbl_hero_name = tk.Label(hero_top, text="No Machine Selected",
                                      font=("Segoe UI", 12, "bold"), fg="#FFFFFF", bg="#0F172A")
        self.lbl_hero_name.pack(side="left")

        self.lbl_hero_badge = tk.Label(hero_top, text="● SELECT A TARGET",
                                       font=("Segoe UI", 8, "bold"), fg="#94A3B8", bg="#1E293B", padx=8, pady=2)
        self.lbl_hero_badge.pack(side="left", padx=(10, 0))

        hero_quick_actions = tk.Frame(hero_top, bg="#0F172A")
        hero_quick_actions.pack(side="right")

        self.btn_quick_copy_ip = self._create_btn(hero_quick_actions, "📋 Copy IP", "#1E293B", "#334155",
                                                  self.copy_target_ip, padx=6, pady=2, font=("Segoe UI", 8))
        self.btn_quick_copy_ip.pack(side="left", padx=2)

        self.btn_quick_browser = self._create_btn(hero_quick_actions, "🌐 Open Browser", "#2563EB", "#3B82F6",
                                                  self.open_web_admin_action, padx=8, pady=2, font=("Segoe UI", 8, "bold"))
        self.btn_quick_browser.pack(side="left", padx=2)

        # Meta tags row
        self.hero_meta_frame = tk.Frame(self.hero_card, bg="#0F172A")
        self.hero_meta_frame.pack(fill="x", pady=(6, 0))

        self.lbl_hero_ip = tk.Label(self.hero_meta_frame, text="Host: --", font=("Segoe UI", 8), fg="#38BDF8", bg="#0F172A")
        self.lbl_hero_ip.pack(side="left", padx=(0, 14))

        self.lbl_hero_loc = tk.Label(self.hero_meta_frame, text="Location: --", font=("Segoe UI", 8), fg="#94A3B8", bg="#0F172A")
        self.lbl_hero_loc.pack(side="left", padx=(0, 14))

        self.lbl_hero_notes = tk.Label(self.hero_meta_frame, text="Notes: Select a machine from the left roster to begin.",
                                       font=("Segoe UI", 8), fg="#64748B", bg="#0F172A")
        self.lbl_hero_notes.pack(side="left", fill="x", expand=True)

        # ---------------------------------------------------------
        # 2B. REMOTE OPERATIONS & DIAGNOSTICS CENTER
        # ---------------------------------------------------------
        ops_card = tk.Frame(right_panel, bg="#0F172A", padx=14, pady=10, relief="flat",
                            highlightbackground="#1E293B", highlightthickness=1)
        ops_card.grid(row=1, column=0, sticky="ew", pady=(0, 10))

        ops_card.columnconfigure(0, weight=1)
        ops_card.columnconfigure(1, weight=1)
        ops_card.columnconfigure(2, weight=1)

        # Category 1: Access & Shell
        cat1 = tk.Frame(ops_card, bg="#0F172A")
        cat1.grid(row=0, column=0, sticky="nsew", padx=4)
        tk.Label(cat1, text="REMOTE ACCESS & SHELL", font=("Segoe UI", 8, "bold"), fg="#38BDF8", bg="#0F172A").pack(anchor="w", pady=(0, 4))

        self._create_btn(cat1, "🌐 Web Admin (Zero-Click Dev Auth)", "#1D4ED8", "#2563EB",
                         self.open_web_admin_action, padx=8, pady=5, font=("Segoe UI", 8, "bold")).pack(fill="x", pady=2)
        self._create_btn(cat1, "💻 Open Native SSH Terminal", "#0369A1", "#0284C7",
                         self.open_ssh_terminal_action, padx=8, pady=5, font=("Segoe UI", 8, "bold")).pack(fill="x", pady=2)
        self._create_btn(cat1, "💾 Pull DB Backup to Disk", "#047857", "#059669",
                         self.pull_db_backup_action, padx=8, pady=5, font=("Segoe UI", 8, "bold")).pack(fill="x", pady=2)

        # Category 2: Firmware & Deployment
        cat2 = tk.Frame(ops_card, bg="#0F172A")
        cat2.grid(row=0, column=1, sticky="nsew", padx=4)
        tk.Label(cat2, text="FIRMWARE & DEPLOYMENT", font=("Segoe UI", 8, "bold"), fg="#A78BFA", bg="#0F172A").pack(anchor="w", pady=(0, 4))

        self._create_btn(cat2, "🚀 Deploy Host OTA (SFTP)", "#6D28D9", "#7C3AED",
                         self.push_host_update_action, padx=8, pady=5, font=("Segoe UI", 8, "bold")).pack(fill="x", pady=2)
        self._create_btn(cat2, "⚡ Flash ESP32 Firmware (.bin)", "#B45309", "#D97706",
                         self.flash_esp32_action, padx=8, pady=5, font=("Segoe UI", 8, "bold")).pack(fill="x", pady=2)
        self._create_btn(cat2, "🔌 Pulse ESP32 Reset (GPIO 1)", "#C2410C", "#EA580C",
                         self.esp32_gpio_reset_action, padx=8, pady=5, font=("Segoe UI", 8, "bold")).pack(fill="x", pady=2)

        # Category 3: System & Security Management
        cat3 = tk.Frame(ops_card, bg="#0F172A")
        cat3.grid(row=0, column=2, sticky="nsew", padx=4)
        tk.Label(cat3, text="SYSTEM & SECURITY MGMT", font=("Segoe UI", 8, "bold"), fg="#F43F5E", bg="#0F172A").pack(anchor="w", pady=(0, 4))

        btn_row_m1 = tk.Frame(cat3, bg="#0F172A")
        btn_row_m1.pack(fill="x", pady=2)
        self._create_btn(btn_row_m1, "🔄 Reboot Machine", "#B91C1C", "#DC2626",
                         self.reboot_machine_action, padx=6, pady=5, font=("Segoe UI", 8, "bold")).pack(side="left", fill="x", expand=True, padx=(0, 2))
        self._create_btn(btn_row_m1, "🔑 Reset Owner Pass", "#881337", "#9F1239",
                         self.reset_owner_password_action, padx=6, pady=5, font=("Segoe UI", 8, "bold")).pack(side="left", fill="x", expand=True, padx=(2, 0))

        self._create_btn(cat3, "🗝️ Issue License Key (HWID)", "#0F766E", "#0D9488",
                         self.issue_license_action, padx=8, pady=5, font=("Segoe UI", 8, "bold")).pack(fill="x", pady=2)
        self.btn_live_logs = self._create_btn(cat3, "📜 Stream Live Journal Logs", "#334155", "#475569",
                                              self.toggle_live_logs_action, padx=8, pady=5, font=("Segoe UI", 8, "bold"))
        self.btn_live_logs.pack(fill="x", pady=2)

        # ---------------------------------------------------------
        # 2C. LIVE TERMINAL & DEPLOYMENT CONSOLE (100% FILL)
        # ---------------------------------------------------------
        console_container = tk.Frame(right_panel, bg="#0F172A", relief="flat", padx=10, pady=8,
                                     highlightbackground="#1E293B", highlightthickness=1)
        console_container.grid(row=2, column=0, sticky="nsew")

        console_container.columnconfigure(0, weight=1)
        console_container.rowconfigure(0, weight=0)  # Toolbar
        console_container.rowconfigure(1, weight=1)  # Text output

        # Console Header Toolbar
        c_tool_row = tk.Frame(console_container, bg="#0F172A")
        c_tool_row.grid(row=0, column=0, sticky="ew", pady=(0, 6))

        tk.Label(c_tool_row, text="🖥️ LIVE TERMINAL & DIAGNOSTIC CONSOLE",
                 font=("Segoe UI", 8, "bold"), fg="#38BDF8", bg="#0F172A").pack(side="left")

        # Quick SSH Diagnostics buttons on the right of toolbar
        c_actions = tk.Frame(c_tool_row, bg="#0F172A")
        c_actions.pack(side="right")

        self._create_btn(c_actions, "🩺 Service Status", "#1E293B", "#334155",
                         lambda: self.run_quick_ssh("systemctl status ecofi_portal.service --no-pager", "SERVICE STATUS"),
                         padx=6, pady=1, font=("Segoe UI", 7, "bold")).pack(side="left", padx=2)

        self._create_btn(c_actions, "📊 Uptime & Load", "#1E293B", "#334155",
                         lambda: self.run_quick_ssh("uptime", "UPTIME"),
                         padx=6, pady=1, font=("Segoe UI", 7)).pack(side="left", padx=2)

        self._create_btn(c_actions, "🧠 Memory", "#1E293B", "#334155",
                         lambda: self.run_quick_ssh("free -h", "RAM USAGE"),
                         padx=6, pady=1, font=("Segoe UI", 7)).pack(side="left", padx=2)

        self._create_btn(c_actions, "💾 Disk", "#1E293B", "#334155",
                         lambda: self.run_quick_ssh("df -h /", "DISK USAGE"),
                         padx=6, pady=1, font=("Segoe UI", 7)).pack(side="left", padx=2)

        self._create_btn(c_actions, "🌐 Tailscale", "#1E293B", "#334155",
                         lambda: self.run_quick_ssh("tailscale status", "TAILSCALE STATUS"),
                         padx=6, pady=1, font=("Segoe UI", 7)).pack(side="left", padx=2)

        self.btn_autoscroll = self._create_btn(c_actions, "Auto-scroll: ON", "#065F46", "#047857",
                                               self.toggle_autoscroll, padx=6, pady=1, font=("Segoe UI", 7))
        self.btn_autoscroll.pack(side="left", padx=2)

        self._create_btn(c_actions, "📋 Copy", "#334155", "#475569",
                         self.copy_console_logs, padx=6, pady=1, font=("Segoe UI", 7)).pack(side="left", padx=2)

        self._create_btn(c_actions, "🧹 Clear", "#334155", "#475569",
                         self.clear_console, padx=6, pady=1, font=("Segoe UI", 7)).pack(side="left", padx=2)

        # Embedded Console Output Text
        c_text_box = tk.Frame(console_container, bg="#050811")
        c_text_box.grid(row=1, column=0, sticky="nsew")

        self.txt_console = tk.Text(c_text_box, bg="#050811", fg="#E2E8F0",
                                   font=("Consolas", 10), insertbackground="#38BDF8",
                                   relief="flat", wrap="word", padx=8, pady=8)
        
        # Tags for colored console log output
        self.txt_console.tag_configure("info", foreground="#38BDF8")
        self.txt_console.tag_configure("success", foreground="#34D399", font=("Consolas", 10, "bold"))
        self.txt_console.tag_configure("warn", foreground="#FCD34D")
        self.txt_console.tag_configure("error", foreground="#F87171", font=("Consolas", 10, "bold"))
        self.txt_console.tag_configure("dim", foreground="#64748B")
        self.txt_console.tag_configure("cmd", foreground="#A78BFA", font=("Consolas", 10, "bold"))

        console_scroll = ttk.Scrollbar(c_text_box, orient="vertical", command=self.txt_console.yview, style="Vertical.TScrollbar")
        self.txt_console.configure(yscrollcommand=console_scroll.set)
        self.txt_console.pack(side="left", fill="both", expand=True)
        console_scroll.pack(side="right", fill="y")

        self.log_console("╔══════════════════════════════════════════════════════════════════════════════════════╗\n", "dim")
        self.log_console("║   ECO-VENDO FLEET COMMAND & TELEMETRY STATION INITIALIZED (v1.2 PRO)                 ║\n", "info")
        self.log_console("╚══════════════════════════════════════════════════════════════════════════════════════╝\n\n", "dim")
        self.log_console("[INIT] Loaded {} machines into fleet roster.\n".format(len(self.machines)), "success")
        self.log_console("[INFO] Ready. Select a machine or invoke an action above.\n\n", "info")

        self.refresh_roster_table()
        # Auto-select the first machine if available
        if self.machines:
            first_id = self.machines[0]["id"]
            for item in self.tree.get_children():
                if self.tree.item(item, "tags") == (first_id,):
                    self.tree.selection_set(item)
                    self.on_machine_selected(None)
                    break

    # -------------------------------------------------------------
    # Helper: Polished Hoverable Buttons
    # -------------------------------------------------------------
    def _create_btn(self, parent, text, bg_color, hover_color, command, **kwargs):
        btn = tk.Button(parent, text=text, bg=bg_color, fg="#FFFFFF", relief="flat",
                        cursor="hand2", command=command, **kwargs)
        btn.bind("<Enter>", lambda e, b=btn, c=hover_color: b.configure(bg=c))
        btn.bind("<Leave>", lambda e, b=btn, c=bg_color: b.configure(bg=c))
        return btn

    # -------------------------------------------------------------
    # Clock & Dynamic Dev Password Calculator
    # -------------------------------------------------------------
    def start_clock_timer(self):
        def update():
            now = datetime.datetime.now()
            mm = now.minute
            sec = now.second
            passcode = "dev{:02d}".format(mm)
            sec_left = 60 - sec

            self.lbl_dyn_pass.configure(text=passcode)
            self.lbl_sec_badge.configure(text="{}s left".format(sec_left))
            self.root.after(1000, update)
        update()

    def get_current_dev_password(self):
        return "dev{:02d}".format(datetime.datetime.now().minute)

    def copy_current_password(self):
        pw = self.get_current_dev_password()
        self.root.clipboard_clear()
        self.root.clipboard_append(pw)
        self.btn_copy_pass.configure(text="✅ Copied!", bg="#059669")
        self.root.after(1500, lambda: self.btn_copy_pass.configure(text="📋 Copy Pass", bg="#334155"))
        self.log_console("[DEV] Master password '{}' copied to clipboard.\n".format(pw), "info")

    def _get_web_base_url(self, m):
        if not m:
            return "http://127.0.0.1:5000"
        host = m.get('host', '').strip()
        web_port = m.get('web_port')
        if not web_port:
            if host.startswith('100.'):
                web_port = 5000
            else:
                web_port = 80
        try:
            port_num = int(web_port)
        except (ValueError, TypeError):
            port_num = 5000 if host.startswith('100.') else 80
        if port_num == 80:
            return "http://{}".format(host)
        return "http://{}:{}".format(host, port_num)

    def copy_magic_auth_link(self):
        m = self.selected_machine or (self.machines[0] if self.machines else None)
        if not m:
            messagebox.showinfo("Select Machine", "Please select a machine first.")
            return
        passcode = self.get_current_dev_password()
        base_url = self._get_web_base_url(m)
        url = "{}/admin/dev_auth?token={}".format(base_url, passcode)
        self.root.clipboard_clear()
        self.root.clipboard_append(url)
        self.btn_copy_link.configure(text="✅ URL Copied!", bg="#059669")
        self.root.after(1500, lambda: self.btn_copy_link.configure(text="🔗 Copy Magic Link", bg="#0284C7"))
        self.log_console("[DEV] Direct auto-login URL copied: {}\n".format(url), "info")

    def copy_target_ip(self):
        if not self.selected_machine:
            return
        ip = self.selected_machine.get('host', '')
        self.root.clipboard_clear()
        self.root.clipboard_append(ip)
        self.btn_quick_copy_ip.configure(text="✅ Copied!", bg="#059669")
        self.root.after(1500, lambda: self.btn_quick_copy_ip.configure(text="📋 Copy IP", bg="#1E293B"))

    # -------------------------------------------------------------
    # Console Logger & Helpers
    # -------------------------------------------------------------
    def log_console(self, text, tag=None):
        def append():
            if tag:
                self.txt_console.insert("end", text, tag)
            else:
                self.txt_console.insert("end", text)
            if self.autoscroll_enabled:
                self.txt_console.see("end")
        self.root.after(0, append)

    def clear_console(self):
        self.txt_console.delete("1.0", "end")
        self.log_console("[CONSOLE] Buffer cleared.\n", "dim")

    def copy_console_logs(self):
        content = self.txt_console.get("1.0", "end")
        self.root.clipboard_clear()
        self.root.clipboard_append(content)
        self.log_console("[CONSOLE] All terminal logs copied to clipboard.\n", "info")

    def toggle_autoscroll(self):
        self.autoscroll_enabled = not self.autoscroll_enabled
        if self.autoscroll_enabled:
            self.btn_autoscroll.configure(text="Auto-scroll: ON", bg="#065F46")
            self.txt_console.see("end")
        else:
            self.btn_autoscroll.configure(text="Auto-scroll: OFF", bg="#334155")

    # -------------------------------------------------------------
    # Background Ping Daemon
    # -------------------------------------------------------------
    def start_background_ping_daemon(self):
        t = threading.Thread(target=self._ping_daemon_loop, daemon=True)
        t.start()

    def _ping_daemon_loop(self):
        while True:
            for m in list(self.machines):
                host = m.get('host', '').strip()
                if not host:
                    continue
                self._ping_host(host)
                self.root.after(0, self.update_tree_row, m['id'])
            self.root.after(0, self._update_global_stats)
            time.sleep(8)

    def _ping_host(self, host):
        cmd = ["ping", "-n", "1", "-w", "1000", host] if sys.platform.startswith("win") else ["ping", "-c", "1", "-W", "1", host]
        try:
            start_t = time.time()
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            elapsed_ms = int((time.time() - start_t) * 1000)
            if proc.returncode == 0:
                self.ping_cache[host] = {"online": True, "ms": elapsed_ms, "last_check": time.time()}
            else:
                self.ping_cache[host] = {"online": False, "ms": None, "last_check": time.time()}
        except Exception:
            self.ping_cache[host] = {"online": False, "ms": None, "last_check": time.time()}

    def trigger_all_pings(self):
        def task():
            self.log_console("[PING] Scanning all fleet machine nodes in background...\n", "info")
            for m in list(self.machines):
                self._ping_host(m['host'])
                self.root.after(0, self.update_tree_row, m['id'])
            self.root.after(0, self._update_global_stats)
            self.log_console("[PING] Scan complete.\n", "success")
        threading.Thread(target=task, daemon=True).start()

    def _update_global_stats(self):
        total = len(self.machines)
        online = sum(1 for m in self.machines if self.ping_cache.get(m['host'], {}).get('online', False))
        offline = total - online
        self.lbl_stats_total.configure(text="TOTAL: {}".format(total))
        self.lbl_stats_online.configure(text="ONLINE: {}".format(online))
        self.lbl_stats_offline.configure(text="OFFLINE: {}".format(offline))

    def update_tree_row(self, m_id):
        m = next((item for item in self.machines if item["id"] == m_id), None)
        if not m:
            return
        status_info = self.ping_cache.get(m['host'], {"online": False, "ms": None})
        is_up = status_info['online']
        status_tag = 'online' if is_up else 'offline'
        status_icon = "● ONLINE" if is_up else "○ OFFLINE"
        latency_str = "{} ms".format(status_info['ms']) if is_up else "Timeout"

        for item in self.tree.get_children():
            if self.tree.item(item, "tags") and self.tree.item(item, "tags")[0] == m_id:
                self.tree.item(item, values=(status_icon, m['name'], m['host'], latency_str), tags=(m_id, status_tag))
                break

        # Also update hero card if this machine is actively selected
        if self.selected_machine and self.selected_machine["id"] == m_id:
            self._update_hero_card(m)

    def filter_machines(self, event=None):
        query = self.entry_search.get().strip().lower()
        if not query:
            self.filtered_machines = list(self.machines)
        else:
            self.filtered_machines = [
                m for m in self.machines
                if query in m.get('name', '').lower()
                or query in m.get('host', '').lower()
                or query in m.get('location', '').lower()
                or query in m.get('notes', '').lower()
            ]
        self.refresh_roster_table()

    def refresh_roster_table(self):
        self.tree.delete(*self.tree.get_children())
        for m in self.filtered_machines:
            status_info = self.ping_cache.get(m['host'], {"online": False, "ms": None})
            is_up = status_info['online']
            status_tag = 'online' if is_up else 'offline'
            status_icon = "● ONLINE" if is_up else "○ OFFLINE"
            latency_str = "{} ms".format(status_info['ms']) if is_up else "..."
            self.tree.insert("", "end", tags=(m['id'], status_tag),
                             values=(status_icon, m['name'], m['host'], latency_str))
        self._update_global_stats()

    def on_machine_selected(self, event):
        selected_items = self.tree.selection()
        if not selected_items:
            return
        item_tags = self.tree.item(selected_items[0], "tags")
        if not item_tags:
            return
        m_id = item_tags[0]
        self.selected_machine = next((m for m in self.machines if m["id"] == m_id), None)
        if self.selected_machine:
            self._update_hero_card(self.selected_machine)

    def _update_hero_card(self, m):
        st_info = self.ping_cache.get(m['host'], {"online": False, "ms": None})
        is_up = st_info.get('online', False)
        ms = st_info.get('ms')

        self.lbl_hero_name.configure(text=m['name'])
        if is_up:
            self.lbl_hero_badge.configure(
                text="● ONLINE · {} ms".format(ms) if ms else "● ONLINE",
                fg="#34D399", bg="#064E3B"
            )
        else:
            self.lbl_hero_badge.configure(text="○ OFFLINE · Timeout", fg="#FB7185", bg="#4C0519")

        self.lbl_hero_ip.configure(text="Host: {} (Port: {})".format(m['host'], m.get('port', 22)))
        self.lbl_hero_loc.configure(text="Location: {}".format(m.get('location') or 'Not specified'))
        self.lbl_hero_notes.configure(text="Notes: {}".format(m.get('notes') or 'None'))

    # -------------------------------------------------------------
    # Machine Dialogs (Add / Edit / Delete)
    # -------------------------------------------------------------
    def add_machine_dialog(self):
        self._machine_editor_modal(title="Add New Vendo Machine", machine=None)

    def edit_machine_dialog(self):
        if not self.selected_machine:
            messagebox.showinfo("Select Machine", "Please select a machine from the roster first.")
            return
        self._machine_editor_modal(title="Edit Vendo Machine", machine=self.selected_machine)

    def _machine_editor_modal(self, title, machine=None):
        dlg = tk.Toplevel(self.root)
        dlg.title(title)
        dlg.geometry("480x440")
        dlg.configure(bg="#0B132B")
        dlg.transient(self.root)
        dlg.grab_set()

        form = tk.Frame(dlg, bg="#0B132B", padx=24, pady=18)
        form.pack(fill="both", expand=True)

        fields = [
            ("Machine Name:", "name", machine['name'] if machine else "Vendo #02 (Location)"),
            ("Host / Tailscale IP:", "host", machine['host'] if machine else "100.x.y.z"),
            ("Web Port (80 for LAN, 5000 for Tailscale):", "web_port", str(machine.get('web_port', 5000 if (machine and machine.get('host', '').startswith('100.')) else 80))),
            ("SSH Port:", "port", str(machine.get('port', 22)) if machine else "22"),
            ("SSH Username:", "username", machine.get('username', 'root') if machine else "root"),
            ("SSH Password (blank = default):", "password", machine.get('password', '') if machine else ""),
            ("Physical Location:", "location", machine.get('location', '') if machine else ""),
            ("Notes:", "notes", machine.get('notes', '') if machine else "")
        ]

        entries = {}
        for idx, (label, key, val) in enumerate(fields):
            tk.Label(form, text=label, font=("Segoe UI", 9, "bold"), fg="#38BDF8", bg="#0B132B").grid(
                row=idx, column=0, sticky="w", pady=4
            )
            e = tk.Entry(form, bg="#111C38", fg="#F8FAFC", font=("Segoe UI", 9),
                         insertbackground="#FFFFFF", relief="flat", highlightbackground="#1E293B", highlightthickness=1)
            e.insert(0, val)
            e.grid(row=idx, column=1, sticky="ew", padx=(10, 0), pady=4)
            entries[key] = e

        form.columnconfigure(1, weight=1)

        def save():
            name = entries['name'].get().strip()
            host = entries['host'].get().strip()
            if not name or not host:
                messagebox.showerror("Validation Error", "Name and Host IP are required.", parent=dlg)
                return
            m_dict = {
                "id": machine['id'] if machine else "vendo-" + str(int(time.time())),
                "name": name,
                "host": host,
                "port": int(entries['port'].get().strip() or 22),
                "web_port": int(entries['web_port'].get().strip() or (5000 if host.startswith('100.') else 80)),
                "username": entries['username'].get().strip() or "root",
                "password": entries['password'].get().strip(),
                "location": entries['location'].get().strip(),
                "notes": entries['notes'].get().strip()
            }
            if machine:
                idx = next((i for i, item in enumerate(self.machines) if item["id"] == machine["id"]), -1)
                if idx != -1:
                    self.machines[idx] = m_dict
            else:
                self.machines.append(m_dict)
            self.save_fleet()
            self.filter_machines()
            dlg.destroy()

        btn_bar = tk.Frame(dlg, bg="#0B132B", pady=12)
        btn_bar.pack(fill="x")
        self._create_btn(btn_bar, "Save Machine", "#059669", "#10B981", save, padx=16, pady=5, font=("Segoe UI", 9, "bold")).pack(side="right", padx=20)
        self._create_btn(btn_bar, "Cancel", "#334155", "#475569", dlg.destroy, padx=12, pady=5, font=("Segoe UI", 9)).pack(side="right")

    def delete_machine_action(self):
        if not self.selected_machine:
            messagebox.showinfo("Select Machine", "Please select a machine to delete.")
            return
        m = self.selected_machine
        if messagebox.askyesno("Delete Machine", "Remove '{}' from fleet roster?".format(m['name'])):
            self.machines = [item for item in self.machines if item["id"] != m["id"]]
            self.selected_machine = None
            self.save_fleet()
            self.filter_machines()
            self.lbl_hero_name.configure(text="No Machine Selected")
            self.lbl_hero_badge.configure(text="● SELECT A TARGET", fg="#94A3B8", bg="#1E293B")
            self.lbl_hero_ip.configure(text="Host: --")
            self.lbl_hero_loc.configure(text="Location: --")
            self.lbl_hero_notes.configure(text="Select a machine from the left roster to begin.")

    # -------------------------------------------------------------
    # Remote Operation Actions
    # -------------------------------------------------------------
    def _require_selected(self):
        if not self.selected_machine:
            messagebox.showwarning("Select Machine", "Please select an active machine from the roster first.")
            return None
        return self.selected_machine

    def open_web_admin_action(self):
        """Open web admin directly with one-click dynamic token bypass."""
        m = self._require_selected()
        if not m:
            return
        passcode = self.get_current_dev_password()
        base_url = self._get_web_base_url(m)
        url = "{}/admin/dev_auth?token={}".format(base_url, passcode)
        self.log_console("\n[WEB] Launching browser: {} (Auto-authenticating as devclard)\n".format(url), "cmd")
        webbrowser.open(url)

    def open_ssh_terminal_action(self):
        """Launch Windows Terminal or PowerShell SSH session."""
        m = self._require_selected()
        if not m:
            return
        host = m['host']
        user = m.get('username', 'root')
        port = m.get('port', 22)
        ssh_cmd = "ssh -p {} {}@{}".format(port, user, host)
        self.log_console("\n[SSH] Launching external terminal: {}\n".format(ssh_cmd), "cmd")

        try:
            subprocess.Popen(["wt.exe", "new-tab", "--title", m['name'], "powershell", "-NoExit", "-Command", ssh_cmd])
        except Exception:
            try:
                subprocess.Popen(["cmd.exe", "/c", "start", "powershell", "-NoExit", "-Command", ssh_cmd])
            except Exception as e:
                messagebox.showerror("SSH Error", "Could not launch terminal: " + str(e))

    def run_quick_ssh(self, cmd_str, label="CMD"):
        """Run quick diagnostic command over SSH and stream output to console."""
        m = self._require_selected()
        if not m:
            return

        def task():
            self.log_console("\n[SSH:{}] Executing on {}: '{}'...\n".format(label, m['host'], cmd_str), "cmd")
            try:
                client = self.get_ssh_client(m, timeout=10)
                stdin, stdout, stderr = client.exec_command(cmd_str, timeout=15)
                out = stdout.read().decode('utf-8', errors='replace')
                err = stderr.read().decode('utf-8', errors='replace')
                client.close()

                if out:
                    self.log_console(out + "\n")
                if err:
                    self.log_console(err + "\n", "warn")
                self.log_console("[SSH:{}] Command complete.\n".format(label), "success")
            except Exception as e:
                self.log_console("[SSH:{}] ERROR: {}\n".format(label, str(e)), "error")

        threading.Thread(target=task, daemon=True).start()

    def esp32_gpio_reset_action(self):
        """Remotely pulse GPIO 1 (PA01) LOW -> HIGH to hardware-reset ESP32."""
        m = self._require_selected()
        if not m:
            return
        if not messagebox.askyesno("ESP32 Reset", "Trigger hardware GPIO pulse on EN reset line for '{}'?".format(m['name'])):
            return

        def task():
            self.log_console("\n[ESP32] Connecting to {} via SSH to pulse reset line...\n".format(m['host']), "cmd")
            try:
                client = self.get_ssh_client(m)
                cmd = (
                    "echo 1 > /sys/class/gpio/export 2>/dev/null || true; "
                    "echo out > /sys/class/gpio/gpio1/direction; "
                    "echo 0 > /sys/class/gpio/gpio1/value; "
                    "sleep 0.15; "
                    "echo 1 > /sys/class/gpio/gpio1/value"
                )
                stdin, stdout, stderr = client.exec_command(cmd, timeout=10)
                stdout.channel.recv_exit_status()
                client.close()
                self.log_console("[ESP32] SUCCESS: Hardware pulse delivered to EN reset line!\n", "success")
                messagebox.showinfo("Reset Sent", "ESP32 hardware pulse delivered successfully!")
            except Exception as e:
                self.log_console("[ESP32] ERROR: {}\n".format(str(e)), "error")
                messagebox.showerror("Reset Failed", "Could not pulse ESP32: " + str(e))

        threading.Thread(target=task, daemon=True).start()

    def reboot_machine_action(self):
        """Gracefully reboot the remote Orange Pi."""
        m = self._require_selected()
        if not m:
            return
        if not messagebox.askyesno("Reboot Machine", "Are you sure you want to REBOOT '{}' now?".format(m['name'])):
            return

        def task():
            self.log_console("\n[REBOOT] Sending reboot command to {}...\n".format(m['host']), "warn")
            try:
                client = self.get_ssh_client(m)
                client.exec_command("sync; reboot", timeout=5)
                client.close()
                self.log_console("[REBOOT] SUCCESS: System reboot initiated.\n", "success")
                messagebox.showinfo("Rebooting", "Reboot command sent to " + m['name'])
            except Exception as e:
                self.log_console("[REBOOT] {}\n".format(str(e)), "error")

        threading.Thread(target=task, daemon=True).start()

    def reset_owner_password_action(self):
        """Reset owner admin password in SQLite back to default admin1234."""
        m = self._require_selected()
        if not m:
            return
        if not messagebox.askyesno("Reset Admin Password",
                                   "Reset the owner's web admin password on '{}' back to 'admin1234'?\n\n"
                                   "They will be prompted to set a new password on their next login.".format(m['name'])):
            return

        def task():
            self.log_console("\n[AUTH] Resetting admin password on {}...\n".format(m['host']), "cmd")
            try:
                client = self.get_ssh_client(m)
                py_cmd = (
                    "python3 -c \""
                    "import sqlite3; from werkzeug.security import generate_password_hash; "
                    "h = generate_password_hash('admin1234'); "
                    "conn = sqlite3.connect('/opt/ecofi/vendo_sessions.db'); "
                    "conn.execute('UPDATE admins SET password_hash=? WHERE username=\\'admin\\'', (h,)); "
                    "conn.commit(); conn.close(); print('PASSWORD_RESET_OK')\""
                )
                stdin, stdout, stderr = client.exec_command(py_cmd, timeout=10)
                out = stdout.read().decode().strip()
                client.close()
                if "PASSWORD_RESET_OK" in out:
                    self.log_console("[AUTH] SUCCESS: Admin password restored to default 'admin1234'.\n", "success")
                    messagebox.showinfo("Password Reset", "Admin password successfully reset to 'admin1234'!")
                else:
                    self.log_console("[AUTH] Output: {}\n".format(out), "warn")
            except Exception as e:
                self.log_console("[AUTH] ERROR: {}\n".format(str(e)), "error")
                messagebox.showerror("Reset Failed", "Could not reset password: " + str(e))

        threading.Thread(target=task, daemon=True).start()

    def pull_db_backup_action(self):
        """Download remote SQLite database to user-selected folder."""
        m = self._require_selected()
        if not m:
            return

        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        default_filename = "{}_{}.db".format(m['id'], ts)
        backup_dir = ROOT / 'backups'
        backup_dir.mkdir(parents=True, exist_ok=True)

        target_path = filedialog.asksaveasfilename(
            title="Save Database Backup As",
            initialdir=str(backup_dir),
            initialfile=default_filename,
            filetypes=[("SQLite Database (*.db)", "*.db"), ("All Files", "*.*")]
        )
        if not target_path:
            return

        local_file = Path(target_path)
        local_file.parent.mkdir(parents=True, exist_ok=True)

        def task():
            self.log_console("\n[BACKUP] Pulling /opt/ecofi/vendo_sessions.db from {}...\n".format(m['host']), "cmd")
            try:
                client = self.get_ssh_client(m)
                sftp = client.open_sftp()
                sftp.get('/opt/ecofi/vendo_sessions.db', str(local_file))
                sftp.close()
                client.close()

                size_kb = round(local_file.stat().st_size / 1024, 1)
                self.log_console("[BACKUP] SUCCESS: Saved to {} ({} KB)\n".format(local_file, size_kb), "success")
                messagebox.showinfo("Backup Downloaded", "Database backup saved to:\n{}".format(local_file))
            except Exception as e:
                self.log_console("[BACKUP] ERROR: {}\n".format(str(e)), "error")
                messagebox.showerror("Backup Failed", "Could not pull database: " + str(e))

        threading.Thread(target=task, daemon=True).start()

    def push_host_update_action(self):
        """Deploy local host/ files to remote Orange Pi via SFTP and restart service."""
        m = self._require_selected()
        if not m:
            return
        if not messagebox.askyesno("Deploy Update", "Deploy latest host code to '{}' and restart service?".format(m['name'])):
            return

        def task():
            self.log_console("\n[DEPLOY] Starting OTA host deployment to {}...\n".format(m['host']), "cmd")
            host_dir = ROOT / 'host'
            files_to_deploy = [
                'portal.py', 'time_portal.py', 'esp32_simulator.py',
                'gateway_network.py', 'license_manager.py', 'status_led.py',
                'time_policy.py', 'time_schema.py', 'transition_engine.py',
                'migrate_legacy_sessions.py'
            ]
            try:
                client = self.get_ssh_client(m, timeout=12)
                ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
                b_dir = "/opt/ecofi_backups/backup_" + ts
                client.exec_command("mkdir -p " + b_dir)

                sftp = client.open_sftp()
                for fname in files_to_deploy:
                    loc = host_dir / fname
                    if loc.exists():
                        rem = "/opt/ecofi/" + fname
                        self.log_console("  Uploading {} ({} bytes)...\n".format(fname, loc.stat().st_size), "dim")
                        sftp.put(str(loc), rem)

                ver_file = ROOT / 'VERSION'
                if ver_file.exists():
                    sftp.put(str(ver_file), "/opt/ecofi/VERSION")

                sftp.close()

                # Verify remote python syntax before restarting service!
                self.log_console("  Validating remote Python syntax...\n", "dim")
                _, stdout, stderr = client.exec_command("python3 -m py_compile /opt/ecofi/*.py")
                compile_err = stderr.read().decode().strip()
                if compile_err:
                    raise RuntimeError("Remote syntax check failed: " + compile_err)

                self.log_console("  Restarting ecofi_portal.service...\n", "dim")
                _, stdout, _ = client.exec_command("systemctl restart ecofi_portal.service && systemctl is-active ecofi_portal.service")
                status = stdout.read().decode().strip()
                client.close()

                if status == "active":
                    self.log_console("[DEPLOY] SUCCESS: Remote service active and verified healthy!\n", "success")
                    messagebox.showinfo("Deploy Complete", "Host files deployed successfully! ecofi_portal is active.")
                else:
                    self.log_console("[DEPLOY] WARNING: Service returned status: {}\n".format(status), "warn")
                    messagebox.showwarning("Deploy Warning", "Service status: " + status)
            except Exception as e:
                self.log_console("[DEPLOY] ERROR: {}\n".format(str(e)), "error")
                messagebox.showerror("Deploy Failed", "Deployment error: " + str(e))

        threading.Thread(target=task, daemon=True).start()

    def flash_esp32_action(self):
        """Pick .bin file from Windows and flash remote ESP32 via esptool."""
        m = self._require_selected()
        if not m:
            return
        bin_path = filedialog.askopenfilename(
            title="Select ESP32 Compiled Binary",
            filetypes=[("ESP32 Binary (*.bin)", "*.bin"), ("All Files", "*.*")]
        )
        if not bin_path:
            return

        bin_file = Path(bin_path)
        if not messagebox.askyesno("Confirm Flashing",
                                   "Flash '{}' ({} KB)\nover /dev/ttyS3 to '{}'?".format(
                                       bin_file.name, round(bin_file.stat().st_size / 1024, 1), m['name'])):
            return

        def task():
            self.log_console("\n[FLASH] Uploading {} to {}...\n".format(bin_file.name, m['host']), "cmd")
            client = None
            try:
                client = self.get_ssh_client(m, timeout=15)
                sftp = client.open_sftp()
                sftp.put(str(bin_file), "/tmp/esp32_remote_firmware.bin")
                sftp.close()

                flash_offset = "0x0" if "factory" in bin_file.name.lower() else "0x10000"
                self.log_console("[FLASH] Stopping portal service & forcing ESP32 ROM bootloader via GPIO (Offset: {})...\n".format(flash_offset), "warn")
                flash_cmd = (
                    "systemctl stop ecofi_portal.service; "
                    "for p in 0 1; do [ ! -d /sys/class/gpio/gpio$p ] && echo $p > /sys/class/gpio/export 2>/dev/null || true; done; "
                    "echo out > /sys/class/gpio/gpio0/direction; echo 0 > /sys/class/gpio/gpio0/value; "
                    "echo out > /sys/class/gpio/gpio1/direction; echo 0 > /sys/class/gpio/gpio1/value; "
                    "sleep 0.15; "
                    "echo 1 > /sys/class/gpio/gpio1/value; sleep 0.1; echo in > /sys/class/gpio/gpio1/direction; "
                    "sleep 0.2; "
                    "python3 -m esptool --chip esp32 --port /dev/ttyS3 --baud 115200 --before no_reset --after no_reset "
                    "write_flash -z --flash_mode dio --flash_freq 40m --flash_size detect {} /tmp/esp32_remote_firmware.bin; "
                    "FLASH_EC=$?; "
                    "echo in > /sys/class/gpio/gpio0/direction; "
                    "echo out > /sys/class/gpio/gpio1/direction; echo 0 > /sys/class/gpio/gpio1/value; "
                    "sleep 0.15; "
                    "echo 1 > /sys/class/gpio/gpio1/value; sleep 0.1; echo in > /sys/class/gpio/gpio1/direction; "
                    "systemctl start ecofi_portal.service; "
                    "exit $FLASH_EC"
                ).format(flash_offset)
                stdin, stdout, stderr = client.exec_command(flash_cmd, timeout=180)
                for line in iter(stdout.readline, ""):
                    self.log_console("  " + line)
                err_out = stderr.read().decode()
                exit_code = stdout.channel.recv_exit_status()

                if exit_code == 0:
                    self.log_console("[FLASH] SUCCESS: Custom firmware written, verified, and ESP32 rebooted!\n", "success")
                    messagebox.showinfo("Flash Success", "ESP32 firmware successfully written and verified!")
                else:
                    self.log_console("[FLASH] ERROR (Code {}): {}\n".format(exit_code, err_out), "error")
                    messagebox.showerror("Flash Error", "esptool failed. Check console log for details.")
            except Exception as e:
                self.log_console("[FLASH] ERROR: {}\n".format(str(e)), "error")
                messagebox.showerror("Flash Exception", str(e))
                if client:
                    try:
                        client.exec_command("systemctl start ecofi_portal.service")
                    except Exception:
                        pass
            finally:
                if client:
                    try:
                        client.close()
                    except Exception:
                        pass

        threading.Thread(target=task, daemon=True).start()

    def toggle_live_logs_action(self):
        """Stream or stop live journalctl logs from remote machine."""
        if self.log_stream_running:
            self.log_stream_running = False
            self.btn_live_logs.configure(text="📜 Stream Live Journal Logs", bg="#334155")
            self.log_console("\n[LOGS] Live stream stopped.\n", "info")
            return
        m = self._require_selected()
        if not m:
            return

        self.log_stream_running = True
        self.btn_live_logs.configure(text="⏹️ Stop Live Logs Stream", bg="#B91C1C")
        self.log_console("\n[LOGS] Streaming live journalctl logs from {} (Click button again to stop)...\n".format(m['host']), "cmd")

        def task():
            try:
                client = self.get_ssh_client(m)
                stdin, stdout, stderr = client.exec_command("journalctl -u ecofi_portal.service -f -n 25", timeout=None)
                while self.log_stream_running:
                    line = stdout.readline()
                    if not line:
                        break
                    self.log_console("  " + line)
                client.close()
            except Exception as e:
                if self.log_stream_running:
                    self.log_console("[LOGS] Stream disconnected: {}\n".format(str(e)), "warn")
            self.log_stream_running = False
            self.root.after(0, lambda: self.btn_live_logs.configure(text="📜 Stream Live Journal Logs", bg="#334155"))

        t = threading.Thread(target=task, daemon=True)
        t.start()

    def issue_license_action(self):
        """Fetch HWID from remote machine, calculate activation key, and optionally push to machine."""
        m = self._require_selected()
        if not m:
            return

        dlg = tk.Toplevel(self.root)
        dlg.title("Issue License Key — " + m['name'])
        dlg.geometry("560x380")
        dlg.configure(bg="#0B132B")
        dlg.transient(self.root)
        dlg.grab_set()

        form = tk.Frame(dlg, bg="#0B132B", padx=24, pady=18)
        form.pack(fill="both", expand=True)

        tk.Label(form, text="Machine HWID:", font=("Segoe UI", 9, "bold"), fg="#38BDF8", bg="#0B132B").pack(anchor="w")
        e_hwid = tk.Entry(form, bg="#111C38", fg="#FCD34D", font=("Consolas", 10),
                          insertbackground="#FFFFFF", relief="flat", highlightbackground="#1E293B", highlightthickness=1)
        e_hwid.pack(fill="x", pady=(2, 6))

        def fetch_hwid():
            try:
                client = self.get_ssh_client(m)
                stdin, stdout, stderr = client.exec_command(
                    "PYTHONPATH=/opt/ecofi python3 -c 'import license_manager as lm; print(lm.get_machine_hwid())'"
                )
                hwid = stdout.read().decode().strip()
                err = stderr.read().decode().strip()
                client.close()
                if not hwid:
                    raise RuntimeError("Remote error: " + (err or "Empty HWID returned."))
                e_hwid.delete(0, "end")
                e_hwid.insert(0, hwid)
                self.log_console("[LICENSE] HWID fetched for {}: {}\n".format(m['name'], hwid), "success")
            except Exception as e:
                self.log_console("[LICENSE] HWID fetch error: {}\n".format(str(e)), "error")
                messagebox.showerror("HWID Fetch Failed", str(e), parent=dlg)

        self._create_btn(form, "🔍 Fetch HWID from Machine over SSH", "#1E293B", "#334155", fetch_hwid,
                         padx=8, pady=3, font=("Segoe UI", 8)).pack(anchor="w", pady=(0, 10))

        tk.Label(form, text="License Tier:", font=("Segoe UI", 9, "bold"), fg="#38BDF8", bg="#0B132B").pack(anchor="w")
        tier_cb = ttk.Combobox(form, values=["COMMERCIAL", "ENTERPRISE", "TRIAL"], state="readonly")
        tier_cb.set("COMMERCIAL")
        tier_cb.pack(fill="x", pady=(2, 8))

        tk.Label(form, text="Generated Activation Key (PIN):", font=("Segoe UI", 9, "bold"), fg="#38BDF8", bg="#0B132B").pack(anchor="w")
        e_pin = tk.Entry(form, bg="#111C38", fg="#10B981", font=("Consolas", 11, "bold"),
                         insertbackground="#FFFFFF", relief="flat", highlightbackground="#1E293B", highlightthickness=1)
        e_pin.pack(fill="x", pady=(2, 10))

        def compute_key():
            hwid = e_hwid.get().strip()
            if not hwid or not compute_activation_pin:
                messagebox.showerror("Error", "Valid HWID required.", parent=dlg)
                return
            key = compute_activation_pin(hwid, tier_cb.get())
            e_pin.delete(0, "end")
            e_pin.insert(0, key)

        def push_key():
            pin = e_pin.get().strip()
            hwid = e_hwid.get().strip()
            tier = tier_cb.get().strip()
            if not pin:
                messagebox.showerror("Error", "Please compute key first.", parent=dlg)
                return
            try:
                client = self.get_ssh_client(m)
                py_cmd = (
                    "PYTHONPATH=/opt/ecofi python3 -c \""
                    "import license_manager as lm, json; "
                    "res = lm.activate_machine('{}', tier='{}'); "
                    "print(json.dumps(res))\"".format(pin, tier)
                )
                stdin, stdout, stderr = client.exec_command(py_cmd, timeout=10)
                out_raw = stdout.read().decode().strip()
                err_raw = stderr.read().decode().strip()
                res = None
                if out_raw:
                    try:
                        res = json.loads(out_raw)
                    except Exception:
                        pass

                if res and res.get('success'):
                    _, stdout_svc, _ = client.exec_command("systemctl restart ecofi_portal.service && systemctl is-active ecofi_portal.service")
                    svc_status = stdout_svc.read().decode().strip()
                    client.close()
                    self.log_console("[LICENSE] SUCCESS: Activated key pushed to {} ({} Edition)!\n".format(m['name'], tier), "success")
                    messagebox.showinfo("Activated", "License successfully written and verified on machine!\nStatus: ACTIVATED ({})".format(tier), parent=dlg)
                    dlg.destroy()
                else:
                    msg = (res.get('message') if res else (out_raw or err_raw)) or "Activation failed"
                    client.close()
                    self.log_console("[LICENSE] Activation failed: {}\n".format(msg), "error")
                    messagebox.showerror("Activation Failed", msg, parent=dlg)
            except Exception as e:
                self.log_console("[LICENSE] Activation failed: {}\n".format(str(e)), "error")
                messagebox.showerror("Activation Failed", str(e), parent=dlg)

        b_bar = tk.Frame(form, bg="#0B132B")
        b_bar.pack(fill="x", pady=8)
        self._create_btn(b_bar, "Generate Key", "#2563EB", "#3B82F6", compute_key, padx=12, pady=4, font=("Segoe UI", 9, "bold")).pack(side="left")
        self._create_btn(b_bar, "🚀 Push & Activate on Machine", "#059669", "#10B981", push_key, padx=12, pady=4, font=("Segoe UI", 9, "bold")).pack(side="right")


def main():
    root = tk.Tk()
    app = FleetManagerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
