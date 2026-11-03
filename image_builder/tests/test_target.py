import pytest

from image_builder.payload.check_target import check_target, release_fields


def base_root(
    tmp_path, board="rpi4b", distro="trixie", architecture="arm64", os_id="debian"
):
    etc = tmp_path / "etc"
    etc.mkdir()
    (etc / "os-release").write_text(f'ID="{os_id}"\nVERSION_CODENAME={distro}\n')
    (etc / "armbian-release").write_text(f'BOARD="{board}"\nARCH={architecture}\n')
    return tmp_path


def test_base_identity_matches_selected_payload(tmp_path):
    check_target(
        base_root(tmp_path), {"armbian_board": "rpi4b", "base": {"distro": "trixie"}}
    )


@pytest.mark.parametrize(
    "change",
    [
        {"board": "orangepi5"},
        {"distro": "bookworm"},
        {"architecture": "armhf"},
        {"os_id": "ubuntu"},
    ],
)
def test_wrong_base_is_rejected_before_chroot_modifications(tmp_path, change):
    root = base_root(tmp_path, **change)
    with pytest.raises(ValueError):
        check_target(root, {"armbian_board": "rpi4b", "base": {"distro": "trixie"}})


def test_release_files_are_read_as_data(tmp_path):
    path = tmp_path / "os-release"
    path.write_text(
        "# comment\nID='debian'\nUNTRUSTED='$(touch should-not-exist)'\nEMPTY=\n"
    )
    fields = release_fields(path)
    assert fields == {
        "ID": "debian",
        "UNTRUSTED": "$(touch should-not-exist)",
        "EMPTY": "",
    }
    assert not (tmp_path / "should-not-exist").exists()


def test_different_signed_release_cannot_substitute_selected_version(tmp_path):
    root = base_root(tmp_path)
    with (root / "etc/armbian-release").open("a") as stream:
        stream.write("VERSION=25.5.1\n")
    with pytest.raises(ValueError, match="inventory version"):
        check_target(
            root,
            {
                "armbian_board": "rpi4b",
                "base": {"distro": "trixie", "armbian_version": "26.8.1"},
            },
        )
