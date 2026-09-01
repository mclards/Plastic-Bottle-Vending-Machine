#!/usr/bin/env python3
"""
ECO-Fi Master Vendor License Generator GUI
Desktop App for Windows/Linux to generate machine-locked activation keys.
"""

import os
import sys
import json
import time
import tkinter as tk
from tkinter import ttk, messagebox

# Import license engine logic
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "host"))
from license_manager import compute_activation_pin

class LicenseGeneratorApp:
    def __init__(self, root):
        self.root = root
        self.root.title("ECO-Fi Vendo - Master License Key Generator")
        self.root.geometry("620x580")
        self.root.resizable(False, False)
        self.root.configure(bg="#0F172A")

        self.setup_ui()

    def setup_ui(self):
        # Header Banner
        header_frame = tk.Frame(self.root, bg="#1E293B", pady=15)
        header_frame.pack(fill="x")

        title_lbl = tk.Label(
            header_frame,
            text="ECO-Fi LICENSE GENERATOR",
            font=("Segoe UI", 16, "bold"),
            fg="#10B981",
            bg="#1E293B"
        )
        title_lbl.pack()

        subtitle_lbl = tk.Label(
            header_frame,
            text="Master Vendor Cryptographic Key & Certificate Issuer",
            font=("Segoe UI", 9, "italic"),
            fg="#94A3B8",
            bg="#1E293B"
        )
        subtitle_lbl.pack(pady=(2, 0))

        # Main Content Form
        form_frame = tk.Frame(self.root, bg="#0F172A", padx=30, pady=20)
        form_frame.pack(fill="both", expand=True)

        # 1. Machine Hardware ID (HWID) Input
        hwid_lbl = tk.Label(
            form_frame,
            text="1. Target Machine Hardware ID (HWID):",
            font=("Segoe UI", 10, "bold"),
            fg="#F8FAFC",
            bg="#0F172A"
        )
        hwid_lbl.pack(anchor="w", pady=(0, 4))

        self.hwid_entry = tk.Entry(
            form_frame,
            font=("Consolas", 12),
            bg="#1E293B",
            fg="#38BDF8",
            insertbackground="white",
            relief="flat",
            highlightthickness=1,
            highlightbackground="#334155",
            highlightcolor="#10B981"
        )
        self.hwid_entry.pack(fill="x", ipady=6, pady=(0, 15))
        self.hwid_entry.insert(0, "ECOFI-")

        # 2. Licensee / Client Name
        client_lbl = tk.Label(
            form_frame,
            text="2. Client / Establishment Name:",
            font=("Segoe UI", 10, "bold"),
            fg="#F8FAFC",
            bg="#0F172A"
        )
        client_lbl.pack(anchor="w", pady=(0, 4))

        self.client_entry = tk.Entry(
            form_frame,
            font=("Segoe UI", 11),
            bg="#1E293B",
            fg="#F8FAFC",
            insertbackground="white",
            relief="flat",
            highlightthickness=1,
            highlightbackground="#334155",
            highlightcolor="#10B981"
        )
        self.client_entry.pack(fill="x", ipady=6, pady=(0, 15))
        self.client_entry.insert(0, "Barangay / Store Owner")

        # 3. License Tier Selection
        tier_lbl = tk.Label(
            form_frame,
            text="3. Commercial License Tier:",
            font=("Segoe UI", 10, "bold"),
            fg="#F8FAFC",
            bg="#0F172A"
        )
        tier_lbl.pack(anchor="w", pady=(0, 4))

        self.tier_var = tk.StringVar(value="COMMERCIAL")
        tiers = [
            ("COMMERCIAL", "Commercial Standard (50 Users, Full Vendo Features)"),
            ("THESIS_STUDENT", "Student / Thesis Edition (10 Users + Debug Telemetry)"),
            ("ENTERPRISE_LGU", "LGU / Enterprise (100+ Users, Multi-Station Analytics)")
        ]

        for code, label in tiers:
            rb = tk.Radiobutton(
                form_frame,
                text=label,
                variable=self.tier_var,
                value=code,
                font=("Segoe UI", 9),
                fg="#CBD5E1",
                bg="#0F172A",
                activebackground="#0F172A",
                activeforeground="#10B981",
                selectcolor="#1E293B"
            )
            rb.pack(anchor="w", pady=2)

        # Generate Button
        btn_gen = tk.Button(
            form_frame,
            text="GENERATE ACTIVATION KEY",
            font=("Segoe UI", 11, "bold"),
            bg="#10B981",
            fg="#0F172A",
            activebackground="#059669",
            activeforeground="#FFFFFF",
            relief="flat",
            cursor="hand2",
            command=self.generate_key
        )
        btn_gen.pack(fill="x", pady=(20, 15), ipady=8)

        # Output Section
        out_frame = tk.Frame(form_frame, bg="#1E293B", padx=15, pady=12, highlightthickness=1, highlightbackground="#334155")
        out_frame.pack(fill="x")

        out_title = tk.Label(
            out_frame,
            text="GENERATED ACTIVATION PIN:",
            font=("Segoe UI", 9, "bold"),
            fg="#94A3B8",
            bg="#1E293B"
        )
        out_title.pack(anchor="w")

        self.pin_display = tk.Entry(
            out_frame,
            font=("Consolas", 15, "bold"),
            bg="#1E293B",
            fg="#F59E0B",
            relief="flat",
            readonlybackground="#1E293B",
            state="readonly",
            justify="center"
        )
        self.pin_display.pack(fill="x", pady=6)

        # Copy & Export Buttons
        btn_row = tk.Frame(out_frame, bg="#1E293B")
        btn_row.pack(fill="x", pady=(4, 0))

        btn_copy = tk.Button(
            btn_row,
            text="📋 Copy PIN to Clipboard",
            font=("Segoe UI", 9, "bold"),
            bg="#334155",
            fg="#F8FAFC",
            relief="flat",
            cursor="hand2",
            command=self.copy_pin
        )
        btn_copy.pack(side="left", fill="x", expand=True, padx=(0, 5), ipady=4)

        btn_save = tk.Button(
            btn_row,
            text="💾 Export .lic File",
            font=("Segoe UI", 9, "bold"),
            bg="#334155",
            fg="#F8FAFC",
            relief="flat",
            cursor="hand2",
            command=self.export_license_file
        )
        btn_save.pack(side="right", fill="x", expand=True, padx=(5, 0), ipady=4)

    def generate_key(self):
        hwid = self.hwid_entry.get().strip().upper()
        tier = self.tier_var.get()

        if not hwid or hwid == "ECOFI-":
            messagebox.showerror("Validation Error", "Please enter the Target Machine Hardware ID (HWID).")
            return

        pin = compute_activation_pin(hwid, tier)

        self.pin_display.config(state="normal")
        self.pin_display.delete(0, tk.END)
        self.pin_display.insert(0, pin)
        self.pin_display.config(state="readonly")

    def copy_pin(self):
        pin = self.pin_display.get()
        if not pin:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(pin)
        messagebox.showinfo("Copied", f"Activation PIN [{pin}] copied to clipboard!")

    def export_license_file(self):
        hwid = self.hwid_entry.get().strip().upper()
        pin = self.pin_display.get()
        client = self.client_entry.get().strip()
        tier = self.tier_var.get()

        if not pin:
            self.generate_key()
            pin = self.pin_display.get()

        if not pin:
            return

        lic_data = {
            "vendor": "ECO-Fi Technologies",
            "licensee": client,
            "machine_hwid": hwid,
            "tier": tier,
            "activation_key": pin,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "expiry_date": "PERPETUAL"
        }

        filename = f"license_{hwid}.lic"
        with open(filename, "w") as f:
            json.dump(lic_data, f, indent=4)

        messagebox.showinfo("Export Successful", f"License file saved as:\n{filename}\n\nYou can copy this directly to /opt/ecofi/license.key on the target machine.")

def main():
    root = tk.Tk()
    app = LicenseGeneratorApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()
