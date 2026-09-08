# PisoFi OS Firmware Forensics & Credentials Audit Report

**Target Image:** `resources/PisoFi_Opi1&PC_v5.3.0-05-10-26_EXT.img`  
**Image Size:** 3,162,022,400 bytes (3,016 MiB)  
**Partition Scheme:** MBR / 1 Primary Ext4 Partition (`0.img` at offset 4,194,304 bytes)  
**Filesystem UUID:** `e0b06b9a-4769-41f3-900c-63ae4d7a3f9b`  
**Base Distribution:** Armbian 5.83 (Debian 9.9 "Stretch", Kernel 4.19.38-sunxi armhf)  
**Architecture:** Allwinner Sun8i (Orange Pi One / Orange Pi PC)  

---

## 1. System & Architecture Overview

| Property | Value |
| :--- | :--- |
| **Hostname** | `localhost` |
| **Armbian Version** | 5.83 (`sun8i`, `next` branch) |
| **Linux Kernel** | `4.19.38-sunxi` |
| **Web Server** | Nginx 1.10.3 + PHP 7.0-FPM |
| **Database** | MariaDB / MySQL 10.1 (`pisofi` database) |
| **Web Application Path** | `/var/www/html/pisofi/` (symlinked/cached from `.cache/tmp/55/05/pfi/`) |

---

## 2. User Accounts & Password Hashes

### Interactive Accounts in `/etc/passwd`
* **`root`** (UID `0`, GID `0`, Home: `/root`, Shell: `/bin/bash`)
* **`pi`** (UID `1000`, GID `1000`, Home: `/home/pi`, Shell: `/bin/bash`)
* **`www-data`** (UID `33`, GID `33`, Home: `/var/www`, Shell: `/usr/sbin/nologin`)

### Cryptographic Hashes in `/etc/shadow`
The image uses 5,000-round SHA-512 crypt (`$6$`) hashes:

```text
root:$6$TTi4wUHU$urKYqTvH352SZlU5.MkoHoCdvmGq.bDnpXdI0oUgl5XZgA52hZbBEHrJ4XF8cKNXWJNLhsUISoXTSaZ5/0.Nm/:18019:0:99999:7:::
pi:$6$Fdi9Su/A$CYpTq/Rm7zqgLINcyySlgurEHcOGqWxqpZDHj3QjgMyeMh/jTrjlIlrZymFBiZYYeoNdm0P9h7tqbOeUxLG31/:18020:0:99999:7:::
```

#### Hash Breakdown:
* **`root` salt:** `TTi4wUHU` (Created on epoch day `18019` = May 4, 2019)
* **`pi` salt:** `Fdi9Su/A` (Created on epoch day `18020` = May 5, 2019)

### Why Default Armbian/Raspberry Passwords Failed
1. Armbian default install password is `1234`. Upon first boot, Armbian interactively mandates creating a custom root password and adding a regular user (`pi`).
2. The PisoFi creator configured a private custom password during packaging and wiped all command history:
   * `/root/.bash_history` (Truncated to 0 bytes)
   * `/home/pi/.bash_history` (Truncated to 0 bytes)
   * `/home/pi/.python_history` (Truncated to 0 bytes)
3. From `/var/log/auth.log`, the final administrative action performed on the live build system before image export was:
   ```text
   May 11 23:35:41 localhost sshd[17435]: pam_unix(sshd:session): session closed for user pi
   May 11 23:35:41 localhost sudo: pi : TTY=pts/0 ; PWD=/home/pi ; USER=root ; COMMAND=/sbin/shutdown now
   ```

---

## 3. SSH Configuration & Firewall Restrictions

### OpenSSH Server (`/etc/ssh/sshd_config`)
```text
Port 22
PermitRootLogin yes
PasswordAuthentication yes
UsePAM yes
```
SSH is configured to allow root password logins directly.

### Firewall Block (`/var/www/html/pisofi/scripts/pfirules`)
Even though the SSH service is enabled on the OS, PisoFi's default iptables script deliberately drops inbound packets on port 22:
```text
-A INPUT -p tcp --dport 22 -j DROP
```
* **Dashboard Toggle:** Toggling "Allow SSH" under **Admin -> Settings -> Security** triggers `SystemController::securityPost()`, which executes `/usr/local/bin/pisofier resetRules` to remove the DROP rule.

---

## 4. Privilege Escalation in `/etc/sudoers`

A critical misconfiguration was identified in `/etc/sudoers`:
```text
www-data ALL = (ALL) NOPASSWD:ALL
```
* **Implication:** The web server process (`www-data`) running PHP-FPM possesses unrestricted, passwordless `sudo` rights across the entire operating system. Any PHP script or web endpoint that runs shell commands can execute arbitrary commands with full `root` authority.

---

## 5. Web Portal Secrets & Internal Credentials

### Database Credentials (`/etc/environment`)
```ini
KCFGDBN=pisofi
KCFGDBU=wipi
KCFGDBP=wipi
KCFGDBH=localhost
```
* **Database Name:** `pisofi`
* **MySQL User:** `wipi`
* **MySQL Password:** `wipi`

### Web Admin Credentials & Encryption
* **Default Admin Username:** `administrator`
* **Default Admin Password:** `admin12345`
* **Front-end Encryption:** PisoFi does not submit plain passwords. Instead, `/auth/signin/` generates a random 30-character session token stored in cookie `csp` and hidden input `asin`. The browser encrypts the credentials using `CryptoJS.AES` (AES-256-CBC) using the `csp` token before submission.

---

## 6. Remote Management & Reverse Tunnels

Located in `/home/pi/.ngrok2/ngrok.yml`:
```yaml
authtoken: NGROK_TOKEN
region: us
tunnels:
    pisofi-app:
        addr: 88
        proto: http
        schemes:
            - http
            - https
    pisofi-ssh:
        addr: 22
        proto: tcp
version: "2"
```
The firmware contains pre-configured tunnels to expose both the web interface (port 88) and SSH (port 22) externally via Ngrok.

---

## 7. Command Execution Surfaces in Web Portal

Because `www-data` has `NOPASSWD:ALL`, shell invocations in the PHP codebase execute with root capabilities.

In `app/Controllers/ToolsController.php`:
1. **`applyPatch()`**:
   ```php
   $patch = $request->getParam('patch');
   $patchInit = "/tmp/$patch";
   exec("sudo rm {$patchInit}* -rf");
   ```
   * The `$patch` parameter is interpolated into a `sudo rm` command without shell sanitization.

2. **`speedtestPost()`**:
   ```php
   $serverId = $request->getParam('server');
   exec("sudo /usr/local/bin/pfi-speed-test $serverId > /dev/null 2>&1 &");
   ```
   * The `$serverId` parameter is passed unquoted into a `sudo` shell script.

---

## 8. Root Access Recovery & Modification Playbook

To take full control of an Orange Pi running this image, choose one of the following methods:

### Method 1: SD Card `/etc/shadow` Replacement (Recommended)
1. Turn off the Orange Pi and insert the microSD card into a PC or Linux/WSL environment.
2. Mount partition 1 (ext4 filesystem).
3. Open `/etc/shadow` as root:
   ```bash
   sudo nano /path_to_sd/etc/shadow
   ```
4. Replace the `root:` entry with the known hash for password `root`:
   ```text
   root:$6$JIArBU6F1WcXAkV2$n13SEPVG7J/mKPL1Fr0wuadMbziDVKwGQrA484i5K/MzA3IY8l1lpcx960SYyFmR1I.QTgesqTzZu1M9je9YI0:18019:0:99999:7:::
   ```
   *(Alternatively, empty the password field `root::18019:0:99999:7:::` to log in with no password).*
5. Unmount the card, insert it into the Orange Pi, and boot.
6. Connect via SSH:
   ```bash
   ssh root@10.0.0.1
   # Password: root
   ```

### Method 2: Deploying via `build_ecofi_img.sh`
This project includes an automated builder [`build_ecofi_img.sh`](file:///d:/PROJECTS_IO\Plastic-Bottle-Vending-Machine\build_ecofi_img.sh) that mounts the stock PisoFi image, patches `/etc/shadow` with `root:root`, installs the EcoFi bottle vending portal, and configures networking (`eth0` = WAN DHCP, `eth1` = LAN 10.0.0.1/19 AP).
* Running `build_ecofi_img.bat` or `./build_ecofi_img.sh` generates a ready-to-flash image with SSH credentials permanently known.

---

## 9. Extracted Reference Files in Repository

The raw configuration files extracted from the image have been preserved in:
* [`pisofi_inspect/extracted_configs/passwd`](file:///d:/PROJECTS_IO/Plastic-Bottle-Vending-Machine/pisofi_inspect/extracted_configs/passwd)
* [`pisofi_inspect/extracted_configs/shadow`](file:///d:/PROJECTS_IO/Plastic-Bottle-Vending-Machine/pisofi_inspect/extracted_configs/shadow)
* [`pisofi_inspect/extracted_configs/sudoers`](file:///d:/PROJECTS_IO/Plastic-Bottle-Vending-Machine/pisofi_inspect/extracted_configs/sudoers)
* [`pisofi_inspect/extracted_configs/environment`](file:///d:/PROJECTS_IO/Plastic-Bottle-Vending-Machine/pisofi_inspect/extracted_configs/environment)
* [`pisofi_inspect/extracted_configs/sshd_config`](file:///d:/PROJECTS_IO/Plastic-Bottle-Vending-Machine/pisofi_inspect/extracted_configs/sshd_config)
* [`pisofi_inspect/extracted_configs/armbian-release`](file:///d:/PROJECTS_IO/Plastic-Bottle-Vending-Machine/pisofi_inspect/extracted_configs/armbian-release)

