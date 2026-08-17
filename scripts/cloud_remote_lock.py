"""Hold the shared cloud transaction lock for an SSH connection lifetime."""

from __future__ import annotations

import concurrent.futures
import re
import subprocess
from typing import Callable

from scripts.cloud_release_lib import SshConfig


REMOTE_E2E_LOCK = "/opt/car-agent/shared/bin/remote-e2e-lock.sh"
_RUN_ID = re.compile(r"^e2e-[0-9a-f]{32}$")
_LOCK_KINDS = ("release", "rollback", "backup", "migration", "e2e")


class RemoteLockError(RuntimeError):
    pass


def redact_lock_error(detail: str) -> str:
    lowered = detail.lower()
    kind = next((item for item in _LOCK_KINDS if item in lowered), "unknown")
    return f"remote lock unavailable: {kind}"


class RemoteCloudLock:
    def __init__(
        self,
        *,
        ssh: SshConfig,
        run_id: str,
        popen: Callable[..., object] = subprocess.Popen,
    ) -> None:
        if _RUN_ID.fullmatch(run_id) is None:
            raise RemoteLockError("remote lock run id is invalid")
        self.ssh = ssh
        self.run_id = run_id
        self.identity = run_id
        self._popen = popen
        self._process = None

    def acquire(self) -> "RemoteCloudLock":
        command = f"sudo {REMOTE_E2E_LOCK} hold --run-id {self.run_id}"
        self._process = self._popen(
            self.ssh.ssh_argv(command),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        reader = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = reader.submit(self._process.stdout.readline)
        try:
            raw_ack = future.result(timeout=20)
        except TimeoutError as exc:
            self._terminate()
            reader.shutdown(wait=False, cancel_futures=True)
            raise RemoteLockError("remote lock acknowledgement timed out") from exc
        reader.shutdown(wait=True)
        ack = raw_ack.decode("utf-8", errors="replace").strip()
        if ack != f"READY {self.run_id}":
            self._terminate()
            detail = self._process.stderr.read(4096).decode(
                "utf-8", errors="replace",
            )
            self._process = None
            raise RemoteLockError(redact_lock_error(detail))
        self.ensure_held()
        return self

    def _terminate(self) -> None:
        if self._process is None:
            return
        if self._process.poll() is None:
            self._process.terminate()
        self._process.wait(timeout=5)

    def ensure_held(self) -> None:
        process = self._process
        if process is None or process.poll() is not None:
            raise RemoteLockError("remote lock lease was lost")

    def release(self) -> None:
        process = self._process
        if process is None:
            return
        self._process = None
        if process.poll() is not None:
            raise RemoteLockError("remote lock lease was lost")
        try:
            process.stdin.write(b"\n")
            process.stdin.close()
            returncode = process.wait(timeout=15)
        except (BrokenPipeError, OSError, subprocess.TimeoutExpired) as exc:
            try:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                pass
            raise RemoteLockError("remote lock lease was lost") from exc
        if returncode != 0:
            raise RemoteLockError("remote lock lease was lost")

    def __enter__(self) -> "RemoteCloudLock":
        return self.acquire()

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.release()
