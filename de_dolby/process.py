"""Common subprocess lifecycle interface and typed failure taxonomy."""

from __future__ import annotations

import subprocess
import os
import signal
from dataclasses import dataclass
from typing import Protocol


class ProcessError(RuntimeError):
    def __init__(self, message: str, command: tuple[str, ...], stderr: str = ""):
        super().__init__(message)
        self.command = command
        self.stderr = stderr


class ProcessFailed(ProcessError):
    def __init__(
        self, command: tuple[str, ...], returncode: int, stderr: str = ""
    ):
        detail = f"\n{stderr}" if stderr else ""
        super().__init__(
            f"Command failed (exit {returncode}): {' '.join(command)}{detail}",
            command,
            stderr,
        )
        self.returncode = returncode


class ProcessTimedOut(ProcessError):
    def __init__(self, command: tuple[str, ...], timeout_seconds: float):
        super().__init__(
            f"Command timed out after {timeout_seconds:g}s: {' '.join(command)}",
            command,
        )
        self.timeout_seconds = timeout_seconds


class ProcessCancelled(ProcessError):
    """The caller cancelled execution (normally Ctrl+C)."""


@dataclass(frozen=True)
class ProcessRequest:
    command: tuple[str, ...]
    capture_stdout: bool = True
    stdin_data: bytes | None = None
    pipe_stdin: bool = False
    timeout_seconds: float | None = None
    check: bool = True


class ProcessRunner(Protocol):
    def run(self, request: ProcessRequest) -> subprocess.CompletedProcess:
        """Run one request or raise a typed process error."""


class SubprocessRunner:
    """Standard-library runner; process-tree policy is added by platform slices."""

    def run(self, request: ProcessRequest) -> subprocess.CompletedProcess:
        command = list(request.command)
        stdin = (
            subprocess.PIPE
            if request.stdin_data is not None or request.pipe_stdin
            else None
        )
        try:
            result = subprocess.run(
                command,
                stdin=stdin,
                stdout=subprocess.PIPE if request.capture_stdout else None,
                stderr=subprocess.PIPE,
                input=request.stdin_data,
                check=False,
                timeout=request.timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise ProcessTimedOut(
                request.command, request.timeout_seconds or 0
            ) from exc
        if request.check and result.returncode != 0:
            stderr = (
                result.stderr.decode(errors="replace") if result.stderr else ""
            )
            if not stderr and result.stdout:
                stderr = result.stdout.decode(errors="replace")
            raise ProcessFailed(request.command, result.returncode, stderr)
        return result


class PosixProcessRunner:
    """Run each command in a dedicated POSIX process group."""

    def __init__(self, grace_seconds: float = 2.0):
        self.grace_seconds = grace_seconds

    def run(self, request: ProcessRequest) -> subprocess.CompletedProcess:
        command = list(request.command)
        stdin = (
            subprocess.PIPE
            if request.stdin_data is not None or request.pipe_stdin
            else None
        )
        process = subprocess.Popen(
            command,
            stdin=stdin,
            stdout=subprocess.PIPE if request.capture_stdout else None,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        try:
            stdout, stderr = process.communicate(
                input=request.stdin_data, timeout=request.timeout_seconds
            )
        except subprocess.TimeoutExpired as exc:
            self._terminate_group(process)
            raise ProcessTimedOut(
                request.command, request.timeout_seconds or 0
            ) from exc
        except KeyboardInterrupt as exc:
            self._terminate_group(process)
            raise ProcessCancelled(
                f"Command cancelled: {' '.join(request.command)}",
                request.command,
            ) from exc
        result = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
        if request.check and result.returncode != 0:
            detail = stderr.decode(errors="replace") if stderr else ""
            if not detail and stdout:
                detail = stdout.decode(errors="replace")
            raise ProcessFailed(request.command, result.returncode, detail)
        return result

    def _terminate_group(self, process: subprocess.Popen) -> None:
        try:
            group_id = os.getpgid(process.pid)
        except ProcessLookupError:
            return
        try:
            os.killpg(group_id, signal.SIGTERM)
            process.wait(timeout=self.grace_seconds)
            return
        except subprocess.TimeoutExpired:
            os.killpg(group_id, getattr(signal, "SIGKILL", 9))
            process.wait()
        except ProcessLookupError:
            return


class WindowsProcessRunner:
    """Run commands in Windows process groups and terminate descendant trees."""

    def __init__(self, grace_seconds: float = 2.0):
        self.grace_seconds = grace_seconds

    def run(self, request: ProcessRequest) -> subprocess.CompletedProcess:
        command = list(request.command)
        stdin = (
            subprocess.PIPE
            if request.stdin_data is not None or request.pipe_stdin
            else None
        )
        process = subprocess.Popen(
            command,
            stdin=stdin,
            stdout=subprocess.PIPE if request.capture_stdout else None,
            stderr=subprocess.PIPE,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200),
        )
        try:
            stdout, stderr = process.communicate(
                input=request.stdin_data, timeout=request.timeout_seconds
            )
        except subprocess.TimeoutExpired as exc:
            self._terminate_tree(process)
            raise ProcessTimedOut(
                request.command, request.timeout_seconds or 0
            ) from exc
        except KeyboardInterrupt as exc:
            self._terminate_tree(process)
            raise ProcessCancelled(
                f"Command cancelled: {' '.join(request.command)}",
                request.command,
            ) from exc
        result = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
        if request.check and result.returncode != 0:
            detail = stderr.decode(errors="replace") if stderr else ""
            if not detail and stdout:
                detail = stdout.decode(errors="replace")
            raise ProcessFailed(request.command, result.returncode, detail)
        return result

    def _terminate_tree(self, process: subprocess.Popen) -> None:
        try:
            process.send_signal(getattr(signal, "CTRL_BREAK_EVENT", 1))
            process.wait(timeout=self.grace_seconds)
            return
        except subprocess.TimeoutExpired:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            process.wait()
        except (OSError, ProcessLookupError):
            return


default_runner: ProcessRunner = (
    PosixProcessRunner() if os.name == "posix" else WindowsProcessRunner()
)


def process_group_popen_kwargs() -> dict[str, object]:
    if os.name == "posix":
        return {"start_new_session": True}
    return {
        "creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)
    }


def terminate_process_tree(
    process: subprocess.Popen, *, grace_seconds: float = 2.0
) -> None:
    if os.name == "posix":
        PosixProcessRunner(grace_seconds)._terminate_group(process)
    else:
        WindowsProcessRunner(grace_seconds)._terminate_tree(process)
