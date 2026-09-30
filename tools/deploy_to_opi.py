import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.opi_access import connect

ssh = connect()
sftp = ssh.open_sftp()

print("Uploading host/portal.py -> /opt/ecofi/portal.py...")
sftp.put('host/portal.py', '/opt/ecofi/portal.py')
sftp.chmod('/opt/ecofi/portal.py', 0o755)

print("Uploading host/tools/flash_esp32.py -> /opt/ecofi/tools/flash_esp32.py...")
sftp.put('host/tools/flash_esp32.py', '/opt/ecofi/tools/flash_esp32.py')
sftp.chmod('/opt/ecofi/tools/flash_esp32.py', 0o755)

print("Restarting ecofi_portal.service...")
stdin, stdout, stderr = ssh.exec_command('systemctl restart ecofi_portal.service')
print("STDOUT:", stdout.read().decode())
print("STDERR:", stderr.read().decode())

print("Deployment complete.")
