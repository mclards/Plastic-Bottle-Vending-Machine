#!/bin/bash
set -euo pipefail
ROOT=/var/tmp/pisofi-custom-test-v1/root
mkdir -p "$ROOT/run/mysqld"
chown 109:114 "$ROOT/run/mysqld"
exec chroot "$ROOT" /tmp/ecofi-qemu-arm-static /usr/sbin/mysqld \
    --no-defaults --user=mysql --datadir=/var/lib/mysql \
    --socket=/run/mysqld/mysqld.sock --pid-file=/run/mysqld/ecofi-build.pid \
    --skip-networking --innodb-use-native-aio=0 --innodb-buffer-pool-size=64M \
    --innodb-log-file-size=3M --innodb-log-buffer-size=3M \
    --log-error=/run/mysqld/ecofi-build.log
