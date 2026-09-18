"""Ships Python source to the EV3 over SSH and runs it there.

SSH/base64-exec approach adapted from ev3dev_MCP (MIT, HannaFrangi).
"""

from __future__ import annotations

import base64
import logging
import socket
import threading
from pathlib import Path
from typing import Any

import paramiko

from ev3_mcp.config import Settings, load_settings

logger = logging.getLogger("ev3_mcp.ssh")

BRICK_API_PATH = Path(__file__).resolve().parent / "brick_api.py"


class RobotBusyError(RuntimeError):
    pass


class SSHRunner:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or load_settings()
        self._client: paramiko.SSHClient | None = None
        self._last_error: str | None = None
        self._busy = False
        self._busy_lock = threading.Lock()
        self._connect_lock = threading.Lock()

    # --- source assembly --------------------------------------------------

    def _preamble(self, budget_s: float) -> str:
        s = self.settings
        return (
            "_MAX_DURATION = {0}\n"
            "_BUDGET_S = {1}\n"
            "_LEFT_PORT = {2!r}\n"
            "_RIGHT_PORT = {3!r}\n"
        ).format(s.max_duration, budget_s, s.left_motor, s.right_motor)

    def build_program(self, body: str, budget_s: float | None = None) -> str:
        api = BRICK_API_PATH.read_text(encoding="utf-8")
        budget = self.settings.clamp_timeout(budget_s)
        return (
            self._preamble(budget)
            + "\n"
            + api
            + "\n\n# --- host-supplied ---\n"
            + body
            + "\n\n_safe_stop()\n"
        )

    # --- connection -------------------------------------------------------

    @property
    def connected(self) -> bool:
        if self.settings.dry_run or self._client is None:
            return False
        transport = self._client.get_transport()
        return transport is not None and transport.is_active()

    def _open_client(self) -> paramiko.SSHClient:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        kwargs: dict[str, Any] = {
            "hostname": self.settings.host,
            "username": self.settings.user,
            "timeout": 10,
            "allow_agent": False,
            "look_for_keys": False,
        }
        if self.settings.password:
            kwargs["password"] = self.settings.password
        elif self.settings.ssh_key_path.is_file():
            kwargs["key_filename"] = str(self.settings.ssh_key_path)
        else:
            raise RuntimeError(
                "No SSH credentials: set EV3_PASSWORD or place a key at "
                f"{self.settings.ssh_key_path}"
            )
        client.connect(**kwargs)
        return client

    def connect(self) -> None:
        if self.settings.dry_run:
            return
        with self._connect_lock:
            if self.connected:
                return
            self._close_unlocked()
            try:
                self._client = self._open_client()
                self._last_error = None
            except Exception as exc:
                self._last_error = str(exc)
                self._client = None
                raise

    def _close_unlocked(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None

    def close(self) -> None:
        with self._connect_lock:
            self._close_unlocked()

    def _ensure_connected(self) -> None:
        if not self.settings.dry_run and not self.connected:
            self.connect()

    # --- execution --------------------------------------------------------

    def _bootstrap(self) -> str:
        return (
            f"{self.settings.python_bin} -c "
            "'import base64,sys; exec(base64.b64decode(sys.stdin.read()).decode(\"utf-8\"))'"
        )

    def _exec(self, program: str, timeout_s: float) -> tuple[str, str, int]:
        assert self._client is not None
        payload = base64.b64encode(program.encode("utf-8")).decode("ascii")
        stdin, stdout, stderr = self._client.exec_command(self._bootstrap(), timeout=timeout_s)
        stdin.write(payload)
        stdin.channel.shutdown_write()
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        return out, err, stdout.channel.recv_exit_status()

    def emergency_stop(self) -> None:
        """Best-effort motor cut on its own channel; never raises."""
        if self.settings.dry_run or not self.connected:
            return
        try:
            self._exec(self.build_program("stop()"), timeout_s=10)
        except Exception as exc:
            logger.warning("emergency stop failed: %s", exc)

    def run_source(
        self,
        body: str,
        timeout_s: float | None = None,
        *,
        bypass_lock: bool = False,
    ) -> dict[str, Any]:
        timeout = self.settings.clamp_timeout(timeout_s)
        # The brick gets slightly less than the host allows, so it can exit
        # cleanly and report partial results before the watchdog cuts it off.
        program = self.build_program(body, max(1.0, timeout - 2.0))

        if not bypass_lock:
            with self._busy_lock:
                if self._busy:
                    raise RobotBusyError(
                        "Robot is already running a program; wait for it or call stop"
                    )
                self._busy = True

        try:
            if self.settings.dry_run:
                logger.info("[dry-run] would run %d bytes with timeout %ss", len(program), timeout)
                return {
                    "ok": True,
                    "dry_run": True,
                    "timeout_s": timeout,
                    "stdout": "",
                    "stderr": "",
                    "exit_status": 0,
                    "program": program,
                }

            try:
                self._ensure_connected()
            except Exception as exc:
                # Unreachable brick is an expected state (powered off, off the
                # network), so report it rather than raising an opaque MCP error.
                self._last_error = str(exc)
                return {
                    "ok": False,
                    "dry_run": False,
                    "connected": False,
                    "error": f"cannot reach the robot at {self.settings.host}: {exc}",
                }

            try:
                out, err, status = self._exec(program, timeout)
            except socket.timeout:
                self.emergency_stop()
                self._last_error = f"timed out after {timeout}s; motors stopped"
                return {
                    "ok": False,
                    "dry_run": False,
                    "timed_out": True,
                    "timeout_s": timeout,
                    "stdout": "",
                    "stderr": "",
                    "error": self._last_error,
                }

            ok = status == 0
            if not ok:
                # A crash can leave motors running: ev3dev motor state lives in
                # sysfs and outlives the process that set it.
                self.emergency_stop()
            self._last_error = None if ok else (err.strip() or f"exited {status}")
            return {
                "ok": ok,
                "dry_run": False,
                "timeout_s": timeout,
                "stdout": out,
                "stderr": err,
                "exit_status": status,
                "error": self._last_error,
            }
        finally:
            if not bypass_lock:
                with self._busy_lock:
                    self._busy = False

    def status(self) -> dict[str, Any]:
        s = self.settings
        with self._busy_lock:
            busy = self._busy
        return {
            "dry_run": s.dry_run,
            "connected": self.connected,
            "host": s.host,
            "user": s.user,
            "python_bin": s.python_bin,
            "left_motor": s.left_motor,
            "right_motor": s.right_motor,
            "max_speed": s.max_speed,
            "max_duration": s.max_duration,
            "skills_dir": str(s.skills_dir),
            "busy": busy,
            "last_error": self._last_error,
        }


_runner: SSHRunner | None = None


def get_runner() -> SSHRunner:
    global _runner
    if _runner is None:
        _runner = SSHRunner()
    return _runner
