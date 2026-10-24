"""Comprehensive Network, Entitlements, Bandwidth, DNS, Limits, and QoS Audit Script.

Executes live audits on the physical Orange Pi gateway via SSH.
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.opi_access import connect, execute


REMOTE_SCRIPT = """import json, socket, subprocess, sqlite3, ipaddress, re

results = {}

# 1. Network Interfaces & Routing
def check_interfaces():
    res = subprocess.run(["ip", "-4", "addr", "show"], stdout=subprocess.PIPE, universal_newlines=True)
    ifaces = {}
    current_iface = None
    for line in res.stdout.splitlines():
        m_iface = re.match(r"^\\d+:\\s+([^:]+):", line)
        if m_iface:
            current_iface = m_iface.group(1).split("@")[0]
            ifaces[current_iface] = {}
        elif current_iface and "inet " in line:
            parts = line.strip().split()
            if len(parts) >= 2:
                ip_cidr = parts[1]
                if "/" in ip_cidr:
                    ip, pfx = ip_cidr.split("/", 1)
                    ifaces[current_iface] = {"ip": ip, "prefixlen": int(pfx)}
    
    res_r = subprocess.run(["ip", "route", "show", "default"], stdout=subprocess.PIPE, universal_newlines=True)
    def_route = res_r.stdout.strip()
    
    res_f = subprocess.run(["sysctl", "-n", "net.ipv4.ip_forward"], stdout=subprocess.PIPE, universal_newlines=True)
    fwd = res_f.stdout.strip()
    
    return {
        "interfaces": ifaces,
        "default_route": def_route,
        "ip_forwarding": fwd == "1"
    }

results["interfaces"] = check_interfaces()

# 2. DNS & Captive Portal Discovery
def check_dns():
    dns_tests = {}
    domains = [
        ("captive.apple.com", "10.0.0.1"),
        ("connectivitycheck.gstatic.com", "10.0.0.1"),
        ("msftconnecttest.com", "10.0.0.1"),
        ("ecofi-vendo.com", "10.0.0.1"),
        ("cloudflare.com", "public"),
    ]
    for dom, expected in domains:
        r = subprocess.run(["dig", "@10.0.0.1", "+short", dom], stdout=subprocess.PIPE, universal_newlines=True)
        out = r.stdout.strip().replace("\\n", ",")
        if expected == "public":
            passed = bool(out and "10.0.0.1" not in out)
        else:
            passed = expected in out
        dns_tests[dom] = {"resolved": out, "expected": expected, "pass": passed}
        
    r_relay = subprocess.run(["dig", "@10.0.0.1", "mask.icloud.com"], stdout=subprocess.PIPE, universal_newlines=True)
    dns_tests["mask.icloud.com (NXDOMAIN)"] = {
        "pass": "NXDOMAIN" in r_relay.stdout,
        "output": "NXDOMAIN confirmed" if "NXDOMAIN" in r_relay.stdout else "FAILED"
    }
    
    r_rfc = subprocess.run(["curl", "-s", "-i", "http://10.0.0.1/api/captive-portal"], stdout=subprocess.PIPE, universal_newlines=True)
    has_json_header = "application/captive+json" in r_rfc.stdout
    has_captive_field = '"captive"' in r_rfc.stdout
    dns_tests["RFC 8910 Option 114"] = {
        "pass": has_json_header and has_captive_field,
        "content_type_ok": has_json_header,
        "json_body_ok": has_captive_field
    }
    
    r_wispr = subprocess.run(["curl", "-s", "http://10.0.0.1/hotspot.html"], stdout=subprocess.PIPE, universal_newlines=True)
    dns_tests["WISPr 2.0 /hotspot.html"] = {
        "pass": "<WISPAccessGatewayParam" in r_wispr.stdout and "<LoginURL>http://10.0.0.1/</LoginURL>" in r_wispr.stdout
    }
    
    return dns_tests

results["dns_and_discovery"] = check_dns()

# 3. Firewall & Security Chains
def check_firewall():
    r_fwd = subprocess.run(["iptables", "-L", "ECOFI_FORWARD", "-n", "-v"], stdout=subprocess.PIPE, universal_newlines=True)
    r_nat = subprocess.run(["iptables", "-t", "nat", "-L", "ECOFI_PORTAL", "-n", "-v"], stdout=subprocess.PIPE, universal_newlines=True)
    r_post = subprocess.run(["iptables", "-t", "nat", "-L", "POSTROUTING", "-n", "-v"], stdout=subprocess.PIPE, universal_newlines=True)
    r_in = subprocess.run(["iptables", "-L", "ECOFI_INPUT", "-n", "-v"], stdout=subprocess.PIPE, universal_newlines=True)
    r_mangle = subprocess.run(["iptables", "-t", "mangle", "-L", "PREROUTING", "-n", "-v"], stdout=subprocess.PIPE, universal_newlines=True)
    
    r_sets = subprocess.run(["ipset", "list", "-n"], stdout=subprocess.PIPE, universal_newlines=True)
    set_names = set(r_sets.stdout.strip().split())
    
    return {
        "has_ecofi_forward": "Chain ECOFI_FORWARD" in r_fwd.stdout,
        "forward_rules_count": len(r_fwd.stdout.strip().splitlines()) - 2,
        "has_auth_forward_rule": "match-set ecofi_auth src match-set ecofi_pairs src,src" in r_fwd.stdout,
        "has_tcp_reset_reject": "reject-with tcp-reset" in r_fwd.stdout,
        "has_udp_unreach_reject": "reject-with icmp-port-unreachable" in r_fwd.stdout,
        "has_portal_redirect": "tcp dpt:80 redir ports 80" in r_nat.stdout,
        "has_nat_masquerade": "MASQUERADE" in r_post.stdout and "eth0" in r_post.stdout,
        "has_https_fast_reset": "tcp dpt:443 reject-with tcp-reset" in r_in.stdout,
        "has_anti_tethering_ttl": "TTL == 63" in r_mangle.stdout and "TTL == 127" in r_mangle.stdout,
        "has_gaming_qos_mark": "multiport dports 5000:5500,7000:8000,27000:27050,30000:30010 MARK set 0xa" in r_mangle.stdout,
        "ipsets": list(set_names)
    }

results["firewall"] = check_firewall()

# 4. Traffic Shaping & TC
def check_tc():
    r_qdisc = subprocess.run(["tc", "qdisc", "show"], stdout=subprocess.PIPE, universal_newlines=True)
    r_cls_lan = subprocess.run(["tc", "class", "show", "dev", "eth1"], stdout=subprocess.PIPE, universal_newlines=True)
    r_cls_ifb = subprocess.run(["tc", "class", "show", "dev", "ifb0"], stdout=subprocess.PIPE, universal_newlines=True)
    
    lan_classes = [l.strip() for l in r_cls_lan.stdout.strip().splitlines() if l.strip()]
    ifb_classes = [l.strip() for l in r_cls_ifb.stdout.strip().splitlines() if l.strip()]
    
    return {
        "htb_lan_active": "qdisc htb 1: dev eth1" in r_qdisc.stdout,
        "htb_ifb0_active": "qdisc htb 1: dev ifb0" in r_qdisc.stdout,
        "ingress_redirect_active": "qdisc ingress ffff: dev eth1" in r_qdisc.stdout,
        "lan_class_count": len(lan_classes),
        "ifb_class_count": len(ifb_classes),
        "classes_eth1": lan_classes,
        "classes_ifb0": ifb_classes
    }

results["traffic_shaping"] = check_tc()

# 5. Database & Entitlements State
def check_db():
    conn = sqlite3.connect("/opt/ecofi/vendo_sessions.db")
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    c.execute('''
        SELECT c.id, c.ip, c.mac, c.desired_state, c.applied_state,
               g.dl_kbps, g.ul_kbps, g.remaining_seconds, g.state as grant_state
        FROM connections c
        LEFT JOIN time_grants g ON c.selected_grant_id = g.id
        WHERE c.applied_state = 'ACTIVE'
    ''')
    active_conns = [dict(row) for row in c.fetchall()]
    
    c.execute("SELECT id, ip, mac, desired_state, applied_state FROM connections WHERE desired_state != 'ACTIVE'")
    inactive_conns = [dict(row) for row in c.fetchall()]
    
    c.execute("SELECT key, value FROM config WHERE key IN ('default_dl_kbps', 'default_ul_kbps', 'speed_override_enabled', 'qos_gaming_enabled', 'anti_tethering_enabled', 'starlink_blocker_enabled')")
    configs = dict(c.fetchall())
    
    c.execute("SELECT domain, note FROM walled_garden")
    gardens = [row["domain"] for row in c.fetchall()]
    
    c.execute("SELECT mac FROM mac_control WHERE type='block'")
    blocked = [row["mac"] for row in c.fetchall()]
    
    r_auth = subprocess.run(["ipset", "list", "ecofi_auth"], stdout=subprocess.PIPE, universal_newlines=True)
    r_pairs = subprocess.run(["ipset", "list", "ecofi_pairs"], stdout=subprocess.PIPE, universal_newlines=True)
    auth_members = [l.strip() for l in r_auth.stdout.split("Members:")[1].splitlines() if l.strip()] if "Members:" in r_auth.stdout else []
    pairs_members = [l.strip() for l in r_pairs.stdout.split("Members:")[1].splitlines() if l.strip()] if "Members:" in r_pairs.stdout else []
    
    return {
        "active_connections": active_conns,
        "inactive_connections_count": len(inactive_conns),
        "configs": configs,
        "walled_garden": gardens,
        "blocked_macs": blocked,
        "kernel_ipset_auth": auth_members,
        "kernel_ipset_pairs": pairs_members
    }

results["db_and_leases"] = check_db()

print(json.dumps(results, indent=2))
"""


def main():
    print("Connecting to live Orange Pi...")
    client = connect()
    print("Connected successfully.")

    sftp = client.open_sftp()
    with sftp.file('/tmp/remote_audit.py', 'w') as f:
        f.write(REMOTE_SCRIPT)
    sftp.close()

    print("Running comprehensive remote audit (/tmp/remote_audit.py)...\n")
    code, out, err = execute(client, 'python3 /tmp/remote_audit.py && rm -f /tmp/remote_audit.py')
    if err:
        print("REMOTE STDERR:", err)

    try:
        data = json.loads(out)
        print("==================================================")
        print("       1. NETWORK TOPOLOGY & INTERFACES           ")
        print("==================================================")
        ifaces = data['interfaces']['interfaces']
        print("  LAN (eth1)        :", ifaces.get('eth1', 'NOT FOUND'))
        print("  WAN (eth0)        :", ifaces.get('eth0', 'NOT FOUND'))
        print("  Default Gateway   :", data['interfaces']['default_route'])
        print("  IP Forwarding     :", "ENABLED (1)" if data['interfaces']['ip_forwarding'] else "DISABLED (0)")

        print("\n==================================================")
        print("       2. DNS & DISCOVERY PROTOCOLS               ")
        print("==================================================")
        for k, v in data['dns_and_discovery'].items():
            status = "PASS" if v.get('pass') else "FAIL"
            print("  [{}] {:28s} -> {}".format(status, k, v))

        print("\n==================================================")
        print("       3. FIREWALL & ACCESS CONTROL               ")
        print("==================================================")
        fw = data['firewall']
        for k, v in fw.items():
            if k == 'ipsets':
                print("  {:30s}: {}".format(k, ', '.join(v)))
            else:
                status = "PASS" if v else "FAIL"
                print("  [{}] {:28s}: {}".format(status, k, v))

        print("\n==================================================")
        print("       4. TRAFFIC CONTROL & BANDWIDTH SHAPING     ")
        print("==================================================")
        tc = data['traffic_shaping']
        print("  HTB LAN Active        :", tc['htb_lan_active'])
        print("  HTB ifb0 Active       :", tc['htb_ifb0_active'])
        print("  Ingress Redirect Active:", tc['ingress_redirect_active'])
        print("  Active LAN Classes (DL):", len(tc['classes_eth1']))
        for c in tc['classes_eth1']:
            print("    ->", c)
        print("  Active IFB Classes (UL):", len(tc['classes_ifb0']))
        for c in tc['classes_ifb0']:
            print("    ->", c)

        print("\n==================================================")
        print("       5. ENTITLEMENTS, SESSIONS & KERNEL LEASES  ")
        print("==================================================")
        db = data['db_and_leases']
        print("  Active DB Connections :", len(db['active_connections']))
        for conn in db['active_connections']:
            print("    -> IP: {:15s} MAC: {:17s} Rem: {:6.0f}s State: {:6s} DL/UL: {}/{} kbps".format(
                conn.get('ip', ''), conn.get('mac', ''),
                conn.get('remaining_seconds') or 0,
                conn.get('grant_state', ''),
                conn.get('dl_kbps', ''), conn.get('ul_kbps', '')
            ))
        print("  Inactive DB Conns     :", db['inactive_connections_count'])
        print("  Kernel ipset ecofi_auth :", db['kernel_ipset_auth'])
        print("  Kernel ipset ecofi_pairs:", db['kernel_ipset_pairs'])
        print("  Configured Limits & QoS :", db['configs'])
        print("  Walled Garden Domains   :", db['walled_garden'])
        print("  Blocked MAC List        :", db['blocked_macs'])

    except Exception as e:
        print("Failed to parse JSON output:", e)
        print("Raw output:\n", out)

    client.close()


if __name__ == '__main__':
    main()
