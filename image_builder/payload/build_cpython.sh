#!/usr/bin/env bash
# Compile the pinned CPython source into /opt/synapse-python. Run inside an
# ARM64 Debian chroot that already has cpython-build-packages.txt installed.
# Shared by build-python.sh (once per CI run) and install.sh (local builds).
set -euo pipefail
[[ $# == 2 ]] || { echo 'Usage: build_cpython.sh CPYTHON.tar.gz runtime_libraries.py' >&2; exit 2; }
source_archive=$(realpath -- "$1")
helper=$(realpath -- "$2")
prefix=/opt/synapse-python
[[ ! -e $prefix ]] || { echo "$prefix already exists" >&2; exit 1; }
source_dir=$(mktemp -d /tmp/synapse-cpython.XXXXXXXX)
trap 'rm -rf -- "$source_dir"' EXIT
tar -xzf "$source_archive" -C "$source_dir" --strip-components=1
(
    cd "$source_dir"
    ./configure --prefix="$prefix" --with-ensurepip=install --disable-test-modules
    make -j"$(nproc)"
    make altinstall
)
# Drop what the appliance never uses: the static libpython (nothing embeds it),
# debug symbols, and -O/-OO bytecode (Synapse does not run with -O).
rm -f "$prefix"/lib/python3.12/config-3.12-*/libpython3.12.a
find "$prefix" -type f -name '*.so' -exec strip --strip-unneeded {} +
strip --strip-unneeded "$prefix/bin/python3.12"
find "$prefix" -type f \( -name '*.opt-1.pyc' -o -name '*.opt-2.pyc' \) -delete
"$prefix/bin/python3.12" -c 'import ssl, sqlite3, bz2, lzma, zlib, ctypes, readline, venv'
# Record what this interpreter was built for and which Debian packages provide
# the libraries it links against, so other images can verify and keep them.
(
    # shellcheck source=/dev/null
    . /etc/os-release
    printf '%s\n' "$ID" "$VERSION_CODENAME" "$(dpkg --print-architecture)"
) > "$prefix/.synapse-build-os"
python3 "$helper" check "$prefix"
python3 "$helper" packages "$prefix" > "$prefix/.synapse-runtime-packages"
[[ -s $prefix/.synapse-runtime-packages ]] || { echo 'No runtime packages recorded for CPython' >&2; exit 1; }
