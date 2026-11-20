# eMudhra trust-store response

Reviewed 2026-11-04. This response follows official Certifi, Bun, Mozilla-derived
Temurin and Google sources. It addresses certificate trust in the image builder,
generated Linux images and repository CI. It is not a general dependency audit.

## Policy and compatible official updates

| Component | Selected update or check |
| --- | --- |
| Bun | Official 1.4.4, with its actual embedded roots audited before dependency installation |
| Python certificates | Certifi 2026.10.23, newer than the fixed 2026.10.18 release; runtime dependency minimum enforced |
| pip | Official 26.3, including an audit of its separate vendored Certifi bundle |
| Java CI | Latest available Temurin 17 patch via `check-latest`; official 17.0.21+9 public trust store independently checked |
| Browser CI | Google's current stable Chrome for Testing, resolved from its official manifest on each run |
| Image system TLS | Upgrade installed CA, OpenSSL, GnuTLS, NSS, curl, Git and Chromium packages from the base image's signed distro repositories; audit the resulting CA bundle |
| GitHub JavaScript actions | Existing Node 24 action releases; after checkout, use the filtered OpenSSL CA bundle through per-action `NODE_OPTIONS` |
| Rust manager | Locked reqwest has TLS disabled and no bundled webpki/native-tls trust dependency; Cargo receives the filtered CA bundle |

Latest means the latest compatible official release checked on the review date,
or the current patch/stable version explicitly resolved during CI. Debian
security backports are selected through signed apt repositories, rather than
replacing distro libraries with incompatible upstream builds. CPython remains
the previously verified latest compatible 3.12.16 source; NumPy currently
requires this Python family. Bun runs project JavaScript. GitHub supplies the
JavaScript runtime inside its own actions.

`payload/tls-policy.json` records the exact SHA256 fingerprints of the two affected
trust paths identified by Temurin's Mozilla/NSS guidance:

- `emSign ECC Root CA - G3`: `86a1ecba089c4a8d3bbe2734c612ba341d813e043cf9e8a862cd5c57a36bbe6b`
- `AAA Certificate Services`: `d7a7a0fb5d7e2731d771e9484ebcdef71d5f0c3e0a2948782bc83ee0ea699ef4`

Unrelated emSign roots are left to vendor policy. In particular, Temurin's
advisory states that emSign G1 is unrelated to the compromised intermediates
and remains trusted by Mozilla. A name containing `emSign` alone is not a
sufficient reason to delete a root.

## Enforcement

The builder filters these exact roots before downloading inputs. TLS certificate
and hostname verification remain enabled. Proxy and unrelated custom trust
continue to work. Older pip is bootstrapped with an explicitly filtered PEM
bundle using its documented `legacy-certs` option so its old bundled CA store
cannot silently add a removed root back. Updated pip, Certifi and Python default
trust are audited separately. Git and Cargo receive explicit CA paths, and
Git/OpenSSL fallback CA directories are isolated for CI.

Inside an image, the builder disables affected Debian CA entries and quarantines
matching custom-root files before rebuilding the system bundle. Other custom
roots are preserved. A temporary read-only build-host CA mount supports build
network access; it is removed before compression and is not installed as image
trust. The final image contains a TLS audit, package inventories and policy hash
alongside its existing Armbian provenance. Armbian SHA256 and pinned-key OpenPGP
verification remain mandatory.

Base images are accepted only from Armbian's official endpoints: `dl.armbian.com`,
`github.armbian.com`, `raw.githubusercontent.com/armbian/`, `codeload.github.com/python/cpython/`,
GitHub release assets under `github.com/armbian/`, Armbian's own `cache.armbian.com`
artifact cache, and the pinned mirror registry. Boards with no recommended image may
opt into a community (`csc`) build via `allow_community` in `boards.json`; such builds
come from `github.com/armbian/community` releases with their checksum/signature sidecars
on `cache.armbian.com`, are still SHA256- and pinned-key GPG-verified, and `ci`/`nightly`
repositories remain rejected.

The managed development runtime has an explicit OpenSSL negative trust entry.
OpenSSL can include that entry in CA enumeration even though it rejects the
certificate. The audit recognizes it only after checking its exact fingerprint,
that it is preloaded into the runtime, and OpenSSL's actual certificate-rejected
result. The platform's rejection is never removed or bypassed.

Google browser downloads must match the exact official stable-manifest URL,
pass ZIP CRC/path checks and report the expected version. The calculated archive
hash records provenance; Google does not supply an independently signed digest
through this manifest. It is not equivalent to Armbian's detached-signature check.

## Verification and remaining boundaries

Regression tests exercise known affected public root fixtures, preservation of
unrelated roots, required hostname/certificate verification, pip bootstrap,
browser URL restrictions and archive traversal rejection. Actual Bun 1.4.4,
Certifi 2026.10.23 and pip 26.3 bundles passed inspection. The checksum-verified
official Temurin 17.0.21+9 archive's public `cacerts` passed inspection. A root
container integration check confirmed that a seeded affected custom root is
quarantined and an unrelated custom root is retained.

The advisory site and `storage.googleapis.com` are blocked by this environment's
network policy. Therefore its current complete patched-tools table and live
compromised-chain probes could not be checked here, and the current Chrome
download could not be exercised locally. Store inspection is not a substitute
for those live-chain probes or Java signed-code validation. CI must complete
before its new browser and ARM64 image outputs can be considered validated.
Physical board boot/camera testing remains required.

The first checkout action runs before repository code can configure CA trust;
its embedded Node/runtime trust is controlled by GitHub. The environment's
Node 24.22.1 embedded bundle was inspected and lacked the two affected roots,
but that does not establish every future runner's state. Windows/macOS native
stores, users' browsers, installed desktop webviews, previously built images,
external services and firmware are outside this repository's patch boundary.
Rebuild and reflash old images; source changes do not update deployed devices.

## Official references

- [Certifi security advisory, fixed in 2026.10.18](https://github.com/certifi/python-certifi/security/advisories/GHSA-6q48-6c7w-9gw2)
- [Certifi official package 2026.10.23](https://pypi.org/project/certifi/2026.10.23/)
- [Certifi maintainer follow-up](https://github.com/certifi/python-certifi/issues/439)
- [Bun affected-root issue](https://github.com/oven-sh/bun/issues/46526)
- [Bun 1.4.4 release](https://github.com/oven-sh/bun/releases/tag/bun-v1.4.4)
- [Temurin response and exact root scope](https://adoptium.net/news/2026/10/temurin-october-2026-emudhra)
- [Temurin 17.0.21+9 binaries and checksums](https://github.com/adoptium/temurin17-binaries/releases/tag/jdk-17.0.21%2B9)
- [pip HTTPS certificate documentation](https://pip.pypa.io/en/stable/topics/https-certificates/)
- [Debian update-ca-certificates](https://manpages.debian.org/trixie/ca-certificates/update-ca-certificates.8.en.html)
- [Google Chrome for Testing](https://github.com/GoogleChromeLabs/chrome-for-testing)
- [Official curl CA refresh discussion](https://github.com/curl/curl/issues/23515)
