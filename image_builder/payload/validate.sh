#!/usr/bin/env bash
set -euo pipefail
cd /home/synapse/Synapse
runuser -u synapse -- /home/synapse/.venv/bin/python - <<'PY'
import importlib.util
from pathlib import Path
import cv2, cscore, ntcore, robotpy_apriltag, wpimath
from periphery import GPIO, SPI, PWM, Serial
from synapse.core.synapse import Synapse
spec = importlib.util.find_spec('synapse_ui')
assert spec and spec.origin, 'Packaged web UI not discoverable'
assert (Path(spec.origin).parent / 'dashboard.html').is_file(), 'Web UI not built'
assert cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11).bytesList.size
print('Synapse, native ARM64 vision dependencies, and packaged UI validated')
PY
# Re-check after install.sh removed the build toolchain: nothing Synapse
# needs may have gone with it.
/home/synapse/.venv/bin/python -m pip check
/home/synapse/.venv/bin/python /tmp/synapse-build/tls_policy.py audit --system
/opt/synapse-python/bin/python3.12 -c 'import ssl, sqlite3, bz2, lzma, zlib, ctypes, readline, venv'
python3 /tmp/synapse-build/runtime_libraries.py check /opt/synapse-python
test -x /usr/lib/systemd/systemd-networkd-wait-online
python3 /tmp/synapse-build/hardware.py validate
netplan generate
# The direct-link drop-in in /etc/systemd/network targets this generated name.
test -f /run/systemd/network/10-netplan-zz-synapse-ethernet.network
grep -Eqx 'LinkLocalAddressing=(yes|ipv4)' /run/systemd/network/10-netplan-zz-synapse-ethernet.network
test -f /etc/systemd/network/10-netplan-zz-synapse-ethernet.network.d/50-synapse-direct-link.conf
/home/synapse/.venv/bin/python /tmp/synapse-build/smoke.py
systemctl is-enabled synapse-runtime.service synapse-firstboot.service ssh.service avahi-daemon.service synapse-usb-ethernet.service systemd-networkd.service
systemd-analyze verify /etc/systemd/system/synapse-runtime.service /etc/systemd/system/synapse-firstboot.service /etc/systemd/system/synapse-usb-ethernet.service
test ! -s /etc/machine-id
test ! -e /var/lib/synapse/identity-initialized
test -f /home/synapse/Synapse/config/settings.yml
test -f /etc/armbian-release
