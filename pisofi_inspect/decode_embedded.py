"""Decode inert analysis text from known data encodings; never evaluate payloads."""
import base64
import codecs
import hashlib
import json
from pathlib import Path
import re
import csv

out = Path(__file__).resolve().parent / 'analysis'
source = out / 'evidence/var/www/html/pisofi/public/img/user11-128x128.jpg'
raw = source.read_bytes()
decoded = base64.b64decode(codecs.decode(raw.decode('ascii'), 'rot_13'))
dest = out / 'embedded_payload.decoded.php.txt'
dest.write_bytes(decoded)
coin = out / 'readable/.cache/tmp/55/05/pfi/scripts/coinrdr.txt'
text = coin.read_text(encoding='utf-8')
symbols = {int(k): bytes.fromhex(v).decode('utf-8') for k, v in
           re.findall(r'(\d+)\s*=>\s*"([0-9A-F]+)"', text)}
symbolic = re.sub(r'\$Mzt9l\[(\d+)\]', lambda m: symbols.get(int(m[1]), m[0]), text)
# Collapse adjacent simple quoted literals for analysis only.
join = re.compile(r'"([^"\\]*)"\s*\.\s*"([^"\\]*)"')
while join.search(symbolic):
    symbolic = join.sub(lambda m: '"' + m[1] + m[2] + '"', symbolic)
(out / 'coinreader_symbolic.php.txt').write_text(symbolic, encoding='utf-8')
(out / 'coinreader_symbol_table.json').write_text(json.dumps(symbols, indent=2))
(out / 'embedded_payload_metadata.json').write_text(json.dumps({
    'source_in_image': '/var/www/html/pisofi/public/img/user11-128x128.jpg',
    'encoding': 'base64_decode(str_rot13(file_contents))',
    'encoded_bytes': len(raw), 'decoded_bytes': len(decoded),
    'encoded_sha256': hashlib.sha256(raw).hexdigest(),
    'decoded_sha256': hashlib.sha256(decoded).hexdigest(),
    'execution_performed': False,
}, indent=2))
print('Decoded payload:', len(decoded), 'bytes; coinreader symbols:', len(symbols))

# Embedded repair archive: filenames are data only, never output destinations.
img = out / 'evidence/var/www/html/pisofi/public/img'
path_lines = (img / 'user10-128x128.jpg').read_text().splitlines()
data_lines = (img / 'user9-128x128.jpg').read_text().splitlines()
hashes = {r['path']: r['sha256'] for r in csv.DictReader(
    (out / 'filesystem_sha256.tsv').open(), delimiter='\t')}
archive = out / 'repair_archive'
archive.mkdir(exist_ok=True)
entries = []
assert len(path_lines) == len(data_lines), 'Repair archive path/content count mismatch'
for n, (path_line, data_line) in enumerate(zip(path_lines, data_lines)):
    if not path_line:
        continue
    path = base64.b64decode(codecs.decode(path_line, 'rot_13')).decode('utf-8')
    data = base64.b64decode(codecs.decode(data_line, 'rot_13'))
    digest = hashlib.sha256(data).hexdigest()
    target = archive / f'{n:04d}.bin'
    target.write_bytes(data)
    entries.append({'index': n, 'target_path_in_image': path, 'bytes': len(data),
                    'sha256': digest, 'current_file_sha256': hashes.get(path),
                    'matches_current_file': digest == hashes.get(path),
                    'analysis_file': target.relative_to(out).as_posix()})
(out / 'repair_archive_manifest.json').write_text(json.dumps(entries, indent=2))
print('Repair archive entries:', len(entries), '; matching current image:',
      sum(e['matches_current_file'] for e in entries))
