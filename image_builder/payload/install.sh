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
apt_options=()
if [[ -n ${SSL_CERT_FILE:-} ]]; then
    apt_options=(-o "Acquire::https::CaInfo=$SSL_CERT_FILE")
fi
apt-get "${apt_options[@]}" update
# Refresh installed TLS components from signed distro repositories. Keep the
# Debian ABI/package names appropriate for this particular base image.
tls_packages=()
for package in ca-certificates openssl curl git libssl3 libssl3t64 libgnutls30 libgnutls30t64 libnss3 chromium; do
    if [[ $(dpkg-query -W -f='${db:Status-Status}' "$package" 2>/dev/null || true) == installed ]]; then
        tls_packages+=("$package")
    fi
done
apt-get "${apt_options[@]}" install --yes --only-upgrade --no-install-recommends "${tls_packages[@]}"
# Keep Debian's default Python, board kernel, firmware, bootloader and network
# configuration intact. Build a private CPython because NumPy 1.26 needs <3.13.
apt-get "${apt_options[@]}" install --yes --no-install-recommends \
    python3 build-essential ca-certificates libssl-dev zlib1g-dev libbz2-dev \
    libreadline-dev libsqlite3-dev libffi-dev liblzma-dev libncurses-dev \
    uuid-dev xz-utils libgl1 libglib2.0-0 libatomic1 libstdc++6 \
    openssh-server avahi-daemon v4l-utils unzip sudo iproute2 \
    device-tree-compiler gpiod python3-libgpiod netplan.io
python3 /tmp/synapse-build/tls_policy.py debian
board=$(python3 -c 'import json; print(json.load(open("image-metadata.json"))["armbian_board"])')
python3 /tmp/synapse-build/hardware.py configure --board "$board"
install -D -m 0644 hardware.py /usr/lib/synapse-image/hardware.py
netplan generate
if [[ $board == radxa-dragon-q6a ]]; then
    update-grub
    systemctl mask serial-getty@ttyMSM0.service
elif [[ $board == rpi4b ]]; then
    systemctl mask serial-getty@serial0.service serial-getty@ttyAMA0.service serial-getty@ttyS0.service hciuart.service
elif [[ $board == orangepi5 ]]; then
    # Armbian vendor kernels otherwise force the USB-C controller into host mode.
    systemctl mask orangepi5-usb2-init.service
fi
if [[ -f synapse-python.tar.xz ]]; then
    # Prebuilt interpreter from build-python.sh (shared by all trixie arm64
    # targets); skips the in-image compile and the toolchain install below.
    mkdir -p /opt/synapse-python
    tar -xJf synapse-python.tar.xz -C /opt/synapse-python --strip-components=1
else
    source_dir=$(mktemp -d /tmp/synapse-python.XXXXXXXX)
    trap 'rm -rf -- "$source_dir"' EXIT
    tar -xzf cpython.tar.gz -C "$source_dir" --strip-components=1
    (
        cd "$source_dir"
        ./configure --prefix=/opt/synapse-python --with-ensurepip=install --disable-test-modules
        make -j"$(nproc)"
        make altinstall
        # Trim dead weight: the static libpython and unstripped binaries.
        rm -f /opt/synapse-python/lib/python3.12/config-*/libpython3.12.a
        find /opt/synapse-python -name '*.so' -exec strip --strip-unneeded {} + 2>/dev/null || true
        strip --strip-unneeded /opt/synapse-python/bin/python3.12 2>/dev/null || true
    )
fi
/opt/synapse-python/bin/python3.12 -c 'import ssl, sqlite3, bz2, lzma, ctypes, readline, venv'
id synapse >/dev/null 2>&1 || useradd --create-home --shell /bin/bash synapse
# No shared image password. Retain Armbian's first-login credential setup.
usermod -aG video synapse
if getent group render >/dev/null; then usermod -aG render synapse; fi
for group in gpio spi dialout; do
    getent group "$group" >/dev/null || groupadd --system "$group"
    usermod -aG "$group" synapse
done
/opt/synapse-python/bin/python3.12 -m venv /home/synapse/.venv
python3 /tmp/synapse-build/tls_policy.py bootstrap --python /home/synapse/.venv/bin/python
wheel=$(python3 -c 'import json; print(json.load(open("image-metadata.json"))["wheel"]["filename"])')
# Binary-only dependencies fail early if RobotPy/OpenCV stop supporting ARM64.
# Prefer the pre-downloaded wheelhouse (offline); fall back to the official
# PyPI index. OS Python is never modified.
pip_args=(--isolated --only-binary=:all:)
if [[ -d wheelhouse && -n $(ls -A wheelhouse 2>/dev/null) ]]; then
    pip_args+=(--no-index --find-links=/tmp/synapse-build/wheelhouse)
else
    pip_args+=(--index-url https://pypi.org/simple)
fi
/home/synapse/.venv/bin/python -m pip "${pip_args[@]}" \
    --requirement hardware-requirements.txt "$wheel"
/home/synapse/.venv/bin/python -m pip check
# The device never runs the simulator viewer; drop its large 3D assets.
rm -rf /home/synapse/.venv/lib/python3.12/site-packages/synapse_sim/assets/*.glb \
       /home/synapse/.venv/lib/python3.12/site-packages/synapse_sim/assets/apriltags
python3 /tmp/synapse-build/prepare_project.py synapse /home/synapse/Synapse
install -o synapse -g synapse -m 0644 main.py /home/synapse/Synapse/main.py
install -o synapse -g synapse -m 0644 settings.yml /home/synapse/Synapse/config/settings.yml
chown -R synapse:synapse /home/synapse/.venv
install -d /usr/share/synapse-image
cp image-metadata.json /usr/share/synapse-image/
/home/synapse/.venv/bin/python /tmp/synapse-build/tls_policy.py audit --system > /usr/share/synapse-image/tls-audit.json
/home/synapse/.venv/bin/python -m pip freeze --all > /usr/share/synapse-image/python-requirements.txt
dpkg-query -W > /usr/share/synapse-image/debian-packages.txt
install -m 0644 synapse-runtime.service synapse-firstboot.service synapse-usb-ethernet.service /etc/systemd/system/
install -m 0755 synapse-firstboot synapse-usb-ethernet synapse-pwm-permissions /usr/local/sbin/
install -D -m 0644 60-synapse-io.rules /etc/udev/rules.d/60-synapse-io.rules
systemctl enable synapse-runtime.service synapse-firstboot.service ssh.service avahi-daemon.service
systemctl enable systemd-networkd.service synapse-usb-ethernet.service
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
# The image runtime only needs the shared libraries, not the C toolchain used
# to build CPython. Purging -dev packages keeps their runtime libraries.
apt-get purge --yes build-essential libc6-dev dpkg-dev \
    libssl-dev zlib1g-dev libbz2-dev libreadline-dev libsqlite3-dev \
    libffi-dev liblzma-dev libncurses-dev uuid-dev
apt-get clean
rm -rf /var/lib/apt/lists/* /root/.cache/pip
