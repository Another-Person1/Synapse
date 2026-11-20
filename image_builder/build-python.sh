#!/usr/bin/env bash
# Build the private CPython once for all Synapse images and pack /opt/synapse-python.
#
# Every image target is Debian trixie arm64, so the compiled interpreter is
# identical for all boards. Run on a native ARM64 Linux host with root:
#
#   sudo bash image_builder/build-python.sh --board raspberrypi \
#       --work-dir "$RUNNER_TEMP/synapse-python-build" --output dist/synapse-python.tar.xz
#
# The output tarball contains synapse-python/... and is unpacked into each
# image by payload/install.sh (skipping the in-image compile). A local image
# build without --python-tarball still compiles CPython inside the image.
set -euo pipefail

board=''
work=''
output=''
while [[ $# -gt 0 ]]; do
    case "$1" in
        --board) board=$(realpath -m -- "$2"); shift 2;;
        --work-dir) work=$(realpath -m -- "$2"); shift 2;;
        --output) output=$(realpath -m -- "$2"); shift 2;;
        *) echo "Unknown argument: $1" >&2; exit 2;;
    esac
done
[[ -n $board && -n $work && -n $output ]] || { echo 'Usage: build-python.sh --board BOARD --work-dir DIR --output FILE.tar.xz' >&2; exit 2; }
[[ $(id -u) == 0 ]] || { echo 'CPython build requires root (loop devices, chroot).' >&2; exit 1; }
[[ $(uname -m) == aarch64 ]] || { echo 'Use a native ARM64 Linux build host.' >&2; exit 1; }
for tool in losetup lsblk mount umount chroot xz python3 udevadm partprobe dumpe2fs e2fsck; do
    command -v "$tool" >/dev/null || { echo "Missing host tool: $tool" >&2; exit 1; }
done

cd "$(dirname -- "${BASH_SOURCE[0]}")/.."
mkdir -p "$(dirname -- "$output")" "$work/downloads"

# Resolve, verify and download the base image, its signature and the pinned
# CPython source through the builder's audited TLS + official-URL guards.
read -r base python_source < <(python3 - "$work/downloads" "$board" <<'PY'
import json, sys
from pathlib import Path
from image_builder import build

cache = Path(sys.argv[1])
cache.mkdir(parents=True, exist_ok=True)
with build.open_official(build.SOURCES["armbian_index"]) as response:
    index = json.load(response)
asset = build.select_image(index, sys.argv[2])
build.load_mirrors()
url = asset["file_url"]
name = url.rsplit("/", 1)[-1]
checksum = build.parse_checksum(
    build.open_armbian(asset["file_url_sha"]).read(1024 * 1024).decode(), name
)
base = cache / name
build.download(url, base, checksum)
signature = cache / (name + ".asc")
with build.open_armbian(asset.get("file_url_asc", url + ".asc")) as response:
    signature.write_bytes(response.read(1024 * 1024))
build.verify_signature(base, signature)
python_source = cache / "cpython.tar.gz"
build.download(build.SOURCES["python_url"], python_source, build.SOURCES["python_sha256"])
print(base)
print(python_source)
PY
)

build_dir=$(mktemp -d "$work/build.XXXXXXXX")
root=$build_dir/root
mkdir "$root"
loop=''
cleanup() {
    result=$?
    trap - EXIT
    set +e
    if mountpoint -q "$root"; then umount -R "$root"; fi
    if [[ -n $loop ]] && ! losetup -d "$loop"; then
        echo "Loop detach failed. Retaining $build_dir for recovery." >&2
        exit 1
    fi
    rm -rf -- "$build_dir"
    exit "$result"
}
trap cleanup EXIT

xz -dc -- "$base" > "$build_dir/image.img"
loop=$(losetup --find --show --partscan "$build_dir/image.img")
udevadm settle
rootdev=''
while read -r device filesystem; do
    [[ $filesystem == ext4 ]] || continue
    mount -o ro,noload "$device" "$root"
    if [[ -f $root/etc/armbian-release && -f $root/etc/os-release ]]; then
        [[ -z $rootdev ]] || { echo 'More than one Armbian root filesystem.' >&2; exit 1; }
        rootdev=$device
    fi
    umount "$root"
done < <(lsblk -lnpo NAME,FSTYPE "$loop")
[[ -n $rootdev ]] || { echo 'No ext4 Armbian root filesystem found.' >&2; exit 1; }

mount "$rootdev" "$root"
cp -L /etc/resolv.conf "$root/etc/resolv.conf"
mkdir -p "$root/tmp/synapse-python-build"
cp "$python_source" "$root/tmp/synapse-python-build/cpython.tar.gz"
if [[ -n ${SSL_CERT_FILE:-} && -f ${SSL_CERT_FILE:-} ]]; then
    cp -L "$SSL_CERT_FILE" "$root/tmp/synapse-python-build/ca.pem"
fi
cat > "$root/tmp/synapse-python-build/build.sh" <<'INNER'
#!/usr/bin/env bash
set -euo pipefail
cd /tmp/synapse-python-build
export DEBIAN_FRONTEND=noninteractive
apt_options=()
if [[ -f ca.pem ]]; then
    apt_options=(-o "Acquire::https::CaInfo=/tmp/synapse-python-build/ca.pem")
fi
apt-get "${apt_options[@]}" update
apt-get "${apt_options[@]}" install --yes --no-install-recommends \
    build-essential python3 ca-certificates libssl-dev zlib1g-dev libbz2-dev \
    libreadline-dev libsqlite3-dev libffi-dev liblzma-dev libncurses-dev uuid-dev xz-utils
source_dir=$(mktemp -d /tmp/cpython.XXXXXXXX)
tar -xzf cpython.tar.gz -C "$source_dir" --strip-components=1
cd "$source_dir"
./configure --prefix=/opt/synapse-python --with-ensurepip=install --disable-test-modules
make -j"$(nproc)"
make altinstall
# Trim dead weight: static libpython, unstripped binaries (test modules are
# already excluded by --disable-test-modules).
rm -f /opt/synapse-python/lib/python3.12/config-*/libpython3.12.a
find /opt/synapse-python -name '*.so' -exec strip --strip-unneeded {} + 2>/dev/null || true
strip --strip-unneeded /opt/synapse-python/bin/python3.12 2>/dev/null || true
/opt/synapse-python/bin/python3.12 -m compileall -q /opt/synapse-python/lib/python3.12
/opt/synapse-python/bin/python3.12 -c 'import ssl, sqlite3, bz2, lzma, ctypes, readline, venv'
INNER
chmod 755 "$root/tmp/synapse-python-build/build.sh"
chroot "$root" /bin/bash /tmp/synapse-python-build/build.sh

# Pack only the interpreter; the base image itself is discarded.
umount "$root"
mount -o ro "$rootdev" "$root"
tar -cJf "$output" -C "$root/opt" synapse-python
umount "$root"
losetup -d "$loop"
loop=''
echo "Built $output"
sha256sum "$output"
