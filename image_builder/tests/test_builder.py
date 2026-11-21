import hashlib
import io
import json
import subprocess
import urllib.request
import zipfile

import pytest

from image_builder import build


def asset(
    board="rpi4b", version="26.8.1", distro="trixie", branch="current", **changes
):
    source_board = build.BOARDS[board]["armbian_board"]
    filename = f"Armbian_{version}_{source_board.capitalize()}_{distro}_{branch}_6.18.42_minimal.img.xz"
    url = f"https://dl.armbian.com/{source_board}/archive/{filename}"
    result = {
        "board_slug": source_board,
        "board_support": "conf",
        "variant": "minimal",
        "distro": distro,
        "branch": branch,
        "armbian_version": version,
        "file_url": url,
        "file_url_sha": url + ".sha",
        "file_extension": "img.xz",
        "file_application": "",
        "file_companion": "",
        "promoted": "true",
        "download_repository": "archive",
        "file_date": "2026-08-08T16:03:40Z",
        "kernel_version": "6.18.42",
    }
    result.update(changes)
    return result


@pytest.mark.parametrize("board", build.BOARDS)
def test_each_board_resolves_official_minimal(board):
    expected = asset(board)
    other_board = (
        "orangepi5" if build.BOARDS[board]["armbian_board"] == "rpi4b" else "rpi4b"
    )
    assert (
        build.select_image(
            {"assets": [expected, asset(other_board, version="99.0.0")]}, board
        )
        == expected
    )


def test_latest_stable_beats_older_recommendation_and_newer_nightly():
    stable = asset(version="26.8.2", promoted="false")
    index = {
        "assets": [
            asset(version="26.8.1"),
            stable,
            asset(version="26.11.0-trunk.121", download_repository="ci"),
        ]
    }
    assert build.select_image(index, "rpi4b") == stable


@pytest.mark.parametrize(
    "board", ["raspberrypi", "rpi4b", "raspberrypi5", "cm4", "cm5"]
)
def test_pi_aliases_select_latest_stable_from_real_upstream_slug(board):
    latest = asset(version="26.8.2", promoted="false")
    index = {
        "assets": [
            asset(version="26.8.1"),
            latest,
            asset(version="99.0.0", board_slug="raspberrypi"),
            asset(version="26.11.0-trunk.121", download_repository="ci"),
        ]
    }
    assert build.select_image(index, board) == latest


def test_version_sort_is_numeric_not_lexical():
    candidates = [asset(version="26.9.1"), asset(version="26.10.1")]
    assert (
        build.select_image({"assets": candidates}, "rpi4b")["armbian_version"]
        == "26.10.1"
    )


def test_recommended_kernel_wins_within_same_release():
    current = asset("orangepi5", branch="current", promoted="false")
    vendor = asset("orangepi5", branch="vendor")
    assert build.select_image({"assets": [current, vendor]}, "orangepi5") == vendor


def community_asset(board="cubie-a7z", version="26.11.0-trunk.130", **changes):
    """A community (csc) build: GitHub release asset, cache.armbian.com sidecars."""
    source_board = build.BOARDS[board]["armbian_board"]
    filename = (
        f"Armbian_community_{version}_{source_board.capitalize()}"
        f"_trixie_vendor_6.6.98_minimal.img.xz"
    )
    url = f"https://github.com/armbian/community/releases/download/{version}/{filename}"
    cache = f"https://cache.armbian.com/artifacts/{source_board}/archive/{filename}"
    result = {
        "board_slug": source_board,
        "board_support": "csc",
        "variant": "minimal",
        "distro": "trixie",
        "branch": "vendor",
        "armbian_version": version,
        "file_url": url,
        "file_url_sha": cache + ".sha",
        "file_url_asc": cache + ".asc",
        "file_extension": "img.xz",
        "file_application": "",
        "file_companion": "",
        "promoted": "true",
        "download_repository": "community",
        "file_date": "2026-11-01T00:00:00Z",
        "kernel_version": "6.6.98",
    }
    result.update(changes)
    return result


def test_community_build_is_accepted_only_where_the_board_opts_in():
    cubie = community_asset()
    assert build.select_image({"assets": [cubie]}, "cubie-a7z") == cubie
    with pytest.raises(build.BuildError, match="no supported stable"):
        build.select_image({"assets": [cubie]}, "rpi4b")
    with pytest.raises(build.BuildError, match="no supported stable"):
        build.select_image({"assets": [community_asset(board="rock-5b")]}, "rock-5b")


@pytest.mark.parametrize(
    "change",
    [
        {"download_repository": "ci"},
        {"download_repository": "nightly"},
        {"board_support": "conf", "download_repository": "community"},
        {"armbian_version": "26.11.0"},
        {"file_url": "https://github.com/stranger/community/releases/download/26.11.0-trunk.130/x.img.xz"},
    ],
)
def test_malformed_community_records_are_rejected(change):
    with pytest.raises(build.BuildError):
        build.select_image({"assets": [community_asset(**change)]}, "cubie-a7z")


def test_prepare_accepts_cache_artifact_sidecars_only_for_opted_in_community_boards(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(build, "load_mirrors", lambda: None)
    monkeypatch.setattr(
        build, "verify_signature", lambda image, signature: ["test-signer"]
    )
    wheel = make_wheel(tmp_path / "synapsefrc-1.0-py3-none-any.whl")
    base_contents = b"official community base"
    base_hash = hashlib.sha256(base_contents).hexdigest()
    tarball = tmp_path / "synapse-python.tar.xz"
    tarball.write_bytes(b"prebuilt interpreter")

    def remote(url):
        if url.endswith(".sha"):
            filename = url.split("/")[-1].removesuffix(".sha")
            return io.BytesIO(f"{base_hash}  {filename}\n".encode())
        return io.BytesIO(base_contents)

    monkeypatch.setattr(build, "open_official", remote)
    work = tmp_path / "work"
    work.mkdir()
    base, metadata = build.prepare(
        "cubie-a7z", {"assets": [community_asset()]}, wheel, work, tarball
    )
    assert build.sha256(base) == base_hash
    assert metadata["armbian_board"] == "cubie-a7z"
    assert metadata["base"]["armbian_version"] == "26.11.0-trunk.130"
    assert metadata["base"]["download_repository"] == "community"

    # The same sidecar host must stay rejected for a conf-only board.
    candidate = asset("rpi4b")
    filename = candidate["file_url"].split("/")[-1]
    candidate["file_url_sha"] = (
        f"https://cache.armbian.com/artifacts/rpi4b/archive/{filename}.sha"
    )
    with pytest.raises(build.BuildError, match="checksum URL"):
        build.prepare("rpi4b", {"assets": [candidate]}, wheel, tmp_path / "work2")


def test_stable_release_beats_older_community_trunk_for_opted_in_board():
    conf = asset("cubie-a7z", version="26.11.2")
    trunk = community_asset(version="26.11.0-trunk.130")
    assert build.select_image({"assets": [trunk, conf]}, "cubie-a7z") == conf


@pytest.mark.parametrize(
    "change",
    [
        {"variant": "gnome_desktop"},
        {"distro": "noble"},
        {"distro": "forky"},
        {"board_support": "csc"},
        {"file_application": "omv"},
        {"file_companion": "ufs"},
        {"file_extension": "img.zst"},
        {"armbian_version": "26.11.0-trunk.121"},
        {"download_repository": "ci"},
    ],
)
def test_unavailable_requested_image_fails_without_fallback(change):
    with pytest.raises(
        build.BuildError, match="no supported stable Debian CLI minimal"
    ):
        build.select_image({"assets": [asset(**change)]}, "rpi4b")


@pytest.mark.parametrize("suffix", ["-ufs", "-omv", "-ha"])
def test_nonstandard_storage_or_app_bundle_is_not_plain_minimal(suffix):
    candidate = asset("radxa-dragon-q6a")
    candidate["file_url"] = candidate["file_url"].replace(
        "6.18.42_", f"6.18.42{suffix}_"
    )
    with pytest.raises(build.BuildError):
        build.select_image({"assets": [candidate]}, "radxa-dragon-q6a")


@pytest.mark.parametrize(
    "url",
    [
        "http://dl.armbian.com/image.img.xz",
        "https://example.org/image.img.xz",
        "https://github.com/stranger/armbian/releases/image.img.xz",
        "https://github.com.evil.example/armbian/os/image.img.xz",
        "https://user:password@dl.armbian.com/image.img.xz",
        "https://dl.armbian.com:443/image.img.xz",
    ],
)
def test_unofficial_sources_are_rejected(url):
    with pytest.raises(build.BuildError):
        build.official_url(url)


def test_external_mirror_redirect_is_rejected_before_connecting():
    handler = build.OfficialRedirects()
    with pytest.raises(build.BuildError, match="outside official"):
        handler.redirect_request(
            urllib.request.Request("https://dl.armbian.com/a"),
            None,
            302,
            "Found",
            {},
            "https://external-mirror.example/a",
        )


def test_cache_artifact_redirect_to_registered_mirror_host_is_allowed(monkeypatch):
    # cache.armbian.com redirects artifact paths to registered mirror hosts
    # under /cache/; the registry itself only lists their /dl/ bases.
    monkeypatch.setattr(build, "MIRROR_BASES", {"https://mirror.example/dl/"})
    monkeypatch.setattr(
        build, "MIRROR_CACHE_BASES", {"https://mirror.example/cache/"}
    )
    handler = build.OfficialRedirects()
    request = handler.redirect_request(
        urllib.request.Request("https://cache.armbian.com/artifacts/a/b.img.xz.sha"),
        None,
        302,
        "Found",
        {},
        "https://mirror.example/cache/artifacts/a/b.img.xz.sha",
    )
    assert request is not None
    with pytest.raises(build.BuildError, match="outside official"):
        handler.redirect_request(
            urllib.request.Request("https://cache.armbian.com/artifacts/a/b.img.xz.sha"),
            None,
            302,
            "Found",
            {},
            "https://unregistered.example/cache/artifacts/a/b.img.xz.sha",
        )


@pytest.mark.parametrize(
    "format_string",
    [
        "{digest}",
        "{digest}  {filename}",
        "{digest} *{filename}",
        "SHA256 ({filename}) = {digest}",
    ],
)
def test_supported_official_checksum_formats(format_string):
    digest = "a" * 64
    text = format_string.format(digest=digest, filename="image.img.xz")
    assert build.parse_checksum(text, "image.img.xz") == digest


def test_checksum_cannot_verify_another_filename():
    with pytest.raises(build.BuildError):
        build.parse_checksum("a" * 64 + "  different.img.xz", "image.img.xz")


@pytest.mark.parametrize("index", [{}, {"assets": None}, {"assets": [None]}])
def test_inventory_schema_changes_fail_explicitly(index):
    with pytest.raises(build.BuildError, match="inventory"):
        build.select_image(index, "rpi4b")


def test_conflicting_checksums_fail_closed():
    with pytest.raises(build.BuildError):
        build.parse_checksum("a" * 64 + "\n" + "b" * 64, "image.img.xz")


def test_corrupt_download_never_replaces_cached_file(tmp_path, monkeypatch):
    cached = tmp_path / "base.img.xz"
    cached.write_bytes(b"old")
    monkeypatch.setattr(build, "open_official", lambda url: io.BytesIO(b"corrupt"))
    with pytest.raises(build.BuildError, match="SHA256 mismatch"):
        build.download("https://dl.armbian.com/base.img.xz", cached, "a" * 64)
    assert cached.read_bytes() == b"old"
    assert not cached.with_suffix(".xz.part").exists()


def test_verified_cache_is_reused_without_network(tmp_path, monkeypatch):
    cached = tmp_path / "base.img.xz"
    cached.write_bytes(b"verified")

    def unexpected_network(url):
        raise AssertionError("Network should not be needed")

    monkeypatch.setattr(build, "open_official", unexpected_network)
    build.download("https://dl.armbian.com/base.img.xz", cached, build.sha256(cached))


def make_wheel(path, include_ui=True):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "synapsefrc-1.0.dist-info/METADATA", "Name: synapsefrc\nVersion: 1.0\n"
        )
        archive.writestr("synapse/core/synapse.py", "")
        if include_ui:
            archive.writestr("synapse_ui/dashboard.html", "dashboard")
            archive.writestr("synapse_ui/__init__.py", "")
    return path


def test_wheel_must_include_packaged_ui(tmp_path):
    wheel = make_wheel(tmp_path / "synapsefrc-1.0-py3-none-any.whl", include_ui=False)
    with pytest.raises(build.BuildError, match="missing the built web UI"):
        build.wheel_details(wheel)


def test_wheel_version_cannot_escape_output_directory(tmp_path):
    wheel = tmp_path / "synapsefrc-1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(
            "synapsefrc-1.0.dist-info/METADATA",
            "Name: synapsefrc\nVersion: ../../outside\n",
        )
    with pytest.raises(build.BuildError, match="invalid version"):
        build.wheel_details(wheel)


def test_prepare_records_actual_inputs_and_copies_verified_payload(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(build, "load_mirrors", lambda: None)
    monkeypatch.setattr(
        build, "verify_signature", lambda image, signature: ["test-signer"]
    )
    wheel = make_wheel(tmp_path / "synapsefrc-1.0-py3-none-any.whl")
    base_contents, python_contents = b"official base", b"official CPython"
    base_hash = hashlib.sha256(base_contents).hexdigest()
    source_hash = hashlib.sha256(python_contents).hexdigest()
    monkeypatch.setitem(build.SOURCES, "python_sha256", source_hash)

    def remote(url):
        if url.endswith(".sha"):
            return io.BytesIO(base_hash.encode())
        return io.BytesIO(python_contents if "cpython" in url else base_contents)

    monkeypatch.setattr(build, "open_official", remote)
    work = tmp_path / "work"
    work.mkdir()
    base, metadata = build.prepare(
        "raspberrypi5", {"assets": [asset("raspberrypi5")]}, wheel, work
    )
    assert build.sha256(base) == base_hash
    assert metadata["board"] == "raspberrypi5"
    assert metadata["armbian_board"] == "rpi4b"
    assert metadata["wheel"]["sha256"] == build.sha256(wheel)
    assert (work / "payload" / wheel.name).read_bytes() == wheel.read_bytes()
    stored = json.loads((work / "payload/image-metadata.json").read_text())
    assert stored == metadata
    with pytest.raises(build.BuildError, match="Payload already exists"):
        build.prepare("raspberrypi5", {"assets": [asset("raspberrypi5")]}, wheel, work)


def test_prepare_hashes_prebuilt_python_and_every_wheelhouse_wheel(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(build, "load_mirrors", lambda: None)
    monkeypatch.setattr(build, "verify_signature", lambda image, signature: ["s"])
    wheel = make_wheel(tmp_path / "synapsefrc-1.0-py3-none-any.whl")
    base_contents = b"official base"
    base_hash = hashlib.sha256(base_contents).hexdigest()

    def remote(url):
        if "cpython" in url:
            raise AssertionError("CPython source must not be fetched when prebuilt")
        if url.endswith(".sha"):
            return io.BytesIO(base_hash.encode())
        return io.BytesIO(base_contents)

    monkeypatch.setattr(build, "open_official", remote)
    tarball = tmp_path / "synapse-python.tar.xz"
    tarball.write_bytes(b"prebuilt interpreter")
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    (wheelhouse / "numpy-1.26.4-cp312-cp312-manylinux_2_17_aarch64.whl").write_bytes(b"n")
    (wheelhouse / "napa_vision-0.5.1-py3-none-any.whl").write_bytes(b"a")
    work = tmp_path / "work"
    work.mkdir()
    _, metadata = build.prepare(
        "rpi4b", {"assets": [asset()]}, wheel, work, tarball, wheelhouse
    )
    payload = work / "payload"
    assert not (payload / "cpython.tar.gz").exists()
    assert metadata["prebuilt_python"]["sha256"] == build.sha256(tarball)
    assert [w["filename"] for w in metadata["wheelhouse"]] == sorted(
        p.name for p in wheelhouse.iterdir()
    )
    sums = (payload / "SHA256SUMS").read_text().splitlines()
    assert f"{build.sha256(tarball)}  synapse-python.tar.xz" in sums
    for entry in metadata["wheelhouse"]:
        assert f"{entry['sha256']}  wheelhouse/{entry['filename']}" in sums


def test_prepare_rejects_non_wheel_wheelhouse_entries(tmp_path, monkeypatch):
    monkeypatch.setattr(build, "load_mirrors", lambda: None)
    monkeypatch.setattr(build, "verify_signature", lambda image, signature: ["s"])
    base_hash = hashlib.sha256(b"base").hexdigest()
    monkeypatch.setattr(
        build,
        "open_official",
        lambda url: io.BytesIO(base_hash.encode() if url.endswith(".sha") else b"base"),
    )
    wheel = make_wheel(tmp_path / "synapsefrc-1.0-py3-none-any.whl")
    tarball = tmp_path / "synapse-python.tar.xz"
    tarball.write_bytes(b"prebuilt")
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    (wheelhouse / "install.sh").write_text("echo not a wheel\n")
    work = tmp_path / "work"
    work.mkdir()
    with pytest.raises(build.BuildError, match="only wheel files"):
        build.prepare("rpi4b", {"assets": [asset()]}, wheel, work, tarball, wheelhouse)


def test_checksum_url_must_match_image(tmp_path):
    wheel = make_wheel(tmp_path / "synapsefrc-1.0-py3-none-any.whl")
    candidate = asset(file_url_sha="https://dl.armbian.com/different.sha")
    with pytest.raises(build.BuildError, match="checksum URL"):
        build.prepare("rpi4b", {"assets": [candidate]}, wheel, tmp_path)


def test_shell_scripts_parse():
    scripts = list(build.HERE.glob("*.sh")) + list(
        (build.HERE / "payload").glob("*.sh")
    )
    scripts.append(build.HERE / "payload/synapse-firstboot")
    for script in scripts:
        subprocess.run(["bash", "-n", str(script)], check=True)
