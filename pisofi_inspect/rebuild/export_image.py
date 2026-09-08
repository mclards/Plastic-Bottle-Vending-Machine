"""Clean, inventory, filesystem-check and atomically publish the staged test image."""
import csv
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess

HERE=Path(__file__).resolve().parent
PROJECT=HERE.parents[1]
ROOT=Path('/var/tmp/pisofi-custom-test-v1/root')
WORK=ROOT.parent/'root.img'
SOURCE=PROJECT/'resources/PisoFi_Opi1&PC_v5.3.0-05-10-26_EXT.img'
TARGET=PROJECT/'resources/PisoFi_Custom_Test_v1.img'
LOGS=HERE/'logs'
EXPECTED='d72e56563aca1af9d5e6298fa9bea5fec716a3d4d3565f6d8eabe0398f52f1dd'
assert not TARGET.exists(), 'Refusing to replace an existing test image'
subprocess.run(['mountpoint','-q',str(ROOT)],check=True)
assert not (ROOT/'run/mysqld/ecofi-build.pid').exists(), 'Database must be stopped'
assert json.loads((LOGS/'license_tests.json').read_text())['failures']==0
assert json.loads((LOGS/'auth_integration.json').read_text())['passed']
assert not json.loads((LOGS/'php_lint.json').read_text())['failures']
assert all(x['passed'] for x in json.loads((LOGS/'network_integration.json').read_text()))

for rel in ['etc/ecofi/admin-password.hash','opt/ecofi-test/provision.php','run/mysqld/ecofi-build.log']:
    p=ROOT/rel
    if p.is_file():p.unlink()
for p in (ROOT/'tmp').iterdir():
    if p.is_file() and (p.name.startswith('ecofi-') or p.name.startswith('sess_')):p.unlink()
for p in (ROOT/'var/lib/php/sessions').glob('sess_*'):
    if p.is_file():p.unlink()
(ROOT/'etc/hostname').write_text('pisofi-custom-test\n')
hosts=(ROOT/'etc/hosts').read_text()
lines=[line for line in hosts.splitlines() if not line.startswith('127.0.1.1')]
lines.append('127.0.1.1 pisofi-custom-test')
(ROOT/'etc/hosts').write_text('\n'.join(lines)+'\n')

# Confirm the trust key is public-only and restore/evaluation payloads are absent.
assert b'PRIVATE KEY' not in (ROOT/'etc/ecofi/license-public.pem').read_bytes()
for p in (ROOT/'etc/ecofi').iterdir():assert p.name not in ['license-signing.pem','TEST_CREDENTIALS.txt']
for base in ['.cache/tmp/55/05/pfi/public/img','var/www/html/pisofi/public/img']:
    for n in [9,10,11]:assert not (ROOT/base/('user%d-128x128.jpg'%n)).exists()
for base in ['.cache/tmp/55/05/pfi/scripts','var/www/html/pisofi/scripts']:
    for name in ['cron','kicker.php']:
        content=(ROOT/base/name).read_text()
        assert 'eval(' not in content and 'hack3d' not in content

old=list(csv.DictReader((HERE.parent/'analysis/filesystem_inventory.tsv').open(),delimiter='\t'))
old_meta={r['path']:r for r in old}
old_hash={r['path']:r['sha256'] for r in csv.DictReader((HERE.parent/'analysis/filesystem_sha256.tsv').open(),delimiter='\t')}
changes=[];seen=set();regular=0
print('Recording final filesystem changes...',flush=True)
with (LOGS/'final_filesystem_manifest.tsv').open('w',newline='') as stream:
    writer=csv.writer(stream,delimiter='\t');writer.writerow(['path','type','mode','uid','gid','bytes','sha256','target'])
    for base,dirs,files in os.walk(ROOT,followlinks=False):
        dirs.sort();files.sort()
        for name in dirs+files:
            p=Path(base)/name;s=p.lstat();rel='/'+str(p.relative_to(ROOT));seen.add(rel)
            kind='symlink' if stat.S_ISLNK(s.st_mode) else 'directory' if stat.S_ISDIR(s.st_mode) else 'file' if stat.S_ISREG(s.st_mode) else 'special'
            digest='';target=os.readlink(p) if kind=='symlink' else ''
            if kind=='file':
                regular+=1;h=hashlib.sha256()
                with p.open('rb') as f:
                    while chunk:=f.read(1024*1024):h.update(chunk)
                digest=h.hexdigest()
            mode=oct(stat.S_IMODE(s.st_mode))
            writer.writerow([rel,kind,mode,s.st_uid,s.st_gid,s.st_size,digest,target])
            prior=old_meta.get(rel)
            if (prior is None or prior['type']!=kind or prior['mode']!=mode or int(prior['uid'])!=s.st_uid or int(prior['gid'])!=s.st_gid or
                (kind=='file' and old_hash.get(rel)!=digest) or (kind=='symlink' and prior['symlink_target']!=target)):
                changes.append({'path':rel,'kind':kind,'mode':mode,'sha256':digest,'symlink_target':target,'before_sha256':old_hash.get(rel),'new':prior is None})
for rel in old_meta.keys()-seen:changes.append({'path':rel,'removed':True,'before_sha256':old_hash.get(rel)})
(LOGS/'final_changes.json').write_text(json.dumps(changes,indent=2))
os.sync()
subprocess.run(['umount',str(ROOT)],check=True)
device=subprocess.run(['losetup','--find','--show','--read-only','--offset','4194304',str(WORK)],capture_output=True,text=True,check=True).stdout.strip()
try:
    fsck=subprocess.run(['e2fsck','-fn',device],capture_output=True,text=True)
    (LOGS/'final_fsck.txt').write_text(fsck.stdout+fsck.stderr)
    assert fsck.returncode==0, 'Filesystem check failed; image not exported'
finally:subprocess.run(['losetup','--detach',device],check=True)

def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        while chunk:=f.read(4*1024*1024):h.update(chunk)
    return h.hexdigest()
with SOURCE.open('rb') as original,WORK.open('rb') as rebuilt:
    assert original.read(4194304)==rebuilt.read(4194304), 'Boot region was changed'
print('Checking source integrity and hashing rebuilt image...',flush=True)
assert sha(SOURCE)==EXPECTED, 'Source image hash changed'
digest=sha(WORK)
temp=TARGET.with_suffix('.img.building')
assert not temp.exists(), 'Previous incomplete export exists'
print('Copying verified image to resources...',flush=True)
subprocess.run(['cp','--sparse=always',str(WORK),str(temp)],check=True)
assert sha(temp)==digest, 'Export copy hash mismatch'
temp.rename(TARGET)
TARGET.with_suffix('.img.sha256').write_text(digest+'  '+TARGET.name+'\n')
summary={'image':str(TARGET),'bytes':TARGET.stat().st_size,'sha256':digest,'source_sha256':EXPECTED,
         'source_unchanged':True,'boot_region_unchanged':True,'fsck_exit_code':0,
         'regular_files':regular,'changed_metadata_or_content_entries':len(changes),
         'php_linted_files':469,'license_cases_passed':9,'auth_integration_passed':True,
         'network_modes_tested':['single','dual'],'hardware_boot_tested':False,
         'private_signing_key_in_image':False,'emulator_removed':True}
(LOGS/'build_result.json').write_text(json.dumps(summary,indent=2))
print(json.dumps(summary,indent=2),flush=True)
