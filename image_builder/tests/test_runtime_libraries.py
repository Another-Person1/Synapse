# SPDX-FileCopyrightText: 2026 Dan Peled
#
# SPDX-License-Identifier: GPL-3.0-or-later

from image_builder.payload import runtime_libraries as rl

LDD = """\
\tlinux-vdso.so.1 (0x0000ffff9f5c0000)
\tlibssl.so.3 => /lib/aarch64-linux-gnu/libssl.so.3 (0x0000ffff9f400000)
\tlibopenblas.so.0 => /venv/numpy.libs/libopenblas.so.0 (0x0000ffff9e000000)
\tlibmissing.so.1 => not found
\t/lib/ld-linux-aarch64.so.1 (0x0000ffff9f580000)
"""


def test_parse_ldd_separates_resolved_and_missing():
    resolved, missing = rl.parse_ldd(LDD)
    assert resolved == {
        "/lib/aarch64-linux-gnu/libssl.so.3",
        "/venv/numpy.libs/libopenblas.so.0",
    }
    assert missing == {"libmissing.so.1"}


def test_system_candidates_skip_bundled_libraries_and_cover_usrmerge(tmp_path):
    bundled = tmp_path / "venv" / "numpy.libs" / "libopenblas.so.0"
    candidates = rl.system_candidates(
        {"/lib/aarch64-linux-gnu/libssl.so.3", str(bundled)}, [tmp_path / "venv"]
    )
    assert "/lib/aarch64-linux-gnu/libssl.so.3" in candidates
    assert "/usr/lib/aarch64-linux-gnu/libssl.so.3" in candidates
    assert not any("openblas" in c for c in candidates)


def test_parse_dpkg_search_strips_architecture_and_splits_owners():
    output = (
        "libssl3t64:arm64: /usr/lib/aarch64-linux-gnu/libssl.so.3\n"
        "libgl1:arm64, libgl1-mesa:arm64: /usr/lib/aarch64-linux-gnu/libGL.so.1\n"
        "diversion by foo from: /usr/lib/x\n"
        "dpkg-query: no path found matching pattern /lib/nothing\n"
    )
    assert rl.parse_dpkg_search(output) == {"libssl3t64", "libgl1", "libgl1-mesa"}


def test_cli_rejects_unknown_mode_and_missing_root(tmp_path):
    assert rl.main(["runtime_libraries.py", "remove", str(tmp_path)]) == 2
    assert rl.main(["runtime_libraries.py", "check", str(tmp_path / "absent")]) == 2
