import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools.opi_access import connect, execute

def main():
    client = connect()
    try:
        commands = [
            'python3 -c "import sqlite3; c=sqlite3.connect(\'/opt/ecofi/vendo_sessions.db\'); print(\'journal_mode:\', c.execute(\'PRAGMA journal_mode;\').fetchall()); print(\'integrity:\', c.execute(\'PRAGMA integrity_check;\').fetchall()); c.close()"',
            'ls -lh /opt/ecofi/vendo_sessions.db*'
        ]
        for cmd in commands:
            code, out, err = execute(client, cmd)
            print("CMD:", cmd)
            print("CODE:", code)
            print("OUT:", out.strip())
            if err.strip():
                print("ERR:", err.strip())
            print("-" * 40)
    finally:
        client.close()

if __name__ == '__main__':
    main()
