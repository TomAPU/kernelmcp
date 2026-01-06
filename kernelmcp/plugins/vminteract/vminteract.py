from __future__ import annotations
import os
from typing import Optional, Dict, Any
from functools import wraps

from kernelmcp.plugins import McpPlugin, mcp_tool
from kernelmcp.infra import VMManage, RemoteGdbMI, SyzLogparser
from kernelmcp.infra.utils import compile_c_code


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
        vm_start(timeout_sec: int = 20) -> Dict[str, Any]
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
        self.vmoutput = self.config.data.get("vmoutput", "")
        self.gdboutput = self.config.data.get("gdboutput", "")

        if not ezqm_dir:
            self.logger.warning(
                f"{self.NAME} plugin: 'ezqmlocalfolder' not set; VM interaction disabled.")
            self.vm: Optional[VMManage] = None
            return

        try:
            self.vm = VMManage(
                promptstr=vmprompt, ezqmlocalfolder=ezqm_dir, logger=self.logger, vmoutput=self.vmoutput)
            self.logger.info(
                f"{self.NAME} plugin initialized with ezqm folder: {ezqm_dir}")
        except FileNotFoundError as e:
            self.logger.error(f"{self.NAME} initialization failed: {e}")
            self.vm = None
        except Exception as e:
            self.logger.error(f"{self.NAME} unexpected error during init: {e}")
            self.vm = None
        # GDB
        gdbport = self.vm.localconf.get("gdbport", 0)
        vmlinux = self.vm.localconf.get("vmlinux", "")
        if not gdbport or not vmlinux:
            self.logger.warning(
                f"{self.NAME} plugin: 'gdbport' or 'vmlinux' not set in ezqmlocal.json; GDB interaction disabled.")
        else:
            self.logger.info(
                f"{self.NAME} plugin initialized with GDB port: {gdbport} and vmlinux: {vmlinux}")



    @mcp_tool
    def try_crash_kernel(self, code: str, flags: Optional[list[str]] = None, timeout_sec: int = 20) -> Dict[str, Any]:
        """
        Use given C code to try to crash the kernel running in the virtual machine.
        This function starts a fresh VM, compile the provided C `code` with provided `flags`, upload it to the VM and
        execute it. When the execution finishes or times out, it retrieves the kernel log and parses.
        Returns a dict with the following schema:
        {
            "success": bool,          # True if a kernel crash was detected
            "reason": str,            # Human friendly reason message
            "crash_details": list     # Parsed crash details from syz-logparser (if success is True)
        }
        Args:
        code: C code string to compile and run inside the VM.
        flags: Optional list of gcc flags to use during compilation. Defaults to ["-static"].
        timeout_sec: Timeout in seconds for executing the uploaded binary inside the VM.
        """

        # Compile code
        if not flags:
            flags = ["-static"]

        res = compile_c_code(self.logger, code, flags=flags)
        if not res["success"]:
            return {"success": False, "reason": f"Compilation failed: {res['reason']}"}
        bin_path = res["binary_path"]
        assert bin_path is not None

        def cleanup_temp_binary() -> None:
            try:
                os.remove(bin_path)
            except FileNotFoundError:
                pass
            except Exception as e:
                self.logger.warning(
                    f"Failed to remove temp binary {bin_path}: {e}")

        try:
            # Restart VM to ensure clean state
            if not self.vm.restart_vm(timeout_sec=timeout_sec):
                return {
                    "success": False,
                    "reason": "Failed to restart the virtual machine",
                }
            
            # Upload binary
            up = self.vm.upload_file(bin_path, "/root/poc")
            if not up["success"]:
                return {"success": False, "reason": f"Upload failed: {up['reason']}"}
            
            # Execute binary
            try:
                self.vm.sendline("/root/poc")
            except Exception as e:
                self.logger.warning(f"Failed to execute uploaded binary: {e}")
                return {"success": False, "reason": f"Execution failed: {e}"}
            
            # Receive output
            try:
                output = self.vm.receive(timeout=timeout_sec)
            except Exception as e:
                self.logger.warning(f"Failed to receive output from binary execution: {e}")
                return {"success": False, "reason": f"Receive failed: {e}"}
        
            content = output.get("content", "")
            parsed = SyzLogparser(logger=self.logger, content=content).parse()
            if parsed:
                return {"success": True, "reason": "Kernel crash detected", "crash_details": parsed}
            else:
                return {"success": False, "reason": "No crash detected"}
        finally:
            cleanup_temp_binary()
