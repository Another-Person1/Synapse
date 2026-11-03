"""Create writable runtime directories, including their shared parent."""

import os
import pwd
import sys
from pathlib import Path


def prepare_project(project: Path, uid: int, gid: int) -> None:
    for directory in (
        project,
        *(project / name for name in ("config", "pipelines", "deploy", "logs")),
    ):
        directory.mkdir(parents=True, exist_ok=True)
        os.chown(directory, uid, gid)
        directory.chmod(0o755)


if __name__ == "__main__":
    account = pwd.getpwnam(sys.argv[1])
    prepare_project(Path(sys.argv[2]), account.pw_uid, account.pw_gid)
