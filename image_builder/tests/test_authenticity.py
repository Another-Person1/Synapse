import io
import json
import shutil
import subprocess

import pytest

from image_builder import build


def test_only_registry_listed_https_mirror_paths_are_accepted(monkeypatch):
    registry = {"EU": ["https://mirror.example/armbian/", "http://unsafe.example/dl/"]}
    monkeypatch.setattr(
        build, "open_official", lambda url: io.BytesIO(json.dumps(registry).encode())
    )
    monkeypatch.setattr(build, "MIRROR_BASES", set())
    build.load_mirrors()
    assert build.official_url("https://mirror.example/armbian/image.xz")
    for url in (
        "https://mirror.example/other/image.xz",
        "https://mirror.example.evil/armbian/a",
        "http://unsafe.example/dl/a",
        "https://mirror.example/armbian/%2e%2e/secret",
    ):
        with pytest.raises(build.BuildError):
            build.official_url(url)


@pytest.mark.skipif(not shutil.which("gpg"), reason="GnuPG required")
def test_real_signature_rejects_tampering_and_unapproved_signer(tmp_path, monkeypatch):
    home = tmp_path / "gnupg"
    home.mkdir(mode=0o700)
    command = ["gpg", "--batch", "--no-options", "--homedir", str(home)]
    subprocess.run(
        command
        + [
            "--pinentry-mode",
            "loopback",
            "--passphrase",
            "",
            "--quick-generate-key",
            "Builder test <test@example.invalid>",
            "ed25519",
            "sign",
            "0",
        ],
        check=True,
        capture_output=True,
    )
    listing = subprocess.check_output(
        command + ["--with-colons", "--list-keys"], text=True
    )
    fingerprint = next(
        line.split(":")[9] for line in listing.splitlines() if line.startswith("fpr:")
    )
    keys = tmp_path / "keys"
    keys.mkdir()
    (keys / "test.asc").write_bytes(
        subprocess.check_output(command + ["--armor", "--export", fingerprint])
    )
    image = tmp_path / "image.xz"
    image.write_bytes(b"authentic image bytes")
    signature = tmp_path / "image.xz.asc"
    subprocess.run(
        command + ["--armor", "--detach-sign", "--output", str(signature), str(image)],
        check=True,
        capture_output=True,
    )
    monkeypatch.setattr(build, "HERE", tmp_path)
    monkeypatch.setattr(build, "SIGNING_FINGERPRINTS", {fingerprint})
    assert build.verify_signature(image, signature) == [fingerprint]
    image.write_bytes(b"tampered image bytes")
    with pytest.raises(build.BuildError, match="signature is invalid"):
        build.verify_signature(image, signature)
    image.write_bytes(b"authentic image bytes")
    monkeypatch.setattr(build, "SIGNING_FINGERPRINTS", {"0" * 40})
    with pytest.raises(build.BuildError, match="unapproved key"):
        build.verify_signature(image, signature)
    signature.write_text("not a signature")
    with pytest.raises(build.BuildError):
        build.verify_signature(image, signature)
