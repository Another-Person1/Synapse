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
# pip check and the TLS audit already ran at the end of install.sh.
python3 /tmp/synapse-build/hardware.py validate
netplan generate
/home/synapse/.venv/bin/python /tmp/synapse-build/smoke.py
systemctl is-enabled synapse-runtime.service synapse-firstboot.service ssh.service avahi-daemon.service synapse-usb-ethernet.service systemd-networkd.service
systemd-analyze verify /etc/systemd/system/synapse-runtime.service /etc/systemd/system/synapse-firstboot.service /etc/systemd/system/synapse-usb-ethernet.service
test ! -s /etc/machine-id
test ! -e /var/lib/synapse/identity-initialized
test -f /home/synapse/Synapse/config/settings.yml
test -f /etc/armbian-release
