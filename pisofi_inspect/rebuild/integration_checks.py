"""Check real database/authentication integration without running OS-changing portal controllers."""
import json
from pathlib import Path
import subprocess

HERE=Path(__file__).resolve().parent
ROOT=Path('/var/tmp/pisofi-custom-test-v1/root')
PHP=['chroot',str(ROOT),'/tmp/ecofi-qemu-arm-static','/usr/bin/php7.0','-n']
for module in ['json','ctype','mbstring','pdo','mysqlnd','pdo_mysql']:
    PHP+=['-d','extension='+module+'.so']
credentials=(HERE/'private/TEST_CREDENTIALS.txt').read_text()
password=credentials.split('Dashboard password: ',1)[1].splitlines()[0]
script='''<?php
ini_set('session.save_path','/tmp');session_start();
require '/.cache/tmp/55/05/pfi/vendor/autoload.php';
$cfg=parse_ini_file('/etc/environment');
$capsule=new \\Illuminate\\Database\\Capsule\\Manager();
$capsule->addConnection(['driver'=>'mysql','unix_socket'=>'/run/mysqld/mysqld.sock','database'=>'pisofi','username'=>$cfg['KCFGDBU'],'password'=>$cfg['KCFGDBP'],'charset'=>'utf8','collation'=>'utf8_unicode_ci','prefix'=>'']);
$capsule->setAsGlobal();$capsule->bootEloquent();
$password=stream_get_contents(STDIN);$checks=[];
$auth=new \\App\\Auth\\Auth();
$checks['wrong_password_rejected']=$auth->attempt('administrator','NOT-THE-GENERATED-PASSWORD')===false;
$checks['new_admin_login_succeeds']=$auth->attempt('administrator',$password)===true;
$checks['authenticated_session_valid']=$auth->check()===true;
$checks['admin_role']=$_SESSION['user_admin']===true;
$license=new \\App\\Pisofi\\Server\\DeviceLicense();
$checks['signed_license_with_autoloader']=$license->isLicensed() && $license->registeredPCs()===256;
$checks['registration_compatibility']=\\App\\Pisofi\\Server\\DeviceChecker::isRegistered()===true;
$checks['settings_license_compatibility']=json_decode(\\App\\Models\\PisofiSetting::getValue('license'),true)['actor']==='ecofi_signed_local';
$db=$capsule->getConnection();$db->beginTransaction();
try {
    $attempt=\\App\\Models\\LoginAttempt::log('ecofi-build-check','REDACT_TEST_NO_SECRET','127.0.0.1','Offline integration test');
    $checks['failed_password_redacted']=$attempt->pass==='[redacted]';
} finally { $db->rollBack(); }
$checks['users_preserved']=\\App\\Models\\User::count()===1;
echo json_encode(['checks'=>$checks,'passed'=>!in_array(false,$checks,true)],JSON_PRETTY_PRINT),"\\n";
exit(in_array(false,$checks,true)?1:0);
'''
(ROOT/'tmp/ecofi-integration.php').write_text(script)
r=subprocess.run(PHP+['/tmp/ecofi-integration.php'],input=password,capture_output=True,text=True)
(HERE/'logs/auth_integration.json').write_text(r.stdout)
print(r.stdout,flush=True)
if r.returncode:
    print(r.stderr)
    raise SystemExit(1)

snap='''<?php
$c=parse_ini_file('/etc/environment');$db=new PDO('mysql:unix_socket=/run/mysqld/mysqld.sock;dbname=pisofi',$c['KCFGDBU'],$c['KCFGDBP']);
echo json_encode($db->query('SELECT interface_name,status,is_wan,is_main,ip_address FROM networks ORDER BY interface_name')->fetchAll(PDO::FETCH_ASSOC));
'''
(ROOT/'tmp/ecofi-network-snapshot.php').write_text(snap)
network=[]
for mode in ['single','dual']:
    result=subprocess.run(PHP+['/opt/ecofi-test/network.php','--'+mode],capture_output=True,text=True,check=True)
    rows=json.loads(subprocess.run(PHP+['/tmp/ecofi-network-snapshot.php'],capture_output=True,text=True,check=True).stdout)
    d={x['interface_name']:x for x in rows}
    if mode=='single':
        ok=int(d['eth0']['is_wan'])==0 and int(d['eth0']['is_main'])==1 and d['eth0']['ip_address']=='10.0.0.1' and int(d['eth1']['status'])==2
    else:
        ok=int(d['eth0']['is_wan'])==1 and int(d['eth1']['status'])==1 and int(d['eth1']['is_main'])==1 and d['eth1']['ip_address']=='10.0.0.1'
    network.append({'mode':mode,'passed':ok,'records':rows})
    assert ok,mode+' network adaptation failed'
(HERE/'logs/network_integration.json').write_text(json.dumps(network,indent=2))
print('Single-port and dual-port database adaptation checks passed.',flush=True)
for p in (ROOT/'tmp').glob('ecofi-*'):
    if p.name!='ecofi-qemu-arm-static' and p.is_file():p.unlink()
