# AGENTS.md

Guidance for AI agents working in this repository: **Synapse**, an FRC coprocessor
vision runtime. This is a fork of `DanPeled/Synapse`.

## Repository map

| Path | What it is |
|---|---|
| `synapse_core` | Python runtime core (`synapse` package) |
| `synapse_net` | Networking: NetworkTables client, sockets, protobuf definitions |
| `synapse_installer` | SSH deploy/sync CLI (`python -m synapse_installer`) |
| `synapse_lib` | Robot-side vendordep library (Java + Python) |
| `synapse_ui` | Next.js web UI (static export to `synapse_ui/out`) |
| `synapse_sim` | 3D simulator + headless renderer (`assets/viewer.bundle.js` is committed) |
| `synapse_manager` | Tauri desktop manager (vite + tsc, Rust via cargo) |
| `image_builder` | Signed Armbian device-image builder (see below) |
| `examples` | Robot-side example projects |

## Remotes and branches

- `upstream` = `DanPeled/Synapse` (canonical). `origin` = this fork.
- `main` tracks `upstream/main`; keep it fast-forwarded to upstream.
- `codex/synapse-image-builder` is the contribution branch for the image
  builder. Keep it rebased on top of `main`. Never force-push it without
  explicit confirmation.

## JavaScript frontends — Bun only

- Bun **1.4.4** is pinned in `.bun-version`, each frontend's `packageManager`,
  and `bun.lock`. **Never use npm or pnpm**: do not add `package-lock.json` or
  `pnpm-lock.yaml`; run `bun install --frozen-lockfile`.
- Builds: `synapse_ui` → `bun run build`; `synapse_sim` → `bun run build:headless`
  (if you change `synapse_sim/backend`, rebuild and commit the regenerated
  `src/synapse_sim/assets/viewer.bundle.js` — CI verifies it is up to date);
  `synapse_manager` → `bun run build` (tsc + vite).
- `makefile`: `make install`, `make test`, `make format`, `make generate_buf`.

## Python

- Packaging is `setup.py`, which reads versions from `versions.properties`
  (`synapsePythonVersion`, `wpilibPythonVersion`). `python_requires` is
  `>=3.11, <=3.14`.
- Tests use pytest via the dev extra: `pip install ".[dev]"`, then
  `make test` (or `python -m pytest`, ignoring `synapse_lib/`).
- New `*.py` files need the SPDX header (`GPL-3.0-or-later`, copyright
  "Dan Peled") — `make license` adds them.
- Dependency rules: runtime deps stay in `install_requires`; dev/codegen-only
  extras (e.g. `betterproto[compiler]`) go in `extras_require["dev"]`. Do not
  move CLI deps (paramiko, scp, ...) out of `install_requires` — the published
  `synapse_installer` command must work on a plain `pip install`.
- `opencv_contrib_python` is a superset of `opencv_python` (includes
  `cv2.aruco`); depend on contrib only.

## Image builder (`image_builder/`)

Builds signed Armbian Debian **minimal** images with Synapse preinstalled.

- Entry points: `build.py` (`resolve` / `build`), `modify-image.sh` (loop-mount
  + chroot modification), `build-python.sh` (builds the shared CPython once),
  `payload/` (in-image install/validate scripts).
- Pins: `boards.json` (board → Armbian board/image target), `sources.json`
  (CPython version/commit/sha256).
- CI: `.github/workflows/synapse_images.yml`, jobs `test`, `matrix`, `wheel`,
  `wheelhouse` (downloads ARM64 wheels natively on an ARM runner),
  `cpython` (builds one shared CPython, cached on distro+sources), `image`
  (per board, `max-parallel: 4`), and `release` (attaches images to the GitHub
  release on `v*` tags).
- Local build needs native ARM64 Linux + root: `bash image_builder/build-wheel.sh`,
  then `python3 -m image_builder.build build --board <board> --wheel dist/image-builder/*.whl --work-dir <fresh> [--python-tarball ...] [--wheelhouse ...]`.
- **Do not weaken the security invariants**: official HTTPS URLs only
  (`official_url`), SHA256 + Armbian signature verification, TLS trust policy
  (`payload/tls_policy.py`, `check_runtime.ts` — distrusts compromised eMudhra
  roots), pinned signing-key fingerprints in `build.py`, refusal to modify
  images whose root is not the final partition, `e2fsck`/`sfdisk --verify`
  gates, and the fail-closed cleanup trap in `modify-image.sh`.
- The shipped image is shrunk after the build (last partition only; starts and
  UUIDs never move; 256 MiB margin) so 8 GB SD/eMMC suffice; Armbian first boot
  re-expands it. `fstrim` runs after the shrink so deleted build files compress
  as zeros.
- `synapse_imager/` and `release_images.yml`/`publish_images.yml` were removed
  on purpose — `image_builder/` owns device images now. Do not re-add them.

## CI conventions

- `publish_pypi.yml` runs on `v*` tags plus manual dispatch;
  `synapselib_dev_release.yml` runs on pushes to `main` touching `synapse_lib`;
  the PR labeler is manual-only and requires a PR number.
- Action versions are kept current (`actions/checkout@v6`,
  `setup-python@v6`, `setup-java@v5`, `cache@v5`, `upload-artifact@v7`,
  `download-artifact@v8`, `oven-sh/setup-bun@v2`); validation workflows use
  `concurrency` with `cancel-in-progress: true`.

## Before pushing — verification checklist

1. `python -m compileall -q` on changed Python files; `bash -n` on changed
   shell scripts (CI also runs `shellcheck image_builder/`).
2. Every `.github/workflows/*.yml` still parses as YAML.
3. `bun install --frozen-lockfile` in `synapse_ui`, `synapse_sim`,
   `synapse_manager`; `bun run build` / `build:headless` succeed.
4. `python3 -m image_builder.build resolve --board <board>` resolves against
   the live Armbian index.
5. `image_builder/tests` pass under pytest (CI runs them with pytest +
   paramiko; `test_project_permissions` needs Linux root).
6. Git hygiene: no conflict markers, no `*.rej`/`*.orig`, no temp dirs
   (`.tmp-test`, `__pycache__` are never committed), `git fsck` clean.

## Commit style

Imperative subject line, body explaining *why*. When committing through
Mistral Vibe, the `Generated by Mistral Vibe` / `Co-Authored-By` trailer is
added automatically.
