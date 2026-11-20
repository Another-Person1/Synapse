#!/usr/bin/env python3
"""Map the native libraries a tree loads to the Debian packages providing them.

``packages ROOT...`` prints the Debian packages owning every system library
that ELF files under ROOT link against. install.sh marks them manually
installed before purging build-only packages, so cleanup can never remove a
library that Synapse's interpreter or its Python packages need.

``check ROOT...`` fails if any ELF file under ROOT has an unresolved library.
"""

import subprocess
import sys
from pathlib import Path


def elf_files(roots):
    for root in roots:
        for path in sorted(Path(root).rglob("*")):
            if path.is_symlink() or not path.is_file():
                continue
            try:
                with path.open("rb") as stream:
                    if stream.read(4) == b"\x7fELF":
                        yield path
            except OSError:
                continue


def parse_ldd(output: str) -> tuple[set[str], set[str]]:
    """Return (resolved library paths, missing sonames) from ldd output."""
    resolved, missing = set(), set()
    for line in output.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[1] == "=>":
            if parts[2] == "not":
                missing.add(parts[0])
            elif parts[2].startswith("/"):
                resolved.add(parts[2])
    return resolved, missing


def scan(roots) -> tuple[set[str], dict[str, Path]]:
    resolved, missing = set(), {}
    for path in elf_files(roots):
        result = subprocess.run(["ldd", str(path)], capture_output=True, text=True)
        found, absent = parse_ldd(result.stdout)
        resolved |= found
        for name in absent:
            missing.setdefault(name, path)
    return resolved, missing


def system_candidates(paths, roots) -> list[str]:
    """Spellings dpkg may own for each library outside ROOT (usrmerge aware)."""
    scanned = [Path(root).resolve() for root in roots]
    candidates = set()
    for raw in paths:
        for spelling in {Path(raw), Path(raw).resolve()}:
            if any(spelling.is_relative_to(root) for root in scanned):
                continue  # bundled inside the scanned tree, e.g. numpy.libs
            path = spelling.as_posix()
            candidates.add(path)
            if path.startswith("/usr/lib/"):
                candidates.add(path.removeprefix("/usr"))
            elif path.startswith("/lib/"):
                candidates.add("/usr" + path)
    return sorted(candidates)


def parse_dpkg_search(output: str) -> set[str]:
    packages = set()
    for line in output.splitlines():
        if line.startswith("diversion ") or ": /" not in line:
            continue
        for owner in line.split(": /", 1)[0].split(","):
            name = owner.strip().split(":", 1)[0]
            if name:
                packages.add(name)
    return packages


def owning_packages(paths) -> set[str]:
    if not paths:
        return set()
    # Exit status is 1 when some spelling is unowned; the others still print.
    result = subprocess.run(
        ["dpkg-query", "--search", *paths], capture_output=True, text=True
    )
    return parse_dpkg_search(result.stdout)


def main(argv) -> int:
    if len(argv) < 3 or argv[1] not in {"packages", "check"}:
        print(__doc__, file=sys.stderr)
        return 2
    roots = argv[2:]
    for root in roots:
        if not Path(root).is_dir():
            print(f"Not a directory: {root}", file=sys.stderr)
            return 2
    resolved, missing = scan(roots)
    if argv[1] == "check":
        for name, path in sorted(missing.items()):
            print(f"{path}: missing {name}", file=sys.stderr)
        return 1 if missing else 0
    packages = owning_packages(system_candidates(resolved, roots))
    if resolved and not packages:
        print("Could not map linked libraries to Debian packages", file=sys.stderr)
        return 1
    print("\n".join(sorted(packages)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
