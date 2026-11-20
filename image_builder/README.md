# Synapse Linux image builder

Build a flashable ARM64 Synapse appliance from the **latest stable, supported
Armbian Debian CLI minimal image** for each board. The base image is resolved at
build time from [Armbian's published inventory](https://raw.githubusercontent.com/armbian/armbian.github.io/data/data/armbian-images.json).
Development/nightly builds, desktop images, Ubuntu, application bundles and
alternate UFS sector layouts are excluded. Missing Debian minimal images fail
explicitly; there is no automatic fallback. A newer Armbian release is preferred
before Armbian's recommendation chooses the kernel branch within that release.

| Target | Hardware | Armbian image set |
| --- | --- | --- |
| `raspberrypi` | Pi 4, Pi 5, CM4, CM5 | shared `rpi4b` base |
| `radxa-dragon-q6a` | Radxa Dragon Q6A | `radxa-dragon-q6a`, ordinary 512-byte-sector image |
| `orangepi5` | Orange Pi 5 | `orangepi5` |
| `orangepi5-plus` | Orange Pi 5 Plus | `orangepi5-plus` |

See [hardware and networking](HARDWARE.md) for `synapse.local:3000`, USB-C Ethernet,
carrier requirements and the enabled SPI, GPIO, PWM and UART pins. Header I2C is
not enabled by Synapse. Legacy Pi CLI aliases still resolve to `raspberrypi`.

The initial official inventory check found stable Debian Trixie minimal images
for all five originally requested boards: Armbian 26.8.1 for Pi 4/5, Q6A and Orange Pi 5, and 26.8.2 for
Orange Pi 5 Plus. These versions are observations, not build pins. Releases
marked `trunk` are not stable releases even when their version number is higher.

## Build using GitHub Actions

Run **Synapse Linux images** with `workflow_dispatch`, selecting one board or
`all`.
Pushes changing the builder on `main` or `codex/synapse-image-builder` also build
four artifacts covering the board families above. Pull requests run checks without privileged image builds.
The workflow builds the current checkout's Python wheel and static UI once,
then modifies each distinct board image on a native ARM64 Ubuntu runner.
Pi 4, Pi 5, CM4 and CM5 share the generically named `raspberrypi` artifact.
Armbian still identifies its shared base as `rpi4b`. The builder checks for all
four models' firmware trees; carrier-specific boot and camera validation remain necessary. It uploads
`.img.xz`, `.sha256` and `.json` artifacts. On `v*` tags every board is built
and the images are attached to the GitHub release.

## Build locally

Use a native ARM64 Linux host with root/sudo, functioning loop devices and mount
capabilities, at least 8 GB RAM, and approximately 25 GB free disk for one image.
Do not run this modifier inside an unprivileged container. Native x86 and macOS
image modification are not supported. The platform-independent wheel can be
built on x86 Linux.

Host tools: Python 3.12 with `build`, Bun 1.4.4, `git`,
`sudo`, `parted`, `gdisk`, `fdisk`, `e2fsprogs`, `dosfstools`, `util-linux`, `udev`, `xz-utils`, `gnupg`.
The host's `python3` must support `hashlib.file_digest` (Python 3.11 or later).
The modifier detects the ext4 root filesystem instead of assuming a partition
number. It refuses images whose root is not the final partition.

```sh
python3.12 -m venv .venv
.venv/bin/python image_builder/payload/tls_policy.py bootstrap
.venv/bin/python -m pip install build
SYNAPSE_BUILD_PYTHON="$PWD/.venv/bin/python" bash image_builder/build-wheel.sh

# Inspect the current upstream selection without downloading an image.
python3 -m image_builder.build resolve --board orangepi5-plus

# Supply the exact wheel filename produced above. Use a fresh work directory.
python3 -m image_builder.build build --board orangepi5-plus \
  --wheel dist/image-builder/synapsefrc-2027.0.0a1-py3-none-any.whl \
  --work-dir /var/tmp/synapse-orangepi5-plus --output-dir dist/images
```

Stop any Next.js development server before building the wheel. The UI build
uses the same `.next` directory. The builder refuses existing output files and
existing payload directories rather than mixing two runs. Each work directory
has an exclusive lock. The compressed upstream download is verified before use;
it is never modified. Scratch images and loop mounts are cleaned up on failure.
If the kernel refuses cleanup, the modifier preserves the working directory and
reports the loop device for recovery rather than deleting a mounted tree.

## Runtime and deployment

The image installs the current checkout's wheel, its static UI, native RobotPy
and OpenCV dependencies, SSH, mDNS and the `synapse-runtime.service` service.
No network downloads are required to start Synapse on first boot. Debian's
system Python is preserved. A checksum-pinned official CPython 3.12 source
release is built into `/opt/synapse-python` because the runtime's NumPy 1.26.4
pin does not support Trixie's default Python 3.13. Update `sources.json` when
qualifying a newer Python 3.12 security release.

The application runs as `synapse`, with project files in
`/home/synapse/Synapse` and a virtual environment at `/home/synapse/.venv`.
These paths match the current installer, which preserves the image-owned service
when deploying with the `synapse` account. The initial project has no cameras
and team number zero. Set the team and camera configuration before robot use.
The default hostname is `synapse`, and the runtime serves its static UI at
`http://synapse.local:3000`. It also uses TCP 8765
for its WebSocket, TCP 8080 for its file server and UDP device discovery; consult
the runtime configuration before placing the device on a restricted network.

Armbian's first-login flow and DHCP configuration are retained. No shared Synapse
password, SSH private key or build-host machine ID is shipped. Complete Armbian's
first login using a console or its documented SSH provisioning process, then
set a password for `synapse` with `sudo passwd synapse` if using the current
password-based deployment client. Use a development installation that includes
this builder's image-service detection. Older clients try to recreate the service
and require administrator privileges. The service account is not a general sudo
administrator. Limited sudo permissions cover the runtime's
hostname, hosts-file, IP-address, reboot and service-restart operations.
Use `python -m synapse_installer deploy <device>` for ordinary project deployment;
`install` and `sync` are unnecessary for the preinstalled runtime. Additional
project dependencies still need to be installed into its virtual environment.

```sh
systemctl status synapse-runtime.service
journalctl -b -u synapse-runtime.service
sudo systemctl restart synapse-runtime.service
```

The builder retains Armbian's kernel, device trees, firmware, boot partitions,
UUID/PARTUUID references and first-boot filesystem expansion. It grows the final
root partition without shrinking or moving boot partitions. It does not flash
SPI, alter EEPROM/UEFI firmware, apply PhotonVision-specific optimizations, force
CPU affinity or install NPU SDKs. Board-specific UART/PWM provisioning frees the
Raspberry Pi Bluetooth UART and analogue-audio PWM resources; see the hardware notes.
Q6A boot media and UFS conversion still require the board vendor's instructions.
Do not attach multiple clones with identical partition identifiers at once.

## Certificate security

See [the eMudhra response](SECURITY.md) for the current official tool versions,
trust-store policy, checks and remaining validation limits. Builds audit Python,
pip, Certifi and Bun trust, refresh installed image TLS packages through signed
distro repositories, and reject the two affected CA trust paths. Existing images
need to be rebuilt and reflashed.

## Verification and provenance

The builder verifies the upstream SHA256 and detached OpenPGP signature over
the compressed image before decompression. Signatures must validate against a
pinned, documented Armbian primary-key fingerprint in an isolated GnuPG keyring.
Missing, invalid, expired, revoked or unapproved signatures stop the build.
See [trust anchors](keys/README.md) for the key sources and fingerprints.

External mirrors are accepted only under HTTPS URL prefixes published by
[Armbian's mirror registry](https://dl.armbian.com/mirrors), fetched over HTTPS
for each preparation. Unlisted hosts and paths, HTTP downgrades, URL credentials
and path traversal are rejected. If the redirector's selected mirror is
unreachable, another registered HTTPS mirror is tried. Network policy still
applies; no proxy restrictions are bypassed. A mirror cannot authenticate a
modified image merely by supplying a matching checksum: the signature is also
required. This authenticates the base against Armbian's pinned key, not the
modified Synapse output or the security of every upstream component.

The wheel and CPython archive are checked again in the target root. Debian apt
retains its signed repository verification, and Python packages use official
PyPI with TLS verification and binary-only dependencies.

Each output has a checksum and provenance JSON recording the selected base URL,
Armbian/kernel release, base hash, signature URL/hash and signer fingerprints,
mirror-registry URL, wheel version/hash, CPython commit/hash, builder
commit and dirty status. The image retains that input metadata and installed
Debian/Python package inventories in `/usr/share/synapse-image`. Base resolution
and transitive apt/PyPI dependencies intentionally follow upstream updates;
these are traceable builds, not byte-for-byte reproducible builds.

```sh
.venv/bin/python -m pytest image_builder/tests -q
shellcheck image_builder/*.sh image_builder/payload/*.sh image_builder/payload/synapse-firstboot \
  image_builder/payload/synapse-usb-ethernet image_builder/payload/synapse-pwm-permissions
```

Chroot validation checks native imports, AprilTag dictionary support, packaged
UI assets, dependency consistency, enabled services and unit validity. It also
starts the default project as `synapse` and checks the dashboard, WebSocket
handshake and file-server HTTP endpoint before stopping the test process. It
cannot prove that an
image boots, USB/CSI cameras work, thermal limits are safe or storage expands on
real hardware. Before releasing a board image, boot it on that board and check
first login, unique SSH keys/machine ID, DHCP/mDNS, the runtime UI, an actual USB
camera, NetworkTables, deployment/restart, reboot and full storage expansion.
CSI support and hardware acceleration are not implied by an ARM64 image build.

## Official sources used

- [Synapse installation](https://danpeled.gitbook.io/synapse/getting-started/installing-software)
  and [custom hardware requirements](https://danpeled.gitbook.io/synapse/getting-started/hardware-selection/deploying-on-custom-hardware).
  Their [official documentation source](https://github.com/DanPeled/Synapse/tree/docs/docs/getting-started)
  was used where the website was unavailable. The current repository's dependency
  declarations take precedence over the older Python range in those pages.
- Armbian board pages: [Pi 4](https://armbian.com/boards/rpi4b),
  [Pi 5](https://armbian.com/boards/raspberrypi5),
  [Q6A](https://armbian.com/boards/radxa-dragon-q6a),
  [Orange Pi 5](https://armbian.com/boards/orangepi5),
  [Orange Pi 5 Plus](https://armbian.com/boards/orangepi5-plus).
- [Armbian inventory generator](https://github.com/armbian/armbian.github.io/blob/main/scripts/generate-armbian-images-json.sh),
  [Pi 5 artifact reuse](https://github.com/armbian/armbian.github.io/blob/main/release-targets/reusable.yml),
  and [official board definitions](https://github.com/armbian/build/tree/main/config/boards).
- [PhotonVision image modifier](https://github.com/PhotonVision/photon-image-modifier)
  and the [requested fork](https://github.com/Another-Person1/photon-image-modifier)
  informed the whole-image/chroot/service approach. No PhotonVision installer or
  board-specific binary payload is copied or executed.
- [Official CPython source](https://github.com/python/cpython) and
  [NumPy 1.26.4](https://pypi.org/project/numpy/1.26.4/).
