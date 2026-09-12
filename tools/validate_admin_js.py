#!/usr/bin/env python3
import re
import subprocess
import sys
import os

with open('host/portal.py', 'r', encoding='utf-8') as f:
    text = f.read()

start_marker = "ADMIN_HTML = '"
s_pos = text.find(start_marker)
if s_pos == -1:
    print('ERROR: start marker not found')
    sys.exit(1)

s_pos += len(start_marker)
# Find ending quote at end of the line
eol = text.find('\n', s_pos)
# The quote is right before \n
e_pos = eol - 1
while e_pos > s_pos and text[e_pos] != "'":
    e_pos -= 1

raw_html = text[s_pos:e_pos]
print('ADMIN_HTML extracted, length: %d' % len(raw_html))

# Unescape
html = raw_html.replace(r"\'", "'").replace(r"\n", "\n").replace(r"\\", "\\")

scripts = re.findall(r'<script>(.*?)</script>', html, re.DOTALL)
print('Found %d script tags in ADMIN_HTML' % len(scripts))

for i, s in enumerate(scripts):
    tmp_file = 'temp_check_%d.js' % i
    with open(tmp_file, 'w', encoding='utf-8') as tf:
        tf.write(s)
    try:
        res = subprocess.run(['node', '--check', tmp_file], stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        if res.returncode != 0:
            print('Syntax error in script %d:\n%s' % (i, res.stderr))
            sys.exit(1)
        else:
            print('Script %d: Syntax OK (node --check passed)' % i)
    finally:
        if os.path.exists(tmp_file):
            os.remove(tmp_file)

print('ALL JAVASCRIPT IN ADMIN_HTML VALID!')
