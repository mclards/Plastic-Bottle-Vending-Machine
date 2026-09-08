"""Collect GPIO implementation and inspect potentially hidden non-image assets."""
import csv
import hashlib
import json
from pathlib import Path

root = Path('/mnt/pisofi-inspect-ro')
out = Path('/mnt/d/PROJECTS_IO/Plastic-Bottle-Vending-Machine/pisofi_inspect/analysis')
rows = list(csv.DictReader((out / 'filesystem_inventory.tsv').open(), delimiter='\t'))
extra, anomalies = [], []
for row in rows:
    rel = row['path']
    if row['type'] != 'file':
        continue
    src = root / rel.lstrip('/')
    if rel.startswith('/usr/src/gpio/') or rel in [
        '/usr/lib/os-release',
        '/.cache/tmp/55/05/pfi/backups/restore/pisofi.data',
        '/.cache/tmp/55/05/pfi/backups/restore/checksum',
        '/.cache/tmp/55/05/pfi/backups/restore/backup_type']:
        dest = out / 'evidence' / rel.lstrip('/')
        dest.parent.mkdir(parents=True, exist_ok=True)
        data = src.read_bytes()
        dest.write_bytes(data)
        extra.append({'path': rel, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
    if rel.startswith(('/.cache/', '/var/www/', '/home/')) and src.suffix.lower() in ('.jpg', '.jpeg', '.png', '.gif', '.ico', '.woff', '.woff2'):
        with src.open('rb') as f:
            head = f.read(24)
        expected = {'.jpg': (b'\xff\xd8\xff',), '.jpeg': (b'\xff\xd8\xff',),
                    '.png': (b'\x89PNG\r\n\x1a\n',), '.gif': (b'GIF87a', b'GIF89a'),
                    '.ico': (b'\x00\x00\x01\x00', b'\x89PNG'),
                    '.woff': (b'wOFF',), '.woff2': (b'wOF2',)}
        if not head.startswith(expected[src.suffix.lower()]):
            anomalies.append({'path': rel, 'bytes': int(row['bytes']), 'header_hex': head.hex()})
(out / 'gpio_and_backup_evidence.json').write_text(json.dumps(extra, indent=2))
(out / 'asset_magic_anomalies.json').write_text(json.dumps(anomalies, indent=2))
print('Additional evidence:', len(extra), '; asset magic anomalies:', len(anomalies))
