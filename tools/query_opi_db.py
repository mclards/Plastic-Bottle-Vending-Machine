import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.opi_access import connect, execute

def main():
    c = connect()
    code, out, err = execute(c, """python3 -c "
import sqlite3
conn = sqlite3.connect('/opt/ecofi/vendo_sessions.db')
c = conn.cursor()
print('--- OPEN DEPOSIT SESSIONS ---')
c.execute('SELECT id, status, owner_id, created_at, updated_at, error FROM deposit_sessions ORDER BY created_at DESC LIMIT 5')
for row in c.fetchall():
    print(row)
print('--- DEPOSIT RECOVERY ---')
c.execute('SELECT event_id, reason, received_at, resolved_at FROM deposit_recovery ORDER BY received_at DESC LIMIT 5')
for row in c.fetchall():
    print(row)
" """)
    print(out)
    if err:
        print("ERR:", err)

if __name__ == '__main__':
    main()
