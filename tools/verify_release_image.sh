#!/bin/bash
# Verify packaged content without executing services or modifying the image.
set -euo pipefail
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
version=$(tr -d '\r\n' < "$root/VERSION")
image="$root/resources/EcoFi_Opi_v${version}.img"
mount_dir=$(mktemp -d /tmp/ecofi-verify.XXXXXX)
nginx_check=$(mktemp /tmp/ecofi-nginx.XXXXXX)
cleanup() {
    if mountpoint -q "$mount_dir"; then umount "$mount_dir" || return; fi
    rmdir "$mount_dir"
    rm -f -- "$nginx_check"
    for suffix in body proxy fastcgi uwsgi scgi; do rmdir "$nginx_check.$suffix" 2>/dev/null || true; done
}
trap cleanup EXIT
(cd "$root/resources" && sha256sum -c "$(basename "$image").sha256")
mount -o ro,noload,loop,offset=4194304 "$image" "$mount_dir"
python3 - "$root" "$mount_dir" "$version" <<'PY'
import hashlib,sys
from pathlib import Path
root,mnt=map(Path,sys.argv[1:3]);version=sys.argv[3]
app=mnt/'opt/ecofi'
assert (app/'VERSION').read_text().strip()==version
modules=['portal.py','license_manager.py','esp32_simulator.py','gateway_network.py','time_schema.py','time_policy.py','transition_engine.py','time_portal.py','migrate_legacy_sessions.py']
for name in modules:
    assert (app/name).read_bytes()==(root/'host'/name).read_bytes(),name
assets=[p for p in (root/'host/static').rglob('*') if p.is_file()]
for path in assets:
    relative=path.relative_to(root/'host')
    assert hashlib.sha256(path.read_bytes()).digest()==hashlib.sha256((app/relative).read_bytes()).digest(),relative
assert (root/'host/ecofi.service').read_bytes()==(mnt/'etc/systemd/system/ecofi_portal.service').read_bytes()
for private in ['vendo_sessions.db','license.key','hwid_override.txt','deposit_journal.json']:
    assert not (app/private).exists(),private
for unit in ['ecofi_portal.service','ecofi_firewall.service']:
    link=mnt/'etc/systemd/system/multi-user.target.wants'/unit
    assert link.is_symlink(),unit
    target=link.readlink()
    assert (mnt/str(target).lstrip('/')).is_file(),unit
print('PASS: release version, nine runtime modules, {} static assets, service units and private-state exclusion'.format(len(assets)))
PY
(cd "$mount_dir/opt/ecofi" && sha256sum -c release-sha256.txt >/dev/null)
cat > "$nginx_check" <<EOF
pid /tmp/ecofi-nginx-test.pid;
error_log stderr;
events { worker_connections 16; }
http {
    access_log off;
    client_body_temp_path $nginx_check.body;
    proxy_temp_path $nginx_check.proxy;
    fastcgi_temp_path $nginx_check.fastcgi;
    uwsgi_temp_path $nginx_check.uwsgi;
    scgi_temp_path $nginx_check.scgi;
    include $mount_dir/etc/nginx/mime.types;
    include $mount_dir/etc/nginx/sites-enabled/ecofi;
}
EOF
qemu-arm-static -L "$mount_dir" "$mount_dir/usr/sbin/nginx" -t -c "$nginx_check" -p "$mount_dir/"
printf 'PASS: embedded file manifest and target ARM nginx site syntax\n'
