# PisoFi Firmware Dump (v5.3.0)

This directory contains a complete backup of the PisoFi portal source code, extracted from the `PisoFi_Opi1&PC_v5.3.0-05-10-26_EXT.img` disk image.

## Overview
PisoFi is a commercial captive portal software widely used in the Philippines for "Pisowifi" coin-operated internet vending machines. It natively runs on Debian-based Linux boards (like Orange Pi and Raspberry Pi) using a traditional LAMP stack (Linux, Apache/Nginx, MySQL/SQLite, PHP).

The source code here was extracted from `/var/www/html/pisofi` and serves as a vital reference for the **Smart Eco-Fi** system, allowing us to inspect production-grade traffic shaping and hardware integration logic.

## Directory Structure

### `/public`
Contains the web-facing assets for the Captive Portal:
- `hotspot.html` / `index.php`: The main entry points for the user portal.
- `/assets`, `/css`, `/js`, `/img`: Frontend UI libraries (Bootstrap, custom CSS templates, logos).

### `/scripts`
This is the core backend engine where the Linux OS interacts with the hardware.
Notable files include:
*   `coinreader.php` / `coinrdr`: Handles GPIO interrupts for reading coin slot pulses.
*   `pfirules`: A massive bash/php script that generates `iptables` and `nftables` firewall rules for MAC whitelisting and captive portal redirection.
*   `tcharvester`: The daemon responsible for executing Linux `tc` (Traffic Control) commands to enforce upload/download speed limits per connected client.
*   `kicker.php`: A background script that continually monitors session timers and drops connections when time expires.
*   `datasync.php` / `remotebackup.php`: Cloud synchronization logic for remote management dashboards.

## Purpose in this Repository
This code is included **strictly for research and reference**. The Smart Eco-Fi system uses a vastly different architecture (ESP32 for hardware + Python/Flask for the portal), but examining how PisoFi implements anti-tethering (`iptables` TTL mangling) and traffic shaping provides an excellent benchmark for our own networking logic.
