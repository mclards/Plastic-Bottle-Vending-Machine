import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.opi_access import connect, execute

def main():
    c = connect()
    code, out, err = execute(c, """python3 -c "
import sqlite3, subprocess
conn = sqlite3.connect('/opt/ecofi/vendo_sessions.db')
c = conn.cursor()
c.execute('''SELECT c.ip, c.applied_state, g.remaining_seconds, g.state 
             FROM connections c LEFT JOIN time_grants g ON c.selected_grant_id = g.id 
             WHERE c.ip='10.0.25.23' ''')
row = c.fetchone()
print('Client 10.0.25.23:', row)

r = subprocess.run(['ipset', 'list', 'ecofi_auth'], stdout=subprocess.PIPE, universal_newlines=True)
print('ipset ecofi_auth:', [l.strip() for l in r.stdout.split('Members:')[1].splitlines() if l.strip()] if 'Members:' in r.stdout else [])

r_cls = subprocess.run(['tc', 'class', 'show', 'dev', 'eth1'], stdout=subprocess.PIPE, universal_newlines=True)
print('tc classes eth1:', [l.strip() for l in r_cls.stdout.splitlines() if '1:1a17' in l])
" """)
    print(out)
    if err:
        print('ERR:', err)
    c.close()

if __name__ == '__main__':
    main()
