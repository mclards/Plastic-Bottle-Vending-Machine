import json
from pathlib import Path
import shutil
import subprocess
import os
import stat

HERE=Path(__file__).resolve().parent
ROOT=Path('/var/tmp/pisofi-custom-test-v1/root')
creds=(HERE/'private/TEST_CREDENTIALS.txt').read_text()
password=creds.split('SSH/console password: ',1)[1].splitlines()[0]
shadow={line.split(':')[0]:line.split(':')[1] for line in (ROOT/'etc/shadow').read_text().splitlines()}
expected=shadow['root'];salt=expected.split('$')[2]
actual=subprocess.run(['openssl','passwd','-6','-salt',salt,'-stdin'],input=password.encode(),capture_output=True,check=True).stdout.decode().strip()
assert actual==expected==shadow['pi']
shutil.copyfile(HERE/'payload/ecofi-firstboot',ROOT/'usr/local/sbin/ecofi-firstboot')
(ROOT/'usr/local/sbin/ecofi-firstboot').chmod(0o755)
(ROOT/'run/sshd').mkdir(exist_ok=True);(ROOT/'run/sshd').chmod(0o755)
cmd=['chroot',str(ROOT),'/tmp/ecofi-qemu-arm-static']
for name,minor in [('null',3),('urandom',9),('random',8)]:
    p=ROOT/'dev'/name
    if not p.exists():os.mknod(p,stat.S_IFCHR|0o666,os.makedev(1,minor))
subprocess.run(cmd+['/usr/bin/ssh-keygen','-A'],check=True,capture_output=True)
r=subprocess.run(cmd+['/usr/sbin/sshd','-T'],check=True,capture_output=True,text=True)
config=dict(line.split(' ',1) for line in r.stdout.splitlines() if ' ' in line)
assert config['permitrootlogin']=='yes'
assert config['passwordauthentication']=='yes'
assert config['permitemptypasswords']=='no'
for p in (ROOT/'etc/ssh').glob('ssh_host_*'):
    if p.is_file() and not p.is_symlink():p.unlink()
for name in ['null','random','urandom']:(ROOT/'dev'/name).unlink()
(HERE/'logs/access_checks.json').write_text(json.dumps({'root_and_pi_password_hash_verified':True,'ssh_key_generation_tested':True,'sshd_configuration_valid':True,'effective':{k:config[k] for k in ['port','permitrootlogin','passwordauthentication','permitemptypasswords']},'test_host_keys_removed':True},indent=2))
print('Root/pi password hashes and target SSH configuration verified; temporary host keys removed.')
