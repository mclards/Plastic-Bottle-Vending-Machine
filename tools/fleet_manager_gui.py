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
        self.root.title("VMC ECO-VENDO — Fleet Command & Manager v1.0")
        self.root.geometry("1100x720")
        self.root.minsize(980, 640)
        self.root.configure(bg="#0B132B")

        self.machines = []
        self.selected_machine = None
        self.ping_cache = {}  # {host: {"online": bool, "ms": int, "last_check": float}}
        self.log_stream_thread = None
        self.log_stream_running = False

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
        style.configure(".", background="#0B132B", foreground="#F8FAFC", font=("Segoe UI", 9))
        style.configure("Treeview",
                        background="#111C38",
                        foreground="#F8FAFC",
                        fieldbackground="#111C38",
                        rowheight=32,
                        font=("Segoe UI", 9))
        style.map("Treeview",
                  background=[('selected', '#1E3A8A')],
                  foreground=[('selected', '#FFFFFF')])
        style.configure("Treeview.Heading",
                        background="#1C2D54",
                        foreground="#38BDF8",
                        font=("Segoe UI", 9, "bold"),
                        relief="flat")
        style.map("Treeview.Heading", background=[('active', '#253C70')])

        # 1. Top Header Bar
        header = tk.Frame(self.root, bg="#111C38", height=60, padx=20, pady=10)
        header.pack(fill="x")

        title_box = tk.Frame(header, bg="#111C38")
        title_box.pack(side="left")

        tk.Label(title_box, text="⚡ VMC ECO-VENDO FLEET COMMAND",
                 font=("Segoe UI", 14, "bold"), fg="#10B981", bg="#111C38").pack(anchor="w")
        tk.Label(title_box, text="Remote OTA Updates · Diagnostics · ESP32 Flashing · Master Dev Access",
                 font=("Segoe UI", 9), fg="#94A3B8", bg="#111C38").pack(anchor="w")

        # Dynamic Dev Password Widget (Top Right)
        self.dev_cred_box = tk.Frame(header, bg="#1C2D54", padx=12, pady=6, relief="ridge", bd=1)
        self.dev_cred_box.pack(side="right")

        tk.Label(self.dev_cred_box, text="MASTER DEV CREDENTIALS:",
                 font=("Segoe UI", 8, "bold"), fg="#38BDF8", bg="#1C2D54").pack(anchor="e")
        self.lbl_dev_creds = tk.Label(self.dev_cred_box, text="User: devclard | Pass: dev--",
                                      font=("Consolas", 10, "bold"), fg="#FCD34D", bg="#1C2D54")
        self.lbl_dev_creds.pack(anchor="e")

        # 2. Main Paned Layout
        main_pane = tk.PanedWindow(self.root, orient="horizontal", bg="#0B132B", bd=0, sashwidth=6)
        main_pane.pack(fill="both", expand=True, padx=14, pady=10)

        # LEFT PANE: Machine Roster
        left_frame = tk.Frame(main_pane, bg="#111C38", relief="flat", padx=10, pady=10)
        main_pane.add(left_frame, minsize=380, width=440)

        # Roster Header & Buttons
        roster_top = tk.Frame(left_frame, bg="#111C38")
        roster_top.pack(fill="x", pady=(0, 8))

        tk.Label(roster_top, text="MACHINES ROSTER",
                 font=("Segoe UI", 10, "bold"), fg="#F8FAFC", bg="#111C38").pack(side="left")

        btn_box = tk.Frame(roster_top, bg="#111C38")
        btn_box.pack(side="right")

        tk.Button(btn_box, text="+ Add", bg="#059669", fg="#FFFFFF", font=("Segoe UI", 8, "bold"),
                  relief="flat", padx=6, pady=2, command=self.add_machine_dialog).pack(side="left", padx=2)
        tk.Button(btn_box, text="✏️ Edit", bg="#2563EB", fg="#FFFFFF", font=("Segoe UI", 8, "bold"),
                  relief="flat", padx=6, pady=2, command=self.edit_machine_dialog).pack(side="left", padx=2)
        tk.Button(btn_box, text="🗑️ Del", bg="#DC2626", fg="#FFFFFF", font=("Segoe UI", 8, "bold"),
                  relief="flat", padx=6, pady=2, command=self.delete_machine_action).pack(side="left", padx=2)

        # Treeview for Machines
        tree_frame = tk.Frame(left_frame, bg="#111C38")
        tree_frame.pack(fill="both", expand=True)

        cols = ("status", "name", "host", "latency")
        self.tree = ttk.Treeview(tree_frame, columns=cols, show="headings", selectmode="browse")
        self.tree.heading("status", text="Status")
        self.tree.heading("name", text="Machine Name")
        self.tree.heading("host", text="Host / Tailscale IP")
        self.tree.heading("latency", text="Ping")

        self.tree.column("status", width=65, anchor="center")
        self.tree.column("name", width=160, anchor="w")
        self.tree.column("host", width=125, anchor="w")
        self.tree.column("latency", width=60, anchor="center")

        tree_scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=tree_scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        tree_scroll.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self.on_machine_selected)

        # Refresh button under tree
        refresh_bar = tk.Frame(left_frame, bg="#111C38", pady=6)
        refresh_bar.pack(fill="x")
        tk.Button(refresh_bar, text="🔄 Refresh All Pings", bg="#334155", fg="#F8FAFC",
                  font=("Segoe UI", 8), relief="flat", command=self.trigger_all_pings).pack(side="left")

        # RIGHT PANE: Machine Controls & Log Console
        right_frame = tk.Frame(main_pane, bg="#0B132B")
        main_pane.add(right_frame, minsize=520)

        # Details Header Card
        self.detail_card = tk.Frame(right_frame, bg="#111C38", padx=16, pady=12, relief="flat")
        self.detail_card.pack(fill="x", pady=(0, 10))

        self.lbl_selected_title = tk.Label(self.detail_card, text="No Machine Selected",
                                           font=("Segoe UI", 12, "bold"), fg="#F8FAFC", bg="#111C38")
        self.lbl_selected_title.pack(anchor="w")

        self.lbl_selected_meta = tk.Label(self.detail_card, text="Select a machine from the left roster to perform maintenance.",
                                          font=("Segoe UI", 9), fg="#94A3B8", bg="#111C38")
        self.lbl_selected_meta.pack(anchor="w", pady=(2, 0))

        # Command Action Buttons Grid
        action_grid = tk.LabelFrame(right_frame, text=" Remote Operations & Diagnostics ",
                                    bg="#111C38", fg="#38BDF8", font=("Segoe UI", 9, "bold"),
                                    padx=12, pady=10)
        action_grid.pack(fill="x", pady=(0, 10))

        # Row 1: Access & Shell
        r1 = tk.Frame(action_grid, bg="#111C38")
        r1.pack(fill="x", pady=3)
        tk.Button(r1, text="🌐 Open Web Admin (Auto-Login)", bg="#2563EB", fg="#FFFFFF",
                  font=("Segoe UI", 9, "bold"), padx=10, pady=4, relief="flat",
                  command=self.open_web_admin_action).pack(side="left", padx=3)
        tk.Button(r1, text="💻 Open SSH Terminal", bg="#0284C7", fg="#FFFFFF",
                  font=("Segoe UI", 9, "bold"), padx=10, pady=4, relief="flat",
                  command=self.open_ssh_terminal_action).pack(side="left", padx=3)
        tk.Button(r1, text="💾 Pull DB Backup", bg="#059669", fg="#FFFFFF",
                  font=("Segoe UI", 9, "bold"), padx=10, pady=4, relief="flat",
                  command=self.pull_db_backup_action).pack(side="left", padx=3)

        # Row 2: Maintenance & Recoveries
        r2 = tk.Frame(action_grid, bg="#111C38")
        r2.pack(fill="x", pady=3)
        tk.Button(r2, text="🚀 Push Host Update (SFTP Deploy)", bg="#7C3AED", fg="#FFFFFF",
                  font=("Segoe UI", 9, "bold"), padx=10, pady=4, relief="flat",
                  command=self.push_host_update_action).pack(side="left", padx=3)
        tk.Button(r2, text="⚡ Flash ESP32 (.bin)", bg="#D97706", fg="#FFFFFF",
                  font=("Segoe UI", 9, "bold"), padx=10, pady=4, relief="flat",
                  command=self.flash_esp32_action).pack(side="left", padx=3)
        tk.Button(r2, text="🔌 ESP32 GPIO Reset", bg="#B45309", fg="#FFFFFF",
                  font=("Segoe UI", 9, "bold"), padx=10, pady=4, relief="flat",
                  command=self.esp32_gpio_reset_action).pack(side="left", padx=3)

        # Row 3: Admin & Licensing
        r3 = tk.Frame(action_grid, bg="#111C38")
        r3.pack(fill="x", pady=3)
        tk.Button(r3, text="🔄 Reboot Machine", bg="#DC2626", fg="#FFFFFF",
                  font=("Segoe UI", 8, "bold"), padx=8, pady=3, relief="flat",
                  command=self.reboot_machine_action).pack(side="left", padx=3)
        tk.Button(r3, text="🔑 Reset Owner Password", bg="#991B1B", fg="#FFFFFF",
                  font=("Segoe UI", 8, "bold"), padx=8, pady=3, relief="flat",
                  command=self.reset_owner_password_action).pack(side="left", padx=3)
        tk.Button(r3, text="📜 Stream Live Journal Logs", bg="#334155", fg="#FFFFFF",
                  font=("Segoe UI", 8, "bold"), padx=8, pady=3, relief="flat",
                  command=self.toggle_live_logs_action).pack(side="left", padx=3)
        tk.Button(r3, text="🗝️ Issue License Key", bg="#0D9488", fg="#FFFFFF",
                  font=("Segoe UI", 8, "bold"), padx=8, pady=3, relief="flat",
                  command=self.issue_license_action).pack(side="left", padx=3)

        # Embedded Console Output / Log Stream Box
        console_frame = tk.LabelFrame(right_frame, text=" Live Terminal & Deployment Console ",
                                      bg="#111C38", fg="#38BDF8", font=("Segoe UI", 9, "bold"),
                                      padx=10, pady=8)
        console_frame.pack(fill="both", expand=True)

        self.txt_console = tk.Text(console_frame, bg="#050B14", fg="#A7F3D0",
                                   font=("Consolas", 9), insertbackground="#FFFFFF",
                                   relief="flat", wrap="word")
        console_scroll = ttk.Scrollbar(console_frame, orient="vertical", command=self.txt_console.yview)
        self.txt_console.configure(yscrollcommand=console_scroll.set)
        self.txt_console.pack(side="left", fill="both", expand=True)
        console_scroll.pack(side="right", fill="y")

        self.log_console("System initialized. EcoVendo Fleet Manager ready.\n")
        self.refresh_roster_table()

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
            self.lbl_dev_creds.configure(
                text="User: devclard  |  Pass: {}  ({}s left)".format(passcode, sec_left)
            )
            self.root.after(1000, update)
        update()

    def get_current_dev_password(self):
        return "dev{:02d}".format(datetime.datetime.now().minute)

    # -------------------------------------------------------------
    # Console Logger
    # -------------------------------------------------------------
    def log_console(self, text, color=None):
        def append():
            self.txt_console.insert("end", text)
            self.txt_console.see("end")
        self.root.after(0, append)

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
            time.sleep(8)

    def _ping_host(self, host):
        cmd = ["ping", "-n", "1", "-w", "1200", host] if sys.platform.startswith("win") else ["ping", "-c", "1", "-W", "1", host]
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
        threading.Thread(target=lambda: [self._ping_host(m['host']) for m in self.machines], daemon=True).start()
        self.log_console("[PING] Scanning all machine nodes in background...\n")

    def update_tree_row(self, m_id):
        m = next((item for item in self.machines if item["id"] == m_id), None)
        if not m:
            return
        status_info = self.ping_cache.get(m['host'], {"online": False, "ms": None})
        status_icon = "🟢 UP" if status_info['online'] else "🔴 DOWN"
        latency_str = "{} ms".format(status_info['ms']) if status_info['online'] else "Timeout"

        for item in self.tree.get_children():
            if self.tree.item(item, "tags") == (m_id,):
                self.tree.item(item, values=(status_icon, m['name'], m['host'], latency_str))
                return

    def refresh_roster_table(self):
        self.tree.delete(*self.tree.get_children())
        for m in self.machines:
            status_info = self.ping_cache.get(m['host'], {"online": False, "ms": None})
            status_icon = "🟢 UP" if status_info['online'] else "🔴 DOWN"
            latency_str = "{} ms".format(status_info['ms']) if status_info['online'] else "..."
            self.tree.insert("", "end", tags=(m['id'],),
                             values=(status_icon, m['name'], m['host'], latency_str))

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
            m = self.selected_machine
            st = self.ping_cache.get(m['host'], {}).get('online', False)
            status_text = "🟢 ONLINE" if st else "🔴 OFFLINE"
            self.lbl_selected_title.configure(text="{}  [{}]".format(m['name'], status_text))
            self.lbl_selected_meta.configure(
                text="Host: {} | Location: {} | Notes: {}".format(
                    m['host'], m.get('location', 'N/A'), m.get('notes', 'None')
                )
            )

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
        dlg.geometry("450x420")
        dlg.configure(bg="#0B132B")
        dlg.transient(self.root)
        dlg.grab_set()

        form = tk.Frame(dlg, bg="#0B132B", padx=20, pady=15)
        form.pack(fill="both", expand=True)

        fields = [
            ("Machine Name:", "name", machine['name'] if machine else "Vendo #02 (Location)"),
            ("Host / Tailscale IP:", "host", machine['host'] if machine else "100.x.y.z"),
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
                         insertbackground="#FFFFFF", relief="flat")
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
            self.refresh_roster_table()
            dlg.destroy()

        btn_bar = tk.Frame(dlg, bg="#0B132B", pady=10)
        btn_bar.pack(fill="x")
        tk.Button(btn_bar, text="Save Machine", bg="#10B981", fg="#FFFFFF",
                  font=("Segoe UI", 9, "bold"), padx=15, pady=4, relief="flat", command=save).pack(side="right", padx=20)
        tk.Button(btn_bar, text="Cancel", bg="#334155", fg="#FFFFFF",
                  font=("Segoe UI", 9), padx=10, pady=4, relief="flat", command=dlg.destroy).pack(side="right")

    def delete_machine_action(self):
        if not self.selected_machine:
            messagebox.showinfo("Select Machine", "Please select a machine to delete.")
            return
        m = self.selected_machine
        if messagebox.askyesno("Delete Machine", "Remove '{}' from fleet roster?".format(m['name'])):
            self.machines = [item for item in self.machines if item["id"] != m["id"]]
            self.selected_machine = None
            self.save_fleet()
            self.refresh_roster_table()
            self.lbl_selected_title.configure(text="No Machine Selected")
            self.lbl_selected_meta.configure(text="Select a machine from the left roster.")

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
        # Open auto-login bypass endpoint
        url = "http://{}/admin/dev_auth?token={}".format(m['host'], passcode)
        self.log_console("[WEB] Launching browser: {} (Auto-authenticating as devclard)\n".format(url))
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
        self.log_console("[SSH] Launching terminal: {}\n".format(ssh_cmd))

        # Try Windows Terminal, fallback to PowerShell
        try:
            subprocess.Popen(["wt.exe", "new-tab", "--title", m['name'], "powershell", "-NoExit", "-Command", ssh_cmd])
        except Exception:
            try:
                subprocess.Popen(["cmd.exe", "/c", "start", "powershell", "-NoExit", "-Command", ssh_cmd])
            except Exception as e:
                messagebox.showerror("SSH Error", "Could not launch terminal: " + str(e))

    def esp32_gpio_reset_action(self):
        """Remotely pulse GPIO 1 (PA01) LOW -> HIGH to hardware-reset ESP32."""
        m = self._require_selected()
        if not m:
            return
        if not messagebox.askyesno("ESP32 Reset", "Trigger hardware GPIO pulse on EN reset line for '{}'?".format(m['name'])):
            return

        def task():
            self.log_console("\n[ESP32] Connecting to {} via SSH to pulse reset line...\n".format(m['host']))
            try:
                client = self.get_ssh_client(m)
                # Export and pulse PA01 (GPIO 1)
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
                self.log_console("[ESP32] SUCCESS: Hardware pulse delivered to EN reset line!\n")
                messagebox.showinfo("Reset Sent", "ESP32 hardware pulse delivered successfully!")
            except Exception as e:
                self.log_console("[ESP32] ERROR: {}\n".format(str(e)))
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
            self.log_console("\n[REBOOT] Sending reboot command to {}...\n".format(m['host']))
            try:
                client = self.get_ssh_client(m)
                client.exec_command("sync; reboot", timeout=5)
                client.close()
                self.log_console("[REBOOT] SUCCESS: System reboot initiated.\n")
                messagebox.showinfo("Rebooting", "Reboot command sent to " + m['name'])
            except Exception as e:
                self.log_console("[REBOOT] {}\n".format(str(e)))

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
            self.log_console("\n[AUTH] Resetting admin password on {}...\n".format(m['host']))
            try:
                client = self.get_ssh_client(m)
                # Generate standard hash for admin1234
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
                    self.log_console("[AUTH] SUCCESS: Admin password restored to default 'admin1234'.\n")
                    messagebox.showinfo("Password Reset", "Admin password successfully reset to 'admin1234'!")
                else:
                    self.log_console("[AUTH] Output: {}\n".format(out))
            except Exception as e:
                self.log_console("[AUTH] ERROR: {}\n".format(str(e)))
                messagebox.showerror("Reset Failed", "Could not reset password: " + str(e))

        threading.Thread(target=task, daemon=True).start()

    def pull_db_backup_action(self):
        """Download remote SQLite database to local backups folder."""
        m = self._require_selected()
        if not m:
            return

        def task():
            self.log_console("\n[BACKUP] Pulling vendo_sessions.db from {}...\n".format(m['host']))
            try:
                client = self.get_ssh_client(m)
                sftp = client.open_sftp()
                backup_dir = ROOT / 'backups'
                backup_dir.mkdir(parents=True, exist_ok=True)
                ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                local_file = backup_dir / "{}_{}.db".format(m['id'], ts)

                sftp.get('/opt/ecofi/vendo_sessions.db', str(local_file))
                sftp.close()
                client.close()

                size_kb = round(local_file.stat().st_size / 1024, 1)
                self.log_console("[BACKUP] SUCCESS: Saved to {} ({} KB)\n".format(local_file.name, size_kb))
                messagebox.showinfo("Backup Downloaded", "Database backup saved to:\n{}".format(local_file))
            except Exception as e:
                self.log_console("[BACKUP] ERROR: {}\n".format(str(e)))
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
            self.log_console("\n[DEPLOY] Starting OTA host deployment to {}...\n".format(m['host']))
            host_dir = ROOT / 'host'
            files_to_deploy = [
                'portal.py', 'time_portal.py', 'esp32_simulator.py',
                'gateway_network.py', 'license_manager.py', 'status_led.py',
                'time_policy.py', 'time_schema.py', 'transition_engine.py',
                'VERSION'
            ]
            try:
                client = self.get_ssh_client(m, timeout=12)
                # Create remote rollback directory
                ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
                b_dir = "/opt/ecofi_backups/backup_" + ts
                client.exec_command("mkdir -p " + b_dir)

                sftp = client.open_sftp()
                for fname in files_to_deploy:
                    loc = host_dir / fname
                    if loc.exists():
                        rem = "/opt/ecofi/" + fname
                        self.log_console("  Uploading {} ({} bytes)...\n".format(fname, loc.stat().st_size))
                        sftp.put(str(loc), rem)
                sftp.close()

                self.log_console("  Restarting ecofi_portal.service...\n")
                _, stdout, _ = client.exec_command("systemctl restart ecofi_portal.service && systemctl is-active ecofi_portal.service")
                status = stdout.read().decode().strip()
                client.close()

                if status == "active":
                    self.log_console("[DEPLOY] SUCCESS: Remote service active and verified healthy!\n")
                    messagebox.showinfo("Deploy Complete", "Host files deployed successfully! ecofi_portal is active.")
                else:
                    self.log_console("[DEPLOY] WARNING: Service returned status: {}\n".format(status))
                    messagebox.showwarning("Deploy Warning", "Service status: " + status)
            except Exception as e:
                self.log_console("[DEPLOY] ERROR: {}\n".format(str(e)))
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
            self.log_console("\n[FLASH] Uploading {} to {}...\n".format(bin_file.name, m['host']))
            try:
                client = self.get_ssh_client(m, timeout=15)
                sftp = client.open_sftp()
                sftp.put(str(bin_file), "/tmp/esp32_remote_firmware.bin")
                sftp.close()

                self.log_console("[FLASH] Stopping portal service & invoking esptool flasher on /dev/ttyS3...\n")
                flash_cmd = (
                    "systemctl stop ecofi_portal.service && "
                    "esptool.py --chip esp32 --port /dev/ttyS3 --baud 115200 --before default_reset --after hard_reset "
                    "write_flash -z --flash_mode dio --flash_freq 40m --flash_size detect 0x10000 /tmp/esp32_remote_firmware.bin && "
                    "systemctl start ecofi_portal.service"
                )
                stdin, stdout, stderr = client.exec_command(flash_cmd, timeout=90)
                for line in iter(stdout.readline, ""):
                    self.log_console("  " + line)
                err_out = stderr.read().decode()
                exit_code = stdout.channel.recv_exit_status()
                client.close()

                if exit_code == 0:
                    self.log_console("[FLASH] SUCCESS: Custom firmware written, verified, and ESP32 rebooted!\n")
                    messagebox.showinfo("Flash Success", "ESP32 firmware successfully written and verified!")
                else:
                    self.log_console("[FLASH] ERROR (Code {}): {}\n".format(exit_code, err_out))
                    messagebox.showerror("Flash Error", "esptool failed. Check console log for details.")
            except Exception as e:
                self.log_console("[FLASH] ERROR: {}\n".format(str(e)))
                messagebox.showerror("Flash Exception", str(e))

        threading.Thread(target=task, daemon=True).start()

    def toggle_live_logs_action(self):
        """Stream or stop live journalctl logs from remote machine."""
        if self.log_stream_running:
            self.log_stream_running = False
            self.log_console("\n[LOGS] Live stream stopped.\n")
            return
        m = self._require_selected()
        if not m:
            return

        self.log_stream_running = True
        self.log_console("\n[LOGS] Streaming live journalctl logs from {} (Click button again to stop)...\n".format(m['host']))

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
                    self.log_console("[LOGS] Stream disconnected: {}\n".format(str(e)))
            self.log_stream_running = False

        t = threading.Thread(target=task, daemon=True)
        t.start()

    def issue_license_action(self):
        """Fetch HWID from remote machine, calculate activation key, and optionally push to machine."""
        m = self._require_selected()
        if not m:
            return

        dlg = tk.Toplevel(self.root)
        dlg.title("Issue License Key — " + m['name'])
        dlg.geometry("540x360")
        dlg.configure(bg="#0B132B")
        dlg.transient(self.root)
        dlg.grab_set()

        form = tk.Frame(dlg, bg="#0B132B", padx=20, pady=15)
        form.pack(fill="both", expand=True)

        tk.Label(form, text="Machine HWID:", font=("Segoe UI", 9, "bold"), fg="#38BDF8", bg="#0B132B").pack(anchor="w")
        e_hwid = tk.Entry(form, bg="#111C38", fg="#FCD34D", font=("Consolas", 10), insertbackground="#FFFFFF", relief="flat")
        e_hwid.pack(fill="x", pady=(2, 6))

        def fetch_hwid():
            try:
                client = self.get_ssh_client(m)
                stdin, stdout, stderr = client.exec_command("python3 -c 'import license_manager as lm; print(lm.get_machine_hwid())'")
                hwid = stdout.read().decode().strip()
                client.close()
                e_hwid.delete(0, "end")
                e_hwid.insert(0, hwid)
            except Exception as e:
                messagebox.showerror("HWID Fetch Failed", str(e), parent=dlg)

        tk.Button(form, text="🔍 Fetch HWID from Machine over SSH", bg="#334155", fg="#FFFFFF",
                  font=("Segoe UI", 8), relief="flat", command=fetch_hwid).pack(anchor="w", pady=(0, 10))

        tk.Label(form, text="License Tier:", font=("Segoe UI", 9, "bold"), fg="#38BDF8", bg="#0B132B").pack(anchor="w")
        tier_cb = ttk.Combobox(form, values=["COMMERCIAL", "ENTERPRISE", "TRIAL"], state="readonly")
        tier_cb.set("COMMERCIAL")
        tier_cb.pack(fill="x", pady=(2, 8))

        tk.Label(form, text="Generated Activation Key (PIN):", font=("Segoe UI", 9, "bold"), fg="#38BDF8", bg="#0B132B").pack(anchor="w")
        e_pin = tk.Entry(form, bg="#111C38", fg="#10B981", font=("Consolas", 11, "bold"), insertbackground="#FFFFFF", relief="flat")
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
            if not pin:
                messagebox.showerror("Error", "Please compute key first.", parent=dlg)
                return
            try:
                client = self.get_ssh_client(m)
                data = json.dumps({"hwid": hwid, "tier": tier_cb.get(), "activation_key": pin, "activated_at": int(time.time())})
                client.exec_command("echo '{}' > /opt/ecofi/license.key && systemctl restart ecofi_portal.service".format(data))
                client.close()
                messagebox.showinfo("Activated", "License successfully written to machine and service restarted!", parent=dlg)
                dlg.destroy()
            except Exception as e:
                messagebox.showerror("Activation Failed", str(e), parent=dlg)

        b_bar = tk.Frame(form, bg="#0B132B")
        b_bar.pack(fill="x", pady=6)
        tk.Button(b_bar, text="Generate Key", bg="#2563EB", fg="#FFFFFF", font=("Segoe UI", 9, "bold"),
                  relief="flat", padx=10, pady=3, command=compute_key).pack(side="left")
        tk.Button(b_bar, text="🚀 Push & Activate on Machine", bg="#059669", fg="#FFFFFF", font=("Segoe UI", 9, "bold"),
                  relief="flat", padx=10, pady=3, command=push_key).pack(side="right")


def main():
    root = tk.Tk()
    app = FleetManagerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
