"""Apply integration refinements found by the staged runtime checks."""
import json
from pathlib import Path
import shutil
import uuid

HERE=Path(__file__).resolve().parent
ROOT=Path('/var/tmp/pisofi-custom-test-v1/root')
report=json.loads((HERE/'logs/database_provision.json').read_text())
networks={n['interface_name']:n for n in report['networks']}
(ROOT/'etc/ecofi/network-defaults.json').write_text(json.dumps(networks,indent=2))
(ROOT/'etc/ecofi/network-defaults.json').chmod(0o600)
for src,dst,mode in [('network.php','opt/ecofi-test/network.php',0o600),('ecofi-test-network','usr/local/sbin/ecofi-test-network',0o755)]:
    shutil.copyfile(HERE/'payload'/src,ROOT/dst);(ROOT/dst).chmod(mode)
profiles=ROOT/'etc/NetworkManager/system-connections'
for filename,iface in [('Wired connection 1','eth0'),('eth1','eth1')]:
    ip='method=auto' if iface=='eth0' else 'method=manual\naddress1=10.0.0.1/19\nnever-default=true\ndns=1.1.1.1;1.0.0.1;'
    (profiles/filename).write_text('[connection]\nid=ecofi-'+iface+'\nuuid='+str(uuid.uuid4())+'\ntype=ethernet\nautoconnect=true\ninterface-name='+iface+'\n\n[ethernet]\n\n[ipv4]\n'+ip+'\n\n[ipv6]\nmethod=link-local\n')
    (profiles/filename).chmod(0o600)
# The firstboot unit creates fresh SSH host keys; systemd creates its own machine ID.
(ROOT/'etc/machine-id').write_text('')
dbus=ROOT/'var/lib/dbus/machine-id'
if dbus.exists() or dbus.is_symlink():dbus.unlink()
dbus.symlink_to('/etc/machine-id')
# Seed an accurate offline clock without altering the host clock.
from datetime import datetime,timezone
(ROOT/'etc/fake-hwclock.data').write_text(datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')+'\n')
shutil.copyfile(ROOT/'etc/ecofi/license.json',HERE/'private/test-license.json')
print('Applied portable network profiles and fresh-identity provisioning.')
