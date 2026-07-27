"""Tests for BuildDispatcher."""
import os
from pathlib import Path
from unittest.mock import MagicMock, call, patch
import pytest
from cauldron_site_astro.dispatcher import SubprocessBuildDispatcher, BuildDispatcher

def _make_dispatcher(tmp_path, **overrides):
    defaults = dict(
        pending_path=tmp_path / "build.pending",
        pid_path=tmp_path / "build.pid",
        python_exe="/usr/bin/python3",
        manage_py=str(tmp_path / "manage.py"),
        log_path=str(tmp_path / "build.log"),
    )
    defaults.update(overrides)
    return SubprocessBuildDispatcher(**defaults)

class TestBuildDispatcherInterface:
    def test_is_abstract(self):
        import inspect
        assert inspect.isabstract(BuildDispatcher)

    def test_subprocess_dispatcher_is_concrete(self, tmp_path):
        d = _make_dispatcher(tmp_path)
        assert isinstance(d, BuildDispatcher)

class TestDispatch:
    def test_dispatch_returns_immediately(self, tmp_path):
        """dispatch() must return without blocking."""
        d = _make_dispatcher(tmp_path)
        with patch("subprocess.Popen") as mock_popen:
            mock_popen.return_value = MagicMock(pid=12345)
            import time
            t0 = time.monotonic()
            d.dispatch()
            elapsed = time.monotonic() - t0
        assert elapsed < 1.0  # must be fast

    def test_dispatch_touches_pending_file(self, tmp_path):
        d = _make_dispatcher(tmp_path)
        with patch("subprocess.Popen") as mock_popen:
            mock_popen.return_value = MagicMock(pid=12345)
            d.dispatch()
        assert (tmp_path / "build.pending").exists()

    def test_dispatch_launches_worker(self, tmp_path):
        """First dispatch launches exactly one worker subprocess."""
        d = _make_dispatcher(tmp_path)
        with patch("subprocess.Popen") as mock_popen:
            mock_popen.return_value = MagicMock(pid=12345)
            d.dispatch()
        assert mock_popen.call_count == 1
        args = mock_popen.call_args[0][0]
        assert "cauldron_site_build" in args
        assert "--worker" in args

    def test_dispatch_no_shell(self, tmp_path):
        """Worker must be launched without shell=True."""
        d = _make_dispatcher(tmp_path)
        with patch("subprocess.Popen") as mock_popen:
            mock_popen.return_value = MagicMock(pid=12345)
            d.dispatch()
        kwargs = mock_popen.call_args[1]
        assert kwargs.get("shell") is not True

    def test_dispatch_uses_correct_python(self, tmp_path):
        d = _make_dispatcher(tmp_path, python_exe="/custom/python")
        with patch("subprocess.Popen") as mock_popen:
            mock_popen.return_value = MagicMock(pid=12345)
            d.dispatch()
        cmd = mock_popen.call_args[0][0]
        assert cmd[0] == "/custom/python"

class TestCoalescing:
    def test_duplicate_dispatch_while_worker_running_does_not_launch_second(self, tmp_path):
        """Second dispatch() while worker is running touches pending but does not call Popen again."""
        d = _make_dispatcher(tmp_path)
        # Write a fake PID that appears alive
        pid_path = tmp_path / "build.pid"
        fake_pid = os.getpid()  # our own PID — definitely alive
        pid_path.write_text(str(fake_pid))

        with patch("subprocess.Popen") as mock_popen:
            d.dispatch()

        mock_popen.assert_not_called()
        assert (tmp_path / "build.pending").exists()

    def test_three_rapid_dispatches_launch_one_worker(self, tmp_path):
        """Three rapid dispatches result in exactly one worker launch."""
        d = _make_dispatcher(tmp_path)
        popen_calls = []

        def fake_popen(cmd, **kwargs):
            proc = MagicMock()
            proc.pid = 99999
            # After first launch, simulate worker writing its PID
            pid_path = tmp_path / "build.pid"
            pid_path.write_text("99999")
            popen_calls.append(cmd)
            return proc

        with patch("subprocess.Popen", side_effect=fake_popen):
            with patch("os.kill", return_value=None):  # PID 99999 appears alive
                d.dispatch()
                d.dispatch()
                d.dispatch()

        assert len(popen_calls) == 1

    def test_stale_pid_file_allows_new_worker(self, tmp_path):
        """A PID file referencing a dead process is treated as stale → new worker launched."""
        d = _make_dispatcher(tmp_path)
        pid_path = tmp_path / "build.pid"
        pid_path.write_text("999999")  # non-existent PID

        with patch("subprocess.Popen") as mock_popen:
            mock_popen.return_value = MagicMock(pid=12345)
            d.dispatch()

        assert mock_popen.call_count == 1

class TestWorkerFailure:
    def test_popen_failure_is_logged(self, tmp_path):
        d = _make_dispatcher(tmp_path)
        with patch("subprocess.Popen", side_effect=OSError("no such file")):
            with patch("cauldron_site_astro.dispatcher.logger") as mock_logger:
                d.dispatch()
        mock_logger.error.assert_called()
        error_msg = str(mock_logger.error.call_args)
        assert "launch" in error_msg.lower() or "worker" in error_msg.lower() or "failed" in error_msg.lower()

    def test_pending_file_error_is_logged(self, tmp_path):
        """If touching the pending file fails, log error and return."""
        d = _make_dispatcher(tmp_path, pending_path="/proc/nonexistent/file")
        with patch("subprocess.Popen") as mock_popen:
            with patch("cauldron_site_astro.dispatcher.logger") as mock_logger:
                d.dispatch()
        mock_popen.assert_not_called()
        mock_logger.error.assert_called()
