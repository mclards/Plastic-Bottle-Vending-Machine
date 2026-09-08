"""Issue a signed license from Ubuntu WSL. The signing key stays outside the image."""
import argparse
import base64
import json
from pathlib import Path
import secrets
import subprocess

def issue(key, device='*', expires=None, scope='TEST'):
    payload = {'schema': 1, 'issuer': 'Eco-Fi', 'scope': scope, 'device': device,
               'id': 'Eco-Fi-TEST-' + secrets.token_hex(12).upper(),
               'tier': 'FUNCTIONAL_TEST', 'expires_at': expires,
               'features': {'charging': True, 'vendos': 256, 'desktops': 256}}
    raw = json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()
    signature = subprocess.run(['openssl', 'dgst', '-sha256', '-sign', str(key)], input=raw, capture_output=True, check=True).stdout
    return {'payload': base64.b64encode(raw).decode(), 'signature': base64.b64encode(signature).decode()}

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--key', type=Path, default=Path(__file__).resolve().parent / 'private/license-signing.pem')
    parser.add_argument('--device', default='*')
    parser.add_argument('--expires', type=int, help='UTC Unix timestamp; omitted means permanent')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    doc = issue(args.key, args.device, args.expires, 'TEST' if args.device == '*' else 'DEVICE')
    args.output.write_text(json.dumps(doc, indent=2) + '\n')
    print('Signed license written:', args.output)
