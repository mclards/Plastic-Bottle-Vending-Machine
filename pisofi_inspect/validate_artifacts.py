"""Verify extraction provenance, decoded archive hashes, report links and counts."""
import ast
import csv
import hashlib
import json
from pathlib import Path
import re

folder = Path(__file__).resolve().parent
out = folder / 'analysis'
hashes = {r['path']: r['sha256'] for r in csv.DictReader(
    (out / 'filesystem_sha256.tsv').open(encoding='utf-8'), delimiter='\t')}
errors = []
manifest = json.loads((out / 'evidence_manifest.json').read_text(encoding='utf-8'))
for entry in manifest:
    p = out / 'evidence' / entry['path'].lstrip('/')
    actual = hashlib.sha256(p.read_bytes()).hexdigest()
    if actual != entry['sha256'] or actual != hashes[entry['path']]:
        errors.append('Evidence hash mismatch: ' + entry['path'])
extra = json.loads((out / 'gpio_and_backup_evidence.json').read_text(encoding='utf-8'))
for entry in extra:
    actual = hashlib.sha256((out / 'evidence' / entry['path'].lstrip('/')).read_bytes()).hexdigest()
    if actual != entry['sha256'] or actual != hashes[entry['path']]:
        errors.append('Additional evidence hash mismatch: ' + entry['path'])
archive = json.loads((out / 'repair_archive_manifest.json').read_text())
for entry in archive:
    actual = hashlib.sha256((out / entry['analysis_file']).read_bytes()).hexdigest()
    if actual != entry['sha256'] or actual != hashes[entry['target_path_in_image']]:
        errors.append('Repair archive hash mismatch: ' + entry['target_path_in_image'])
report = folder / 'FULL_IMAGE_AUDIT.md'
links = re.findall(r'\]\(([^)]+)\)', report.read_text(encoding='utf-8'))
for link in links:
    if not (folder / link).exists():
        errors.append('Broken evidence link: ' + link)
for source in folder.glob('*.py'):
    ast.parse(source.read_text(encoding='utf-8'))
before = (out / 'image_sha256_before.txt').read_text().split()[0]
after = (out / 'image_sha256_after.txt').read_text().split()[0]
if before != after:
    errors.append('Source image hash changed')
if (out / 'fsck_exit_status.txt').read_text().strip() != '0':
    errors.append('Filesystem check did not exit cleanly')
result = {'regular_file_hashes': len(hashes), 'evidence_files_verified': len(manifest),
          'additional_evidence_files_verified': len(extra),
          'repair_archive_entries_verified': len(archive), 'report_links_checked': len(links),
          'image_before_after_match': before == after, 'errors': errors}
(out / 'artifact_validation.json').write_text(json.dumps(result, indent=2))
print(json.dumps(result, indent=2))
raise SystemExit(bool(errors))
