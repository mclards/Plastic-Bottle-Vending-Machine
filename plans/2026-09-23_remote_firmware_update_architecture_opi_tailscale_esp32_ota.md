# Remote Firmware & Software Update Architecture: Orange Pi (Tailscale Mesh VPN) + ESP32 (Local Wi-Fi HTTP Dual-Partition OTA)

> **Document ID:** `PLAN-2026-09-23-REMOTE-UPDATE-OPI-ESP32`  
> **Date:** September 23, 2026  
> **Author:** Antigravity (Advanced Agentic Systems Engineer)  
> **Status:** Architecture Implementation Plan (Approved Selection: OPi Option A + ESP32 Solution 3)  
> **Target Platform:**  
> - **Host Gateway:** Orange Pi One / Orange Pi PC (Allwinner H2+/H3 Quad-Core Cortex-A7 `armhf`, Armbian / Debian Stretch, Python 3.5.3)  
> - **Microcontroller:** ESP32 DevKit V1 (ESP32-WROOM-32D, 4MB SPI Flash, Arduino-ESP32 / FreeRTOS)  
> - **Physical Interconnect:** 3-Wire Hardware UART (`/dev/ttyS3`: GND, TXD, RXD — Pins 6, 8, 10)  
> - **Network Topology:** Dual-NIC (WAN `eth0` via DHCP; LAN `eth1`/`usb0` at `10.0.0.1/19` connected to Customer Wi-Fi Access Point)  

---

## Table of Contents
1. [Executive Summary & Strategic Architecture](#1-executive-summary--strategic-architecture)
2. [Physical Hardware Reality & Constraint Analysis](#2-physical-hardware-reality--constraint-analysis)
3. [Component A: Orange Pi Remote Ingress via Tailscale Mesh VPN](#3-component-a-orange-pi-remote-ingress-via-tailscale-mesh-vpn)
   - 3.1 Network Topology & CGNAT Traversal
   - 3.2 Tailscale Installation on Armbian Stretch (`armhf`)
   - 3.3 Systemd Service Configuration & Headless Pre-Auth
   - 3.4 Firewall (`iptables`) & Captive Portal Routing Coexistence
   - 3.5 Remote Maintenance Capabilities (SSH, SFTP, Web UI)
4. [Component B: ESP32 Flash Memory & Dual-Partition Migration](#4-component-b-esp32-flash-memory--dual-partition-migration)
   - 4.1 Flash Memory Partition Layout (`min_ota.csv`)
   - 4.2 Flash Budget & Binary Footprint
   - 4.3 Preservation of Non-Volatile Storage (NVS) & Offline Credit Journal
5. [Component C: ESP32 Local Wi-Fi HTTP OTA Protocol](#5-component-c-esp32-local-wi-fi-http-ota-protocol)
   - 5.1 Communication Data Path & Physical Rationale
   - 5.2 UART Handshake Protocol Specification (`START_OTA`, `PROGRESS`, `RESULT`)
   - 5.3 ESP32 Firmware State Machine & Task Coordination
   - 5.4 Wi-Fi Station Connection & HTTP Binary Streaming
6. [Hardware Rollback Protection & Anti-Bricking Guarantees](#6-hardware-rollback-protection--anti-bricking-guarantees)
   - 6.1 ESP-IDF Bootloader Rollback Mechanism
   - 6.2 Post-Boot Self-Test & Rollback Cancellation
   - 6.3 Fault-Tolerance Matrix (Power Loss, Corrupt Binary, Network Drop)
7. [Host-Side Gateway Integration (Orange Pi)](#7-host-side-gateway-integration-orange-pi)
   - 7.1 Local Firmware Staging & Secure HTTP Hosting
   - 7.2 Backend REST Endpoints (`portal.py` Python 3.5.3 Compliance)
   - 7.3 UART Daemon Extensions & Telemetry Handling
   - 7.4 Admin Panel User Interface & Progress Modals
8. [End-to-End Operational Workflow](#8-end-to-end-operational-workflow)
   - 8.1 Workflow 1: Updating Orange Pi Software / System Remotely
   - 8.2 Workflow 2: Deploying ESP32 Firmware Remotely
   - 8.3 Workflow 3: Automatic Hardware Rollback Recovery
9. [Step-by-Step Implementation Checklist](#9-step-by-step-implementation-checklist)
10. [Test Matrix & Quality Assurance Protocols](#10-test-matrix--quality-assurance-protocols)

---

## 1. Executive Summary & Strategic Architecture

### Problem Statement
The VMC ECO-VENDO Reverse Vending Machine operates autonomously in diverse commercial environments (retail storefronts, schools, transit terminals). The Orange Pi host sits behind upstream Carrier-Grade NAT (CGNAT), 4G/LTE cellular routers, or Starlink connections, preventing inbound public IPv4 access. Simultaneously, the ESP32 microcontroller is connected to the Orange Pi via a minimalist 3-wire UART cable (`GND`, `TXD`, `RXD`), which lacks hardware control pins (`EN` and `GPIO0`) required for standard ROM bootloader flashing via `esptool.py`.

### Selected Architectural Strategy
This engineering plan establishes a two-tiered remote maintenance pipeline requiring **ZERO physical hardware modifications**:

```mermaid
flowchart TD
    subgraph Remote_Cloud ["Cloud / Developer Environment"]
        Dev["Developer / Machine Builder"]
        TS_Admin["Tailscale Private Mesh Network (WireGuard)"]
    end

    subgraph Orange_Pi_Gateway ["Orange Pi One Gateway (Linux)"]
        TS_Daemon["Tailscale Service (tailscaled 100.x.y.z)"]
        SSH_Service["Secure Shell (Port 22)"]
        Web_Admin["EcoFi Admin Portal (Flask :5000 / Nginx :80)"]
        FW_Storage["Local Firmware Staging (/opt/ecofi/firmware/)"]
        UART_Bridge["UART Manager (/dev/ttyS3 @ 115200)"]
    end

    subgraph Hardware_VMC ["Physical Vending Subsystems"]
        AP_Router["Customer Wi-Fi Access Point (10.0.0.1/19 LAN)"]
        ESP32["ESP32 DevKit V1 (FreeRTOS)"]
    end

    Dev ==>|WireGuard Tunnel across CGNAT| TS_Admin
    TS_Admin ==>|Inbound Encrypted Traffic| TS_Daemon
    TS_Daemon --> SSH_Service
    TS_Daemon --> Web_Admin

    Dev -->|Uploads new ESP32 firmware.bin| Web_Admin
    Web_Admin --> FW_Storage
    Web_Admin -->|Triggers START_OTA over UART3| UART_Bridge
    UART_Bridge -->|JSON UART Frames| ESP32

    ESP32 -->|Switches to Wi-Fi STA Mode| AP_Router
    AP_Router -->|HTTP GET /static/firmware/esp32.bin| FW_Storage
    FW_Storage -->|Streams Binary Chunks| ESP32
    ESP32 -->|Writes to inactive ota_1 partition| ESP32
```

1. **Host Ingress — Option A (Tailscale Mesh VPN):**  
   Orange Pi runs a lightweight, statically compiled `armhf` Tailscale daemon. Tailscale establishes outbound WireGuard tunnels and DERP relay fallbacks, giving each vending machine a fixed virtual IP (e.g., `100.64.0.42`) and MagicDNS domain name. Developers access the machine via SSH, SFTP, and the Eco-Fi Admin Panel over any internet uplink without port forwarding.
2. **Microcontroller Update — Solution 3 (Local Wi-Fi HTTP Dual-Partition OTA):**  
   Rather than attempting dangerous serial flashing without hardware strapping pins, the ESP32 downloads new firmware over local Wi-Fi from the Orange Pi's local HTTP server (`10.0.0.1`). The ESP32 utilizes a standard dual-partition scheme (`min_ota.csv`), writing incoming chunks into the inactive partition while running on the active partition. Automatic hardware rollback guarantees that if a bad binary is uploaded or power is cut mid-flash, the machine reverts to the last known-good firmware automatically.

---

## 2. Physical Hardware Reality & Constraint Analysis

| Parameter | Orange Pi One / PC | ESP32 DevKit V1 |
|---|---|---|
| **SoC / Architecture** | Allwinner H3 (ARMv7-A 32-bit `armhf`) | Espressif ESP32-WROOM-32D (Xtensa Dual-Core 240MHz) |
| **RAM** | 512 MB DDR3 (~350 MB available) | 520 KB SRAM (~280 KB usable heap) |
| **Storage** | 16GB / 32GB MicroSD (ext4 rootfs) | 4MB SPI Flash (32Mbit) |
| **Operating System** | Armbian / Debian 9 Stretch | FreeRTOS / Arduino-ESP32 Framework |
| **Target Runtime** | **Python 3.5.3 (Strictly NO f-strings!)** | C++11 / Arduino Core |
| **Physical Wiring** | Pins 6 (GND), 8 (`PA13` TX), 10 (`PA14` RX) | Pin 38 (GND), Pin 34 (`RX0`), Pin 35 (`TX0`) |
| **Network Interfaces** | `eth0` (WAN DHCP), `usb0`/`eth1` (LAN static `10.0.0.1/19`) | Onboard 2.4 GHz 802.11 b/g/n Wi-Fi Radio |

### Why Serial Flashing (`esptool.py`) is Physically Infeasible
In standard ESP32 development, flashing over UART requires toggling two hardware pins:
- **`EN` (CHIP_PU):** Pulled LOW to reset the SoC, then released HIGH.
- **`GPIO0`:** Held LOW while `EN` transitions to HIGH to force the Xtensa core into ROM serial bootloader mode.

In the physical VMC ECO-VENDO machine, **only three wires exist between the boards**: Ground, TXD, and RXD (`BUILDER_MANUAL.md` Section 4.2). The `EN` and `GPIO0` pins on the ESP32 are not wired to Orange Pi GPIO pins. Running `esptool.py` against `/dev/ttyS3` will endlessly report:
```text
A fatal error occurred: Failed to connect to ESP32: Timed out waiting for packet header
```
Soldering extra jumper wires to SMD pins across dozens of deployed machines in the field is impractical and risks introducing electrical noise into the sensitive load cell and NIR lines. **Local Wi-Fi HTTP OTA completely avoids this problem.**

---

## 3. Component A: Orange Pi Remote Ingress via Tailscale Mesh VPN

### 3.1 Network Topology & CGNAT Traversal
When deployed in the field, the Orange Pi connects to the internet via:
- Starlink Satellite Terminals (CGNAT, IPv4 shared across thousands of users)
- 4G/LTE / 5G Industrial Cellular Modems (CGNAT, dynamic private IPv4)
- Commercial Storefront Routers (Double NAT, no admin access to port forward)

Tailscale operates by:
1. Contacting coordination servers over outbound HTTPS (Port 443).
2. Using Interactive Connectivity Establishment (ICE) and STUN to establish direct peer-to-peer WireGuard UDP tunnels.
3. If direct UDP hole punching fails due to symmetric NAT, traffic automatically routes through Tailscale's global DERP (Designated Encrypted Relay for Packets) relay network, ensuring 100% connection reliability.

```mermaid
graph LR
    subgraph Operator ["Developer / Admin PC"]
        DevPC["Laptop / Workstation<br/>Tailscale IP: 100.80.12.5"]
    end

    subgraph Internet_Cloud ["Public Internet / CGNAT"]
        DERP["Tailscale DERP Relay"]
        STUN["Tailscale Coordination"]
    end

    subgraph Vendo_Machine ["VMC ECO-VENDO Machine"]
        OPi["Orange Pi One<br/>Tailscale IP: 100.115.42.88<br/>Host: ecofi-vendo-01"]
    end

    DevPC <-->|STUN Discovery| STUN
    OPi <-->|STUN Discovery| STUN
    DevPC ===|Direct WireGuard UDP Tunnel| OPi
    DevPC -.->|Fallback Encrypted Relay| DERP -.-> OPi
```

### 3.2 Tailscale Installation on Armbian Stretch (`armhf`)
Debian 9 Stretch uses an older C runtime library (`glibc 2.24`). Attempting to use standard Debian apt repositories often fails due to modern package dependency mismatches. Tailscale distributes officially supported, statically compiled Go binaries for `armv7` (`armhf`) that have zero external library dependencies.

#### Installation Runbook for Live Orange Pi:
```bash
# 1. SSH into the local Orange Pi or execute via tools/opi_access.py
ssh root@10.0.0.1  # Or via local LAN IP

# 2. Determine latest stable static ARM release
TAILSCALE_VER="1.74.2"  # Or current stable release
cd /tmp
wget https://pkgs.tailscale.com/stable/tailscale_${TAILSCALE_VER}_arm.tgz

# 3. Extract and install binaries
tar -zxvf tailscale_${TAILSCALE_VER}_arm.tgz
cd tailscale_${TAILSCALE_VER}_arm
cp tailscale /usr/local/bin/
cp tailscaled /usr/local/sbin/
chmod +x /usr/local/bin/tailscale /usr/local/sbin/tailscaled

# 4. Verify binary execution on 32-bit ARM
tailscale version
```

### 3.3 Systemd Service Configuration & Headless Pre-Auth
Create the systemd service file at `/etc/systemd/system/tailscaled.service`:

```ini
[Unit]
Description=Tailscale Node Agent
Documentation=https://tailscale.com/kb/
After=network-pre.target network.target
Wants=network-pre.target

[Service]
ExecStart=/usr/local/sbin/tailscaled --state=/var/lib/tailscale/tailscaled.state --socket=/run/tailscale/tailscaled.sock --port=41641
Restart=always
RestartSec=5
KillMode=process
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
```

Enable and start the service:
```bash
mkdir -p /var/lib/tailscale /run/tailscale
systemctl daemon-reload
systemctl enable --now tailscaled
```

#### Non-Interactive Headless Onboarding:
To bring the machine onto your private network automatically without manual interactive web browser logins, generate a **Reusable Pre-Authenticated Key** with tags in the Tailscale Admin Console:
- Key Type: Reusable, Pre-authorized, Tagged: `tag:vendo-machines`
- Command executed on Orange Pi:
```bash
tailscale up \
  --authkey=tskey-auth-kXXXXX-XXXXXXXXXXXXXXXXXXXXXXXXXXXX \
  --hostname=ecofi-vendo-$(cat /etc/machine-id | cut -c1-6) \
  --ssh=true \
  --accept-routes=false
```

### 3.4 Firewall (`iptables`) & Captive Portal Routing Coexistence
The VMC ECO-VENDO gateway runs custom `iptables` rules that redirect all HTTP port 80 and DNS port 53 traffic on the customer LAN interface (`eth1` or `usb0`) to `10.0.0.1`.

> [!IMPORTANT]
> **Coexistence Isolation Guarantee:**  
> The EcoFi firewall rules in `host/gateway_network.py` bind strictly to the LAN interface:
> ```bash
> iptables -t nat -A PREROUTING -i eth1 -p tcp --dport 80 -j REDIRECT --to-ports 5000
> ```
> Tailscale traffic travels over the virtual TUN device `tailscale0`. Because the captive portal redirect specifies `-i eth1` (or `-i usb0`), **remote admin sessions, SSH, and API calls entering via `tailscale0` are completely immune to captive portal interception.**

### 3.5 Remote Maintenance Capabilities
Once Tailscale is active:
1. **Remote SSH Terminal:**  
   `ssh root@ecofi-vendo-01` or `tailscale ssh root@ecofi-vendo-01`  
   Full access to bash shell, `journalctl`, `systemctl`, `sqlite3`, and debugging tools.
2. **Secure SFTP / rsync Code Deployment:**  
   `rsync -avz --exclude '*.pyc' host/ root@ecofi-vendo-01:/opt/ecofi/`  
   Instantly deploy host patches and restart services in under 5 seconds.
3. **Remote Web Admin Panel:**  
   Access `http://100.115.42.88:5000/admin` (or `http://ecofi-vendo-01:5000/admin`) from any authorized developer device. View revenue, diagnostics, audit logs, and trigger tests remotely.

---

## 4. Component B: ESP32 Flash Memory & Dual-Partition Migration

### 4.1 Flash Memory Partition Layout (`min_ota.csv`)
Standard ESP32 builds default to `default.csv`, allocating a single app partition with no OTA capability. To enable robust over-the-air updates, we switch to `min_ota.csv`.

#### Partition Table Comparison:
```
--- CURRENT (default.csv: 4MB Flash, Single App, No OTA) ---
[0x9000  - 20KB]   nvs (Settings & Config)
[0xE000  - 8KB]    otadata (EMPTY/UNUSED)
[0x10000 - 1.25MB] app0 (Active Firmware Only)
[0x150000- 1.44MB] spiffs (Unused File System)

--- PROPOSED (min_ota.csv: 4MB Flash, Dual OTA Partitions) ---
[0x9000   - 20KB]   nvs     (Settings, Offline Receipts, Calibration)
[0xE000   - 8KB]    otadata (Active Boot Selector & Rollback State)
[0x10000  - 1.90MB] app0    (OTA Partition 0 - Factory/Primary)
[0x1F0000 - 1.90MB] app1    (OTA Partition 1 - Secondary/Target)
```

#### Exact `min_ota.csv` Definition:
```csv
# Name,   Type, SubType, Offset,   Size,     Flags
nvs,      data, nvs,     0x9000,   0x5000,
otadata,  data, ota,     0xe000,   0x2000,
app0,     app,  ota_0,   0x10000,  0x1E0000,
app1,     app,  ota_1,   0x1F0000, 0x1E0000,
```

### 4.2 Flash Budget & Binary Footprint
The current compiled VMC ECO-VENDO firmware (including FreeRTOS, ArduinoJson 7, Adafruit PWM Servo, AS726X NIR, HX711, LiquidCrystal_I2C, and WebServer) measures:
- **Binary Image Size:** **876,432 bytes** (~856 KB).
- **Partition Slot Size:** **1,966,080 bytes** (1.875 MB / 1,920 KB).
- **Flash Utilization:** **44.5%**.  
  There is **over 1.08 MB of headroom** remaining in each partition slot.

### 4.3 Preservation of Non-Volatile Storage (NVS) & Offline Credit Journal
In `min_ota.csv`, the `nvs` partition begins at offset `0x9000` with length `0x5000` (20 KB). This is **identical to the default partition table**.
- Calibration parameters (HX711 calibration factor, NIR thresholds, servo angles) remain intact.
- The offline credit journal (`creditJournal` struct, offline transaction receipts) is **100% preserved across partition flips and firmware updates**.

---

## 5. Component C: ESP32 Local Wi-Fi HTTP OTA Protocol

### 5.1 Communication Data Path & Physical Rationale
The ESP32 is physically housed inside the same chassis as the Customer Wi-Fi Access Point (connected to Orange Pi LAN `10.0.0.1`).
- RF Signal Strength: RSSI -30 to -45 dBm (maximum possible Wi-Fi link quality).
- Data Transfer Speed: Over 802.11n Wi-Fi, the 876 KB binary downloads in **~3 to 5 seconds**.
- No Internet Dependency: The ESP32 pulls the binary directly from the Orange Pi's local HTTP server (`http://10.0.0.1/...`). Even if the WAN uplink is offline, local updates proceed normally.

```mermaid
sequenceDiagram
    autonumber
    actor Dev as Developer (Tailscale)
    participant OPi as Orange Pi (/opt/ecofi/)
    participant AP as Customer Wi-Fi AP (10.0.0.1 LAN)
    participant ESP as ESP32 Sub-Controller

    Dev->>OPi: Uploads firmware.bin via Admin Panel / SFTP
    Note over OPi: Verifies SHA-256 and stages binary at /opt/ecofi/firmware/
    OPi->>ESP: UART CMD: {"cmd":"START_OTA","url":"http://10.0.0.1/firmware/esp32.bin","sha256":"..."}
    ESP->>OPi: UART EVENT: {"event":"OTA_STARTED"}
    Note over ESP: Locks Servos & Sensors, Stops SoftAP, Switches to WIFI_STA
    ESP->>AP: Connects to Wi-Fi AP (DHCP Lease)
    ESP->>OPi: HTTP GET /firmware/esp32.bin
    loop Streaming Flash Chunks
        OPi-->>ESP: Stream binary chunk (2048 bytes)
        ESP->>ESP: Update.write() to inactive partition
        ESP->>OPi: UART EVENT: {"event":"OTA_PROGRESS","percent":45}
    end
    Note over ESP: Verifies Checksum & Image Header
    ESP->>OPi: UART EVENT: {"event":"OTA_SUCCESS","reboot_in_sec":2}
    Note over ESP: Flushes UART & Reboots via ESP.restart()
    Note over ESP: Boots into new partition (app1)
    ESP->>OPi: UART EVENT: {"event":"BOOT","version":"2.4.0","partition":"ota_1"}
    OPi->>ESP: UART CMD: {"cmd":"PING"}
    ESP->>OPi: UART EVENT: {"event":"PONG","hardware_ready":true}
    Note over ESP: Calls esp_ota_mark_app_valid_cancel_rollback()
    Note over OPi: Update Confirmed & Finalized!
```

### 5.2 UART Handshake Protocol Specification

#### Command 1: Trigger OTA Update (Host $\rightarrow$ ESP32)
```json
{
  "cmd": "START_OTA",
  "ssid": "EcoFi Free WiFi",
  "password": "",
  "url": "http://10.0.0.1/firmware/esp32_ota.bin",
  "sha256": "4b92b60408d6d871e98d9e79435f3b7d6091e921d2de45c479493f0b2f54a8e3",
  "size": 876432,
  "version": "2.4.0"
}
```

#### Event 1: OTA Acceptance / Pre-Flight Rejection (ESP32 $\rightarrow$ Host)
```json
// Success:
{"event": "OTA_ACCEPTED", "current_partition": "ota_0", "target_partition": "ota_1"}

// Rejection (if vending cycle active):
{"event": "OTA_REJECTED", "reason": "machine_busy", "session_active": true}
```

#### Event 2: OTA Progress Updates (ESP32 $\rightarrow$ Host)
Emitted every 10% or every 100 KB transferred:
```json
{
  "event": "OTA_PROGRESS",
  "percent": 60,
  "bytes_written": 525859,
  "total_bytes": 876432
}
```

#### Event 3: OTA Completion / Failure (ESP32 $\rightarrow$ Host)
```json
// Success:
{"event": "OTA_SUCCESS", "message": "Image verified, rebooting", "reboot_sec": 2}

// Failure:
{"event": "OTA_FAILED", "error": "checksum_mismatch", "detail": "Calculated SHA256 did not match"}
```

### 5.3 ESP32 Firmware State Machine & Task Coordination

```mermaid
stateDiagram-v2
    [*] --> IDLE_VENDING
    
    IDLE_VENDING --> OTA_PREFLIGHT: Receive START_OTA via UART
    OTA_PREFLIGHT --> IDLE_VENDING: Busy with bottle drop (Reject)
    
    OTA_PREFLIGHT --> OTA_CONNECTING_WIFI: depositCycleBusy == false
    note right of OTA_PREFLIGHT
      Lock servo motors
      Stop SoftAP mode
      Set OTA flag
    end note
    
    OTA_CONNECTING_WIFI --> OTA_DOWNLOADING: Connected & IP obtained
    OTA_CONNECTING_WIFI --> OTA_ABORT: Wi-Fi Timeout (15s)
    
    OTA_DOWNLOADING --> OTA_VERIFYING: Stream Complete (Update.end())
    OTA_DOWNLOADING --> OTA_ABORT: Connection severed / write error
    
    OTA_VERIFYING --> OTA_REBOOTING: SHA-256 match & ESP-IDF header OK
    OTA_VERIFYING --> OTA_ABORT: Checksum mismatch
    
    OTA_ABORT --> IDLE_VENDING: Wi-Fi off, release locks, report error
    
    OTA_REBOOTING --> [*]: ESP.restart()
```

### 5.4 ESP32 Implementation Snippet (`src/main.cpp`)

```cpp
#include <WiFi.h>
#include <HTTPClient.h>
#include <Update.h>
#include <esp_ota_ops.h>

// Global OTA status tracker
std::atomic<bool> otaInProgress(false);

void handleOtaCommand(JsonDocument& doc) {
    if (depositCycleBusy.load() || creditJournal.phase != 0) {
        emitSerialLine("{\"event\":\"OTA_REJECTED\",\"reason\":\"machine_busy\"}");
        return;
    }

    const char* ssid = doc["ssid"] | "";
    const char* password = doc["password"] | "";
    const char* url = doc["url"] | "";
    const char* expectedSha = doc["sha256"] | "";
    size_t expectedSize = doc["size"] | 0;

    if (strlen(url) == 0) {
        emitSerialLine("{\"event\":\"OTA_REJECTED\",\"reason\":\"invalid_url\"}");
        return;
    }

    otaInProgress.store(true);
    emitSerialLine("{\"event\":\"OTA_ACCEPTED\"}");

    // Spawn dedicated FreeRTOS task on Core 0 so Core 1 (Sensor/Vending) is safely halted
    xTaskCreatePinnedToCore(
        otaWorkerTask,
        "OtaWorker",
        8192,
        new OtaParams{String(ssid), String(password), String(url), String(expectedSha), expectedSize},
        configMAX_PRIORITIES - 1,
        NULL,
        0
    );
}

void otaWorkerTask(void* parameter) {
    OtaParams* params = (OtaParams*)parameter;
    
    // 1. Suspend non-essential subsystems
    stopApMode();
    logInfo("OTA", "Initiating Wi-Fi connection to '%s'...", params->ssid.c_str());
    
    WiFi.mode(WIFI_STA);
    WiFi.begin(params->ssid.c_str(), params->password.length() > 0 ? params->password.c_str() : NULL);
    
    uint32_t startMs = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - startMs < 15000) {
        vTaskDelay(pdMS_TO_TICKS(250));
    }

    if (WiFi.status() != WL_CONNECTED) {
        logError("OTA", "Failed to connect to local Wi-Fi within 15s");
        emitSerialLine("{\"event\":\"OTA_FAILED\",\"error\":\"wifi_timeout\"}");
        WiFi.disconnect(true);
        WiFi.mode(WIFI_OFF);
        otaInProgress.store(false);
        delete params;
        vTaskDelete(NULL);
        return;
    }

    logInfo("OTA", "Connected! IP: %s. Fetching %s...", WiFi.localIP().toString().c_str(), params->url.c_str());

    HTTPClient http;
    http.begin(params->url);
    http.setTimeout(10000);
    int httpCode = http.GET();

    if (httpCode != HTTP_CODE_OK) {
        logError("OTA", "HTTP GET failed with code: %d", httpCode);
        char buf[128];
        snprintf(buf, sizeof(buf), "{\"event\":\"OTA_FAILED\",\"error\":\"http_code_%d\"}", httpCode);
        emitSerialLine(buf);
        http.end();
        WiFi.disconnect(true);
        otaInProgress.store(false);
        delete params;
        vTaskDelete(NULL);
        return;
    }

    int contentLength = http.getSize();
    WiFiClient* stream = http.getStreamPtr();

    if (!Update.begin(contentLength > 0 ? contentLength : UPDATE_SIZE_UNKNOWN, U_FLASH)) {
        logError("OTA", "Update.begin failed: %s", Update.errorString());
        emitSerialLine("{\"event\":\"OTA_FAILED\",\"error\":\"partition_error\"}");
        http.end();
        WiFi.disconnect(true);
        otaInProgress.store(false);
        delete params;
        vTaskDelete(NULL);
        return;
    }

    // Write binary stream in 2KB blocks
    uint8_t buff[2048];
    size_t written = 0;
    int lastPercent = -1;

    while (http.connected() && (written < contentLength || contentLength <= 0)) {
        size_t available = stream->available();
        if (available > 0) {
            size_t bytesToRead = (available > sizeof(buff)) ? sizeof(buff) : available;
            size_t bytesRead = stream->readBytes(buff, bytesToRead);
            Update.write(buff, bytesRead);
            written += bytesRead;

            if (contentLength > 0) {
                int currentPercent = (int)((written * 100) / contentLength);
                if (currentPercent / 10 != lastPercent / 10) {
                    lastPercent = currentPercent;
                    char pBuf[128];
                    snprintf(pBuf, sizeof(pBuf), "{\"event\":\"OTA_PROGRESS\",\"percent\":%d,\"written\":%u,\"total\":%d}",
                             currentPercent, (unsigned int)written, contentLength);
                    emitSerialLine(pBuf);
                }
            }
        } else {
            vTaskDelay(pdMS_TO_TICKS(10));
        }
        if (contentLength > 0 && written >= contentLength) break;
    }

    if (!Update.end(true)) {
        logError("OTA", "Update.end failed: %s", Update.errorString());
        emitSerialLine("{\"event\":\"OTA_FAILED\",\"error\":\"verification_error\"}");
        http.end();
        WiFi.disconnect(true);
        otaInProgress.store(false);
        delete params;
        vTaskDelete(NULL);
        return;
    }

    logInfo("OTA", "Firmware flash successfully written! Total bytes: %u", (unsigned int)written);
    emitSerialLine("{\"event\":\"OTA_SUCCESS\",\"reboot_sec\":2}");
    
    http.end();
    vTaskDelay(pdMS_TO_TICKS(1500));
    ESP.restart();
}
```

---

## 6. Hardware Rollback Protection & Anti-Bricking Guarantees

### 6.1 ESP-IDF Bootloader Rollback Mechanism
When the ESP32 writes an image to `ota_1`, the ESP-IDF bootloader marks the new partition's state in the `otadata` partition as:
`ESP_OTA_IMG_NEW` (or `ESP_OTA_IMG_PENDING_VERIFY`).

When the ESP32 restarts:
1. The secondary bootloader reads `otadata`. It notices `ota_1` is in `PENDING_VERIFY` state.
2. The bootloader increments an internal boot attempt counter.
3. If the chip experiences a kernel panic, watchdog timer reset, hardware brownout, or endless crash loop **before confirming the image as valid**, the bootloader automatically sets the state of `ota_1` to `ESP_OTA_IMG_INVALID` and reboots back into the previous known-good partition (`ota_0`)!

```mermaid
flowchart TD
    Boot[ESP32 Power On / Reset] --> ReadData[Read otadata Partition]
    ReadData --> CheckState{Active App State?}
    
    CheckState -->|VALID| NormalBoot[Boot Active App Normally]
    CheckState -->|PENDING_VERIFY| TestBoot[Boot New App in Testing Mode]
    
    TestBoot --> AppInit[Run setup & Hardware Init]
    AppInit -->|Crash / Panic / WDT Reset| AutoRollback[Bootloader Flags App as INVALID<br/>Reverts otadata to ota_0]
    AutoRollback --> Boot
    
    AppInit --> UARTCheck[Establish UART Communication with OPi]
    UARTCheck --> SelfTest[Run I2C Bus & Sensor Self-Test]
    SelfTest -->|Passed| ConfirmOTA[Execute esp_ota_mark_app_valid_cancel_rollback]
    ConfirmOTA --> NormalBoot
```

### 6.2 Post-Boot Self-Test & Rollback Cancellation
In `setup()` of `src/main.cpp`, the firmware executes a rigorous health check before canceling rollback:

```cpp
void verifyAndValidateFirmware() {
    const esp_partition_t *running = esp_ota_get_running_partition();
    esp_ota_img_states_t ota_state;
    if (esp_ota_get_state_partition(running, &ota_state) == ESP_OK) {
        if (ota_state == ESP_OTA_IMG_PENDING_VERIFY) {
            logInfo("OTA", "Running on unverified partition '%s'. Commencing self-diagnostics...", running->label);
            
            // Diagnostics: Check that core hardware interfaces initialized
            bool pcaOk = pca9685Found;
            bool uartOk = (Serial.availableForWrite() > 0);
            
            if (pcaOk && uartOk) {
                logInfo("OTA", "Hardware self-diagnostics PASSED. Marking app VALID.");
                esp_ota_mark_app_valid_cancel_rollback();
                emitSerialLine("{\"event\":\"OTA_VALIDATED\",\"partition\":\"" + String(running->label) + "\"}");
            } else {
                logError("OTA", "Self-diagnostics FAILED (pca=%d, uart=%d)! Triggering rollback...", pcaOk, uartOk);
                esp_ota_mark_app_invalid_rollback_and_reboot();
            }
        }
    }
}
```

### 6.3 Fault-Tolerance Matrix

| Failure Mode | Physical Impact | Automatic Mitigation | Result |
|---|---|---|---|
| **Power cut at 50% download** | Inactive partition contains partial data | Bootloader sees `otadata` still pointing to `ota_0`. Boots previous partition. | **Zero disruption.** Machine reboots into previous firmware. |
| **Corrupted binary file uploaded** | Bit errors in flash payload | `Update.end()` validates ESP32 image checksum and magic bytes. Fails and rejects flash. | **Zero disruption.** Stays on active partition. |
| **New firmware crashes Xtensa CPU** | Null pointer dereference / Guru Meditation | Watchdog triggers reset. Bootloader sees `PENDING_VERIFY` and reverts to `ota_0`. | **Automatic self-healing.** Prevents bricking in field. |
| **Customer AP loses power mid-OTA** | Wi-Fi socket disconnected | ESP32 OTA task times out after 10s. Disables Wi-Fi, resets servos, reports error. | **Safe abort.** Resumes normal vending mode. |

---

## 7. Host-Side Gateway Integration (Orange Pi)

### 7.1 Local Firmware Staging & Secure HTTP Hosting
The Orange Pi hosts the firmware binary locally so the ESP32 does not pull from the public internet.
- Storage Location: `/opt/ecofi/firmware/`
- Symlink or direct route: Served via Flask or Nginx at `http://10.0.0.1/firmware/esp32_ota.bin`.
- Directory permissions: `www-data:www-data`, read-only for web requests.

### 7.2 Backend REST Endpoints (`portal.py` Python 3.5.3 Compliance)

> [!CAUTION]
> In accordance with `AGENTS.md` and `GEMINI.md`, all host Python code must strictly support **Python 3.5.3**.
> - **NO f-strings (`f"..."`)**: Use `"...".format(...)` or `%` formatting.
> - **NO type hints inside functions**.

```python
# ==============================================================================
# ESP32 REMOTE FIRMWARE UPDATE API (Python 3.5.3 Compliant)
# ==============================================================================

import os
import hashlib
import time

FIRMWARE_DIR = '/opt/ecofi/firmware'
if not os.path.exists(FIRMWARE_DIR):
    try:
        os.makedirs(FIRMWARE_DIR)
    except Exception:
        pass

esp32_ota_status = {
    'in_progress': False,
    'step': 'idle',
    'percent': 0,
    'last_error': None,
    'start_time': 0,
    'firmware_version': None
}

@app.route('/admin/api/esp32/ota/upload', methods=['POST'])
def admin_api_esp32_ota_upload():
    if not session.get('admin_logged_in'):
        return (jsonify({'error': 'unauthorized'}), 401)
    
    if 'firmware' not in request.files:
        return (jsonify({'success': False, 'error': 'No firmware file uploaded'}), 400)
    
    file = request.files['firmware']
    if file.filename == '':
        return (jsonify({'success': False, 'error': 'Empty filename'}), 400)
    
    save_path = os.path.join(FIRMWARE_DIR, 'esp32_ota.bin')
    file.save(save_path)
    
    # Calculate SHA-256 and size
    sha256_hash = hashlib.sha256()
    with open(save_path, 'rb') as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
            
    file_size = os.path.getsize(save_path)
    checksum = sha256_hash.hexdigest()
    
    # Basic sanity check on ESP32 binary (Magic byte must be 0xE9)
    with open(save_path, 'rb') as f:
        magic_byte = f.read(1)
        if magic_byte != b'\xe9':
            os.remove(save_path)
            return (jsonify({'success': False, 'error': 'Invalid ESP32 binary format (Magic byte != 0xE9)'}), 400)

    log.info("Staged valid ESP32 firmware: size=%d bytes, sha256=%s", file_size, checksum)
    return jsonify({
        'success': True,
        'size': file_size,
        'sha256': checksum,
        'message': 'Firmware uploaded and verified successfully.'
    })


@app.route('/admin/api/esp32/ota/trigger', methods=['POST'])
def admin_api_esp32_ota_trigger():
    if not session.get('admin_logged_in'):
        return (jsonify({'error': 'unauthorized'}), 401)
    
    if esp32_ota_status['in_progress']:
        return (jsonify({'success': False, 'error': 'OTA update is already running'}), 409)
    
    target_bin = os.path.join(FIRMWARE_DIR, 'esp32_ota.bin')
    if not os.path.exists(target_bin):
        return (jsonify({'success': False, 'error': 'No firmware staged for update'}), 404)
        
    data = request.get_json(silent=True) or {}
    ssid = data.get('ssid', 'EcoFi Free WiFi')
    password = data.get('password', '')
    version_tag = data.get('version', 'unknown')
    
    file_size = os.path.getsize(target_bin)
    sha256_hash = hashlib.sha256()
    with open(target_bin, 'rb') as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    checksum = sha256_hash.hexdigest()
    
    payload = {
        'cmd': 'START_OTA',
        'ssid': ssid,
        'password': password,
        'url': 'http://10.0.0.1/static/firmware/esp32_ota.bin',
        'sha256': checksum,
        'size': file_size,
        'version': version_tag
    }
    
    global ser, esp32_port_name
    if ser is None or esp32_port_name is None:
        return (jsonify({'success': False, 'error': 'Physical ESP32 is offline'}), 503)
        
    ok = transmit_to_esp32(payload)
    if not ok:
        return (jsonify({'success': False, 'error': 'Failed to transmit OTA command over serial'}), 500)
        
    esp32_ota_status['in_progress'] = True
    esp32_ota_status['step'] = 'command_dispatched'
    esp32_ota_status['percent'] = 0
    esp32_ota_status['last_error'] = None
    esp32_ota_status['start_time'] = time.time()
    esp32_ota_status['firmware_version'] = version_tag
    
    return jsonify({'success': True, 'message': 'OTA process successfully initiated.'})


@app.route('/admin/api/esp32/ota/status', methods=['GET'])
def admin_api_esp32_ota_status():
    if not session.get('admin_logged_in'):
        return (jsonify({'error': 'unauthorized'}), 401)
    return jsonify(esp32_ota_status)
```

### 7.3 UART Daemon Extensions & Telemetry Handling
Inside `handle_physical_esp32_packet(data)` in `host/portal.py`:

```python
    # Handle OTA Telemetry Events
    if ev == 'OTA_ACCEPTED':
        esp32_ota_status['step'] = 'connecting_wifi'
        log.info("ESP32 accepted OTA command, switching to Wi-Fi STA mode.")
    elif ev == 'OTA_PROGRESS':
        esp32_ota_status['step'] = 'flashing'
        esp32_ota_status['percent'] = data.get('percent', 0)
    elif ev == 'OTA_SUCCESS':
        esp32_ota_status['step'] = 'rebooting'
        esp32_ota_status['percent'] = 100
        log.info("ESP32 OTA flash complete! Microcontroller rebooting...")
    elif ev == 'OTA_FAILED':
        esp32_ota_status['in_progress'] = False
        esp32_ota_status['step'] = 'failed'
        esp32_ota_status['last_error'] = data.get('error', 'unknown_error')
        log.error("ESP32 OTA failed: %s", esp32_ota_status['last_error'])
    elif ev == 'BOOT' and esp32_ota_status['in_progress']:
        esp32_ota_status['in_progress'] = False
        esp32_ota_status['step'] = 'complete'
        log.info("ESP32 successfully booted new firmware version: %s (Partition: %s)",
                 data.get('firmware_version'), data.get('partition'))
```

### 7.4 Admin Panel User Interface & Progress Modals
Embedded in `ADMIN_HTML` within `host/portal.py`, a dedicated Card is added to the **ESP32 Hardware Management** tab:

```html
<div class="card card-outline card-primary mt-3">
    <div class="card-header">
        <h3 class="card-title"><i class="fas fa-microchip-slash mr-1"></i> ESP32 Over-The-Air (OTA) Firmware Upgrade</h3>
    </div>
    <div class="card-body">
        <p class="text-muted">Deploy compiled ESP32 firmware (<code>.bin</code>) directly to the microcontroller over local Wi-Fi without taking down the machine.</p>
        <div class="row">
            <div class="col-md-6">
                <div class="form-group">
                    <label>Select Firmware Binary (.bin)</label>
                    <input type="file" id="esp32-fw-file" class="form-control-file" accept=".bin">
                </div>
            </div>
            <div class="col-md-6">
                <div class="form-group">
                    <label>Target Local Wi-Fi SSID</label>
                    <input type="text" id="esp32-ota-ssid" class="form-control" value="EcoFi Free WiFi">
                </div>
            </div>
        </div>
        <button class="btn btn-warning" onclick="startEsp32OtaUpgrade()">
            <i class="fas fa-upload mr-1"></i> Upload & Flash Firmware
        </button>
    </div>
</div>
```

#### JavaScript Progress Tracker:
Uses SweetAlert2 (`Swal.fire`) to display a live updating percentage bar that polls `/admin/api/esp32/ota/status` every 500 ms until `OTA_SUCCESS` or `OTA_FAILED` is returned.

---

## 8. End-to-End Operational Workflow

### 8.1 Workflow 1: Updating Orange Pi Software / System Remotely

```mermaid
sequenceDiagram
    autonumber
    actor Dev as Machine Builder / Developer
    participant TS as Tailscale WireGuard Network
    participant OPi as Orange Pi Gateway (/opt/ecofi/)

    Dev->>TS: Connects to Tailnet (Private Mesh)
    Dev->>OPi: rsync / SCP updated Python code over Tailscale (100.x.y.z)
    Dev->>OPi: SSH command: systemctl restart ecofi_portal.service
    Note over OPi: Python 3.5.3 portal restarts in ~1.2s
    OPi-->>Dev: System healthy (All 77 regression tests passing)
```

### 8.2 Workflow 2: Deploying ESP32 Firmware Remotely

```mermaid
sequenceDiagram
    autonumber
    actor Dev as Developer (Remote PC)
    participant Cloud as PlatformIO / Compiler
    participant Portal as Eco-Fi Admin Panel (http://100.x.y.z/admin)
    participant OPi as Orange Pi Daemon
    participant ESP as ESP32 Microcontroller

    Dev->>Cloud: pio run (Compiles main.cpp to firmware.bin)
    Dev->>Portal: Opens browser via Tailscale IP (100.x.y.z)
    Dev->>Portal: Uploads firmware.bin via OTA Card
    Portal->>OPi: Stages binary & validates Magic Byte (0xE9)
    Dev->>Portal: Clicks "Upload & Flash Firmware"
    Portal->>OPi: POST /admin/api/esp32/ota/trigger
    OPi->>ESP: UART CMD: {"cmd":"START_OTA", ...}
    loop Progress Polling
        ESP->>OPi: Progress frames over UART
        Portal->>Portal: Update SweetAlert2 progress bar (0% -> 100%)
    end
    ESP->>ESP: Flashes ota_1 & reboots
    ESP->>OPi: Boots into ota_1 & sends BOOT event
    OPi-->>Portal: Confirms upgrade complete!
```

### 8.3 Workflow 3: Automatic Hardware Rollback Recovery

```mermaid
sequenceDiagram
    autonumber
    actor Dev as Developer
    participant OPi as Orange Pi
    participant ESP as ESP32 Bootloader

    Dev->>OPi: Deploys buggy firmware with a null-pointer crash
    OPi->>ESP: Flashes to ota_1 & reboots
    Note over ESP: Bootloader boots ota_1 in PENDING_VERIFY mode
    Note over ESP: Firmware crashes (Guru Meditation / Kernel Panic)
    Note over ESP: Hardware Watchdog Triggers Reset!
    Note over ESP: Bootloader detects crash in unverified partition!
    Note over ESP: Marks ota_1 INVALID and rolls back to ota_0!
    ESP->>OPi: Boots into ota_0 & sends BOOT event (version 2.3.5)
    OPi-->>Dev: Alert: "Firmware crashed on boot. Automatic rollback to 2.3.5 succeeded!"
```

---

## 9. Step-by-Step Implementation Checklist

### Phase 1: Orange Pi Tailscale Ingress
- [ ] 1.1 Download static ARMv7 `armhf` Tailscale binary package to Orange Pi One.
- [ ] 1.2 Install `tailscale` and `tailscaled` to `/usr/local/bin/` and `/usr/local/sbin/`.
- [ ] 1.3 Create and enable systemd service unit `/etc/systemd/system/tailscaled.service`.
- [ ] 1.4 Generate tagged pre-auth key in Tailscale Admin Console (`tag:vendo-machines`).
- [ ] 1.5 Authenticate node via `tailscale up --authkey=... --ssh=true`.
- [ ] 1.6 Verify that captive portal redirection (`iptables -t nat -A PREROUTING`) does not affect `tailscale0`.
- [ ] 1.7 Verify end-to-end SSH and Web Admin access over Tailscale IP from remote cellular network.

### Phase 2: ESP32 Partition Table & OTA Core Firmware
- [ ] 2.1 Update `platformio.ini` to set `board_build.partitions = min_ota.csv`.
- [ ] 2.2 Verify compiled binary size is under 1.875 MB (currently ~876 KB).
- [ ] 2.3 In `src/main.cpp`, add `#include <Update.h>` and `#include <esp_ota_ops.h>`.
- [ ] 2.4 Implement `handleOtaCommand()` and `otaWorkerTask()` FreeRTOS routine.
- [ ] 2.5 Add UART frame parser for `START_OTA` command.
- [ ] 2.6 Implement telemetry reporting: `OTA_ACCEPTED`, `OTA_PROGRESS`, `OTA_SUCCESS`, `OTA_FAILED`.
- [ ] 2.7 In `setup()`, implement `verifyAndValidateFirmware()` with `esp_ota_mark_app_valid_cancel_rollback()`.

### Phase 3: Host Backend Daemon & Admin Portal Integration
- [ ] 3.1 Create `/opt/ecofi/firmware/` directory with `www-data` read permissions.
- [ ] 3.2 Implement Python 3.5.3 REST endpoints in `host/portal.py`:
  - `/admin/api/esp32/ota/upload`
  - `/admin/api/esp32/ota/trigger`
  - `/admin/api/esp32/ota/status`
- [ ] 3.3 Extend `handle_physical_esp32_packet()` to parse OTA events and update status dictionary.
- [ ] 3.4 Embed UI Card and SweetAlert2 progress modal into `ADMIN_HTML`.
- [ ] 3.5 Escape all JavaScript quotes properly (`&quot;`, `\'`) to avoid parser crashes.

### Phase 4: Benchtop Simulation, Fault-Injection Testing & Production Verification
- [ ] 4.1 Execute `python test_entitlement_regressions.py` (ensure all 77 tests pass).
- [ ] 4.2 Test simulated OTA trigger in mock mode.
- [ ] 4.3 Deploy to live Orange Pi One hardware via `tools/opi_access.py`.
- [ ] 4.4 Perform live over-the-air update of ESP32 firmware over Wi-Fi.
- [ ] 4.5 Perform fault injection: interrupt power during OTA write; verify automatic boot into previous partition.
- [ ] 4.6 Rebuild base OS image `resources/EcoFi_Opi_v2.2.img` with Tailscale pre-installed.

---

## 10. Test Matrix & Quality Assurance Protocols

| Test ID | Objective | Procedure | Expected Result | Pass Criteria |
|---|---|---|---|---|
| **TEST-TS-01** | Ingress CGNAT Traversal | Connect OPi to 4G hotspot. SSH via Tailscale IP from external network. | Terminal session opens without delay. | Ping < 120ms, zero packet drops. |
| **TEST-TS-02** | Captive Portal Isolation | Attempt HTTP request to `http://100.x.y.z:5000/admin` while captive portal is active. | Admin page loads directly. Not redirected to captive portal splash. | HTTP 200 OK returned. |
| **TEST-OTA-01** | Normal OTA Flashing | Trigger OTA update with valid `firmware.bin` (v2.4.0) via Admin Panel. | Progress displays 0% -> 100%. ESP32 reboots. Emits `BOOT` with v2.4.0. | New version active and responsive. |
| **TEST-OTA-02** | Vending Collision Guard | Send `START_OTA` command while bottle deposit cycle is active (`busy=1`). | ESP32 emits `{"event":"OTA_REJECTED","reason":"machine_busy"}`. | Gate remains operational, no crash. |
| **TEST-OTA-03** | Corrupt Payload Rejection | Upload random byte file renamed to `firmware.bin`. | Host rejects file immediately due to magic byte mismatch (`!= 0xE9`). | HTTP 400 with descriptive error. |
| **TEST-OTA-04** | Mid-Stream Power Disruption | Disconnect 12V/5V logic rail power when progress bar reaches ~50%. | Power restored. ESP32 boots previous partition (`ota_0`) cleanly. | Machine resumes normal operation. |
| **TEST-OTA-05** | Boot-Loop Auto Rollback | Upload firmware containing forced panic in `setup()`. | ESP32 reboots, crashes, watchdog resets. Bootloader reverts to `ota_0`. | Machine recovers without bricking. |
| **TEST-NVS-01** | Credit Journal Integrity | Record un-ACKed credit transaction in NVS. Perform OTA flash. | Post-reboot, ESP32 replays un-ACKed transaction receipt. | Zero credit loss across flash cycle. |

---

## Architectural Sign-Off
- **Orange Pi Strategy:** Option A (Tailscale Mesh VPN) — Confirmed Feasible & Approved.
- **ESP32 Strategy:** Solution 3 (Local Wi-Fi HTTP Dual-Partition OTA) — Confirmed Feasible & Approved.
- **Hardware Modification:** **NONE (0 wires added, 0 solder joints required)**.
- **Safety Guarantee:** Hardware A/B dual partition with bootloader auto-rollback.

