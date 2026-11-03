"""Validate a mounted base using the host Python, before modifying its contents."""

import json
import shlex
import sys
from pathlib import Path


def release_fields(path: Path) -> dict:
    values = {}
    for line in path.read_text().splitlines():
        if "=" not in line or line.startswith("#"):
            continue
        key, value = line.split("=", 1)
        parts = shlex.split(value)
        values[key] = parts[0] if parts else ""
    return values


def check_target(root: Path, metadata: dict) -> None:
    os_release = release_fields(root / "etc/os-release")
    armbian = release_fields(root / "etc/armbian-release")
    if os_release.get("ID") != "debian":
        raise ValueError("Only Debian CLI minimal images are supported")
    if os_release.get("VERSION_CODENAME") != metadata["base"]["distro"]:
        raise ValueError("Unexpected Debian release in the base image")
    if armbian.get("BOARD") != metadata["armbian_board"]:
        raise ValueError("Base image does not match the requested board")
    if armbian.get("ARCH") != "arm64":
        raise ValueError("Expected an ARM64 Armbian base image")
    expected_version = metadata["base"].get("armbian_version")
    if expected_version and armbian.get("VERSION") != expected_version:
        raise ValueError(
            "Armbian release does not match the selected inventory version"
        )


if __name__ == "__main__":
    check_target(Path(sys.argv[1]), json.loads(Path(sys.argv[2]).read_text()))
