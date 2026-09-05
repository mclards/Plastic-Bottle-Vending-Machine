"""Linux gateway enforcement. All grants have a kernel lease and an IP/MAC pair.

The application renews leases every ten seconds. A dead process cannot leave
permanent access behind. Only the ECO-Fi chains/sets and LAN qdiscs are managed.
Compatible with the image's Python 3.5 and iptables 1.6.
"""
import ipaddress
import logging
import shutil
import subprocess
import threading

log = logging.getLogger(__name__)
lock = threading.RLock()
LAN = 'eth1'
WAN = 'eth0'
SUBNET = ipaddress.ip_network('10.0.0.0/19')
_shaped = {}
_pairs = {}
_licensed = None


def run(args, check=True):
    result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            universal_newlines=True, timeout=10)
    if check and result.returncode:
        raise RuntimeError('{}: {}'.format(' '.join(args[:5]), result.stderr.strip()))
    return result


def ipt(*args, **kwargs):
    return run(['iptables', '-w', '5'] + list(args), **kwargs)


def chain(name, table='filter'):
    ipt('-t', table, '-N', name, check=False)
    ipt('-t', table, '-F', name)


def remove_jump(parent, target, table='filter'):
    while ipt('-t', table, '-D', parent, '-j', target, check=False).returncode == 0:
        pass


def lease_set(name, kind):
    run(['ipset', 'create', name, kind, 'timeout', '30', '-exist'])
    run(['ipset', 'flush', name])


def set_license(valid):
    global _licensed
    with lock:
        if _licensed == bool(valid):
            return
        # Insert the new verdict before removing the old one: no open window.
        target = 'RETURN' if valid else 'DROP'
        ipt('-I', 'ECOFI_LICENSE', '1', '-j', target)
        while ipt('-D', 'ECOFI_LICENSE', '2', check=False).returncode == 0:
            pass
        _licensed = bool(valid)
        if not valid:
            run(['ipset', 'flush', 'ecofi_auth'])
            run(['ipset', 'flush', 'ecofi_pairs'])
            _pairs.clear()


def setup(lan='eth1', wan='eth0'):
    global LAN, WAN, _licensed
    with lock:
        LAN, WAN = lan, wan
        # Close forwarding before touching any old rules or checking tools.
        chain('ECOFI_FORWARD')
        ipt('-A', 'ECOFI_FORWARD', '-j', 'DROP')
        remove_jump('FORWARD', 'ECOFI_FORWARD')
        ipt('-I', 'FORWARD', '1', '-j', 'ECOFI_FORWARD')
        ipt('-P', 'FORWARD', 'DROP')
        if not shutil.which('ipset'):
            raise RuntimeError('ipset is required; forwarding remains closed')
        lease_set('ecofi_auth', 'hash:ip')
        lease_set('ecofi_pairs', 'hash:ip,mac')
        run(['ipset', 'create', 'ecofi_garden', 'hash:ip', '-exist'])
        run(['ipset', 'flush', 'ecofi_garden'])
        chain('ECOFI_LICENSE')
        ipt('-A', 'ECOFI_LICENSE', '-j', 'DROP')
        _licensed = False
        chain('ECOFI_MAC_BLOCK')
        # Remove obsolete grants from the old fallback implementation.
        for name in ['ECOFI_AUTH', 'ECOFI_MAC_BLOCK']:
            remove_jump('FORWARD', name)
        chain('ECOFI_AUTH')
        chain('ECOFI_AUTH_NAT', 'nat')
        remove_jump('PREROUTING', 'ECOFI_WALLED_GARDEN', 'nat')
        remove_jump('PREROUTING', 'ECOFI_PORTAL', 'nat')
        # The legacy hook was unscoped. Our new hook carries an input interface.
        while ipt('-t', 'nat', '-D', 'PREROUTING', '-i', LAN, '-j', 'ECOFI_PORTAL', check=False).returncode == 0:
            pass
        chain('ECOFI_PORTAL', 'nat')
        ipt('-t', 'nat', '-A', 'ECOFI_PORTAL', '-d', '10.0.0.1', '-j', 'RETURN')
        ipt('-t', 'nat', '-A', 'ECOFI_PORTAL', '-m', 'set', '--match-set', 'ecofi_auth', 'src',
            '-m', 'set', '--match-set', 'ecofi_pairs', 'src,src', '-j', 'RETURN')
        ipt('-t', 'nat', '-A', 'ECOFI_PORTAL', '-m', 'set', '--match-set', 'ecofi_garden', 'dst', '-j', 'RETURN')
        ipt('-t', 'nat', '-A', 'ECOFI_PORTAL', '-p', 'tcp', '--dport', '80', '-j', 'REDIRECT', '--to-ports', '80')
        ipt('-t', 'nat', '-I', 'PREROUTING', '1', '-i', LAN, '-j', 'ECOFI_PORTAL')
        if ipt('-t', 'nat', '-C', 'POSTROUTING', '-s', str(SUBNET), '-o', WAN, '-j', 'MASQUERADE', check=False).returncode:
            ipt('-t', 'nat', '-A', 'POSTROUTING', '-s', str(SUBNET), '-o', WAN, '-j', 'MASQUERADE')
        # Build behind the terminal DROP, then remove that first DROP last.
        ipt('-A', 'ECOFI_FORWARD', '-j', 'ECOFI_LICENSE')
        ipt('-A', 'ECOFI_FORWARD', '-i', LAN, '-j', 'ECOFI_MAC_BLOCK')
        ipt('-A', 'ECOFI_FORWARD', '-i', LAN, '-o', WAN, '-m', 'set', '--match-set', 'ecofi_auth', 'src',
            '-m', 'set', '--match-set', 'ecofi_pairs', 'src,src', '-j', 'ACCEPT')
        ipt('-A', 'ECOFI_FORWARD', '-i', WAN, '-o', LAN, '-m', 'set', '--match-set', 'ecofi_auth', 'dst',
            '-m', 'conntrack', '--ctstate', 'ESTABLISHED,RELATED', '-j', 'ACCEPT')
        ipt('-A', 'ECOFI_FORWARD', '-i', LAN, '-o', WAN, '-m', 'set', '--match-set', 'ecofi_garden', 'dst', '-j', 'ACCEPT')
        ipt('-A', 'ECOFI_FORWARD', '-i', WAN, '-o', LAN, '-m', 'set', '--match-set', 'ecofi_garden', 'src',
            '-m', 'conntrack', '--ctstate', 'ESTABLISHED,RELATED', '-j', 'ACCEPT')
        ipt('-A', 'ECOFI_FORWARD', '-j', 'DROP')
        ipt('-D', 'ECOFI_FORWARD', '1')
        run(['sysctl', '-w', 'net.ipv4.ip_forward=1'])
        run(['sysctl', '-w', 'net.ipv6.conf.all.forwarding=0'])
        setup_shaping()


def setup_shaping():
    _shaped.clear()
    run(['modprobe', 'ifb', 'numifbs=1'])
    run(['modprobe', 'sch_sfq'], check=False)
    run(['ip', 'link', 'set', 'ifb0', 'up'])
    for device in [LAN, 'ifb0']:
        run(['tc', 'qdisc', 'del', 'dev', device, 'root'], check=False)
        run(['tc', 'qdisc', 'add', 'dev', device, 'root', 'handle', '1:', 'htb', 'default', '99'])
        run(['tc', 'class', 'add', 'dev', device, 'parent', '1:', 'classid', '1:1', 'htb', 'rate', '100mbit'])
        run(['tc', 'class', 'add', 'dev', device, 'parent', '1:1', 'classid', '1:99', 'htb', 'rate', '100mbit'])
        run(['tc', 'qdisc', 'add', 'dev', device, 'parent', '1:99', 'handle', '99:', 'sfq', 'perturb', '10'])
    run(['tc', 'qdisc', 'del', 'dev', LAN, 'ingress'], check=False)
    run(['tc', 'qdisc', 'add', 'dev', LAN, 'ingress'])
    run(['tc', 'filter', 'add', 'dev', LAN, 'parent', 'ffff:', 'protocol', 'ip', 'u32', 'match', 'u32', '0', '0',
         'action', 'mirred', 'egress', 'redirect', 'dev', 'ifb0'])


def shape(ip, dl, ul):
    values = (max(64, int(dl)), max(64, int(ul)))
    if _shaped.get(ip) == values:
        return
    mark = 256 + (int(ipaddress.IPv4Address(ip)) & 8191)
    classid = '1:{:x}'.format(mark)
    for device, direction, rate in [(LAN, 'dst', values[0]), ('ifb0', 'src', values[1])]:
        run(['tc', 'class', 'replace', 'dev', device, 'parent', '1:1', 'classid', classid, 'htb',
             'rate', '{}kbit'.format(rate), 'ceil', '{}kbit'.format(rate), 'burst', '15k'])
        if ip not in _shaped:
            run(['tc', 'qdisc', 'add', 'dev', device, 'parent', classid, 'handle', '{:x}:'.format(mark), 'sfq', 'perturb', '10'], check=False)
            run(['tc', 'filter', 'add', 'dev', device, 'protocol', 'ip', 'parent', '1:', 'prio', str(mark),
                 'u32', 'match', 'ip', direction, ip + '/32', 'flowid', classid])
    _shaped[ip] = values


def revoke(ip):
    with lock:
        run(['ipset', 'del', 'ecofi_auth', ip, '-exist'])
        mac = _pairs.pop(ip, None)
        if mac:
            run(['ipset', 'del', 'ecofi_pairs', ip + ',' + mac, '-exist'])
        if ip in _shaped:
            mark = 256 + (int(ipaddress.IPv4Address(ip)) & 8191)
            for device in [LAN, 'ifb0']:
                run(['tc', 'filter', 'del', 'dev', device, 'protocol', 'ip', 'parent', '1:', 'prio', str(mark)], check=False)
                run(['tc', 'class', 'del', 'dev', device, 'classid', '1:{:x}'.format(mark)], check=False)
            _shaped.pop(ip, None)


def grant(ip, mac, seconds, dl, ul):
    if ipaddress.ip_address(ip) not in SUBNET or ip in ('10.0.0.1', '10.0.0.0'):
        raise ValueError('Client address must belong to the LAN')
    if not mac or mac == '00:00:00:00:00:00':
        revoke(ip)
        return False
    with lock:
        if not _licensed:
            revoke(ip)
            return False
        if _pairs.get(ip) not in (None, mac):
            revoke(ip)
        try:
            shape(ip, dl, ul)  # Never authorize traffic before both shapers succeed.
            lease = str(max(1, min(30, int(seconds))))
            run(['ipset', 'add', 'ecofi_pairs', ip + ',' + mac, 'timeout', lease, '-exist'])
            run(['ipset', 'add', 'ecofi_auth', ip, 'timeout', lease, '-exist'])
            _pairs[ip] = mac
            return True
        except Exception:
            revoke(ip)
            raise


def policies(blocked_macs, garden_ips):
    with lock:
        # Prepare a fresh chain, swap its contents using iptables-restore without
        # flushing other tables. A brief conservative DROP covers the update.
        ipt('-I', 'ECOFI_MAC_BLOCK', '1', '-j', 'DROP')
        while ipt('-D', 'ECOFI_MAC_BLOCK', '2', check=False).returncode == 0:
            pass
        for mac in sorted(blocked_macs):
            ipt('-A', 'ECOFI_MAC_BLOCK', '-m', 'mac', '--mac-source', mac, '-j', 'DROP')
        for ip, mac in list(_pairs.items()):
            if mac.lower() in blocked_macs:
                revoke(ip)
        ipt('-D', 'ECOFI_MAC_BLOCK', '1')
        run(['ipset', 'create', 'ecofi_garden_next', 'hash:ip', '-exist'])
        run(['ipset', 'flush', 'ecofi_garden_next'])
        for ip in sorted(garden_ips):
            if ipaddress.ip_address(ip).version == 4:
                run(['ipset', 'add', 'ecofi_garden_next', ip, '-exist'])
        run(['ipset', 'swap', 'ecofi_garden_next', 'ecofi_garden'])
