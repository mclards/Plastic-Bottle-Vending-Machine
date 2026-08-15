# PisoFi Portal System: Deep Research & Analysis

Based on a deep dive into the **PisoFi** ecosystem (the leading software for coin-operated WiFi hotspots in the Philippines), here is a comprehensive breakdown of its architecture, user experience, and features.

This research can serve as a benchmark for enhancing our **Smart Eco-Fi Vendo System**, as both systems operate on the fundamental concept of exchanging physical deposits (coins vs. plastic bottles) for internet access.

---

## 1. Core Architecture: The Captive Portal
PisoFi relies on a **Captive Portal** routing mechanism. When a user connects to the machine's WiFi SSID (e.g., `PisoWiFi_Vendo`), the router intercepts their HTTP requests and redirects them to a local portal page (often `10.0.0.1`). 
- The user is held in a "walled garden" (no internet access) until they complete a transaction.
- Once the transaction is validated (coins inserted/voucher entered), the gateway firewall dynamically whitelists the user's MAC address for a specific duration or data quota.

## 2. End-User Interface (Captive Portal UI/UX)
The user-facing portal is designed to be extremely lightweight, mobile-first, and foolproof, as it is used by customers ranging from kids to the elderly.

### Standard Layout Elements
*   **Header & Branding:** Typically features a carousel banner (for local ads or instructions) and the WiFi network name.
*   **Real-time Status Display:** A prominent, large-font timer showing `Days : Hours : Mins : Secs` remaining.
*   **Action Buttons (High Contrast):**
    *   `INSERT COIN` (Triggers a modal dialog instructing the user to drop coins into the slot).
    *   `PAUSE TIME` (Allows users to freeze their internet time if they leave the vicinity).
    *   `RESUME TIME` (Unfreezes the time upon returning).
    *   `VOUCHER / WIPASS` (Alternative login using generated codes).
*   **Usability:** PisoFi portals rely heavily on **modals/popups** rather than navigating to new pages, keeping the user anchored to the timer dashboard.

## 3. Administrator Dashboard (Owner UX)
The admin panel is typically accessed via `/admin` and provides a comprehensive, data-rich environment for the machine owner.

### Key Management Tabs
1.  **Dashboard:** Live overview of active clients, CPU/RAM usage of the board (Raspberry Pi/Orange Pi), and a quick summary of daily sales.
2.  **Sales & Analytics:** Detailed logging of earnings, sortable by day/month, with graphical charts.
3.  **Portal Customization:** A built-in WYSIWYG editor or CSS injection panel allowing owners to change background images, button colors, and fonts without coding.
4.  **Timer & Rates:** Configurable rates (e.g., 1 Peso = 15 mins, 5 Pesos = 1 Hour, 10 Pesos = 3 Hours). *Our Eco-Fi system mirrors this logic with `1 Bottle = 15 mins`.*
5.  **Bandwidth Management:** Features to set global speed limits (e.g., 2Mbps per user) to prevent network hogging, and traffic shaping/QoS for gaming.
6.  **Voucher Generation:** Ability to generate and print static codes (WiPass) for users who want to buy bulk time without dropping coins.

## 4. Key Takeaways for "Smart Eco-Fi"

Comparing PisoFi to our current **Smart Eco-Fi Vendo**, we are already on the right track by utilizing a similar SPA (Single Page Application) modal-based Captive Portal and distinct control buttons (Pause/Resume/Insert). 

**Potential Features we could adapt from PisoFi:**
> [!TIP]
> **Voucher System:** If a user deposits 10 bottles but doesn't have time to use 2.5 hours of internet, we could generate a "Voucher Code" on the LCD screen that they can input into the portal later.

> [!NOTE]
> **Admin Analytics:** Implementing SQLite to track "Total Bottles Collected Today/This Month" and visualizing it on the Admin Dashboard with charts.

> [!TIP]
> **Speed Limiting:** If connected to a router running OpenWrt/MikroTik, our Python backend could theoretically push bandwidth limits per user to ensure fair internet distribution.

---

## 5. Sound & Audio Design Paradigm
Instead of relying on hardware speakers mounted on the vending machine cabinet, our Smart Eco-Fi system uses a personalized, software-driven approach.

### The Portal Audio Approach (Smartphone-Driven):
Because our design prioritizes a modern, non-intrusive user experience, all auditory feedback is streamed directly to the user's personal smartphone via the Captive Portal.

1. **Web Audio API Synthesis:** Rather than downloading heavy MP3 files over the local network (which consumes bandwidth and increases load times), the portal uses the browser's native Web Audio API to synthesize sounds on the fly.
2. **Low-Latency Feedback:** 
   - **Ticking Clock:** A synthetic blip plays every second during the 30-second drop window to convey urgency.
   - **Success Chime:** A pleasant dual-tone chord plays the exact millisecond the ESP32 successfully validates a plastic bottle, providing instant gratification.
   - **Error/Reject Buzz:** A harsh sawtooth wave alerts the user if an invalid item (metal, liquid, non-PET) is detected.
3. **Personalized UX:** This approach guarantees zero latency and ensures that the audio cues are personalized to the user's device, eliminating the need for loud, disruptive cabinet speakers in the physical environment.
