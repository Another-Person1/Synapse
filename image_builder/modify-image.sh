#!/usr/bin/env bash
# Run on an ARM64 Linux build host with root and loop/mount capabilities.
set -euo pipefail
[[ $# == 3 ]] || { echo 'Usage: modify-image.sh BASE.img.xz PAYLOAD OUTPUT.img.xz' >&2; exit 2; }
[[ $(id -u) == 0 ]] || { echo 'Image modification requires root.' >&2; exit 1; }
[[ $(uname -m) == aarch64 ]] || { echo 'Use a native ARM64 Linux build host.' >&2; exit 1; }
for tool in losetup lsblk blkid sfdisk parted partprobe sgdisk e2fsck fsck.vfat resize2fs dumpe2fs fstrim mount mountpoint umount unshare chroot xz python3 udevadm; do
    command -v "$tool" >/dev/null || { echo "Missing host tool: $tool" >&2; exit 1; }
done
# Mount everything in a private mount namespace. On the host's shared mount
# tree, the image mounts propagate into other services' namespaces and stay
# there after umount, keeping the root filesystem busy for the shrink.
if [[ -z ${SYNAPSE_PRIVATE_MOUNTS:-} ]]; then
    exec unshare --mount --propagation private \
        env SYNAPSE_PRIVATE_MOUNTS=1 bash "${BASH_SOURCE[0]}" "$@"
fi
base=$(realpath -- "$1")
payload=$(realpath -- "$2")
destination=$(realpath -m -- "$3")
[[ -f $base && -f $payload/image-metadata.json && ! -e $destination ]] || exit 1
[[ $destination == *.img.xz ]] || exit 1
python3 - "$payload/image-metadata.json" "$base" <<'PY'
import hashlib, json, sys
metadata = json.load(open(sys.argv[1]))
with open(sys.argv[2], 'rb') as stream:
    assert hashlib.file_digest(stream, 'sha256').hexdigest() == metadata['base_sha256'], 'Base image checksum mismatch'
PY
build_dir=$(mktemp -d "$(dirname -- "$destination")/.synapse-build.XXXXXXXX")
root=$build_dir/root
mkdir "$root"
loop=''
resolver_saved=no
policy_saved=no

cleanup() {
    result=$?
    trap - EXIT INT TERM
    set +e
    if mountpoint -q "$root"; then
        if [[ $resolver_saved == yes ]]; then
            rm -f "$root/etc/resolv.conf"
            mv "$build_dir/resolv.conf" "$root/etc/resolv.conf"
        fi
        if [[ $policy_saved == yes ]]; then
            rm -f "$root/usr/sbin/policy-rc.d"
            if [[ -e $build_dir/policy-rc.d || -L $build_dir/policy-rc.d ]]; then
                mv "$build_dir/policy-rc.d" "$root/usr/sbin/policy-rc.d"
            fi
        fi
        umount -R "$root"
    fi
    if mountpoint -q "$root"; then
        echo "Unmount failed. Retaining $build_dir and $loop for recovery." >&2
        exit 1
    fi
    if [[ -n $loop ]] && ! losetup -d "$loop"; then
        echo "Loop detach failed. Retaining $build_dir for recovery." >&2
        exit 1
    fi
    rm -rf -- "$build_dir"
    exit "$result"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# Work on a copy, including both GPT headers and every boot/firmware partition.
xz -dc -- "$base" > "$build_dir/image.img"
loop=$(losetup --find --show --partscan "$build_dir/image.img")
udevadm settle
# Find the Armbian root by its contents, not a hard-coded p1/p2 assumption.
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
# Growing only the final partition protects EFI, boot partitions and raw loaders.
sfdisk --json "$loop" > "$build_dir/partitions.json"
python3 - "$build_dir/partitions.json" "$rootdev" <<'PY'
import json, sys
p = json.load(open(sys.argv[1]))['partitiontable']
assert p.get('sectorsize', 512) == 512, '4096-byte UFS images require a separate builder'
last = max(p['partitions'], key=lambda x: x['start'] + x['size'])
assert last['node'] == sys.argv[2], 'Root filesystem is not the final partition; refusing to move other partitions'
PY
# Reserve space for CPython compilation and binary Python dependencies.
truncate -s +6G "$build_dir/image.img"
losetup -c "$loop"
# Recorded once: only GPT has a backup header at the end of the disk, and an
# MBR table must never be rewritten as GPT (that changes PARTUUIDs on boot).
pttype=$(blkid -s PTTYPE -o value "$loop")
if [[ $pttype == gpt ]]; then
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
python3 "$payload/check_target.py" "$root" "$payload/image-metadata.json"
# Firmware writes and package hooks must reach the actual image partitions.
# Resolve UUIDs only against this loop device, never against the build host.
lsblk --json --paths --output NAME,UUID,PARTUUID,FSTYPE "$loop" > "$build_dir/blockdevices.json"
python3 "$payload/boot_partitions.py" "$root" "$build_dir/blockdevices.json" > "$build_dir/boot-mounts.tsv"
while IFS=$'\t' read -r device target filesystem; do
    mkdir -p "$root$target"
    mount -t "$filesystem" "$device" "$root$target"
done < "$build_dir/boot-mounts.tsv"
mount -t proc proc "$root/proc"
mount --rbind /dev "$root/dev"
mount --make-rslave "$root/dev"
mount -t sysfs sysfs "$root/sys"
mount -t tmpfs tmpfs "$root/run"
mkdir -p "$root/tmp/synapse-build"
mount --bind "$payload" "$root/tmp/synapse-build"
mount -o remount,bind,ro "$root/tmp/synapse-build"

# Never start services or use the build host's systemd from the chroot.
if [[ -e $root/usr/sbin/policy-rc.d || -L $root/usr/sbin/policy-rc.d ]]; then
    mv "$root/usr/sbin/policy-rc.d" "$build_dir/policy-rc.d"
fi
policy_saved=yes
printf '#!/bin/sh\nexit 101\n' > "$root/usr/sbin/policy-rc.d"
chmod 755 "$root/usr/sbin/policy-rc.d"
if [[ -e $root/etc/resolv.conf || -L $root/etc/resolv.conf ]]; then
    mv "$root/etc/resolv.conf" "$build_dir/resolv.conf"
else
    touch "$build_dir/resolv.conf"
fi
resolver_saved=yes
cp -L /etc/resolv.conf "$root/etc/resolv.conf"
# Retain build-host proxy/custom trust only through a temporary read-only mount.
# Host paths exported by CI do not exist inside the image's chroot.
python3 "$payload/tls_policy.py" bundle --output "$build_dir/build-ca.pem"
touch "$root/tmp/synapse-build-ca.pem"
mount --bind "$build_dir/build-ca.pem" "$root/tmp/synapse-build-ca.pem"
mount -o remount,bind,ro "$root/tmp/synapse-build-ca.pem"
mkdir -p "$root/tmp/synapse-empty-ca-directory"
chroot_env=(SSL_CERT_FILE=/tmp/synapse-build-ca.pem SSL_CERT_DIR=/tmp/synapse-empty-ca-directory REQUESTS_CA_BUNDLE=/tmp/synapse-build-ca.pem CURL_CA_BUNDLE=/tmp/synapse-build-ca.pem PIP_CERT=/tmp/synapse-build-ca.pem GIT_SSL_CAINFO=/tmp/synapse-build-ca.pem GIT_SSL_CAPATH=/tmp/synapse-empty-ca-directory)
chroot "$root" /usr/bin/env "${chroot_env[@]}" /bin/bash /tmp/synapse-build/install.sh
chroot "$root" /usr/bin/env "${chroot_env[@]}" /bin/bash /tmp/synapse-build/validate.sh
umount "$root/tmp/synapse-build-ca.pem"
rm "$root/tmp/synapse-build-ca.pem"
rmdir "$root/tmp/synapse-empty-ca-directory"

# Restore temporary host configuration before unmounting and compressing.
rm "$root/etc/resolv.conf" "$root/usr/sbin/policy-rc.d"
mv "$build_dir/resolv.conf" "$root/etc/resolv.conf"
resolver_saved=no
if [[ -e $build_dir/policy-rc.d || -L $build_dir/policy-rc.d ]]; then
    mv "$build_dir/policy-rc.d" "$root/usr/sbin/policy-rc.d"
fi
policy_saved=no
sync
umount -R "$root"
# udev re-probes the partition after the last writer closes it.
udevadm settle

# Shrink the root filesystem and the final partition back to the actual
# content plus a small margin. Only the last partition's end moves; partition
# starts and UUIDs are untouched, and Armbian's first boot still expands the
# partition to the full SD/eMMC. This keeps the shipped image small enough for
# 8 GB cards/eMMC instead of carrying the build headroom.
# -p repairs only what is safe without asking; anything worse stops the build.
set +e
e2fsck -pf "$rootdev"
fsck_status=$?
set -e
(( fsck_status <= 1 )) || exit "$fsck_status"
resize2fs -M "$rootdev"
read -r block_count block_size < <(dumpe2fs -h "$rootdev" 2>/dev/null | awk -F': *' '/^Block count:/ {c=$2} /^Block size:/ {s=$2} END {print c, s}')
plan=$(python3 "$payload/shrink_root.py" "$build_dir/partitions.json" "$rootdev" "$block_count" "$block_size")
read -r start_sector new_end_sector new_fs_blocks new_file_bytes < <(python3 -c 'import json, sys; p = json.loads(sys.argv[1]); print(p["start_sector"], p["new_end_sector"], p["new_fs_blocks"], p["new_file_bytes"])' "$plan")
resize2fs "$rootdev" "$new_fs_blocks"
# sfdisk changes only this partition's size (start, type and UUIDs are kept)
# and, unlike parted, never stops to ask before shrinking in a script.
echo ", $((new_end_sector - start_sector))" | sfdisk --no-reread --no-tell-kernel \
    --wipe never --wipe-partitions never -N "$partition" "$loop"
truncate -s "$new_file_bytes" "$build_dir/image.img"
losetup -c "$loop"
if [[ $pttype == gpt ]]; then
    sgdisk --move-second-header "$loop"
fi
partprobe "$loop"
udevadm settle
partition_sectors=$(cat "/sys/class/block/$(basename -- "$rootdev")/size")
(( partition_sectors * 512 >= new_fs_blocks * block_size )) || {
    echo 'Shrunk partition is smaller than its filesystem; refusing the image.' >&2
    exit 1
}
# Discard the blocks freed by the build and by the shrink so they compress as
# zeros. Done after the shrink, because resize2fs -M relocates data.
mount "$rootdev" "$root"
fstrim -v "$root" || echo 'fstrim is unsupported here; the image will compress less well.' >&2
umount "$root"

e2fsck -fn "$rootdev"
while IFS=$'\t' read -r device target filesystem; do
    if [[ $filesystem == vfat ]]; then
        fsck.vfat -n "$device"
    else
        e2fsck -fn "$device"
    fi
done < "$build_dir/boot-mounts.tsv"
sfdisk --verify "$loop"
losetup -d "$loop"
loop=''
# Download size matters more than build time: use xz's strongest preset on
# every core. Decompressing needs only ~65 MiB, fine for any flashing tool.
xz -T0 -9 --stdout "$build_dir/image.img" > "$build_dir/output.img.xz"
xz --test "$build_dir/output.img.xz"
# Same-filesystem link publishes atomically and fails if an output already exists.
ln -- "$build_dir/output.img.xz" "$destination"
