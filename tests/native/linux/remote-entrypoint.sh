#!/bin/sh
set -eu

while [ ! -s /public/id_ed25519.pub ]; do sleep 1; done

if ! id rcbremote >/dev/null 2>&1; then
    useradd --create-home --shell /bin/bash rcbremote
    passwd --delete rcbremote >/dev/null 2>&1 || true
fi
install -d -m 700 -o rcbremote -g rcbremote /home/rcbremote/.ssh
install -m 600 -o rcbremote -g rcbremote /public/id_ed25519.pub /home/rcbremote/.ssh/authorized_keys
mkdir -p /run/sshd
ssh-keygen -A >/dev/null 2>&1

exec /usr/sbin/sshd -D -e \
    -o PasswordAuthentication=no \
    -o PubkeyAuthentication=yes \
    -o PermitRootLogin=no \
    -o UsePAM=no
