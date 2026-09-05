"""Bound-source live checks; modifies only this PC's credit and restores it.

No license changes, physical gate operations, or messages to external recipients.
"""
import http.client
import json
from pathlib import Path
import socket
import ssl
import time

from opi_access import connect, admin_cookie, api

CLIENT = '10.0.7.117'
OUT = Path('audits/2026-09-05-live/live-traffic-tests.json')


def internet_connection():
    return http.client.HTTPSConnection('1.1.1.1', timeout=4,
        source_address=(CLIENT, 0), context=ssl.create_default_context())


def fetch(conn):
    conn.request('GET', '/cdn-cgi/trace', headers={'Connection': 'keep-alive'})
    response = conn.getresponse()
    data = response.read()
    return {'status': response.status, 'bytes': len(data), 'will_close': response.will_close}


def main():
    c = connect()
    results = []
    k = admin_cookie(c)
    original = next(x for x in api('/admin/api/clients', cookie=k) if x['ip'] == CLIENT)
    if original['remaining_seconds'] != 0:
        raise RuntimeError('This probe requires an empty test-client balance to restore exactly.')
    def edit(minutes, dl=3072, ul=1536):
        return api('/admin/api/client/edit', {'ip': CLIENT, 'minutes': minutes,
                   'dl_kbps': dl, 'ul_kbps': ul}, cookie=k)
    try:
        conn = internet_connection()
        try:
            r = fetch(conn)
            results.append({'test': 'zero credit blocks new HTTPS', 'pass': False, 'observed': r})
        except (OSError, http.client.HTTPException) as e:
            results.append({'test': 'zero credit blocks new HTTPS', 'pass': True, 'observed': type(e).__name__})
        finally:
            conn.close()
        edit(2)
        before = api('/api/status')['remaining_seconds']
        conn = internet_connection()
        active = fetch(conn)
        results.append({'test': 'credit permits HTTPS through WAN', 'pass': active['status'] == 200, 'observed': active})
        time.sleep(3.2)
        after = api('/api/status')['remaining_seconds']
        results.append({'test': 'active countdown', 'pass': 2 <= before-after <= 5, 'observed': [before, after]})
        api('/api/client/pause', {'action': 'pause'})
        paused_before = api('/api/status')['remaining_seconds']
        try:
            r = fetch(conn)
            results.append({'test': 'pause stops established HTTPS', 'pass': False, 'observed': r})
        except (OSError, http.client.HTTPException) as e:
            results.append({'test': 'pause stops established HTTPS', 'pass': True, 'observed': type(e).__name__})
        finally:
            conn.close()
        time.sleep(2.2)
        paused_after = api('/api/status')['remaining_seconds']
        results.append({'test': 'paused countdown freezes', 'pass': paused_before == paused_after, 'observed': [paused_before, paused_after]})
        conn = internet_connection()
        try:
            r = fetch(conn)
            results.append({'test': 'pause blocks new HTTPS', 'pass': False, 'observed': r})
        except (OSError, http.client.HTTPException) as e:
            results.append({'test': 'pause blocks new HTTPS', 'pass': True, 'observed': type(e).__name__})
        finally:
            conn.close()
        api('/api/client/pause', {'action': 'resume'})
        conn = internet_connection()
        try:
            r = fetch(conn)
            results.append({'test': 'resume permits HTTPS', 'pass': r['status'] == 200, 'observed': r})
        finally:
            conn.close()
    finally:
        edit(0, original['dl_kbps'], original['ul_kbps'])
        api('/api/client/pause', {'action': 'pause' if original['is_paused'] else 'resume'})
        c.close()
        OUT.write_text(json.dumps(results, indent=2), encoding='utf-8')
        print(json.dumps(results, indent=2))


if __name__ == '__main__':
    main()
