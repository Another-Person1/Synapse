# Armbian image trust anchors

The bundled public keys are used only in an isolated temporary GnuPG home.
The verifier accepts signatures only from these full primary fingerprints:

- `DF00FAF1C577104B50BF1D0093D6889F9F0E78D5`: retrieved from
  https://raw.githubusercontent.com/armbian/build/v25.5.1/config/armbian.key
- `8CFA83D13EB2181EEF5843E41EB30FAF236099FE`: retrieved from
  https://raw.githubusercontent.com/armbian/build/main/config/armbian.key

Both fingerprints are published in Armbian's official authentication instructions:
https://github.com/armbian/documentation/blob/main/docs/getting-started/choosing-an-image.md

Key updates require deliberate review of the full fingerprint against Armbian's
official documentation. A key downloaded from an image mirror is never trusted
automatically. Missing, expired, revoked and invalid signatures fail the build.
SHA256 detects changed bytes; the detached signature authenticates those bytes
against this pinned trust anchor even if a mirror replaces both image and hash.
This does not prove the upstream OS has no vulnerabilities or rule out compromise
of Armbian's signing infrastructure. Rebuilding the filesystem invalidates the
upstream signature; it authenticates the base image, not the final Synapse image.
