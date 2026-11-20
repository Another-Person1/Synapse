#!/usr/bin/env python3
"""Compute the post-build shrink plan for the grown root partition.

The builder grows the image file by a fixed headroom (modify-image.sh) so the
chroot can compile CPython and install wheels, then grows the final partition
to fill it. Before compressing, the root filesystem is shrunk back to its
content plus a small margin: partition start positions and UUIDs never move,
so only the last partition's end changes and Armbian's first-boot expansion
still grows it to the full SD/eMMC on the device.

Usage:
    shrink_root.py PARTITIONS.json ROOTDEV BLOCK_COUNT BLOCK_SIZE [MARGIN_BYTES]

Prints a JSON plan: partition number, start sector, new end sector (1 MiB
aligned), new filesystem block count and the new image file size in bytes
(keeping 1 MiB at the end for the GPT backup header).
"""

import json
import math
import re
import sys

SECTOR = 512
ALIGN_SECTORS = 2048  # 1 MiB alignment
BACKUP_SECTORS = 2048  # 1 MiB kept at the end for the GPT backup header
DEFAULT_MARGIN = 256 * 1024 * 1024  # 256 MiB free space after shrink


def plan_shrink(
    partitions: dict,
    rootdev: str,
    block_count: int,
    block_size: int,
    margin_bytes: int = DEFAULT_MARGIN,
) -> dict:
    table = partitions.get("partitiontable")
    if not isinstance(table, dict) or not isinstance(table.get("partitions"), list):
        raise ValueError("partitions.json does not contain a partition table")
    if table.get("sectorsize", SECTOR) != SECTOR:
        raise ValueError("Only 512-byte sectors are supported")

    matches = [p for p in table["partitions"] if p.get("node") == rootdev]
    if len(matches) != 1:
        raise ValueError(f"Root device {rootdev} not found exactly once in the table")
    root = matches[0]
    start = root["start"]
    last = max(table["partitions"], key=lambda p: p["start"] + p["size"])
    if last["node"] != rootdev:
        raise ValueError("Root filesystem is not the final partition; refusing to shrink")

    match = re.search(r"(\d+)$", rootdev)
    if not match:
        raise ValueError(f"Cannot derive partition number from {rootdev}")
    number = int(match.group(1))

    if block_size % SECTOR:
        raise ValueError(f"Unsupported block size {block_size}")
    if block_count <= 0:
        raise ValueError("Filesystem block count must be positive")

    margin_blocks = margin_bytes // block_size
    new_fs_blocks = block_count + margin_blocks
    sectors_per_block = block_size // SECTOR
    end = start + new_fs_blocks * sectors_per_block
    new_end_sector = math.ceil(end / ALIGN_SECTORS) * ALIGN_SECTORS
    new_file_bytes = (new_end_sector + BACKUP_SECTORS) * SECTOR
    return {
        "partition": number,
        "start_sector": start,
        "new_end_sector": new_end_sector,
        "new_fs_blocks": new_fs_blocks,
        "new_file_bytes": new_file_bytes,
    }


def main(argv) -> int:
    if len(argv) not in (5, 6):
        print(__doc__, file=sys.stderr)
        return 2
    partitions = json.loads(open(argv[1]).read())
    rootdev = argv[2]
    block_count = int(argv[3])
    block_size = int(argv[4])
    margin = int(argv[5]) if len(argv) == 6 else DEFAULT_MARGIN
    print(json.dumps(plan_shrink(partitions, rootdev, block_count, block_size, margin)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
