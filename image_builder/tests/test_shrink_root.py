# SPDX-FileCopyrightText: 2026 Dan Peled
#
# SPDX-License-Identifier: GPL-3.0-or-later

import pytest

from image_builder.payload.shrink_root import plan_shrink


def table(partitions):
    return {"partitiontable": {"sectorsize": 512, "partitions": partitions}}


def test_shrink_last_partition():
    parts = table([
        {"node": "/dev/loop0p1", "start": 2048, "size": 1048576},
        {"node": "/dev/loop0p2", "start": 1050624, "size": 20971520},
    ])
    plan = plan_shrink(parts, "/dev/loop0p2", block_count=1000000, block_size=4096)
    assert plan["partition"] == 2
    assert plan["start_sector"] == 1050624
    # 1000000 * 8 sectors + 65536 margin blocks * 8 = 8522752 sectors
    expected_end = 1050624 + (1000000 + 65536) * 8
    aligned = -(-expected_end // 2048) * 2048
    assert plan["new_end_sector"] == aligned
    assert plan["new_fs_blocks"] == 1000000 + 65536
    assert plan["new_file_bytes"] == (aligned + 2048) * 512
    assert plan["new_end_sector"] < 1050624 + 20971520  # smaller than before


def test_shrink_refuses_non_final_root():
    parts = table([
        {"node": "/dev/loop0p1", "start": 2048, "size": 20971520},
        {"node": "/dev/loop0p2", "start": 20973568, "size": 1048576},
    ])
    with pytest.raises(ValueError, match="final partition"):
        plan_shrink(parts, "/dev/loop0p1", 1000, 4096)


def test_shrink_refuses_unknown_device():
    parts = table([{"node": "/dev/loop0p2", "start": 2048, "size": 4096}])
    with pytest.raises(ValueError, match="not found"):
        plan_shrink(parts, "/dev/loop0p3", 1000, 4096)


def test_shrink_refuses_4k_sectors():
    parts = {"partitiontable": {"sectorsize": 4096, "partitions": [
        {"node": "/dev/loop0p1", "start": 2048, "size": 4096},
    ]}}
    with pytest.raises(ValueError, match="512-byte"):
        plan_shrink(parts, "/dev/loop0p1", 1000, 4096)


def test_shrink_custom_margin():
    parts = table([{"node": "/dev/loop0p2", "start": 2048, "size": 100000}])
    plan = plan_shrink(parts, "/dev/loop0p2", 1000, 4096, margin_bytes=1024 * 1024)
    assert plan["new_fs_blocks"] == 1000 + 256  # 1 MiB / 4 KiB
