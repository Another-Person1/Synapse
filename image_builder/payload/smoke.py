"""Start the image project as its service user and check its real HTTP/WS APIs."""

import argparse
import asyncio
import os
import pwd
import signal
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path("/home/synapse/Synapse"))
    parser.add_argument("--python", default="/home/synapse/.venv/bin/python")
    parser.add_argument("--user", default="synapse")
    args = parser.parse_args()
    account = pwd.getpwnam(args.user)
    identity = {}
    if os.geteuid() == 0:
        identity = {
            "user": account.pw_uid,
            "group": account.pw_gid,
            "extra_groups": os.getgrouplist(args.user, account.pw_gid),
        }
    elif os.geteuid() != account.pw_uid:
        raise RuntimeError("Smoke check must run as root or the requested service user")
    for port in (3000, 8080, 8765):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", port))
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    environment = dict(
        os.environ,
        HOME=account.pw_dir,
        USER=args.user,
        TERM="dumb",
        PYTHONUNBUFFERED="1",
    )
    with tempfile.TemporaryFile(mode="w+b") as log:
        process = subprocess.Popen(
            [args.python, str(args.project / "main.py")],
            cwd=args.project,
            env=environment,
            stdout=log,
            stderr=log,
            start_new_session=True,
            **identity,
        )
        try:
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(
                        f"Runtime exited during startup: {process.returncode}"
                    )
                try:
                    with opener.open("http://127.0.0.1:3000/", timeout=2) as response:
                        html = response.read().decode()
                    if "Camera Stream" in html and "Pipeline Results" in html:
                        break
                except (OSError, urllib.error.URLError):
                    pass
                time.sleep(0.25)
            else:
                raise RuntimeError("Runtime did not serve the expected dashboard")

            async def websocket_check():
                import websockets

                async with websockets.connect(
                    "ws://127.0.0.1:8765", open_timeout=10, proxy=None
                ):
                    pass

            # Use the image interpreter, which has websockets and native deps.
            asyncio.run(websocket_check())
            with opener.open("http://127.0.0.1:8080/", timeout=5) as response:
                assert response.status == 200, "Project file server unavailable"
            if process.poll() is not None:
                raise RuntimeError("Runtime exited after serving the dashboard")
            print(
                "Runtime dashboard, WebSocket handshake and file-server HTTP checks passed"
            )
        except Exception:
            log.seek(0)
            print(log.read().decode(errors="replace"))
            raise
        finally:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=10)
            except ProcessLookupError:
                pass
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)


if __name__ == "__main__":
    main()
