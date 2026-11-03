#!/usr/bin/env bash
set -euo pipefail
cd /tmp/synapse-build
[[ $(id -u) == 0 && $(dpkg --print-architecture) == arm64 ]] || {
    echo 'Install only inside an ARM64 Armbian image chroot.' >&2; exit 1;
}
# The host-side check_target.py validates Debian, board identity and ARM64 before
# entering this chroot. Do not assume a minimal root already includes Python.
sha256sum --check SHA256SUMS
export DEBIAN_FRONTEND=noninteractive
apt-get update
# Keep Debian's default Python, board kernel, firmware, bootloader and network
# configuration intact. Build a private CPython because NumPy 1.26 needs <3.13.
apt-get install --yes --no-install-recommends \
    python3 build-essential ca-certificates libssl-dev zlib1g-dev libbz2-dev \
    libreadline-dev libsqlite3-dev libffi-dev liblzma-dev libncurses-dev \
    uuid-dev xz-utils libgl1 libglib2.0-0 libatomic1 libstdc++6 \
    openssh-server avahi-daemon v4l-utils unzip sudo iproute2
source_dir=$(mktemp -d /tmp/synapse-python.XXXXXXXX)
trap 'rm -rf -- "$source_dir"' EXIT
tar -xzf cpython.tar.gz -C "$source_dir" --strip-components=1
(
    cd "$source_dir"
    ./configure --prefix=/opt/synapse-python --with-ensurepip=install
    make -j2
    make altinstall
)
/opt/synapse-python/bin/python3.12 -c 'import ssl, sqlite3, bz2, lzma, ctypes, readline, venv'
id synapse >/dev/null 2>&1 || useradd --create-home --shell /bin/bash synapse
# No shared image password. Retain Armbian's first-login credential setup.
usermod -aG video synapse
if getent group render >/dev/null; then usermod -aG render synapse; fi
/opt/synapse-python/bin/python3.12 -m venv /home/synapse/.venv
wheel=$(python3 -c 'import json; print(json.load(open("image-metadata.json"))["wheel"]["filename"])')
# Binary-only dependencies fail early if RobotPy/OpenCV stop supporting ARM64.
# Packages come from the official PyPI index; OS Python is never modified.
/home/synapse/.venv/bin/python -m pip --isolated install \
    --index-url https://pypi.org/simple --only-binary=:all: "$wheel"
/home/synapse/.venv/bin/python -m pip check
python3 /tmp/synapse-build/prepare_project.py synapse /home/synapse/Synapse
install -o synapse -g synapse -m 0644 main.py /home/synapse/Synapse/main.py
install -o synapse -g synapse -m 0644 settings.yml /home/synapse/Synapse/config/settings.yml
chown -R synapse:synapse /home/synapse/.venv
install -d /usr/share/synapse-image
cp image-metadata.json /usr/share/synapse-image/
/home/synapse/.venv/bin/python -m pip freeze --all > /usr/share/synapse-image/python-requirements.txt
dpkg-query -W > /usr/share/synapse-image/debian-packages.txt
install -m 0644 synapse-runtime.service synapse-firstboot.service /etc/systemd/system/
install -m 0755 synapse-firstboot /usr/local/sbin/
systemctl enable synapse-runtime.service synapse-firstboot.service ssh.service avahi-daemon.service
install -d /etc/systemd/journald.conf.d
printf '[Journal]\nSystemMaxUse=100M\nRuntimeMaxUse=50M\n' > /etc/systemd/journald.conf.d/60-synapse.conf
# The existing runtime uses sudo for these explicit appliance operations.
# SSH users still need the Armbian administrator for general administration.
cat > /etc/sudoers.d/synapse-runtime <<'EOF'
synapse ALL=(root) NOPASSWD: /usr/bin/hostname *, /usr/bin/hostnamectl set-hostname *, /usr/bin/cat /etc/hosts, /usr/bin/tee /etc/hosts, /usr/sbin/reboot, /usr/sbin/ip addr add *, /usr/bin/systemctl restart synapse-runtime.service, /usr/bin/systemctl restart synapse-runtime
EOF
chmod 0440 /etc/sudoers.d/synapse-runtime
visudo -cf /etc/sudoers.d/synapse-runtime
# Every flashed copy gets a new machine identity and host keys on boot.
rm -f /etc/ssh/ssh_host_* /var/lib/dbus/machine-id
truncate -s 0 /etc/machine-id
ln -s /etc/machine-id /var/lib/dbus/machine-id
rm -f /var/lib/synapse/identity-initialized
apt-get clean
rm -rf /var/lib/apt/lists/* /root/.cache/pip
