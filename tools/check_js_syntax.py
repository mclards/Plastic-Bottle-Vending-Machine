import ast
import os
import re
import subprocess
import sys
import tempfile

def main():
    portal_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'host', 'portal.py')
    with open(portal_path, 'r', encoding='utf-8') as f:
        content = f.read()

    tree = ast.parse(content)
    admin_html = None
    portal_html = None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                val = getattr(node.value, 'value', getattr(node.value, 's', None))
                if getattr(target, 'id', None) == 'ADMIN_HTML':
                    admin_html = val
                elif getattr(target, 'id', None) == 'PORTAL_HTML':
                    portal_html = val

    if not admin_html:
        print("ERROR: ADMIN_HTML not found!")
        sys.exit(1)

    print("ADMIN_HTML size: {} chars".format(len(admin_html)))
    scripts = re.findall(r'<script(?:\s+[^>]*)?>(.*?)</script>', admin_html, re.DOTALL | re.IGNORECASE)
    print("Found {} script block(s) in ADMIN_HTML".format(len(scripts)))

    failed = False
    for i, s in enumerate(scripts):
        with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8') as tf:
            tf.write(s)
            tname = tf.name
        try:
            res = subprocess.run(['node', '--check', tname], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if res.returncode != 0:
                print("ADMIN_HTML Script block {} FAILED syntax check:".format(i))
                print(res.stderr.decode('utf-8'))
                failed = True
            else:
                print("ADMIN_HTML Script block {} PASSED syntax check!".format(i))
        finally:
            if os.path.exists(tname):
                os.remove(tname)

    if portal_html:
        portal_scripts = re.findall(r'<script(?:\s+[^>]*)?>(.*?)</script>', portal_html, re.DOTALL | re.IGNORECASE)
        print("Found {} script block(s) in PORTAL_HTML".format(len(portal_scripts)))
        for i, s in enumerate(portal_scripts):
            # Replace Jinja {{ ... }} and {% ... %} with harmless values for node syntax check
            s_clean = re.sub(r'\{\{.*?\}\}', '0', s)
            s_clean = re.sub(r'\{%.*?%\}', '', s_clean)
            with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8') as tf:
                tf.write(s_clean)
                tname = tf.name
            try:
                res = subprocess.run(['node', '--check', tname], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                if res.returncode != 0:
                    print("PORTAL_HTML Script block {} FAILED syntax check:".format(i))
                    print(res.stderr.decode('utf-8'))
                    failed = True
                else:
                    print("PORTAL_HTML Script block {} PASSED syntax check!".format(i))
            finally:
                if os.path.exists(tname):
                    os.remove(tname)

    if failed:
        sys.exit(1)
    print("\nSUCCESS: All JavaScript in ADMIN_HTML & PORTAL_HTML is syntactically valid!")

if __name__ == '__main__':
    main()
