"""Build dispatcher — decouples signal receivers from the Astro build process."""
import abc
import logging
import os
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


class BuildDispatcher(abc.ABC):
    """Interface for scheduling site builds. Signal receivers call dispatch() and return immediately."""

    @abc.abstractmethod
    def dispatch(self) -> None:
        """Enqueue a build. Must return immediately without blocking."""
        ...


class SubprocessBuildDispatcher(BuildDispatcher):
    """Launches cauldron_site_build --worker as a detached subprocess.

    Coalescing:
    - pending_path: touched to request a build
    - pid_path: PID file of the running worker

    When dispatch() is called:
    1. Touch pending_path (atomic signal that a build is needed).
    2. If a live worker holds pid_path: return (it will loop after its build).
    3. Otherwise: launch a detached worker subprocess.

    The --worker management command:
    1. Writes its PID to pid_path.
    2. Loops: clears pending, runs build, re-checks pending.
    3. When no pending: removes pid_path and exits.
    """

    def __init__(
        self,
        *,
        pending_path: "Path | str",
        pid_path: "Path | str",
        python_exe: str,
        manage_py: str,
        log_path: str,
    ):
        self._pending = Path(pending_path)
        self._pid_path = Path(pid_path)
        self._python_exe = python_exe
        self._manage_py = manage_py
        self._log_path = log_path

    def dispatch(self) -> None:
        # 1. Touch pending
        try:
            self._pending.parent.mkdir(parents=True, exist_ok=True)
            self._pending.touch()
        except OSError as exc:
            logger.error("cauldron.site.astro: cannot write pending file: %s", exc)
            return
        # 2. Check if worker alive
        if self._worker_running():
            return
        # 3. Launch worker
        try:
            self._launch_worker()
        except Exception as exc:
            logger.error("cauldron.site.astro: failed to launch build worker: %s", exc)

    def _worker_running(self) -> bool:
        try:
            pid = int(self._pid_path.read_text().strip())
            os.kill(pid, 0)
            return True
        except (OSError, ValueError, FileNotFoundError):
            return False

    def _launch_worker(self) -> None:
        log_path = Path(self._log_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a") as log_fd:
            subprocess.Popen(
                [self._python_exe, self._manage_py, "cauldron_site_build", "--worker"],
                stdout=log_fd,
                stderr=log_fd,
                close_fds=True,
                start_new_session=True,
                env=os.environ.copy(),
            )


def get_dispatcher() -> "BuildDispatcher":
    """Construct the configured BuildDispatcher from Django settings."""
    from django.conf import settings
    from cauldron_site_astro.config import get_site_astro_config

    cfg = get_site_astro_config()
    output_root = Path(cfg.output_root) if cfg.output_root else Path("/tmp/cauldron_public")

    base_dir = getattr(settings, "BASE_DIR", None)
    default_manage_py = str(Path(base_dir) / "manage.py") if base_dir else ""
    default_log = (
        str(Path(base_dir) / "logs" / "site_build.log")
        if base_dir
        else "/tmp/cauldron_site_build.log"
    )

    return SubprocessBuildDispatcher(
        pending_path=Path(str(output_root) + ".build.pending"),
        pid_path=Path(str(output_root) + ".build.pid"),
        python_exe=sys.executable,
        manage_py=cfg.manage_py_path or default_manage_py,
        log_path=cfg.build_log_file or default_log,
    )
