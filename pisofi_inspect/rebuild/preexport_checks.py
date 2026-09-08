"""Final targeted checks after integration changes, then stop the isolated database cleanly."""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time

HERE=Path(__file__).resolve().parent
ROOT=Path('/var/tmp/pisofi-custom-test-v1/root')
LOGS=HERE/'logs'
shutil.copyfile(HERE/'payload/ecofi-license',ROOT/'usr/local/sbin/ecofi-license')
(ROOT/'usr/local/sbin/ecofi-license').chmod(0o755)
PHP=['chroot',str(ROOT),'/tmp/ecofi-qemu-arm-static','/usr/bin/php7.0','-n','-d','extension=json.so']
for rel in ['/opt/ecofi-test/network.php','/usr/local/sbin/ecofi-license']:
    subprocess.run(PHP+['-l',rel],check=True)
for rel in ['/usr/local/sbin/ecofi-firstboot','/usr/local/sbin/ecofi-test-network']:
    subprocess.run(['bash','-n',str(ROOT/rel.lstrip('/'))],check=True)
status=subprocess.run(PHP+['/usr/local/sbin/ecofi-license','status'],capture_output=True,text=True,check=True)
(LOGS/'license_cli_status.json').write_text(status.stdout)
check='''<?php
$c=parse_ini_file('/etc/environment');$d=new PDO('mysql:unix_socket=/run/mysqld/mysqld.sock;dbname=pisofi',$c['KCFGDBU'],$c['KCFGDBP']);
$r=$d->query('CHECK TABLE users,settings,networks,active_clients,connection_sessions QUICK')->fetchAll(PDO::FETCH_ASSOC);
echo json_encode($r,JSON_PRETTY_PRINT),"\\n";foreach($r as $row)if($row['Msg_type']==='error')exit(1);
'''
(ROOT/'tmp/ecofi-check-tables.php').write_text(check)
r=subprocess.run(PHP+['-d','extension=pdo.so','-d','extension=mysqlnd.so','-d','extension=pdo_mysql.so','/tmp/ecofi-check-tables.php'],capture_output=True,text=True,check=True)
(LOGS/'database_check.json').write_text(r.stdout)
print(r.stdout,flush=True)
pid=int((ROOT/'run/mysqld/ecofi-build.pid').read_text())
cmdline=Path('/proc')/str(pid)/'cmdline'
assert b'/usr/sbin/mysqld' in cmdline.read_bytes() and b'--skip-networking' in cmdline.read_bytes()
os.kill(pid,signal.SIGTERM)
for _ in range(100):
    if not (ROOT/'run/mysqld/ecofi-build.pid').exists():break
    time.sleep(.2)
else:raise RuntimeError('Database did not shut down cleanly')
db_log=(ROOT/'run/mysqld/ecofi-build.log').read_text()
(LOGS/'database_runtime.log').write_text(db_log)
assert 'Shutdown complete' in db_log
print('Database shut down cleanly.',flush=True)
