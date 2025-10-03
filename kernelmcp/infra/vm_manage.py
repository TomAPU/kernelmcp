# vm_manage.py
from __future__ import annotations
import json
import logging
import re
import time
from pathlib import Path
from typing import Optional, Dict, Any, List

import pexpect

from .utils import run_cmd


class _LoggerWriter:
    """File-like object that forwards writes to logger.info(line)."""

    def __init__(self, logger: logging.Logger):
        self.logger = logger
        self._buf = ""

    def write(self, s: str) -> None:
        # Buffer by lines to avoid spamming partial fragments
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            # Preserve raw VM output lines at info level for later inspection
            self.logger.info(line)

    def flush(self) -> None:
        if self._buf:
            self.logger.info(self._buf)
            self._buf = ""


class VMManage:
    """Lightweight wrapper for running/controlling ezqm-based VMs.

    API:
      - start_vm(timeout_sec: int = 20) -> bool
      - stop_vm() -> None
      - restart_vm(timeout_sec: int = 20) -> bool
      - exec_cmd(cmd: str, timeout_sec: int = 20) -> Dict[str, Any]
      - upload_file(host_path: str, vm_path: str, timeout: Optional[int] = None) -> Dict[str, Any]

    Notes:
      - We keep the running ezqm process in self._child (pexpect).
      - All VM console output is logged at logger.info.
      - We consider VM "ready" when the expected prompt string appears.
    """

    def __init__(
        self,
        promptstr: str = "",
        ezqmlocalfolder: str = ".",
        logger: Optional[logging.Logger] = None,
    ):
        self.prompt: str = promptstr
        if not self.prompt:
            self.prompt = "root@syzkaller"

        self.project_dir: Path = Path(ezqmlocalfolder).resolve()
        self.logger = logger or logging.getLogger(__name__)
        self.start_marker = "__EZQM_CMD_START__"
        self.stop_marker = "__EZQM_CMD_END__"
        if not self.logger.handlers:
            self.logger.addHandler(logging.NullHandler())

        # Validate local config presence
        self.localconf_path = self.project_dir / "ezqmlocal.json"
        if not self.localconf_path.exists():
            raise FileNotFoundError(
                f"ezqmlocal.json not found in {self.project_dir}")

        with open(self.localconf_path, "r", encoding="utf-8") as f:
            self.localconf: Dict[str, Any] = json.load(f)

        # pexpect child (ezqm) holder
        self._child: Optional[pexpect.spawnbase.SpawnBase] = None

        # Precompile prompt regex (literal match of promptstr)
        self._prompt_re = re.compile(re.escape(self.prompt))

    # ---------- helpers ----------
    def _is_running(self) -> bool:
        return self._child is not None and self._child.isalive()

    def _spawn_ezqm(self) -> None:
        """Spawn ezqm in project_dir and set up logging."""
        # Use a login shell via bash -lc to ensure PATH and aliases behave normally.
        # pexpect handles PTY and interactive flows nicely for ezqm.
        self.logger.debug("Spawning ezqm under %s", self.project_dir)
        child = pexpect.spawn(
            "ezqm",
            cwd=str(self.project_dir),
            encoding="utf-8",
            echo=False,
        )
        # Wire VM output to logger.info
        child.logfile = _LoggerWriter(self.logger)
        self._child = child

    def _wait_for_prompt(self, timeout_sec: int) -> bool:
        """Pump newlines and wait until prompt appears or timeout."""
        if not self._is_running():
            return False

        child = self._child
        assert child is not None

        # Try quickly first without sending, in case prompt is already there
        try:
            idx = child.expect([self._prompt_re], timeout=1)
            if idx == 0:
                return True
        except pexpect.TIMEOUT:
            pass
        except pexpect.EOF:
            return False

        # Then keep nudging with empty lines
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            try:
                child.sendline("")  # nudge VM console
            except Exception:
                return False
            try:
                idx = child.expect([self._prompt_re], timeout=1)
                if idx == 0:
                    return True
            except pexpect.TIMEOUT:
                # keep looping
                pass
            except pexpect.EOF:
                return False
        return False

    def _drain_output(self):
        """Drain any pending output from the child to avoid mixing old text."""
        if not self._is_running():
            return
        child = self._child
        assert child is not None
        try:
            while True:
                _ = child.read_nonblocking(size=4096, timeout=0)
        except pexpect.TIMEOUT:
            pass
        except pexpect.EOF:
            pass

    # ---------- public API ----------

    def start_vm(self, timeout_sec: int = 20) -> bool:
        """Start ezqm, keep the process, and wait for shell prompt."""
        if self._is_running():
            self.logger.debug("VM already running; start_vm() is a no-op.")
            return True

        self._spawn_ezqm()

        ok = self._wait_for_prompt(timeout_sec)
        if not ok:
            self.logger.warning(
                "VM did not reach prompt within %ds", timeout_sec)
        else:
            self.logger.debug("VM reached prompt: %s", self.prompt)
        # These two executions should not be used with self.exec_cmd()
        # Execute bind 'set enable-bracketed-paste off' to avoid ANSI strange characters
        self._child.sendline("bind 'set enable-bracketed-paste off'")
        # Execute stty -echo to disable local echoing of input characters
        self._child.sendline("stty -echo")
        # Sleep 3 second
        time.sleep(3)
        # Drain any pending output
        self._drain_output()
        return ok

    def stop_vm(self) -> None:
        """Terminate the running ezqm process if alive."""
        if not self._is_running():
            self.logger.debug("stop_vm(): VM is not running; nothing to do.")
            self._child = None
            return

        child = self._child
        assert child is not None
        try:
            self.logger.debug("Stopping VM (terminate).")
            child.terminate(force=True)
        except Exception as e:
            self.logger.debug("Ignoring exception during terminate: %s", e)
        finally:
            self._child = None

    def restart_vm(self, timeout_sec: int = 20) -> bool:
        """Stop then start the VM."""
        self.stop_vm()
        return self.start_vm(timeout_sec=timeout_sec)

    def exec_cmd(self, cmd: str, timeout_sec: int = 20) -> Dict[str, Any]:
        """Execute a command on the VM; return dict with success, reason, output.

        On success:
          {"success": True, "reason": "", "output": "<captured text>"}
        On no-VM:
          {"success": False, "reason": "The virtual machine is not started", "output": ""}
        On timeout:
          {"success": False, "reason": "Timeout", "output": "<partial text>"}
        """
        if not self._is_running():
            return {
                "success": False,
                "reason": "The virtual machine is not started",
                "output": "",
            }

        child = self._child
        assert child is not None
        # Drain the pending output to avoid logging the command into output
        self._drain_output()
        try:
            # Execute the command prefixed by an echo of a unique marker
            child.sendline(
                f"echo {self.start_marker};{cmd}; echo {self.stop_marker}")
        except Exception as e:
            return {"success": False, "reason": f"send failed: {e}", "output": ""}

        try:
            child.expect(self.stop_marker, timeout=timeout_sec)
            # child.before includes the command echo and command output; strip the echo line if present
            out = child.before or ""
            # Trim self.start_marker line if present without using regex
            out = out.split(self.start_marker, 1)[-1].lstrip("\r\n")
            # Normalize: remove a leading echoed command line if it matches
            lines = out.splitlines()
            if lines and lines[0].strip() == cmd.strip():
                lines = lines[1:]
            output_text = "\n".join(lines).rstrip()
            return {"success": True, "reason": "", "output": output_text}
        except pexpect.TIMEOUT:
            # Best-effort partial output
            partial = child.before or ""
            return {"success": False, "reason": "Timeout", "output": partial}
        except pexpect.EOF:
            partial = child.before or ""
            return {"success": False, "reason": "EOF", "output": partial}

    def upload_file(
        self,
        host_path: str,
        vm_path: str,
        timeout: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Upload a file/dir from host to VM using ezcp in the local project dir.

        Returns:
          {"success": True, "reason": ""} on success
          {"success": False, "reason": "<errmsg>"} on failure (raised by run_cmd)
        """
        try:
            _ = run_cmd(
                ["ezcp", host_path, vm_path],
                logger=self.logger,
                timeout=timeout,
                cwd=self.project_dir,
            )
            return {"success": True, "reason": ""}
        except Exception as e:
            # run_cmd raises RuntimeError(errmsg) on failure
            return {"success": False, "reason": str(e)}
