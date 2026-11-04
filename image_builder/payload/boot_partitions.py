"""Resolve fstab boot mounts exclusively against the image's own loop devices."""

import json
import sys
from pathlib import Path

BOOT_PATHS = {"/boot", "/boot/firmware", "/boot/efi"}


def boot_mounts(root, blockdevices):
    def flatten(devices):
        for device in devices:
            yield device
            yield from flatten(device.get("children", []))

    devices = list(flatten(blockdevices["blockdevices"]))
    mounts = []
    for line in (root / "etc/fstab").read_text().splitlines():
        fields = line.split()
        if not fields or fields[0].startswith("#") or len(fields) < 3:
            continue
        source, target, filesystem = fields[:3]
        if target not in BOOT_PATHS:
            continue
        if filesystem not in {"vfat", "ext4", "ext2", "ext3"}:
            raise ValueError(f"Unsupported image boot filesystem: {filesystem}")
        if not (root / target.lstrip("/")).resolve().is_relative_to(root.resolve()):
            raise ValueError("Boot mount escapes the image root")
        key, separator, value = source.partition("=")
        if not separator or key not in {"UUID", "PARTUUID"}:
            raise ValueError(f"Boot mount must use an image UUID/PARTUUID: {source}")
        matches = [d for d in devices if d.get(key.lower()) == value]
        if len(matches) != 1 or matches[0].get("fstype") != filesystem:
            raise ValueError(
                f"Boot mount is missing or ambiguous in the image: {target}"
            )
        mounts.append((matches[0]["name"], target, filesystem))
    return sorted(mounts, key=lambda mount: mount[1])


if __name__ == "__main__":
    for mount in boot_mounts(
        Path(sys.argv[1]), json.loads(Path(sys.argv[2]).read_text())
    ):
        print("\t".join(mount))
