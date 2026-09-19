import os, re, sys

errors = []
for root, dirs, files in os.walk('host'):
    for f in files:
        if f.endswith('.py') and not f.startswith('test_'):
            path = os.path.join(root, f)
            with open(path, 'r', encoding='utf-8') as src:
                for line_no, line in enumerate(src, 1):
                    # check for f" or f'
                    if re.search(r'\bf["\']', line):
                        errors.append((path, line_no, line.strip()))

if errors:
    print('ERROR: Found f-strings in host code:')
    for p, l, text in errors:
        print('  {}:{} -> {}'.format(p, l, text))
    sys.exit(1)
else:
    print('SUCCESS: No f-strings in host code! Python 3.5 clean.')

