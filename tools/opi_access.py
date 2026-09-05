"""Audit transport. Credentials stay in memory; defaults come from the builder.

Run from the repository root. This module is also imported by live audit probes.
"""
import argparse
import json
import os
from pathlib import Path
import re
import shlex
import urllib.request

import paramiko

ROOT = Path(__file__).resolve().parents[1]


def connect():
    default = re.search(r'passwords to "([^"]+)"', (ROOT / 'build_ecofi_img.sh').read_text()).group(1)
    client = paramiko.SSHClient()
    keys = ROOT / 'audits' / '2026-09-05-live' / 'known_hosts'
    if keys.exists():
        client.load_host_keys(str(keys))
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(os.environ.get('OPI_HOST', '10.0.0.1'),
                   username=os.environ.get('OPI_USER', 'root'),
                   password=os.environ.get('OPI_PASSWORD', default),
                   timeout=10, auth_timeout=10, look_for_keys=False, allow_agent=False)
    keys.parent.mkdir(parents=True, exist_ok=True)
    client.save_host_keys(str(keys))
    return client


def execute(client, command, timeout=30):
    _, stdout, stderr = client.exec_command(command, timeout=timeout)
    output = stdout.read().decode(errors='replace')
    errors = stderr.read().decode(errors='replace')
    code = stdout.channel.recv_exit_status()
    return code, output, errors


def admin_cookie(client):
    # Root-authorized test session; do not change the owner's admin password.
    source = """import sqlite3
from flask import Flask
from flask.sessions import SecureCookieSessionInterface
a=Flask(__name__)
with sqlite3.connect('/opt/ecofi/vendo_sessions.db') as c:
    a.secret_key=c.execute("SELECT value FROM config WHERE key='flask_secret_key'").fetchone()[0]
print(SecureCookieSessionInterface().get_signing_serializer(a).dumps({'admin_logged_in':True,'admin_username':'admin'}))
"""
    code, result, error = execute(client, 'python3 -c ' + shlex.quote(source))
    if code:
        raise RuntimeError(error)
    return result.strip()


def api(path, data=None, cookie=None, headers=None):
    hdr = dict(headers or {})
    if cookie:
        hdr['Cookie'] = 'session=' + cookie
    if data is not None:
        hdr['Content-Type'] = 'application/json'
    host = os.environ.get('OPI_HOST', '10.0.0.1')
    req = urllib.request.Request('http://' + host + path,
        data=json.dumps(data).encode() if data is not None else None, headers=hdr)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=15) as response:
        return json.loads(response.read())


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--command')
    parser.add_argument('--file', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    client = connect()
    try:
        code, result, error = execute(client, args.file.read_text() if args.file else args.command)
        text = result + ('\nSTDERR:\n' + error if error else '')
        if args.output:
            args.output.write_text(text, encoding='utf-8')
        print(text)
        raise SystemExit(code)
    finally:
        client.close()
