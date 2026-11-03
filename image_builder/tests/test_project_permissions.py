import os
import pwd
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from image_builder.payload.prepare_project import prepare_project


class ProjectPermissionsTest(unittest.TestCase):
    @unittest.skipUnless(
        os.geteuid() == 0, "Requires root to reproduce the image chroot's ownership"
    )
    def test_runtime_user_can_create_logs_and_project_files(self):
        account = pwd.getpwnam("nobody")
        with tempfile.TemporaryDirectory(
            prefix="synapse-project-permissions-"
        ) as temporary:
            parent = Path(temporary)
            parent.chmod(0o755)
            project = parent / "Synapse"
            # Reproduce install -d's implicit root-owned shared parent.
            (project / "config").mkdir(parents=True)
            self.assertEqual(project.stat().st_uid, 0)
            prepare_project(project, account.pw_uid, account.pw_gid)
            prepare_project(project, account.pw_uid, account.pw_gid)
            subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "from pathlib import Path; Path('logs/runtime.log').write_text('started'); Path('recordings').mkdir(); Path('config/settings.yml').write_text('network: {}')",
                ],
                cwd=project,
                user=account.pw_uid,
                group=account.pw_gid,
                extra_groups=[],
                check=True,
            )
            for name in (
                ".",
                "logs",
                "config",
                "pipelines",
                "deploy",
                "logs/runtime.log",
                "recordings",
            ):
                self.assertEqual((project / name).stat().st_uid, account.pw_uid)


if __name__ == "__main__":
    unittest.main()
