import sys, os, re, subprocess, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'host'))
import portal

def check_html(name, html):
    scripts = re.findall(r'<script(?:\s+[^>]*)?>(.*?)</script>', html, flags=re.DOTALL | re.IGNORECASE)
    print("Checking {} ({} script blocks)...".format(name, len(scripts)))
    all_ok = True
    for i, s in enumerate(scripts):
        if not s.strip():
            continue
        cleaned = re.sub(r'\{%[^\n]*?%\}', '/* jinja */', s)
        cleaned = re.sub(r'["\']\{\{[^\n]*?\}\}["\']', '"jinja_var"', cleaned)
        cleaned = re.sub(r'\{\{[^\n]*?\}\}', '1', cleaned)
        fd, tname = tempfile.mkstemp(suffix='.js')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as tf:
                tf.write(cleaned)
            res = subprocess.run(['node', '--check', tname], stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
            if res.returncode != 0:
                print("JS Error in {} block {}:".format(name, i))
                lines = cleaned.splitlines()
                for line_no, l in enumerate(lines[:35]):
                    print("{:3d}: {}".format(line_no+1, l))
                print(res.stderr)
                all_ok = False
            else:
                print("  {} block {} syntax VALID!".format(name, i))
        finally:
            if os.path.exists(tname):
                os.remove(tname)
    return all_ok

ok1 = check_html("PORTAL_HTML", portal.PORTAL_HTML)
ok2 = check_html("ADMIN_HTML", portal.ADMIN_HTML)
if ok1 and ok2:
    print("\nALL JAVASCRIPT IN PORTAL AND ADMIN IS 100% VALID!")
else:
    sys.exit(1)
