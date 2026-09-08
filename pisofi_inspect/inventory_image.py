"""Read-only metadata inventory; never follows image symlinks or executes its code."""
import csv
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
from collections import Counter

root = Path(sys.argv[1])
out = Path(sys.argv[2])
counts = Counter()
sizes = Counter()
extensions = Counter()
special = []
with (out / 'filesystem_inventory.tsv').open('w', newline='') as stream:
    writer = csv.writer(stream, delimiter='\t')
    writer.writerow(['path', 'type', 'mode', 'uid', 'gid', 'bytes', 'mtime', 'symlink_target'])
    for base, dirs, files in os.walk(root, followlinks=False):
        dirs.sort()
        files.sort()
        for name in dirs + files:
            path = Path(base) / name
            s = path.lstat()
            rel = '/' + str(path.relative_to(root))
            kind = ('symlink' if stat.S_ISLNK(s.st_mode) else
                    'directory' if stat.S_ISDIR(s.st_mode) else
                    'file' if stat.S_ISREG(s.st_mode) else 'special')
            target = os.readlink(path) if kind == 'symlink' else ''
            writer.writerow([rel, kind, oct(stat.S_IMODE(s.st_mode)), s.st_uid,
                             s.st_gid, s.st_size, int(s.st_mtime), target])
            counts[kind] += 1
            if kind == 'file':
                sizes[rel.split('/')[1]] += s.st_size
                extensions[path.suffix or '(none)'] += 1
            if s.st_mode & (stat.S_ISUID | stat.S_ISGID) or (kind == 'file' and s.st_mode & 2):
                special.append({'path': rel, 'mode': oct(stat.S_IMODE(s.st_mode)), 'uid': s.st_uid})
(out / 'inventory_summary.json').write_text(json.dumps({
    'counts': counts, 'file_bytes_by_top_directory': sizes,
    'extensions': extensions.most_common(), 'setid_or_world_writable_files': special
}, indent=2))
print(json.dumps({'counts': counts, 'file_bytes_by_top_directory': sizes}, indent=2))
