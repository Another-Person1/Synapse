import zipfile

import pytest

from image_builder.setup_browser import extract_browser, official_download


def test_official_browser_metadata_url():
    version = "157.0.8103.38"
    url = f"https://storage.googleapis.com/chrome-for-testing-public/{version}/linux64/chrome-linux64.zip"
    assert official_download(
        {
            "version": version,
            "downloads": {"chrome": [{"platform": "linux64", "url": url}]},
        }
    ) == (version, url)


def test_external_browser_rehost_is_rejected():
    with pytest.raises(ValueError, match="official Google"):
        official_download(
            {
                "version": "157.0.8103.38",
                "downloads": {
                    "chrome": [
                        {"platform": "linux64", "url": "https://example.org/chrome.zip"}
                    ]
                },
            }
        )


def test_browser_archive_cannot_escape_destination(tmp_path):
    archive = tmp_path / "browser.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("../outside", "bad")
    with pytest.raises(ValueError, match="Unsafe"):
        extract_browser(archive, tmp_path / "browser")
    assert not (tmp_path / "outside").exists()
