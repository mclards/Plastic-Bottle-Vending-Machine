import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.opi_access import connect, execute

REMOTE_TEST = """
import time, json, subprocess, sqlite3, sys
sys.path.insert(0, '/opt/ecofi')

import gateway_network
import transition_engine as engine
import time_schema as storage

TEST_IP = '10.0.20.99'
TEST_MAC = '02:00:00:20:00:99'

print('=== 1. INITIALIZING TEST CLIENT %s (%s) ===' % (TEST_IP, TEST_MAC))
conn = sqlite3.connect('/opt/ecofi/vendo_sessions.db')
conn.row_factory = sqlite3.Row

now = int(time.time())
mono = time.monotonic()

# Enable license in test context
gateway_network.set_license(True)

# Clean up any leftover test data
conn.execute('DELETE FROM connections WHERE ip=?', (TEST_IP,))
conn.commit()

# Create owner & connection
owner_id = 'test-owner-99'
conn.execute('INSERT OR IGNORE INTO credit_owners (id, created_at) VALUES (?, ?)', (owner_id, now))
conn.execute('INSERT OR IGNORE INTO pause_budgets (id, owner_id, pause_count_max, used_count, created_at) VALUES (?, ?, 5, 0, ?)',
             ('pb-test-99', owner_id, now))
conn_id = 'conn-test-99'
conn.execute('''INSERT INTO connections (id, ip, mac, owner_id, desired_state, applied_state, binding_version,
                                        last_settled_at_mono, last_settled_at_utc, updated_at)
                VALUES (?, ?, ?, ?, 'DISCONNECTED', 'DISCONNECTED', 1, ?, ?, ?)''',
             (conn_id, TEST_IP, TEST_MAC, owner_id, mono, now, now))
conn.commit()

cd = engine.connection(conn, conn_id)

# 2. GRANT TIME (120 seconds)
print('\\n=== 2. APPLYING TIME GRANT (120s) ===')
op = 'op-grant-99'
res_grant = engine.apply_operation(conn, owner_id, cd, 'TOP_UP_GRANT',
                                   {'seconds': 120, 'origin': 'test', 'dl_kbps': 3072, 'ul_kbps': 1536},
                                   op, now, mono)
conn.commit()
print('Grant result action:', res_grant.get('action'), 'state:', res_grant.get('state'), 'rem:', res_grant.get('remaining_seconds'))

# Trigger gateway grant
grant_ok = gateway_network.grant(TEST_IP, TEST_MAC, 15, 3072, 1536)
print('gateway_network.grant status:', grant_ok)

# Verify ipset
r_auth = subprocess.run(['ipset', 'test', 'ecofi_auth', TEST_IP], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
r_pair = subprocess.run(['ipset', 'test', 'ecofi_pairs', '%s,%s' % (TEST_IP, TEST_MAC)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
print('ipset ecofi_auth contains IP:', r_auth.returncode == 0)
print('ipset ecofi_pairs contains IP,MAC:', r_pair.returncode == 0)

# Verify tc class
r_tc = subprocess.run(['tc', 'class', 'show', 'dev', 'eth1'], stdout=subprocess.PIPE, universal_newlines=True)
has_tc = any('3072Kbit' in l for l in r_tc.stdout.splitlines())
print('tc eth1 has 3072Kbit class:', has_tc)

# 3. TEST PAUSE
print('\\n=== 3. TESTING PAUSE ===')
cd = engine.connection(conn, conn_id)
op_pause = 'op-pause-99'
res_pause = engine.apply_operation(conn, owner_id, cd, 'PAUSE', {}, op_pause, now, mono)
conn.commit()
print('Pause result is_paused:', res_pause.get('is_paused'), 'state:', res_pause.get('state'))

# Revoke from gateway
gateway_network.revoke(TEST_IP, TEST_MAC)

r_auth_p = subprocess.run(['ipset', 'test', 'ecofi_auth', TEST_IP], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
r_pair_p = subprocess.run(['ipset', 'test', 'ecofi_pairs', '%s,%s' % (TEST_IP, TEST_MAC)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
print('After PAUSE - ipset ecofi_auth contains IP:', r_auth_p.returncode == 0)
print('After PAUSE - ipset ecofi_pairs contains IP,MAC:', r_pair_p.returncode == 0)

# 4. TEST RESUME
print('\\n=== 4. TESTING RESUME ===')
cd = engine.connection(conn, conn_id)
op_resume = 'op-resume-99'
res_resume = engine.apply_operation(conn, owner_id, cd, 'RESUME', {}, op_resume, now, mono)
conn.commit()
print('Resume result is_paused:', res_resume.get('is_paused'), 'state:', res_resume.get('state'))

grant_ok_res = gateway_network.grant(TEST_IP, TEST_MAC, 15, 3072, 1536)
r_auth_r = subprocess.run(['ipset', 'test', 'ecofi_auth', TEST_IP], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
print('After RESUME - ipset ecofi_auth contains IP:', r_auth_r.returncode == 0)

# 5. CLEANUP
print('\\n=== 5. CLEANUP TEST DATA ===')
gateway_network.revoke(TEST_IP, TEST_MAC)
conn.execute('DELETE FROM connections WHERE ip=?', (TEST_IP,))
conn.execute('DELETE FROM time_grants WHERE owner_id=?', (owner_id,))
conn.execute('DELETE FROM pause_budgets WHERE owner_id=?', (owner_id,))
conn.execute('DELETE FROM credit_owners WHERE id=?', (owner_id,))
conn.execute('DELETE FROM value_operations WHERE operation_id LIKE "op-%-99"')
conn.commit()
conn.close()
print('Cleanup completed. Test client fully revoked and purged.')
"""

def main():
    c = connect()
    sftp = c.open_sftp()
    with sftp.file('/tmp/test_entitlement_cycle.py', 'w') as f:
        f.write(REMOTE_TEST)
    sftp.close()

    code, out, err = execute(c, 'python3 /tmp/test_entitlement_cycle.py && rm -f /tmp/test_entitlement_cycle.py')
    print(out)
    if err:
        print('ERR:', err)
    c.close()

if __name__ == '__main__':
    main()
