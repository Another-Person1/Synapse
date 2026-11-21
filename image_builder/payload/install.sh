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
state=$(mktemp -d /tmp/synapse-install.XXXXXXXX)
trap 'rm -rf -- "$state"' EXIT
# Everything installed before this script belongs to the Armbian base image.
# The build-package cleanup at the end never removes any of it.
dpkg-query -W -f='${Package}\n' | LC_ALL=C sort -u > "$state/base-packages"
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
# configuration intact. A private CPython is used because NumPy 1.26 needs <3.13.
apt-get "${apt_options[@]}" install --yes --no-install-recommends \
    python3 ca-certificates xz-utils libgl1 libglib2.0-0 libatomic1 libstdc++6 \
    openssh-server avahi-daemon v4l-utils unzip sudo iproute2 \
    device-tree-compiler gpiod python3-libgpiod netplan.io
python3 /tmp/synapse-build/tls_policy.py debian
board=$(python3 -c 'import json; print(json.load(open("image-metadata.json"))["armbian_board"])')
python3 /tmp/synapse-build/hardware.py configure --board "$board"
install -D -m 0644 hardware.py /usr/lib/synapse-image/hardware.py
netplan generate
case $board in
    radxa-dragon-q6a|radxa-dragon-q8b)
        # Strip the ttyMSM0 console from the kernel command line, then free the
        # header UART (pins 8/10) from the login console.
        update-grub
        systemctl mask serial-getty@ttyMSM0.service
        ;;
    rpi4b)
        systemctl mask serial-getty@serial0.service serial-getty@ttyAMA0.service serial-getty@ttyS0.service hciuart.service
        ;;
    rock-5b)
        # hardware.py pins the kernel console to the display (console=display)
        # and the uart2-m0 overlay turns header pins 8/10 into ttyS2 (replacing
        # the vendor ttyFIQ0 debugger); neither may carry a login console.
        systemctl mask serial-getty@ttyS2.service serial-getty@ttyFIQ0.service
        ;;
    orangepi5)
        # Armbian vendor kernels otherwise force the USB-C controller into host mode.
        systemctl mask orangepi5-usb2-init.service
        ;;
esac
mapfile -t build_packages < <(grep -Ev '^[[:space:]]*(#|$)' cpython-build-packages.txt)
if [[ -f synapse-python.tar.xz ]]; then
    # Prebuilt by build-python.sh from the same pinned source, so no compiler is
    # installed. Refuse it unless it targets this Debian release and architecture.
    install -d -m 0755 /opt/synapse-python
    tar -xJf synapse-python.tar.xz -C /opt/synapse-python --strip-components=1
    built_for=$(paste -sd ' ' /opt/synapse-python/.synapse-build-os)
    # shellcheck source=/dev/null
    this_image=$(. /etc/os-release && printf '%s %s %s' "$ID" "$VERSION_CODENAME" "$(dpkg --print-architecture)")
    [[ $built_for == "$this_image" ]] || {
        echo "Prebuilt CPython was built for '$built_for' but this image is '$this_image'." >&2
        echo 'Rebuild it with build-python.sh for this board, or omit --python-tarball.' >&2
        exit 1
    }
else
    apt-get "${apt_options[@]}" install --yes --no-install-recommends "${build_packages[@]}"
    bash /tmp/synapse-build/build_cpython.sh cpython.tar.gz /tmp/synapse-build/runtime_libraries.py
fi
# Install, and mark as explicitly installed, every Debian library the
# interpreter links against so the cleanup below can never remove one.
mapfile -t python_runtime < /opt/synapse-python/.synapse-runtime-packages
for package in "${python_runtime[@]}"; do
    [[ $package =~ ^[a-z0-9][a-z0-9+.-]+$ ]] || { echo "Invalid runtime package name: $package" >&2; exit 1; }
done
apt-get "${apt_options[@]}" install --yes --no-install-recommends "${python_runtime[@]}"
python3 /tmp/synapse-build/runtime_libraries.py check /opt/synapse-python
/opt/synapse-python/bin/python3.12 -c 'import ssl, sqlite3, bz2, lzma, zlib, ctypes, readline, venv'
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
# Use the hash-checked wheelhouse (offline) when one was supplied, otherwise the
# official PyPI index. OS Python is never modified.
pip_args=(--isolated install --only-binary=:all:)
if compgen -G 'wheelhouse/*.whl' >/dev/null; then
    pip_args+=(--no-index --find-links=/tmp/synapse-build/wheelhouse)
else
    pip_args+=(--index-url https://pypi.org/simple)
fi
/home/synapse/.venv/bin/python -m pip "${pip_args[@]}" \
    --requirement hardware-requirements.txt "$wheel"
/home/synapse/.venv/bin/python -m pip check
python3 /tmp/synapse-build/prepare_project.py synapse /home/synapse/Synapse
install -o synapse -g synapse -m 0644 main.py /home/synapse/Synapse/main.py
install -o synapse -g synapse -m 0644 settings.yml /home/synapse/Synapse/config/settings.yml
chown -R synapse:synapse /home/synapse/.venv
install -d /usr/share/synapse-image
cp image-metadata.json /usr/share/synapse-image/
install -m 0644 synapse-runtime.service synapse-firstboot.service synapse-usb-ethernet.service /etc/systemd/system/
install -m 0755 synapse-firstboot synapse-usb-ethernet synapse-pwm-permissions /usr/local/sbin/
install -D -m 0644 60-synapse-io.rules /etc/udev/rules.d/60-synapse-io.rules
systemctl enable synapse-runtime.service synapse-firstboot.service ssh.service avahi-daemon.service
systemctl enable systemd-networkd.service synapse-usb-ethernet.service
install -d /etc/systemd/journald.conf.d
printf '[Journal]\nSystemMaxUse=100M\nRuntimeMaxUse=50M\n' > /etc/systemd/journald.conf.d/60-synapse.conf
# Every network link is optional (netplan `optional: true`), so a boot without
# an Ethernet cable or DHCP server must not hold network-online.target, and the
# Synapse runtime ordered after it, for wait-online's default 2-minute timeout.
install -d /etc/systemd/system/systemd-networkd-wait-online.service.d
printf '[Service]\nExecStart=\nExecStart=/usr/lib/systemd/systemd-networkd-wait-online --timeout=20\n' \
    > /etc/systemd/system/systemd-networkd-wait-online.service.d/60-synapse.conf
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

# Remove the compiler toolchain: only packages this script added for the
# CPython build, then the dependencies that leaves orphaned. Never touch the
# Armbian base, anything installed explicitly above, or any Debian library that
# the interpreter or Synapse's Python packages (OpenCV, RobotPy, NumPy, napa,
# ...) load. Those are marked manually installed first.
mapfile -t linked < <(python3 /tmp/synapse-build/runtime_libraries.py packages /opt/synapse-python /home/synapse/.venv)
if (( ${#linked[@]} )); then
    apt-mark manual "${linked[@]}" >/dev/null
fi
added_packages() {
    dpkg-query -W -f='${Package}\n' | LC_ALL=C sort -u | LC_ALL=C comm -23 - "$state/base-packages"
}
purge_added() {
    (( $# )) || return 0
    local planned unexpected
    planned=$(apt-get -s purge "$@" | awk '/^(Purg|Remv) / {print $2}' | sed 's/:.*//' | LC_ALL=C sort -u)
    unexpected=$(LC_ALL=C comm -23 <(printf '%s\n' "$planned") <(printf '%s\n' "$@" | LC_ALL=C sort -u) | sed '/^$/d')
    [[ -z $unexpected ]] || {
        echo "Refusing build cleanup that would also remove: $unexpected" >&2
        exit 1
    }
    apt-get purge --yes "$@"
}
mapfile -t added_build < <(LC_ALL=C comm -12 <(printf '%s\n' "${build_packages[@]}" | LC_ALL=C sort -u) <(added_packages))
purge_added "${added_build[@]}"
mapfile -t orphans < <(LC_ALL=C comm -12 \
    <(apt-get -s autoremove | awk '/^Remv / {print $2}' | sed 's/:.*//' | LC_ALL=C sort -u) \
    <(added_packages))
purge_added "${orphans[@]}"
python3 /tmp/synapse-build/runtime_libraries.py check /opt/synapse-python

# Record the final state, after cleanup, so the inventories match the image.
/home/synapse/.venv/bin/python /tmp/synapse-build/tls_policy.py audit --system > /usr/share/synapse-image/tls-audit.json
/home/synapse/.venv/bin/python -m pip freeze --all > /usr/share/synapse-image/python-requirements.txt
dpkg-query -W > /usr/share/synapse-image/debian-packages.txt
apt-get clean
rm -rf /var/lib/apt/lists/* /root/.cache/pip
