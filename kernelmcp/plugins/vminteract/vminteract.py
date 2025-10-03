from __future__ import annotations
import os
from typing import Optional, Dict, Any
from functools import wraps

from kernelmcp.plugins import McpPlugin, mcp_tool
from kernelmcp.infra.vm_manage import VMManage
from kernelmcp.infra.utils import compile_c_code


def ensure_vm():
    """Decorator to ensure the VMManage instance is initialized before using a tool.

    Raises:
        RuntimeError: if the plugin was not properly initialized (e.g. missing config).
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            self = args[0]
            if not getattr(self, 'vm', None):
                raise RuntimeError(
                    f"{self.NAME} plugin not properly initialized with ezqm project dir. Check 'ezqmlocalfolder' in config.")
            return func(*args, **kwargs)
        return wrapper
    return decorator


class VMInteractPlugin(McpPlugin):
    """Plugin that exposes basic virtual machine interaction capabilities via ezqm.

    This plugin wraps a lightweight VM process manager (`VMManage`) which internally
    uses `pexpect` to spawn and interact with the `ezqm` tool in a specified project
    directory. Only start functionality is exposed for now (mirroring the current
    requirements).

    Configuration (from `self.config.data`):
        ezqmlocalfolder (str): Path to a directory containing an `ezqmlocal.json` file.
            Required. If missing or empty, the plugin will not initialize the VM layer
            and subsequent tool calls will raise a RuntimeError.
        prompt (str, optional): Expected shell prompt string used to detect VM readiness.
            Defaults to "root@syzkaller" (consistent with VMManage default).

    Exposed tools:
        start_vm(timeout_sec: int = 20) -> Dict[str, Any]
            Start the VM if not already running and wait for the prompt.
            Returns a dict schema:
              {
                "started": bool,      # True if VM is running after the call
                "already": bool,      # True if VM was already running before this call
                "reached_prompt": bool,  # Result of waiting for prompt (only relevant if not already)
                "timeout_sec": int,
                "message": str        # human friendly status / warning
              }

    Notes:
        * All console output from the underlying VM is logged at info level through the
          main plugin logger (via VMManage's _LoggerWriter).
        * Errors are captured and surfaced in the returned message field while keeping
          the structure predictable for the MCP caller.
    """

    @property
    def NAME(self) -> str:
        return "VMInteract"

    def _post_init(self) -> None:
        # Read config
        ezqm_dir = self.config.data.get("ezqmlocalfolder", "").strip()
        vmprompt = self.config.data.get("vmprompt", "")

        if not ezqm_dir:
            self.logger.warning(
                f"{self.NAME} plugin: 'ezqmlocalfolder' not set; VM interaction disabled.")
            self.vm: Optional[VMManage] = None
            return

        try:
            self.vm = VMManage(
                promptstr=vmprompt, ezqmlocalfolder=ezqm_dir, logger=self.logger)
            self.logger.info(
                f"{self.NAME} plugin initialized with ezqm folder: {ezqm_dir}")
        except FileNotFoundError as e:
            self.logger.error(f"{self.NAME} initialization failed: {e}")
            self.vm = None
        except Exception as e:
            self.logger.error(f"{self.NAME} unexpected error during init: {e}")
            self.vm = None

    @mcp_tool
    @ensure_vm()
    def start_vm(self, timeout_sec: int = 20) -> Dict[str, Any]:
        """
        Start the virtual machine
        """
        return {"success": self.vm.start_vm(timeout_sec=timeout_sec)}

    @mcp_tool
    @ensure_vm()
    def restart_vm(self, timeout_sec: int = 20) -> Dict[str, Any]:
        """
        Restart the virtual machine. Use when the virtual machine is halted or unresponsive.
        """
        return {"success": self.vm.restart_vm(timeout_sec=timeout_sec)}

    @mcp_tool
    @ensure_vm()
    def exec_cmd(self, cmd: str, timeout_sec: int = 20) -> Dict[str, Any]:
        """
        Execute a command in the virtual machine and return its output.
        Use this tool only when the virtual machine is running.

        On success:
          {"success": True, "reason": "", "output": "<captured text>"}
        On no-VM:
          {"success": False, "reason": "The virtual machine is not started", "output": ""}
        On timeout:
          {"success": False, "reason": "Timeout", "output": "<partial text>"}
        """
        return self.vm.exec_cmd(cmd, timeout_sec=timeout_sec)

    @mcp_tool
    @ensure_vm()
    def compile_c_and_upload(self, code: str, dest_path: str, flags: Optional[list[str]] = None) -> Dict[str, Any]:
        """
        Compile given C code into a binary using gcc, then upload it to the virtual machine.
        Use this tool only when the virtual machine is running.
        - Creates a random subdir under /tmp.
        - Writes code into 'prog.c'.
        - Invokes gcc with optional `flags` (by default ["-static"]).
        - Uploads the binary to `dest_path` in the virtual machine.
        - Returns dict with {"success": bool, "reason": str}.
        """

        if not flags:
            flags = ["-static"]

        if not self.vm.is_running():
            return {
                "success": False,
                "reason": "The virtual machine is not started",
            }

        res = compile_c_code(self.logger, code, flags=flags)
        if not res["success"]:
            return {"success": False, "reason": f"Compilation failed: {res['reason']}"}
        bin_path = res["binary_path"]
        assert bin_path is not None

        up = self.vm.upload_file(bin_path, dest_path)
        try:
            os.remove(bin_path)
        except Exception as e:
            self.logger.warning(
                f"Failed to remove temp binary {bin_path}: {e}")

        if not up["success"]:
            return {"success": False, "reason": f"Upload failed: {up['reason']}"}
        return {"success": True, "reason": ""}
