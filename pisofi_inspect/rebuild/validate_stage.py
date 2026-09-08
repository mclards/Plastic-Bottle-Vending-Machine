"""Run target PHP syntax checks and offline signature checks under ARM emulation."""
import base64
import copy
import json
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = Path('/var/tmp/pisofi-custom-test-v1/root')
LOGS = HERE/'logs'
PHP = ['chroot',str(ROOT),'/tmp/ecofi-qemu-arm-static','/usr/bin/php7.0','-n','-d','extension=json.so']
subprocess.run(['mountpoint','-q',str(ROOT)],check=True)
files = []
for base in [ROOT/'.cache/tmp/55/05/pfi/app',ROOT/'.cache/tmp/55/05/pfi/bootstrap',ROOT/'.cache/tmp/55/05/pfi/scripts',ROOT/'var/www/html/pisofi/scripts',ROOT/'opt/ecofi-test']:
    for p in base.rglob('*'):
        if p.is_file() and (p.suffix == '.php' or p.read_bytes()[:5] == b'<?php'):
            files.append(p)
failures = []
for p in ([] if '--license-only' in sys.argv else files):
    rel = '/'+str(p.relative_to(ROOT))
    result = subprocess.run(PHP+['-l',rel],capture_output=True,text=True)
    if result.returncode: failures.append({'path':rel,'output':result.stdout+result.stderr})
if '--license-only' not in sys.argv:
    (LOGS/'php_lint.json').write_text(json.dumps({'checked':len(files),'failures':failures},indent=2))
    print('Target PHP lint:',len(files),'files,',len(failures),'failures',flush=True)
if failures:
    print(json.dumps(failures[:5],indent=2))
    raise SystemExit(1)

doc = json.loads((ROOT/'etc/ecofi/license.json').read_text())
payload = json.loads(base64.b64decode(doc['payload']))
key = HERE/'private/license-signing.pem'
def sign(p):
    raw=json.dumps(p,sort_keys=True,separators=(',',':')).encode()
    sig=subprocess.run(['openssl','dgst','-sha256','-sign',str(key)],input=raw,capture_output=True,check=True).stdout
    return json.dumps({'payload':base64.b64encode(raw).decode(),'signature':base64.b64encode(sig).decode()})
cases=[{'name':'valid test license','document':json.dumps(doc),'device':'TEST-BOARD','now':1800000000,'expected':True}]
tampered=copy.deepcopy(payload);tampered['features']['vendos']=999
bad=copy.deepcopy(doc);bad['payload']=base64.b64encode(json.dumps(tampered).encode()).decode()
cases.append({'name':'unsigned feature edit','document':json.dumps(bad),'device':'TEST-BOARD','now':1800000000,'expected':False})
for name,edits,hwid,want in [
    ('valid device license',{'scope':'DEVICE','device':'BOARD-A'},'BOARD-A',True),
    ('hardware mismatch',{'scope':'DEVICE','device':'BOARD-A'},'BOARD-B',False),
    ('expired signed license',{'expires_at':1700000000},'BOARD-A',False),
    ('invalid production wildcard',{'scope':'DEVICE'},'BOARD-A',False),
    ('unsupported issuer',{'issuer':'OTHER'},'BOARD-A',False),
    ('malformed signed features',{'features':{'charging':True,'vendos':-1,'desktops':1}},'BOARD-A',False),
]:
    p=copy.deepcopy(payload);p.update(edits)
    cases.append({'name':name,'document':sign(p),'device':hwid,'now':1800000000,'expected':want})
cases.append({'name':'invalid envelope','document':'{}','device':'BOARD-A','now':1800000000,'expected':False})
reasons = {'valid test license':'Verified offline','valid device license':'Verified offline',
           'unsigned feature edit':'Invalid license signature','hardware mismatch':'Hardware identity mismatch',
           'expired signed license':'License expired','invalid production wildcard':'Wildcard is restricted to signed test licenses',
           'unsupported issuer':'Unsupported license','malformed signed features':'Invalid feature entitlements',
           'invalid envelope':'Malformed license envelope'}
for case in cases: case['expected_reason'] = reasons[case['name']]
(ROOT/'tmp/ecofi-license-cases.json').write_text(json.dumps(cases))
test='''<?php
require '/.cache/tmp/55/05/pfi/app/Pisofi/Server/LocalLicense.php';
require '/.cache/tmp/55/05/pfi/app/Pisofi/Server/IDeviceLicense.php';
require '/.cache/tmp/55/05/pfi/app/Pisofi/Server/DeviceLicense.php';
$cases=json_decode(file_get_contents('/tmp/ecofi-license-cases.json'),true);
$key=file_get_contents('/etc/ecofi/license-public.pem'); $results=[]; $fail=0;
foreach($cases as $c) {
    $r=\\App\\Pisofi\\Server\\LocalLicense::validateDocument($c['document'],$key,$c['device'],$c['now']);
    $ok=$r['valid']===$c['expected'] && $r['reason']===$c['expected_reason']; if(!$ok)$fail++;
    $results[]=['case'=>$c['name'],'passed'=>$ok,'reason'=>$r['reason']];
}
$license=new \\App\\Pisofi\\Server\\DeviceLicense();
if(!$license->isLicensed() || $license->registeredVendos()!==256 || strlen($license->licenseKey())!==35 || !$license->hasCharging())$fail++;
echo json_encode(['cases'=>$results,'adapter_valid'=>$license->isLicensed(),'failures'=>$fail],JSON_PRETTY_PRINT),"\\n";
exit($fail?1:0);
'''
(ROOT/'tmp/ecofi-test-license.php').write_text(test)
result=subprocess.run(PHP+['/tmp/ecofi-test-license.php'],capture_output=True,text=True)
(LOGS/'license_tests.json').write_text(result.stdout)
print(result.stdout,flush=True)
if result.returncode:
    print(result.stderr)
    raise SystemExit(1)
for p in (ROOT/'tmp').glob('ecofi-*'):
    if p.name != 'ecofi-qemu-arm-static' and p.is_file():p.unlink()
