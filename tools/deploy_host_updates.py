import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.opi_access import connect, execute

def main():
    c = connect()
    sftp = c.open_sftp()
    print('Deploying host/gateway_network.py...')
    sftp.put(str(ROOT / 'host' / 'gateway_network.py'), '/opt/ecofi/gateway_network.py')
    print('Deploying host/portal.py...')
    sftp.put(str(ROOT / 'host' / 'portal.py'), '/opt/ecofi/portal.py')
    print('Deploying host/time_portal.py...')
    sftp.put(str(ROOT / 'host' / 'time_portal.py'), '/opt/ecofi/time_portal.py')
    sftp.close()

    print('Restarting ecofi_portal.service...')
    code, out, err = execute(c, 'systemctl restart ecofi_portal.service')
    print('Service restart code:', code)
    c.close()

if __name__ == '__main__':
    main()
