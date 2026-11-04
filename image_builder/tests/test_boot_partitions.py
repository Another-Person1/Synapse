import pytest

from image_builder.payload.boot_partitions import boot_mounts


def devices():
    return {
        "blockdevices": [
            {
                "name": "/dev/loop7",
                "children": [
                    {
                        "name": "/dev/loop7p1",
                        "uuid": "BOOT",
                        "partuuid": "part-1",
                        "fstype": "vfat",
                    },
                    {"name": "/dev/loop7p2", "uuid": "ROOT", "fstype": "ext4"},
                ],
            }
        ]
    }


def fstab(root, contents):
    (root / "etc").mkdir()
    (root / "etc/fstab").write_text(contents)


def test_firmware_mount_uses_only_image_devices(tmp_path):
    fstab(
        tmp_path,
        "UUID=ROOT / ext4 defaults 0 1\nUUID=BOOT /boot/firmware vfat defaults 0 2\n",
    )
    assert boot_mounts(tmp_path, devices()) == [
        ("/dev/loop7p1", "/boot/firmware", "vfat")
    ]


def test_efi_partuuid_and_nested_mount_order(tmp_path):
    fstab(
        tmp_path,
        "PARTUUID=part-1 /boot/efi vfat defaults 0 2\nUUID=ROOT /boot ext4 defaults 0 2\n",
    )
    assert [m[1] for m in boot_mounts(tmp_path, devices())] == ["/boot", "/boot/efi"]


@pytest.mark.parametrize(
    "source,filesystem",
    [("UUID=HOST", "vfat"), ("UUID=BOOT", "ext4"), ("/dev/sda1", "vfat")],
)
def test_missing_host_or_mismatched_mount_cannot_be_used(tmp_path, source, filesystem):
    fstab(tmp_path, f"{source} /boot/firmware {filesystem} defaults 0 2\n")
    with pytest.raises(ValueError):
        boot_mounts(tmp_path, devices())


def test_boot_symlink_cannot_escape_the_image(tmp_path):
    fstab(tmp_path, "UUID=BOOT /boot/firmware vfat defaults 0 2\n")
    (tmp_path / "boot").symlink_to("/tmp")
    with pytest.raises(ValueError, match="escapes"):
        boot_mounts(tmp_path, devices())


def test_root_only_images_need_no_extra_mount(tmp_path):
    fstab(tmp_path, "UUID=ROOT / ext4 defaults 0 1\ntmpfs /tmp tmpfs defaults 0 0\n")
    assert boot_mounts(tmp_path, devices()) == []
