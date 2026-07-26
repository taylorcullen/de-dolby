import subprocess
from unittest.mock import patch

import pytest

from de_dolby.process import (
    ProcessFailed,
    ProcessRequest,
    ProcessTimedOut,
    ProcessCancelled,
    PosixProcessRunner,
    WindowsProcessRunner,
    SubprocessRunner,
)


def test_runner_maps_request_to_subprocess_boundary():
    completed = subprocess.CompletedProcess(["helper"], 0, b"out", b"")
    request = ProcessRequest(
        ("helper", "--flag"), stdin_data=b"input", timeout_seconds=2
    )
    with patch("de_dolby.process.subprocess.run", return_value=completed) as run:
        assert SubprocessRunner().run(request) is completed
    assert run.call_args.kwargs["timeout"] == 2
    assert run.call_args.kwargs["input"] == b"input"


def test_nonzero_exit_preserves_command_stderr_and_code():
    completed = subprocess.CompletedProcess(["helper"], 9, b"", b"useful error")
    with patch("de_dolby.process.subprocess.run", return_value=completed):
        with pytest.raises(ProcessFailed) as error:
            SubprocessRunner().run(ProcessRequest(("helper",), check=True))
    assert error.value.command == ("helper",)
    assert error.value.returncode == 9
    assert error.value.stderr == "useful error"


def test_check_false_returns_nonzero_result():
    completed = subprocess.CompletedProcess(["helper"], 4, b"", b"warning")
    with patch("de_dolby.process.subprocess.run", return_value=completed):
        assert SubprocessRunner().run(
            ProcessRequest(("helper",), check=False)
        ).returncode == 4


def test_timeout_has_distinct_typed_error():
    expired = subprocess.TimeoutExpired(["helper"], 3)
    with patch("de_dolby.process.subprocess.run", side_effect=expired):
        with pytest.raises(ProcessTimedOut) as error:
            SubprocessRunner().run(
                ProcessRequest(("helper",), timeout_seconds=3)
            )
    assert error.value.timeout_seconds == 3
    assert error.value.command == ("helper",)


def test_posix_runner_starts_new_session_and_returns_output():
    process = type("FakeProcess", (), {
        "pid": 123,
        "returncode": 0,
        "communicate": lambda self, **kwargs: (b"out", b""),
    })()
    with patch("de_dolby.process.subprocess.Popen", return_value=process) as popen:
        result = PosixProcessRunner().run(ProcessRequest(("helper",)))
    assert result.stdout == b"out"
    assert popen.call_args.kwargs["start_new_session"] is True


def test_posix_timeout_terminates_group_then_escalates():
    process = type("FakeProcess", (), {
        "pid": 123,
        "returncode": None,
        "communicate": lambda self, **kwargs: (_ for _ in ()).throw(
            subprocess.TimeoutExpired(["helper"], 1)
        ),
        "wait": lambda self, timeout=None: (
            (_ for _ in ()).throw(subprocess.TimeoutExpired(["helper"], timeout))
            if timeout is not None else 0
        ),
    })()
    runner = PosixProcessRunner(grace_seconds=0.01)
    with patch("de_dolby.process.subprocess.Popen", return_value=process), \
         patch("de_dolby.process.os.getpgid", return_value=456, create=True), \
         patch("de_dolby.process.os.killpg", create=True) as kill_group:
        with pytest.raises(ProcessTimedOut):
            runner.run(ProcessRequest(("helper",), timeout_seconds=1))
    assert [call.args for call in kill_group.call_args_list] == [
        (456, __import__("signal").SIGTERM),
        (456, getattr(__import__("signal"), "SIGKILL", 9)),
    ]


def test_posix_keyboard_interrupt_is_distinct_cancellation():
    process = type("FakeProcess", (), {
        "pid": 123,
        "returncode": None,
        "communicate": lambda self, **kwargs: (_ for _ in ()).throw(
            KeyboardInterrupt()
        ),
        "wait": lambda self, timeout=None: 0,
    })()
    with patch("de_dolby.process.subprocess.Popen", return_value=process), \
         patch("de_dolby.process.os.getpgid", return_value=456, create=True), \
         patch("de_dolby.process.os.killpg", create=True):
        with pytest.raises(ProcessCancelled):
            PosixProcessRunner().run(ProcessRequest(("helper",)))


def test_windows_runner_creates_new_process_group():
    process = type("FakeProcess", (), {
        "pid": 123,
        "returncode": 0,
        "communicate": lambda self, **kwargs: (b"out", b""),
    })()
    with patch("de_dolby.process.subprocess.Popen", return_value=process) as popen:
        WindowsProcessRunner().run(ProcessRequest(("helper",)))
    assert popen.call_args.kwargs["creationflags"] == getattr(
        subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200
    )


def test_windows_timeout_escalates_to_taskkill_tree():
    process = type("FakeProcess", (), {
        "pid": 123,
        "returncode": None,
        "communicate": lambda self, **kwargs: (_ for _ in ()).throw(
            subprocess.TimeoutExpired(["helper"], 1)
        ),
        "send_signal": lambda self, value: None,
        "wait": lambda self, timeout=None: (
            (_ for _ in ()).throw(subprocess.TimeoutExpired(["helper"], timeout))
            if timeout is not None else 0
        ),
    })()
    runner = WindowsProcessRunner(grace_seconds=0.01)
    with patch("de_dolby.process.subprocess.Popen", return_value=process), \
         patch("de_dolby.process.subprocess.run") as taskkill:
        with pytest.raises(ProcessTimedOut):
            runner.run(ProcessRequest(("helper",), timeout_seconds=1))
    assert taskkill.call_args.args[0] == [
        "taskkill", "/PID", "123", "/T", "/F"
    ]


def test_windows_keyboard_interrupt_maps_to_cancellation_after_tree_stop():
    signals = []
    process = type("FakeProcess", (), {
        "pid": 123,
        "returncode": None,
        "communicate": lambda self, **kwargs: (_ for _ in ()).throw(
            KeyboardInterrupt()
        ),
        "send_signal": lambda self, value: signals.append(value),
        "wait": lambda self, timeout=None: 0,
    })()
    with patch("de_dolby.process.subprocess.Popen", return_value=process):
        with pytest.raises(ProcessCancelled):
            WindowsProcessRunner().run(ProcessRequest(("helper",)))
    assert signals
