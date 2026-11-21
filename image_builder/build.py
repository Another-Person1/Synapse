#!/usr/bin/env python3
"""Resolve, verify and customize official Armbian Debian minimal images."""

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from image_builder.payload.tls_policy import hardened_context

HERE = Path(__file__).resolve().parent
BOARDS = json.loads((HERE / "boards.json").read_text())
SOURCES = json.loads((HERE / "sources.json").read_text())
STABLE_VERSION = re.compile(r"\d+\.\d+(?:\.\d+)*")
# Community (CSC) builds carry a moving trunk tag, e.g. 26.11.0-trunk.130.
TRUNK_VERSION = re.compile(r"\d+\.\d+\.\d+-trunk\.\d+")
# Deliberately explicit: a new Debian stable must be qualified before enabling it.
DEBIAN_RELEASES = {"trixie": 13, "bookworm": 12}
MIRROR_REGISTRY = "https://dl.armbian.com/mirrors"
# Registered mirror hosts are also trusted for Armbian's /cache/ artifact
# namespace, which is where cache.armbian.com redirects community sidecars.
MIRROR_CACHE_BASES = set()
MIRROR_BASES = set()
SIGNING_FINGERPRINTS = {
    "DF00FAF1C577104B50BF1D0093D6889F9F0E78D5",
    "8CFA83D13EB2181EEF5843E41EB30FAF236099FE",
}


class BuildError(Exception):
    """An input, upstream availability or build requirement could not be met."""


def image_targets(selected="all"):
    """Public artifact names, independent of Armbian's historical board IDs."""
    boards = BOARDS if selected == "all" else [selected]
    return list(dict.fromkeys(BOARDS[b].get("image_target", b) for b in boards))


def official_url(url: str) -> str:
    """Accept official endpoints and exact paths listed by Armbian's registry."""
    parsed = urllib.parse.urlsplit(url)
    decoded_path = urllib.parse.unquote(parsed.path)
    if (
        any(part in {".", ".."} for part in decoded_path.split("/"))
        or "\\" in decoded_path
    ):
        raise BuildError("Refusing a URL with ambiguous path traversal")
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port:
        raise BuildError(f"Expected an official HTTPS source: {url}")
    host = parsed.hostname
    allowed = (
        # cache.armbian.com is Armbian's own artifact cache; the official
        # inventory publishes checksum/signature URLs there for community builds.
        host in {"dl.armbian.com", "github.armbian.com", "cache.armbian.com"}
        or (host == "raw.githubusercontent.com" and parsed.path.startswith("/armbian/"))
        or (
            host == "codeload.github.com" and parsed.path.startswith("/python/cpython/")
        )
        or (host == "github.com" and parsed.path.startswith("/armbian/"))
        # GitHub's release-asset CDN, reached only through the official URLs above.
        or host == "release-assets.githubusercontent.com"
        or any(url.startswith(base) for base in MIRROR_BASES | MIRROR_CACHE_BASES)
    )
    if not allowed:
        raise BuildError(
            f"Refusing a source outside official project infrastructure: {url}"
        )
    return url


class OfficialRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        official_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_official(url: str):
    request = urllib.request.Request(
        official_url(url), headers={"User-Agent": "Synapse-image-builder/1"}
    )
    # Keep the platform proxy, normal CA trust and TLS verification enabled.
    return urllib.request.build_opener(
        OfficialRedirects(), urllib.request.HTTPSHandler(context=hardened_context())
    ).open(request, timeout=60)


def load_mirrors():
    with open_official(MIRROR_REGISTRY) as response:
        registry = json.load(response)
    if not isinstance(registry, dict):
        raise BuildError("Unexpected Armbian mirror registry format")
    bases = set()
    cache_bases = set()
    for region in registry.values():
        if not isinstance(region, list):
            raise BuildError("Unexpected Armbian mirror registry entries")
        for url in region:
            parsed = urllib.parse.urlsplit(url)
            if (
                parsed.scheme == "https"
                and parsed.hostname
                and not parsed.username
                and not parsed.password
                and not parsed.port
                and not parsed.query
                and not parsed.fragment
                and not any(x in parsed.path.split("/") for x in (".", ".."))
            ):
                bases.add(url.rstrip("/") + "/")
                # cache.armbian.com redirects artifact paths to the same
                # mirror hosts under /cache/ instead of the /dl/ path above.
                cache_bases.add(f"https://{parsed.hostname}/cache/")
    if not bases:
        raise BuildError("Armbian did not publish any usable HTTPS mirrors")
    MIRROR_BASES.clear()
    MIRROR_BASES.update(bases)
    MIRROR_CACHE_BASES.clear()
    MIRROR_CACHE_BASES.update(cache_bases)


def open_armbian(url):
    """Try the redirector, then registered mirrors if a server is unreachable."""
    try:
        return open_official(url)
    except urllib.error.URLError as original:
        if not url.startswith("https://dl.armbian.com/"):
            raise
        relative = url.removeprefix("https://dl.armbian.com/")
        bases = sorted(
            MIRROR_BASES,
            key=lambda base: (
                not urllib.parse.urlsplit(base).hostname.endswith(".armbian.com"),
                base,
            ),
        )
        for base in bases:
            try:
                return open_official(base + relative)
            except urllib.error.URLError:
                continue
        raise BuildError(
            f"No registered Armbian mirror could serve {url}: {original}"
        ) from original


def verify_signature(image, signature):
    if not shutil.which("gpg"):
        raise BuildError("Install gnupg to verify Armbian image authenticity")
    with tempfile.TemporaryDirectory(prefix="synapse-gpg-") as home:
        command = [
            "gpg",
            "--batch",
            "--no-options",
            "--homedir",
            home,
            "--no-auto-key-retrieve",
        ]
        for key in sorted((HERE / "keys").glob("*.asc")):
            subprocess.run(
                command + ["--import", str(key)], check=True, capture_output=True
            )
        result = subprocess.run(
            command + ["--status-fd", "1", "--verify", str(signature), str(image)],
            capture_output=True,
            text=True,
        )
        signers = []
        for line in result.stdout.splitlines():
            fields = line.split()
            if fields[:2] == ["[GNUPG:]", "VALIDSIG"]:
                primary = (
                    fields[-1] if fields[-1] in SIGNING_FINGERPRINTS else fields[2]
                )
                if primary in SIGNING_FINGERPRINTS:
                    signers.append(primary)
        if result.returncode or not signers:
            raise BuildError(
                "Armbian image signature is invalid, expired, revoked or signed by an unapproved key: "
                + result.stderr
            )
        return sorted(set(signers))


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def select_image(index: dict, board: str) -> dict:
    """Choose the newest stable plain Debian minimal image, then recommendation."""
    source_board = BOARDS[board]["armbian_board"]
    # Boards with no recommended (conf) image may opt into the community (csc)
    # build explicitly; it is still checksum- and signature-verified.
    allow_community = bool(BOARDS[board].get("allow_community"))
    if not isinstance(index, dict) or not isinstance(index.get("assets"), list):
        raise BuildError("Armbian inventory does not contain the expected assets list")
    candidates = []
    for asset in index.get("assets", []):
        if not isinstance(asset, dict):
            raise BuildError("Armbian inventory contains an invalid image record")
        community = asset.get("download_repository") == "community"
        version = asset.get("armbian_version", "")
        stable = bool(STABLE_VERSION.fullmatch(version)) or (
            community and bool(TRUNK_VERSION.fullmatch(version))
        )
        if (
            asset.get("board_slug") != source_board
            or asset.get("board_support") != ("csc" if community else "conf")
            or (community and not allow_community)
            or asset.get("variant") != "minimal"
            or asset.get("distro") not in DEBIAN_RELEASES
            or asset.get("file_application")
            or asset.get("file_companion")
            or asset.get("file_extension") != "img.xz"
            or not stable
            or asset.get("download_repository") in {"ci", "nightly"}
        ):
            continue
        url = official_url(asset["file_url"])
        # Reject alternate UFS sector layouts and app bundles (OMV, HA, etc.).
        name = Path(urllib.parse.urlsplit(url).path).name
        if community:
            expected = re.fullmatch(
                r"Armbian_community_"
                + re.escape(version)
                + r"_[A-Za-z0-9-]+_"
                + re.escape(asset["distro"])
                + r"_(current|vendor|legacy|edge)_[0-9.]+_minimal\.img\.xz",
                name,
            )
        else:
            expected = re.fullmatch(
                r"Armbian_[0-9.]+_[A-Za-z0-9-]+_"
                + re.escape(asset["distro"])
                + r"_(current|vendor|legacy|edge)_[0-9.]+_minimal\.img\.xz",
                name,
            )
        if not expected or expected[1] != asset.get("branch"):
            continue
        candidates.append(asset)
    if not candidates:
        raise BuildError(
            f"{board}: no supported stable Debian CLI minimal image is available in "
            "Armbian's official inventory. No desktop, Ubuntu, nightly or older-image fallback was used."
        )

    def rank(asset):
        # Handles both stable "26.11.2" -> (26, 11, 2) and community trunk
        # versions "26.11.0-trunk.130" -> (26, 11, 0, 130).
        version = tuple(int(x) for x in re.findall(r"\d+", asset["armbian_version"]))
        promoted = str(asset.get("promoted", "false")).lower() == "true"
        return (
            version,
            DEBIAN_RELEASES[asset["distro"]],
            promoted,
            {"current": 3, "vendor": 2, "legacy": 1, "edge": 0}[asset["branch"]],
            asset.get("file_date", ""),
        )

    return max(candidates, key=rank)


def parse_checksum(text: str, filename: str) -> str:
    """Accept a bare SHA256 or a matching GNU/BSD SHA256 entry, never another file."""
    matches = []
    for line in text.splitlines():
        line = line.strip()
        if re.fullmatch(r"[a-fA-F0-9]{64}", line):
            matches.append(line.lower())
        elif match := re.fullmatch(r"([a-fA-F0-9]{64})\s+\*?(.+)", line):
            if match[2].removeprefix("./") == filename:
                matches.append(match[1].lower())
        elif match := re.fullmatch(r"SHA256 \((.+)\) = ([a-fA-F0-9]{64})", line):
            if match[1] == filename:
                matches.append(match[2].lower())
    if len(set(matches)) != 1:
        raise BuildError(
            f"Missing or ambiguous official SHA256 checksum for {filename}"
        )
    return matches[0]


def download(url: str, destination: Path, expected: str) -> None:
    if destination.exists() and sha256(destination) == expected:
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    try:
        opener = (
            open_armbian if url.startswith("https://dl.armbian.com/") else open_official
        )
        with opener(url) as response, temporary.open("wb") as stream:
            shutil.copyfileobj(response, stream)
        if sha256(temporary) != expected:
            raise BuildError(f"SHA256 mismatch for {url}; refusing to use the download")
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def wheel_details(wheel: Path) -> dict:
    if not wheel.is_file() or not wheel.name.endswith("-py3-none-any.whl"):
        raise BuildError(
            "Provide the platform-independent Synapse wheel built by build-wheel.sh"
        )
    with zipfile.ZipFile(wheel) as archive:
        metadata_names = [
            n for n in archive.namelist() if n.endswith(".dist-info/METADATA")
        ]
        if len(metadata_names) != 1:
            raise BuildError("Wheel must contain exactly one package metadata record")
        from email.parser import Parser

        metadata = Parser().parsestr(archive.read(metadata_names[0]).decode())
        if metadata["Name"] != "synapsefrc":
            raise BuildError("Expected a synapsefrc wheel")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.!+_-]*", metadata["Version"] or ""):
            raise BuildError(
                "Wheel has an invalid version for an image artifact filename"
            )
        required = {
            "synapse_ui/dashboard.html",
            "synapse_ui/__init__.py",
            "synapse/core/synapse.py",
        }
        if not required.issubset(archive.namelist()):
            raise BuildError("Wheel is missing the built web UI or Synapse runtime")
    return {
        "filename": wheel.name,
        "version": metadata["Version"],
        "sha256": sha256(wheel),
    }


def prepare(
    board: str,
    index: dict,
    wheel: Path,
    work: Path,
    python_tarball: Path | None = None,
    wheelhouse: Path | None = None,
) -> tuple[Path, dict]:
    asset = select_image(index, board)
    wheel_info = wheel_details(wheel)
    url = asset["file_url"]
    filename = Path(urllib.parse.urlsplit(url).path).name

    def sidecar_url(field: str, suffix: str) -> str:
        """Accept the sidecar next to the image or Armbian's artifact-cache copy.

        Community (csc) builds publish checksums/signatures on cache.armbian.com
        instead of next to the GitHub release asset; both are official Armbian
        endpoints and must name exactly this file.
        """
        sidecar = asset.get(field, url + suffix)
        if sidecar == url + suffix:
            return sidecar
        parts = urllib.parse.urlsplit(sidecar)
        if (
            asset.get("download_repository") == "community"
            and BOARDS[board].get("allow_community")
            and parts.scheme == "https"
            and parts.hostname == "cache.armbian.com"
            and parts.path.endswith("/" + filename + suffix)
            and not parts.query
            and not parts.fragment
        ):
            return sidecar
        label = "checksum" if suffix == ".sha" else "signature"
        raise BuildError(f"Inventory {label} URL does not match the selected image")

    checksum_url = sidecar_url("file_url_sha", ".sha")
    load_mirrors()
    opener = open_armbian if checksum_url.startswith("https://dl.armbian.com/") else open_official
    with opener(checksum_url) as response:
        checksum = parse_checksum(response.read(1024 * 1024).decode(), filename)
    cache = work / "downloads"
    base = cache / filename
    download(url, base, checksum)
    signature_url = sidecar_url("file_url_asc", ".asc")
    signature = cache / (filename + ".asc")
    opener = open_armbian if signature_url.startswith("https://dl.armbian.com/") else open_official
    with opener(signature_url) as response:
        signature.write_bytes(response.read(1024 * 1024))
    signers = verify_signature(base, signature)
    payload = work / "payload"
    if payload.exists():
        raise BuildError(
            f"Payload already exists: {payload}. Use a fresh work directory."
        )
    shutil.copytree(
        HERE / "payload", payload, ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
    )
    shutil.copy2(wheel, payload / wheel.name)
    prebuilt_python = None
    if python_tarball is not None:
        if not python_tarball.is_file():
            raise BuildError(f"Prebuilt Python tarball not found: {python_tarball}")
        shutil.copy2(python_tarball, payload / "synapse-python.tar.xz")
        prebuilt_python = {
            "filename": "synapse-python.tar.xz",
            "sha256": sha256(python_tarball),
            "version": SOURCES["python_version"],
            "commit": SOURCES["python_commit"],
        }
    else:
        python_source = cache / f"cpython-{SOURCES['python_version']}.tar.gz"
        download(SOURCES["python_url"], python_source, SOURCES["python_sha256"])
        shutil.copy2(python_source, payload / "cpython.tar.gz")
    wheelhouse_files = None
    if wheelhouse is not None:
        if not wheelhouse.is_dir():
            raise BuildError(f"Wheelhouse directory not found: {wheelhouse}")
        entries = sorted(wheelhouse.iterdir())
        if not entries:
            raise BuildError(f"Wheelhouse is empty: {wheelhouse}")
        if any(not p.is_file() or p.suffix != ".whl" for p in entries):
            raise BuildError("Wheelhouse must contain only wheel files")
        destination = payload / "wheelhouse"
        destination.mkdir()
        wheelhouse_files = []
        for entry in entries:
            shutil.copy2(entry, destination / entry.name)
            wheelhouse_files.append(
                {"filename": entry.name, "sha256": sha256(destination / entry.name)}
            )
    metadata = {
        "schema_version": 1,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "board": board,
        "image_target": image_targets(board)[0],
        "armbian_board": BOARDS[board]["armbian_board"],
        "base": {
            key: asset[key]
            for key in (
                "armbian_version",
                "board_support",
                "download_repository",
                "distro",
                "branch",
                "kernel_version",
                "file_url",
                "file_url_sha",
            )
        },
        "base_sha256": checksum,
        "base_signers": signers,
        "base_signature_url": signature_url,
        "base_signature_sha256": sha256(signature),
        "hardware_profile_sha256": sha256(HERE / "payload/hardware.py"),
        "hardware_requirements_sha256": sha256(HERE / "payload/hardware-requirements.txt"),
        "tls_policy_sha256": sha256(HERE / "payload/tls-policy.json"),
        "mirror_registry": MIRROR_REGISTRY,
        "wheel": wheel_info,
        "python": {
            key: SOURCES[key]
            for key in (
                "python_version",
                "python_commit",
                "python_url",
                "python_sha256",
            )
        },
        "prebuilt_python": prebuilt_python,
        "wheelhouse": wheelhouse_files,
        "builder_commit": subprocess.check_output(
            ["git", "-C", str(HERE), "rev-parse", "HEAD"], text=True
        ).strip(),
        "builder_dirty": bool(
            subprocess.check_output(
                ["git", "-C", str(HERE), "status", "--porcelain"], text=True
            ).strip()
        ),
    }
    (payload / "image-metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    sums = [f"{wheel_info['sha256']}  {wheel.name}\n"]
    if prebuilt_python is not None:
        sums.append(f"{prebuilt_python['sha256']}  synapse-python.tar.xz\n")
    else:
        sums.append(f"{SOURCES['python_sha256']}  cpython.tar.gz\n")
    # The chroot re-checks every wheel before pip installs from the wheelhouse.
    for entry in wheelhouse_files or []:
        sums.append(f"{entry['sha256']}  wheelhouse/{entry['filename']}\n")
    (payload / "SHA256SUMS").write_text("".join(sums))
    return base, metadata


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["resolve", "build"])
    parser.add_argument("--board", required=True, choices=BOARDS)
    parser.add_argument(
        "--index",
        type=Path,
        help="Previously captured official inventory (offline inspection)",
    )
    parser.add_argument("--wheel", type=Path)
    parser.add_argument(
        "--python-tarball",
        type=Path,
        help="Prebuilt CPython aarch64 tarball from build-python.sh; skips the in-image compile",
    )
    parser.add_argument(
        "--wheelhouse",
        type=Path,
        help="Directory of pre-downloaded wheels for an offline chroot pip install",
    )
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("dist/images"))
    args = parser.parse_args(argv)
    try:
        if args.index:
            index = json.loads(args.index.read_text())
        else:
            with open_official(SOURCES["armbian_index"]) as response:
                index = json.load(response)
        selected = select_image(index, args.board)
        if args.command == "resolve":
            print(json.dumps(selected, indent=2))
            return 0
        if not args.wheel or not args.work_dir:
            raise BuildError("build requires --wheel and --work-dir")
        if sys.platform != "linux":
            raise BuildError(
                "Image builds require Linux with root, loop devices and mount capabilities"
            )
        if platform.machine() != "aarch64":
            raise BuildError(
                "Use a native ARM64 Linux host or the ARM64 GitHub Actions image job"
            )
        work = args.work_dir.resolve()
        work.mkdir(parents=True, exist_ok=True)
        # Both the shell modifier and the Python preparation require exclusive access.
        import fcntl

        with (work / ".build.lock").open("w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise BuildError(f"Another builder is using {work}") from None
            base, metadata = prepare(
                args.board,
                index,
                args.wheel.resolve(),
                work,
                python_tarball=args.python_tarball.resolve()
                if args.python_tarball
                else None,
                wheelhouse=args.wheelhouse.resolve() if args.wheelhouse else None,
            )
            output = args.output_dir.resolve()
            output.mkdir(parents=True, exist_ok=True)
            filename = f"synapse-{metadata['wheel']['version']}-{metadata['image_target']}-armbian-{metadata['base']['armbian_version']}.img.xz"
            destination = output / filename
            if destination.exists():
                raise BuildError(f"Refusing to overwrite {destination}")
            command = [
                "bash",
                str(HERE / "modify-image.sh"),
                str(base),
                str(work / "payload"),
                str(destination),
            ]
            if os.geteuid() != 0:
                command.insert(0, "sudo")
            subprocess.run(command, check=True)
            metadata["output"] = {"filename": filename, "sha256": sha256(destination)}
            (output / f"{filename}.sha256").write_text(
                f"{metadata['output']['sha256']}  {filename}\n"
            )
            (output / f"{filename}.json").write_text(
                json.dumps(metadata, indent=2) + "\n"
            )
            print(destination)
        return 0
    except (
        BuildError,
        OSError,
        ValueError,
        urllib.error.URLError,
        subprocess.CalledProcessError,
        zipfile.BadZipFile,
    ) as exc:
        print(f"Image build failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
