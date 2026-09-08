"""Build searchable static indexes from extracted evidence (not runtime claims)."""
import csv
import json
from pathlib import Path
import re
from collections import Counter
from urllib.parse import urlsplit

out = Path(__file__).resolve().parent / 'analysis'
rows = list(csv.DictReader((out / 'filesystem_inventory.tsv').open(encoding='utf-8'), delimiter='\t'))
readable = out / 'readable'
methods, routes, urls, licenses, sinks = [], [], [], [], []
for p in readable.rglob('*.txt'):
    rel = p.relative_to(readable).as_posix()
    for n, line in enumerate(p.read_text(encoding='utf-8').splitlines(), 1):
        if line.lstrip().startswith(('//', '*', '/*')):
            continue
        for match in re.finditer(r'\bfunction\s+([A-Za-z_]\w*)\s*\(', line):
            methods.append({'file': rel, 'line': n, 'function': match[1]})
        if re.search(r'->(?:get|post|put|patch|delete|map|group)\(', line) and '/Routes/' in rel:
            routes.append({'file': rel, 'line': n, 'declaration': line})
        for match in re.finditer(r'''(?:https?|wss?|tcp)://[^\s'"<>\\)]+''', line):
            urls.append({'file': rel, 'line': n, 'url_literal': match[0]})
        if re.search(r'license|is_registered|cipher_key|coinreader_lk|verifier|revok', line, re.I):
            licenses.append({'file': rel, 'line': n, 'text': line})
        if re.search(r'\b(?:exec|shell_exec|system|passthru|popen|proc_open|eval)\s*\(', line):
            sinks.append({'file': rel, 'line': n, 'text': line})
for name, value in [('function_index', methods), ('route_declarations', routes),
                    ('url_literals', urls), ('licensing_references', licenses), ('execution_sinks', sinks)]:
    (out / (name + '.json')).write_text(json.dumps(value, indent=2), encoding='utf-8')

services = []
for p in (out / 'evidence/etc/systemd/system').glob('pisofi*.service'):
    content = p.read_text()
    fields = dict(line.split('=', 1) for line in content.splitlines() if '=' in line)
    links = [r['path'] for r in rows if r['type'] == 'symlink' and '/.wants/' in r['path']]
    links = [r['path'] for r in rows if r['type'] == 'symlink' and '.wants/' in r['path']
             and (r['symlink_target'].endswith('/' + p.name) or r['symlink_target'] == p.name)]
    services.append({'service': p.name, 'user': fields.get('User', 'root (implicit)'),
                     'exec_start': fields.get('ExecStart'), 'enablement_links': links})
(out / 'pisofi_services.json').write_text(json.dumps(services, indent=2))

packages = json.loads((out / 'installed_packages.json').read_text())
key_packages = [p for p in packages if re.match(r'^(nginx|php7.0|mariadb|openssh|linux-image|u-boot|dnsmasq|network-manager|redis|zerotier|iptables|ngrok)', p['Package'])]
summary = {
    'php_files': len(json.loads((out / 'php_analysis_inventory.json').read_text())),
    'function_declarations': len(methods), 'route_and_group_declarations': len(routes),
    'execution_sink_candidates': len(sinks),
    'licensing_reference_files': len(set(x['file'] for x in licenses)),
    'key_packages': key_packages,
    'application_symlinks': [r for r in rows if r['type']=='symlink' and r['path'].startswith('/var/www/html/pisofi')],
    'root_home_files': [r for r in rows if r['type']=='file' and r['path'].startswith('/root/')],
    'domain_literal_counts': dict(Counter(urlsplit(x['url_literal']).netloc for x in urls)),
    'pisofi_services': services,
}
(out / 'source_summary.json').write_text(json.dumps(summary, indent=2))
print(json.dumps({k: v for k, v in summary.items() if k not in ('root_home_files', 'application_symlinks', 'pisofi_services')}, indent=2))
