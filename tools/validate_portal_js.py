import re
import subprocess
import os
import sys

with open('host/portal.py', 'r', encoding='utf-8') as f:
    text = f.read()

s_pos = text.find("PORTAL_HTML = '") + len("PORTAL_HTML = '")
eol = text.find('\n', s_pos)
e_pos = eol - 1
while e_pos > s_pos and text[e_pos] != "'":
    e_pos -= 1

html = text[s_pos:e_pos].replace(r"\'", "'").replace(r"\n", "\n").replace(r"\\", "\\")
scripts = re.findall(r'<script>(.*?)</script>', html, re.DOTALL)
print('Found %d script tags in PORTAL_HTML' % len(scripts))

for i, s in enumerate(scripts):
    tmp = 'temp_portal_%d.js' % i
    # Replace quoted jinja tags first e.g. "{{ ... }}" -> "dummy"
    s_clean = re.sub(r'["\']\{\{.*?\}\}["\']', '"dummy"', s)
    # Then unquoted jinja tags e.g. {{ ... }} -> 0
    s_clean = re.sub(r'\{\{.*?\}\}', '0', s_clean)
    with open(tmp, 'w', encoding='utf-8') as tf:
        tf.write(s_clean)
    try:
        res = subprocess.run(['node', '--check', tmp], stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        if res.returncode != 0:
            print('Syntax error in portal script %d:\n%s' % (i, res.stderr))
            sys.exit(1)
        else:
            print('Portal Script %d: OK' % i)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)

print('ALL JAVASCRIPT IN PORTAL_HTML VALID!')
