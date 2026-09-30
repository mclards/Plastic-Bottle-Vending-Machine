import subprocess
import sys
import re

def main():
    sys.path.insert(0, 'host')
    import portal
    html = portal.ADMIN_HTML

    # Find all <script> tags
    scripts = re.findall(r'<script(?:\s+[^>]*)?>(.*?)</script>', html, re.DOTALL)
    print("Found {} script block(s)".format(len(scripts)))

    for i, s in enumerate(scripts):
        # Skip external scripts or empty ones
        if not s.strip():
            continue
        test_file = 'scratch_admin_check_{}.js'.format(i)
        with open(test_file, 'w', encoding='utf-8') as f:
            f.write(s)
        res = subprocess.run(['node', '--check', test_file], stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        print("Script {} check: returncode={}".format(i, res.returncode))
        if res.returncode != 0:
            print("STDOUT:", res.stdout)
            print("STDERR:", res.stderr)
            import os
            os.remove(test_file)
            sys.exit(1)
        import os
        os.remove(test_file)

    print("ALL JAVASCRIPT VALIDATED CLEANLY VIA NODE --CHECK!")

if __name__ == '__main__':
    main()
