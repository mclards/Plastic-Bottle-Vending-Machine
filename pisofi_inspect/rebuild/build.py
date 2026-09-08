"""Patch only the mounted staging image, with source-hash guards and a change manifest."""
import base64
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
from issue_license import issue

HERE = Path(__file__).resolve().parent
ROOT = Path('/var/tmp/pisofi-custom-test-v1/root')
APP = '/.cache/tmp/55/05/pfi'
PAYLOAD = HERE / 'payload'
PRIVATE = HERE / 'private'
LOGS = HERE / 'logs'
PRIVATE.mkdir(exist_ok=True)
LOGS.mkdir(exist_ok=True)
subprocess.run(['mountpoint', '-q', str(ROOT)], check=True)
source = {r['path']: r['sha256'] for r in csv.DictReader((HERE.parent / 'analysis/filesystem_sha256.tsv').open(), delimiter='\t')}
changes = []

def path(rel):
    p = ROOT / rel.lstrip('/')
    # Do not follow an absolute image symlink into the WSL host.
    for part in [p] + list(p.parents):
        if part == ROOT: break
        if part.is_symlink(): raise RuntimeError('Symlink mutation refused: ' + str(part))
    return p

def get(rel):
    p = path(rel)
    data = p.read_bytes()
    if hashlib.sha256(data).hexdigest() != source[rel]: raise RuntimeError('Base file changed: ' + rel)
    return data.decode('utf-8')

def put(rel, data, mode=0o644):
    p = path(rel)
    old = hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None
    p.parent.mkdir(parents=True, exist_ok=True)
    raw = data.encode() if isinstance(data, str) else data
    p.write_bytes(raw)
    p.chmod(mode)
    os.chown(p, 0, 0)
    changes.append({'path':rel, 'before_sha256':old, 'after_sha256':hashlib.sha256(raw).hexdigest()})

def replace_function(text, name, body):
    m = re.search(r'\bfunction\s+'+re.escape(name)+r'\s*\(', text)
    if not m: raise RuntimeError('Function missing: '+name)
    start = text.index('{', m.end())
    i, level, quote, comment = start+1, 1, None, None
    while level:
        c, nxt = text[i], text[i:i+2]
        if comment:
            if comment == 'line' and c == '\n': comment = None
            elif comment == 'block' and nxt == '*/': comment = None; i += 1
        elif quote:
            if c == '\\': i += 1
            elif c == quote: quote = None
        elif nxt in ('//', '/*'): comment = 'line' if nxt == '//' else 'block'; i += 1
        elif c == '#': comment = 'line'
        elif c in "\"'": quote = c
        elif c == '{': level += 1
        elif c == '}': level -= 1
        i += 1
    return text[:start+1]+'\n'+body+'\n'+text[i-1:]

def cut(text, first, last, replacement):
    start = text.index(first)
    end = text.index(last, start) + len(last)
    return text[:start]+replacement+text[end:]

key = PRIVATE / 'license-signing.pem'
if not key.exists():
    subprocess.run(['openssl','genpkey','-algorithm','RSA','-pkeyopt','rsa_keygen_bits:3072','-out',str(key)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    key.chmod(0o600)
public = subprocess.run(['openssl','pkey','-in',str(key),'-pubout'], capture_output=True, check=True).stdout
put('/etc/ecofi/license-public.pem', public)
license_doc = issue(key)
put('/etc/ecofi/license.json', json.dumps(license_doc, indent=2)+'\n')
(PRIVATE / 'test-license.json').write_text(json.dumps(license_doc, indent=2)+'\n')

for name in ['LocalLicense.php','DeviceLicense.php','DeviceChecker.php']:
    if name != 'LocalLicense.php': get(APP+'/app/Pisofi/Server/'+name)
    put(APP+'/app/Pisofi/Server/'+name, (PAYLOAD/name).read_bytes())

for base in [APP+'/scripts','/var/www/html/pisofi/scripts']:
    cron = get(base+'/cron')
    cron = cut(cron, 'V9zDw: try', 'goto hM6ub;', 'V9zDw: goto hM6ub;')
    cron, count = re.subn(r'\$S7SUV\s*=\s*\$BoUx0\([^;]+;', "$S7SUV = '';", cron)
    assert count == 1
    cron, count = re.subn(r'eval\([^;]+;', '/* Encoded repair execution removed. */', cron)
    assert count == 1
    assert 'hack3d' not in cron and 'sudo rm -rf' not in cron
    put(base+'/cron', cron, 0o755)
    kicker = get(base+'/kicker.php')
    kicker = cut(kicker, 'iXMr0: try', 'goto SLDbs;', 'iXMr0: goto SLDbs;')
    kicker, count = re.subn(r'\$ZXckI\s*=\s*\$aYguy\([^;]+;', "$ZXckI = '';", kicker)
    assert count == 1
    kicker, count = re.subn(r'eval\([^;]+;', '/* Encoded repair execution removed. */', kicker)
    assert count == 1
    put(base+'/kicker.php', kicker, 0o755)
    put(base+'/coinrdr', (PAYLOAD/'coinrdr').read_bytes(), 0o755)
    put(base+'/check_status', "<?php\n// Local licensing is verified by DeviceLicense. No vendor telemetry.\nexit(0);\n", 0o755)
    put(base+'/check_verifier', "<?php\n// Vendor verifier is disabled in the custom test image.\nexit(0);\n", 0o755)
for base in [APP+'/public/img','/var/www/html/pisofi/public/img']:
    for n in [9,10,11]:
        rel = base+'/user%d-128x128.jpg'%n
        p = path(rel)
        if p.exists():
            changes.append({'path':rel, 'before_sha256':hashlib.sha256(p.read_bytes()).hexdigest(), 'removed':True})
            p.unlink()

# Old cloud registration UI becomes a local status view; license files are installed through SSH.
middleware = '''<?php
namespace App\\Middleware;
class DeviceConfigurationMiddleware extends Middleware {
    public function __invoke($request, $response, $next) {
        $s = \\App\\Pisofi\\Server\\LocalLicense::load();
        if (!$s['valid']) return $response->withStatus(403)->withHeader('Content-Type','text/plain')->write('Local license: '.$s['reason'].'. Administrator SSH access remains available.');
        return $next($request, $response);
    }
}
'''
put(APP+'/app/Middleware/DeviceConfigurationMiddleware.php', middleware)
put(APP+'/app/Middleware/DeviceRegistrationMiddleware.php', middleware.replace('DeviceConfigurationMiddleware','DeviceRegistrationMiddleware'))
reg = get(APP+'/app/Pisofi/Server/DeviceRegistrationManager.php')
body = "return ['status'=>'OK', 'message'=>'This custom image uses a locally signed Eco-Fi license.', 'data'=>['owner'=>DeviceChecker::getDeviceOwner(),'license'=>LocalLicense::metadata()]];"
reg = replace_function(reg, 'verifyDeviceRegistration', body)
reg = replace_function(reg, 'registerDevice', body)
put(APP+'/app/Pisofi/Server/DeviceRegistrationManager.php',reg)
userapi = get(APP+'/app/Pisofi/Server/UserApi.php')
put(APP+'/app/Pisofi/Server/UserApi.php', replace_function(userapi, 'getKey', "return LocalLicense::load()['valid'] ? 'Eco-Fi-LOCAL-OWNER' : false;"))
request = get(APP+'/app/Pisofi/Server/PisofiServerRequest.php')
request = replace_function(request,'send', "if (is_callable($callback)) call_user_func($callback, ['status'=>'NG','data'=>null,'message'=>'Vendor cloud integration disabled in this test image.']); return false;")
request = replace_function(request,'download', 'return false;')
put(APP+'/app/Pisofi/Server/PisofiServerRequest.php',request)

disabled = "return $response->withStatus(409)->withJson(['result'=>['status'=>'NG','message'=>'Vendor update and activation operations are disabled in this custom image. Install signed local licenses through SSH.'], 'token'=>$this->token]);"
tools = get(APP+'/app/Controllers/ToolsController.php')
for name in ['runVerifier','applyPatch']:
    tools = replace_function(tools,name,disabled)
tools = tools.replace('sudo /usr/local/bin/pfi-speed-test $serverId >', 'sudo /usr/local/bin/pfi-speed-test {$serverId} >')
tools = tools.replace("$serverId = $request->getParam('server');", "$serverId = $request->getParam('server'); if (!is_scalar($serverId) || !ctype_digit((string)$serverId)) return $response->withStatus(400)->withJson(['error'=>'Invalid server ID']);")
put(APP+'/app/Controllers/ToolsController.php',tools)
package = get(APP+'/app/Controllers/PackageController.php')
for name in ['index','offline','importOfflinePackage','download','installOfflinePackage','install','rollback']:
    package = replace_function(package,name,disabled)
put(APP+'/app/Controllers/PackageController.php',package)
sysctrl = get(APP+'/app/Controllers/SystemController.php')
for name in ['revokeLicense','registerDevice','restoreBackup','toggleRemoteManagement']:
    sysctrl = replace_function(sysctrl,name,disabled)
put(APP+'/app/Controllers/SystemController.php',sysctrl)

# Keep legacy consumers consistent; signed license bytes, not mutable database values, are authoritative.
setting = get(APP+'/app/Models/PisofiSetting.php')
guard = "\n        if ($key === 'license') return json_encode(\\App\\Pisofi\\Server\\LocalLicense::metadata());\n        if (in_array($key, ['is_registered','system_registered'], true)) return 1;\n        if (in_array($key, ['initial_registration','needs_verifier_rerun'], true)) return 0;\n"
setting = re.sub(r'(public static function getValue\([^\n]+\)\s*\{)', lambda m:m[0]+guard, setting, count=1)
put(APP+'/app/Models/PisofiSetting.php',setting)

# Retain normal authentication while replacing predictable session selection.
bootstrap = get(APP+'/bootstrap/app.php')
start = bootstrap.index('$host = ')
end = bootstrap.index('date_default_timezone_set',start)
bootstrap = bootstrap[:start]+"// Use PHP-generated session IDs, never IP/User-Agent derived IDs.\n"+bootstrap[end:]
bootstrap = bootstrap.replace('ini_set("display_errors", 1);','ini_set("display_errors", 0);')
bootstrap = bootstrap.replace("ini_set('session.use_strict_mode', 0);", "ini_set('session.use_strict_mode', 1);")
put(APP+'/bootstrap/app.php',bootstrap)
auth = get(APP+'/app/Auth/Auth.php')
auth = auth.replace("if (password_verify($password, $user->password)){", "if (password_verify($password, $user->password)){\n            session_regenerate_id(true);")
put(APP+'/app/Auth/Auth.php',auth)
attempt = get(APP+'/app/Models/LoginAttempt.php')
attempt, n = re.subn(r"('pass'\s*=>\s*)\$pass", r"\1'[redacted]'", attempt)
assert n == 1
put(APP+'/app/Models/LoginAttempt.php',attempt)

# Locally generated credentials. No password or signing private key is embedded in source code.
root_password = secrets.token_urlsafe(18)
admin_password = secrets.token_urlsafe(18)
root_hash = subprocess.run(['openssl','passwd','-6','-stdin'],input=root_password.encode(),capture_output=True,check=True).stdout.decode().strip()
php = ['chroot',str(ROOT),'/tmp/ecofi-qemu-arm-static','/usr/bin/php7.0','-n']
admin_hash = subprocess.run(php+['-r','echo password_hash(stream_get_contents(STDIN), PASSWORD_BCRYPT);'],input=admin_password.encode(),capture_output=True,check=True).stdout.decode().strip()
assert admin_hash.startswith('$2y$')
shadow = get('/etc/shadow')
shadow,n = re.subn(r'^(root|pi):[^:]*:',lambda m:m[1]+':'+root_hash+':',shadow,flags=re.M)
assert n == 2
put('/etc/shadow',shadow,0o640)
os.chown(ROOT/'etc/shadow',0,42)
put('/etc/ecofi/admin-password.hash',admin_hash+'\n',0o600)
put('/opt/ecofi-test/provision.php',(PAYLOAD/'provision.php').read_bytes(),0o600)
(PRIVATE/'TEST_CREDENTIALS.txt').write_text('Custom PisoFi functional test image\n\nSSH/console user: root (pi also available)\nSSH/console password: '+root_password+'\n\nDashboard URL: http://10.0.0.1/admin\nDashboard user: administrator\nDashboard password: '+admin_password+'\n\nSigning private key: license-signing.pem (keep on this PC; absent from image)\n')

sshd = get('/etc/ssh/sshd_config')
sshd = re.sub(r'^#?PasswordAuthentication.*$', 'PasswordAuthentication yes', sshd, flags=re.M)
sshd = re.sub(r'^#?PermitEmptyPasswords.*$', 'PermitEmptyPasswords no', sshd, flags=re.M)
put('/etc/ssh/sshd_config',sshd)
for p in (ROOT/'etc/ssh').glob('ssh_host_*'):
    if p.is_file() and not p.is_symlink(): p.unlink()

# Mask vendor remote services in the staged filesystem without running systemctl.
for unit in ['pisofi_ngrok','pisofi_datasync','pisofi_remotebackup','pisofi_remotesubscriber','zerotier-one']:
    for base in ['etc/systemd/system','etc/systemd/system/multi-user.target.wants']:
        p = ROOT/base/(unit+'.service')
        if p.is_symlink() or p.is_file(): p.unlink()
    (ROOT/'etc/systemd/system'/(unit+'.service')).symlink_to('/dev/null')
    changes.append({'path':'/etc/systemd/system/'+unit+'.service','masked':True})
for rel in ['var/lib/zerotier-one/identity.secret','var/lib/zerotier-one/identity.public','var/lib/zerotier-one/authtoken.secret']:
    p=path('/'+rel)
    if p.is_file(): p.unlink()

for name in ['ecofi-firstboot','ecofi-test-network']:
    put('/usr/local/sbin/'+name,(PAYLOAD/name).read_bytes(),0o755)
put('/etc/systemd/system/ecofi-firstboot.service','''[Unit]
Description=Initialize custom test image identity and management access
After=network.target
Before=ssh.service
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/ecofi-firstboot
RemainAfterExit=yes
[Install]
WantedBy=multi-user.target
''')
put('/etc/systemd/system/ecofi-test-network.service','''[Unit]
Description=Restore custom image bench management access
After=network.target
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/ecofi-test-network
''')
put('/etc/systemd/system/ecofi-test-network.timer','''[Unit]
Description=Maintain bench management access after firewall reloads
[Timer]
OnBootSec=20
OnUnitActiveSec=30
Unit=ecofi-test-network.service
[Install]
WantedBy=timers.target
''')
for kind,name in [('multi-user','ecofi-firstboot.service'),('timers','ecofi-test-network.timer')]:
    p=ROOT/('etc/systemd/system/'+kind+'.target.wants')/name
    p.parent.mkdir(parents=True,exist_ok=True)
    p.symlink_to('/etc/systemd/system/'+name)
put('/etc/ecofi/build-info.json',json.dumps({'name':'PisoFi Custom Functional Test','build':'v1','base_sha256':'d72e56563aca1af9d5e6298fa9bea5fec716a3d4d3565f6d8eabe0398f52f1dd','license':'Eco-Fi signed TEST, non-expiring, wildcard device','bench_ip':'10.0.0.1/19'},indent=2)+'\n')
(LOGS/'change_manifest.json').write_text(json.dumps(changes,indent=2))
print('Patched paths:',len(changes),'; credentials saved to private/TEST_CREDENTIALS.txt')
