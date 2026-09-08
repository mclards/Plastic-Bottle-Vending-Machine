"""Copy selected evidence and render escaped PHP strings for static inspection.

Readable derivatives are analysis text, NOT executable replacements.
Never executes PHP, ELF files, service scripts, or SQL from the image.
"""
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys

root = Path('/mnt/pisofi-inspect-ro')
out = Path('/mnt/d/PROJECTS_IO/Plastic-Bottle-Vending-Machine/pisofi_inspect/analysis')
evidence = out / 'evidence'
readable = out / 'readable'
prefixes = [
    '/.cache/tmp/55/05/pfi/app', '/.cache/tmp/55/05/pfi/bootstrap',
    '/.cache/tmp/55/05/pfi/scripts', '/.cache/tmp/55/05/pfi/resources',
    '/.cache/tmp/55/05/pfi/packages', '/.cache/tmp/55/05/pfi/composer.json',
    '/.cache/tmp/55/05/pfi/composer.lock', '/.cache/tmp/55/05/pfi/version.json',
    '/.cache/tmp/55/05/pfi/README.md', '/.cache/tmp/55/05/pfi/LICENSE',
    '/home/pi/.dat', '/home/pi/.ngrok2', '/etc', '/usr/local/bin',
    '/boot/armbianEnv.txt', '/boot/boot.cmd', '/var/www/html',
]
skip = ['/etc/ssl/private', '/etc/ssh/ssh_host_', '/etc/shadow', '/etc/gshadow']

def eligible(rel):
    return (any(rel == p or rel.startswith(p.rstrip('/') + '/') for p in prefixes)
            and not any(rel == p or rel.startswith(p) for p in skip))

def render_php(text):
    # Tokenize quoted strings/comments first so formatting does not split literals.
    tokens = re.findall(r'''"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|/\*[\s\S]*?\*/|//[^\n]*|\#[^\n]*|[^"'/#]+|.''', text)
    result = []
    for token in tokens:
        if token.startswith('"'):
            token = re.sub(r'\\x([0-9a-fA-F]{1,2})|\\([0-7]{1,3})',
                           lambda m: chr(int(m[1], 16) if m[1] else int(m[2], 8)), token)
            result.append(token)
        elif token.startswith(("'", '//', '#', '/*')):
            result.append(token)
        else:
            result.append(re.sub(r'([;{}])', r'\1\n', token))
    return ('STATIC ANALYSIS DERIVATIVE — decoded PHP string escapes; do not execute.\n'
            + '\n'.join(line.strip() for line in ''.join(result).splitlines() if line.strip()) + '\n')

rows = list(csv.DictReader((out / 'filesystem_inventory.tsv').open(), delimiter='\t'))
manifest = []
php_files = []
for row in rows:
    rel = row['path']
    if row['type'] != 'file' or not eligible(rel):
        continue
    src = root / rel.lstrip('/')
    # Inventory traversal never followed symlinks; keep that invariant here.
    data = src.read_bytes()
    dest = evidence / rel.lstrip('/')
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    manifest.append({'path': rel, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
    if src.suffix == '.php' or data.startswith(b'<?php'):
        target = readable / (rel.lstrip('/') + '.txt')
        target.parent.mkdir(parents=True, exist_ok=True)
        rendered = render_php(data.decode('utf-8', errors='replace'))
        target.write_text(rendered)
        php_files.append({'path': rel, 'obfuscated_goto': bool(re.search(r'\bgoto\s+\w+', rendered)),
                          'ioncube_reference': 'ioncube' in rendered.lower(),
                          'zend_guard_reference': 'zend guard' in rendered.lower()})
(out / 'evidence_manifest.json').write_text(json.dumps(manifest, indent=2))
(out / 'php_analysis_inventory.json').write_text(json.dumps(php_files, indent=2))

# Parse installed packages without invoking the image's package manager.
packages = []
for record in (root / 'var/lib/dpkg/status').read_text().split('\n\n'):
    fields = dict(line.split(': ', 1) for line in record.splitlines() if ': ' in line and not line.startswith(' '))
    if fields.get('Status') == 'install ok installed':
        packages.append({k: fields.get(k, '') for k in ('Package', 'Version', 'Architecture')})
(out / 'installed_packages.json').write_text(json.dumps(packages, indent=2))
print(json.dumps({'extracted_files': len(manifest), 'extracted_bytes': sum(x['bytes'] for x in manifest),
                  'php_files': len(php_files), 'goto_obfuscated': sum(x['obfuscated_goto'] for x in php_files),
                  'installed_packages': len(packages)}, indent=2))
