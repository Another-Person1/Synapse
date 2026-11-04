"""Install current stable Chrome for Testing using Google's official metadata."""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from image_builder.payload.tls_policy import hardened_context

MANIFEST = "https://raw.githubusercontent.com/GoogleChromeLabs/chrome-for-testing/main/data/last-known-good-versions-with-downloads.json"


def official_download(stable):
    version = stable["version"]
    if not re.fullmatch(r"\d+\.\d+\.\d+\.\d+", version):
        raise ValueError("Unexpected Chrome stable version")
    url = next(
        item["url"]
        for item in stable["downloads"]["chrome"]
        if item["platform"] == "linux64"
    )
    expected = f"https://storage.googleapis.com/chrome-for-testing-public/{version}/linux64/chrome-linux64.zip"
    if url != expected:
        raise ValueError("Browser URL is not the expected official Google download")
    return version, url


class GoogleRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if newurl != req.full_url:
            raise ValueError("Unexpected redirect from Google's browser download")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def extract_browser(archive, destination):
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            if (
                not (destination / member.filename)
                .resolve()
                .is_relative_to(destination.resolve())
            ):
                raise ValueError("Unsafe browser archive path")
        if bundle.testzip() is not None:
            raise ValueError("Browser archive failed its ZIP CRC check")
        bundle.extractall(destination)
        for member in bundle.infolist():
            path = destination / member.filename
            if path.is_file():
                path.chmod((member.external_attr >> 16) & 0o777 or 0o644)
    browser = destination / "chrome-linux64/chrome"
    if not browser.is_file():
        raise ValueError("Official browser entry point is missing")
    browser.chmod(0o755)
    return browser


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--github-env", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise RuntimeError(f"Refusing to overwrite browser installation: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    opener = urllib.request.build_opener(
        GoogleRedirects(), urllib.request.HTTPSHandler(context=hardened_context())
    )
    with opener.open(MANIFEST, timeout=60) as response:
        version, url = official_download(json.load(response)["channels"]["Stable"])
    with tempfile.TemporaryDirectory(
        prefix="synapse-chrome-", dir=output.parent
    ) as directory:
        root = Path(directory)
        archive = root / "chrome.zip"
        with opener.open(url, timeout=60) as response, archive.open("wb") as stream:
            shutil.copyfileobj(response, stream)
        with archive.open("rb") as stream:
            archive_sha256 = hashlib.file_digest(stream, "sha256").hexdigest()
        extracted = root / "browser"
        browser = extract_browser(archive, extracted)
        reported = subprocess.check_output(
            [str(browser), "--version"], text=True
        ).strip()
        if not re.search(rf"\b{re.escape(version)}\b", reported):
            raise RuntimeError(
                "Downloaded browser version does not match official metadata"
            )
        extracted.rename(output)
    browser = output / "chrome-linux64/chrome"
    print(
        json.dumps(
            {
                "version": version,
                "url": url,
                "sha256": archive_sha256,
                "reported": reported,
            }
        )
    )
    if args.github_env:
        with open(os.environ["GITHUB_ENV"], "a") as stream:
            stream.write(f"SYNAPSE_CHROME={browser.as_posix()}\n")


if __name__ == "__main__":
    main()
