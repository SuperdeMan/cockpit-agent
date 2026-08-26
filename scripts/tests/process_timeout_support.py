"""Deterministic real-process readiness probes for timeout/reaping tests.

The helpers in this module deliberately keep the process tree real.  They
replace only one method on the concrete ``Popen`` instance so the test can
inject a timeout after a descendant has proved that it exists.  Cleanup is
still performed by the production runner under test.
"""

from __future__ import annotations

import ctypes
import os
import select
import signal
import subprocess
import time
from pathlib import Path
from typing import Callable


_POLL_INTERVAL_S = 0.01


def atomic_pid_ready_code(
    pid_file: Path,
    ready_file: Path,
    *,
    pid_expression: str = "descendant.pid",
) -> str:
    """Return child-script statements that atomically publish PID then READY."""

    pid_path = str(pid_file)
    ready_path = str(ready_file)
    return (
        "import os; "
        f"_probe_pid=str({pid_expression}); "
        f"_probe_pid_path=Path({pid_path!r}); "
        "_probe_pid_tmp=_probe_pid_path.with_name("
        "_probe_pid_path.name+'.tmp-'+str(os.getpid())); "
        "_probe_pid_tmp.write_text(_probe_pid,encoding='utf-8'); "
        "os.replace(_probe_pid_tmp,_probe_pid_path); "
        f"_probe_ready_path=Path({ready_path!r}); "
        "_probe_ready_tmp=_probe_ready_path.with_name("
        "_probe_ready_path.name+'.tmp-'+str(os.getpid())); "
        "_probe_ready_tmp.write_text(_probe_pid,encoding='utf-8'); "
        "os.replace(_probe_ready_tmp,_probe_ready_path); "
    )


class ProcessReadinessProbe:
    """Retain the original descendant identity and observe its exit safely."""

    def __init__(self, pid_file: Path, ready_file: Path) -> None:
        self.pid_file = pid_file
        self.ready_file = ready_file
        self.pid: int | None = None
        self.ready = False
        self._windows_handle: int | None = None
        self._pidfd: int | None = None
        self._proc_start_time: str | None = None
        self._closed = False

    def capture(
        self,
        process: subprocess.Popen[bytes],
        *,
        deadline_s: float,
        operation_done: Callable[[], bool] | None = None,
    ) -> None:
        """Wait until matching atomic PID and READY records are observable."""

        if self.ready:
            return
        deadline = time.monotonic() + deadline_s
        is_done = operation_done or (lambda: process.poll() is not None)
        while True:
            self._capture_pid_if_available()
            if self.pid is not None and self._ready_matches_pid():
                self.ready = True
                return
            if is_done():
                raise AssertionError(
                    "descendant not started: operation exited before PID+READY"
                )
            if time.monotonic() >= deadline:
                raise AssertionError(
                    "descendant not started: PID+READY deadline exceeded"
                )
            time.sleep(_POLL_INTERVAL_S)

    def _capture_pid_if_available(self) -> None:
        if self.pid is not None:
            return
        try:
            raw_pid = self.pid_file.read_text(encoding="utf-8")
            pid = int(raw_pid)
        except (OSError, UnicodeError, ValueError):
            return
        if pid <= 0:
            return
        self._retain_identity(pid)
        self.pid = pid

    def _ready_matches_pid(self) -> bool:
        try:
            return self.ready_file.read_text(encoding="utf-8") == str(self.pid)
        except (OSError, UnicodeError):
            return False

    def _retain_identity(self, pid: int) -> None:
        if os.name == "nt":
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenProcess.argtypes = [
                ctypes.c_uint32,
                ctypes.c_int,
                ctypes.c_uint32,
            ]
            kernel32.OpenProcess.restype = ctypes.c_void_p
            handle = kernel32.OpenProcess(
                0x0001 | 0x1000 | 0x00100000,
                0,
                pid,
            )
            if not handle:
                raise AssertionError(
                    "descendant not started: original process handle unavailable"
                )
            self._windows_handle = int(handle)
            return

        pidfd_open = getattr(os, "pidfd_open", None)
        if callable(pidfd_open):
            try:
                self._pidfd = pidfd_open(pid, 0)
                return
            except ProcessLookupError as exc:
                raise AssertionError(
                    "descendant not started: original process already exited"
                ) from exc
            except OSError:
                # Kernels may expose the symbol without supporting pidfds.
                pass

        identity = self._read_proc_identity(pid)
        if identity is None:
            raise AssertionError(
                "descendant not started: stable process identity unavailable"
            )
        _, self._proc_start_time = identity

    @staticmethod
    def _read_proc_identity(pid: int) -> tuple[str, str] | None:
        try:
            raw = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
        except (OSError, UnicodeError):
            return None
        closing = raw.rfind(")")
        if closing < 0:
            return None
        fields = raw[closing + 2 :].split()
        if len(fields) <= 19:
            return None
        return fields[0], fields[19]

    def is_exited_now(self) -> bool:
        """Return whether the retained original process has exited."""

        if not self.ready or self.pid is None:
            raise AssertionError("descendant not started: probe was not ready")
        if os.name == "nt":
            if self._windows_handle is None:
                raise AssertionError("original process handle is unavailable")
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.WaitForSingleObject.argtypes = [
                ctypes.c_void_p,
                ctypes.c_uint32,
            ]
            kernel32.WaitForSingleObject.restype = ctypes.c_uint32
            result = kernel32.WaitForSingleObject(self._windows_handle, 0)
            if result == 0:
                return True
            if result == 258:
                return False
            raise AssertionError("could not inspect original process handle")

        if self._pidfd is not None:
            return bool(select.select([self._pidfd], [], [], 0)[0])

        identity = self._read_proc_identity(self.pid)
        if identity is None:
            return True
        state, start_time = identity
        return state == "Z" or start_time != self._proc_start_time

    def assert_exited(self, *, deadline_s: float = 5) -> None:
        deadline = time.monotonic() + deadline_s
        while True:
            if self.is_exited_now():
                return
            if time.monotonic() >= deadline:
                raise AssertionError("retained descendant process is still active")
            time.sleep(_POLL_INTERVAL_S)

    def force_cleanup(self, *, deadline_s: float = 5) -> None:
        """Best-effort exact-identity cleanup for a failed test assertion only."""

        if self._closed:
            return
        try:
            self._capture_pid_if_available()
        except AssertionError:
            return
        if self.pid is None:
            return
        if not self.ready:
            # PID was atomically published, so retain it even if READY never was.
            self.ready = True
        try:
            if self.is_exited_now():
                return
        except AssertionError:
            return

        if os.name == "nt":
            if self._windows_handle is None:
                return
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
            kernel32.TerminateProcess.restype = ctypes.c_int
            kernel32.TerminateProcess(self._windows_handle, 1)
        elif self._pidfd is not None and hasattr(signal, "pidfd_send_signal"):
            try:
                signal.pidfd_send_signal(self._pidfd, signal.SIGKILL)
            except OSError:
                pass
        else:
            identity = self._read_proc_identity(self.pid)
            if (
                identity is not None
                and identity[1] == self._proc_start_time
                and identity[0] != "Z"
            ):
                try:
                    os.kill(self.pid, signal.SIGKILL)
                except OSError:
                    pass

        deadline = time.monotonic() + deadline_s
        while time.monotonic() < deadline:
            try:
                if self.is_exited_now():
                    return
            except AssertionError:
                return
            time.sleep(_POLL_INTERVAL_S)

    def close(self) -> None:
        if self._closed:
            return
        if self._windows_handle is not None:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel32.CloseHandle.restype = ctypes.c_int
            kernel32.CloseHandle(self._windows_handle)
            self._windows_handle = None
        if self._pidfd is not None:
            os.close(self._pidfd)
            self._pidfd = None
        self._closed = True


class MethodArm:
    """Observable one-shot gate installed on one concrete ``Popen`` method."""

    def __init__(self) -> None:
        self.triggered = False
        self.timeout_injections = 0


def _arm_method_after_ready(
    process: subprocess.Popen[bytes],
    probe: ProcessReadinessProbe,
    *,
    method_name: str,
    inject_timeout: bool,
    ready_timeout_s: float,
) -> MethodArm:
    original = getattr(process, method_name)
    original_poll = process.poll
    arm = MethodArm()

    def armed(*args, **kwargs):
        if arm.triggered:
            return original(*args, **kwargs)
        arm.triggered = True
        probe.capture(
            process,
            deadline_s=ready_timeout_s,
            operation_done=lambda: original_poll() is not None,
        )
        if inject_timeout:
            arm.timeout_injections += 1
            timeout = kwargs.get("timeout")
            if timeout is None and args:
                timeout = args[-1]
            raise subprocess.TimeoutExpired(process.args, timeout)
        return original(*args, **kwargs)

    setattr(process, method_name, armed)
    return arm


def arm_wait_timeout_after_ready(
    process: subprocess.Popen[bytes],
    probe: ProcessReadinessProbe,
    *,
    ready_timeout_s: float = 8,
) -> MethodArm:
    return _arm_method_after_ready(
        process,
        probe,
        method_name="wait",
        inject_timeout=True,
        ready_timeout_s=ready_timeout_s,
    )


def arm_wait_after_ready(
    process: subprocess.Popen[bytes],
    probe: ProcessReadinessProbe,
    *,
    ready_timeout_s: float = 8,
) -> MethodArm:
    return _arm_method_after_ready(
        process,
        probe,
        method_name="wait",
        inject_timeout=False,
        ready_timeout_s=ready_timeout_s,
    )


def arm_communicate_timeout_after_ready(
    process: subprocess.Popen[bytes],
    probe: ProcessReadinessProbe,
    *,
    ready_timeout_s: float = 8,
) -> MethodArm:
    return _arm_method_after_ready(
        process,
        probe,
        method_name="communicate",
        inject_timeout=True,
        ready_timeout_s=ready_timeout_s,
    )


def arm_poll_after_ready(
    process: subprocess.Popen[bytes],
    probe: ProcessReadinessProbe,
    *,
    ready_timeout_s: float = 8,
    on_ready: Callable[[], None] | None = None,
) -> MethodArm:
    """Gate the first real poll until READY, without faking process completion."""

    original_poll = process.poll
    arm = MethodArm()

    def armed_poll():
        if arm.triggered:
            return original_poll()
        arm.triggered = True
        probe.capture(
            process,
            deadline_s=ready_timeout_s,
            operation_done=lambda: original_poll() is not None,
        )
        if on_ready is not None:
            on_ready()
        return original_poll()

    process.poll = armed_poll
    return arm


class DeadlineJumpClock:
    """Runner-local clock that crosses one deadline after a readiness arm fires."""

    def __init__(self, arm_getter: Callable[[], MethodArm | None], *, jump_s: float) -> None:
        self._arm_getter = arm_getter
        self._jump_s = jump_s
        self.jumped = False

    def monotonic(self) -> float:
        now = time.monotonic()
        arm = self._arm_getter()
        if arm is not None and arm.triggered and not self.jumped:
            self.jumped = True
            return now + self._jump_s
        return now

    @staticmethod
    def sleep(seconds: float) -> None:
        time.sleep(seconds)
