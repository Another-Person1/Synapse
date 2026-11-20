#!/usr/bin/env bash
# Build the private CPython once and pack /opt/synapse-python for every image.
#
# Compiles inside a scratch copy of the signature-verified Armbian base for
# BOARD, so the interpreter links against that Debian release's libraries.
# install.sh refuses the tarball on an image of a different Debian release or
# architecture. Run on a native ARM64 Linux host with root:
#
#   sudo -E bash image_builder/build-python.sh --board raspberrypi \
#       --work-dir "$RUNNER_TEMP/synapse-python-build" --output dist/synapse-python.tar.xz
#
# A local image build without --python-tarball compiles CPython in-image instead.
set -euo pipefail

board=''
work=''
output=''
while [[ $# -gt 0 ]]; do
    case "$1" in
        --board) board=$2; shift 2;;
        --work-dir) work=$(realpath -m -- "$2"); shift 2;;
        --output) output=$(realpath -m -- "$2"); shift 2;;
        *) echo "Unknown argument: $1" >&2; exit 2;;
    esac
done
[[ -n $board && -n $work && -n $output ]] || { echo 'Usage: build-python.sh --board BOARD --work-dir DIR --output FILE.tar.xz' >&2; exit 2; }
[[ $board =~ ^[a-z0-9-]+$ ]] || { echo "Invalid board name: $board" >&2; exit 2; }
[[ $output == *.tar.xz && ! -e $output ]] || { echo "Output must be a new .tar.xz file: $output" >&2; exit 1; }
[[ $(id -u) == 0 ]] || { echo 'CPython build requires root (loop devices, chroot).' >&2; exit 1; }
[[ $(uname -m) == aarch64 ]] || { echo 'Use a native ARM64 Linux build host.' >&2; exit 1; }
for tool in losetup lsblk blkid sfdisk parted partprobe sgdisk e2fsck resize2fs mount mountpoint umount chroot xz tar python3 udevadm; do
    command -v "$tool" >/dev/null || { echo "Missing host tool: $tool" >&2; exit 1; }
done

repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo"
mkdir -p "$(dirname -- "$output")" "$work/downloads"

# Resolve, download and verify the base image (SHA256 + pinned Armbian
# signature) and the pinned CPython source through the builder's guards.
mapfile -t inputs < <(python3 - "$work/downloads" "$board" <<'PY'
import json, sys
from pathlib import Path
from image_builder import build

cache = Path(sys.argv[1])
with build.open_official(build.SOURCES["armbian_index"]) as response:
    index = json.load(response)
asset = build.select_image(index, sys.argv[2])
url = asset["file_url"]
if asset["file_url_sha"] != url + ".sha" or asset.get("file_url_asc", url + ".asc") != url + ".asc":
    raise SystemExit("Inventory checksum/signature URLs do not match the selected image")
build.load_mirrors()
name = url.rsplit("/", 1)[-1]
with build.open_armbian(asset["file_url_sha"]) as response:
    checksum = build.parse_checksum(response.read(1024 * 1024).decode(), name)
base = cache / name
build.download(url, base, checksum)
signature = cache / (name + ".asc")
with build.open_armbian(url + ".asc") as response:
    signature.write_bytes(response.read(1024 * 1024))
build.verify_signature(base, signature)
python_source = cache / f"cpython-{build.SOURCES['python_version']}.tar.gz"
build.download(build.SOURCES["python_url"], python_source, build.SOURCES["python_sha256"])
print(base)
print(python_source)
PY
)
[[ ${#inputs[@]} == 2 && -f ${inputs[0]} && -f ${inputs[1]} ]] || { echo 'Could not prepare verified build inputs.' >&2; exit 1; }
base=${inputs[0]}
python_source=${inputs[1]}

build_dir=$(mktemp -d "$work/build.XXXXXXXX")
root=$build_dir/root
mkdir "$root"
loop=''
cleanup() {
    result=$?
    trap - EXIT INT TERM
    set +e
    if mountpoint -q "$root"; then umount -R "$root"; fi
    if mountpoint -q "$root"; then
        echo "Unmount failed. Retaining $build_dir and $loop for recovery." >&2
        exit 1
    fi
    if [[ -n $loop ]] && ! losetup -d "$loop"; then
        echo "Loop detach failed. Retaining $build_dir for recovery." >&2
        exit 1
    fi
    rm -rf -- "$build_dir" "$output.part"
    exit "$result"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# A throwaway copy: grow it for the toolchain and compile, then discard it.
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
partition=$(cat "/sys/class/block/$(basename -- "$rootdev")/partition")
sfdisk --json "$loop" > "$build_dir/partitions.json"
python3 - "$build_dir/partitions.json" "$rootdev" <<'PY'
import json, sys
p = json.load(open(sys.argv[1]))['partitiontable']
last = max(p['partitions'], key=lambda x: x['start'] + x['size'])
assert last['node'] == sys.argv[2], 'Root filesystem is not the final partition'
PY
truncate -s +4G "$build_dir/image.img"
losetup -c "$loop"
if [[ $(blkid -s PTTYPE -o value "$loop") == gpt ]]; then
    sgdisk --move-second-header "$loop"
fi
parted --script "$loop" resizepart "$partition" 100%
partprobe "$loop"
udevadm settle
set +e
e2fsck -pf "$rootdev"
fsck_status=$?
set -e
(( fsck_status <= 1 )) || exit "$fsck_status"
resize2fs "$rootdev"

mount "$rootdev" "$root"
mount -t proc proc "$root/proc"
mount --rbind /dev "$root/dev"
mount --make-rslave "$root/dev"
mount -t sysfs sysfs "$root/sys"
mount -t tmpfs tmpfs "$root/run"
# Never start services from the chroot. This root is discarded afterwards.
printf '#!/bin/sh\nexit 101\n' > "$root/usr/sbin/policy-rc.d"
chmod 755 "$root/usr/sbin/policy-rc.d"
rm -f "$root/etc/resolv.conf"
cp -L /etc/resolv.conf "$root/etc/resolv.conf"
stage=$root/tmp/synapse-python-build
mkdir -p "$stage"
python3 image_builder/payload/tls_policy.py bundle --output "$stage/ca.pem"
cp "$python_source" "$stage/cpython.tar.gz"
cp image_builder/payload/build_cpython.sh image_builder/payload/runtime_libraries.py \
    image_builder/payload/cpython-build-packages.txt "$stage/"
# shellcheck disable=SC2016 # expanded by the chroot's shell, not this one
chroot "$root" /usr/bin/env SSL_CERT_FILE=/tmp/synapse-python-build/ca.pem /bin/bash -euo pipefail -c '
    cd /tmp/synapse-python-build
    export DEBIAN_FRONTEND=noninteractive
    apt_options=(-o "Acquire::https::CaInfo=$SSL_CERT_FILE")
    mapfile -t build_packages < <(grep -Ev "^[[:space:]]*(#|$)" cpython-build-packages.txt)
    apt-get "${apt_options[@]}" update
    apt-get "${apt_options[@]}" install --yes --no-install-recommends python3 ca-certificates "${build_packages[@]}"
    bash build_cpython.sh cpython.tar.gz runtime_libraries.py
'

# Pack only the interpreter; root-owned regardless of the host's IDs.
XZ_OPT=-T0 tar --create --xz --numeric-owner --owner=0 --group=0 \
    --file "$output.part" -C "$root/opt" synapse-python
mv -- "$output.part" "$output"
echo "Built $output"
sha256sum "$output"
