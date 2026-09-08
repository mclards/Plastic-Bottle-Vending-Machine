"""Hash every regular file; inspect boot, services, ELF metadata and database layout."""
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

root = Path('/mnt/pisofi-inspect-ro')
project = Path('/mnt/d/PROJECTS_IO/Plastic-Bottle-Vending-Machine')
out = project / 'pisofi_inspect/analysis'
rows = list(csv.DictReader((out / 'filesystem_inventory.tsv').open(), delimiter='\t'))
elfs, services, tables, interesting = [], [], [], []
with (out / 'filesystem_sha256.tsv').open('w', newline='') as stream:
    writer = csv.writer(stream, delimiter='\t')
    writer.writerow(['path', 'bytes', 'sha256'])
    for i, row in enumerate(rows):
        if row['type'] != 'file':
            continue
        p = root / row['path'].lstrip('/')
        h = hashlib.sha256()
        with p.open('rb') as f:
            head = f.read(4096)
            h.update(head)
            while chunk := f.read(1024 * 1024):
                h.update(chunk)
        writer.writerow([row['path'], row['bytes'], h.hexdigest()])
        if head.startswith(b'\x7fELF'):
            elfs.append({'path': row['path'], 'class': head[4], 'endianness': head[5],
                         'machine': int.from_bytes(head[18:20], 'little' if head[5] == 1 else 'big'),
                         'bytes': int(row['bytes'])})
        if row['path'].startswith('/var/lib/mysql/pisofi/'):
            tables.append(row)
        if row['path'].startswith('/etc/systemd/system/') and p.suffix == '.service':
            services.append({'path': row['path'], 'content': p.read_text(errors='replace')})
        if (row['path'].startswith(('/usr/src/pfi/', '/var/spool/cron/')) or
            row['path'] in ['/etc/rc.local.bak', '/log.txt', '/resume.txt'] or
            row['path'].endswith(('.sql', '.sql.gz'))):
            dest = out / 'evidence' / row['path'].lstrip('/')
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(p.read_bytes())
            interesting.append(row['path'])
        if i % 10000 == 0:
            print('Inventory progress:', i, flush=True)
(out / 'elf_inventory.json').write_text(json.dumps(elfs, indent=2))
(out / 'custom_service_definitions.json').write_text(json.dumps(services, indent=2))
(out / 'database_file_inventory.json').write_text(json.dumps(tables, indent=2))
(out / 'additional_evidence.json').write_text(json.dumps(interesting, indent=2))

binary_out = out / 'binary_metadata'
binary_out.mkdir(exist_ok=True)
for entry in elfs:
    if not entry['path'].startswith(('/usr/local/', '/home/', '/.cache/')):
        continue
    p = root / entry['path'].lstrip('/')
    name = entry['path'].strip('/').replace('/', '__')
    metadata = subprocess.run(['readelf', '-h', '-d', '-n', str(p)], capture_output=True, text=True)
    (binary_out / (name + '.elf.txt')).write_text(metadata.stdout + metadata.stderr)
    # Extract strings without executing code; preserve all strings locally.
    with (binary_out / (name + '.strings.txt')).open('w') as target:
        subprocess.run(['strings', '-a', '-n', '6', str(p)], stdout=target, check=True)

image = project / 'resources/PisoFi_Opi1&PC_v5.3.0-05-10-26_EXT.img'
with image.open('rb') as f:
    boot = f.read(4194304)
(out / 'boot_region.bin').write_bytes(boot)
boot_strings = re.findall(rb'[ -~]{8,}', boot)
(out / 'boot_strings.txt').write_text('\n'.join(x.decode('ascii') for x in boot_strings))
device = subprocess.run(['findmnt', '-n', '-o', 'SOURCE', '--target', str(root)],
                        capture_output=True, text=True, check=True).stdout.strip()
superblock = subprocess.run(['dumpe2fs', '-h', device], capture_output=True, text=True)
(out / 'ext4_superblock.txt').write_text(superblock.stdout + superblock.stderr)
print(json.dumps({'hashed_regular_files': sum(r['type']=='file' for r in rows),
                  'elf_files': len(elfs), 'database_files': len(tables),
                  'custom_services': len(services)}, indent=2), flush=True)
