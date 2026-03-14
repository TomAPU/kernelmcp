from __future__ import annotations
import os
from typing import Optional, Dict, Any
from functools import wraps

from kernelmcp.plugins import McpPlugin, mcp_tool
from kernelmcp.infra import VMManage, RemoteGdbMI
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


def ensure_gdb():
    """Decorator to ensure the RemoteGdbMI instance is initialized before using a tool.

    Raises:
        RuntimeError: if the plugin was not properly initialized (e.g. missing config).
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            self = args[0]
            if not getattr(self, 'gdb', None):
                raise RuntimeError(
                    f"{self.NAME} plugin not properly initialized with gdb parameters. Check 'gdbport' and 'vmlinux' in ezqmlocal.json.")
            return func(*args, **kwargs)
        return wrapper
    return decorator


def ensure_vm_not_halted():
    """
    Decorator to ensure the VM is running before using a tool.
    This is done by checking if self.gdb is not in a halted state such as 'stopped'.
    If GDB is not connected, we assume the VM is running.
    This time, we don't do a raise but return a dict with success=False and a reason.
    """
    halted_states = {'stopped'}

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            self: VMInteractPlugin = args[0]
            if getattr(self, 'gdb', None) and self.gdb.get_state()["status"] in halted_states:
                return {
                    "success": False,
                    "reason": "The virtual machine is halted. You may want to resume it in GDB before using this tool.",
                }
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

        if not ezqm_dir:
            self.logger.warning(
                f"{self.NAME} plugin: 'ezqmlocalfolder' not set; VM interaction disabled.")
            self.vm: Optional[VMManage] = None
            return

        try:
            self.vm = VMManage(
                promptstr=vmprompt,
                ezqmlocalfolder=ezqm_dir,
                logger=self.logger,
            )
            self.vmoutput = self.vm.runtime_artifact("vmoutput")
            self.gdboutput = self.vm.runtime_artifact("gdboutput")
            self.logger.info(
                f"{self.NAME} plugin initialized with ezqm folder: {ezqm_dir} (runtime: {self.vm.project_dir})")
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
            self.gdb = RemoteGdbMI(binary_path=vmlinux,
                                   port=gdbport, gdboutput=self.gdboutput, logger=self.logger)

    @mcp_tool
    @ensure_vm()
    def vm_start(self, timeout_sec: int = 20) -> Dict[str, Any]:
        """
        Start the virtual machine
        """
        return {"success": self.vm.start_vm(timeout_sec=timeout_sec)}

    @mcp_tool
    @ensure_vm()
    def vm_restart(self, timeout_sec: int = 20) -> Dict[str, Any]:
        """
        Restart the virtual machine. Use when the virtual machine is halted or unresponsive.
        """
        if not self.vm.restart_vm(timeout_sec=timeout_sec):
            return {
                "success": False,
                "reason": "Failed to restart the virtual machine",
            }
        # Delete and recreate GDB instance to reset connection state
        if getattr(self, 'gdb', None):
            del self.gdb
            gdbport = self.vm.localconf.get("gdbport", 0)
            vmlinux = self.vm.localconf.get("vmlinux", "")
            self.gdb = RemoteGdbMI(binary_path=vmlinux,
                                   port=gdbport, gdboutput=self.gdboutput, logger=self.logger)
        return {"success": True, "reason": ""}

    # It turns out that vm_exec_cmd is too high-level for many scenarios,
    # especially when debugging kernels with breakpoints. And despite I asked LLM to use it only
    # for simple commands, it always tried to use it for everything.
    # So I am deprecating it in favor of vm_sendline + vm_receive, which is lower-level and more flexible.
    # @mcp_tool
    @ensure_vm()
    @ensure_vm_not_halted()
    def vm_exec_cmd(self, cmd: str, timeout_sec: int = 20) -> Dict[str, Any]:
        """
        Execute a command in the virtual machine and return its output.
        This is a high-level primitive, more suitable for simple commands (such as viewing configurations in the virtual machine via cat).
        DO NOT use this tool for interactive commands (such as vim, top, etc) or commands that may hit breakpoints.
        Use this tool only when the virtual machine is running.
        """
        # Blacklist commands that contains "\n"
        if "\n" in cmd:
            return {
                "success": False,
                "reason": "Multi-line commands are not supported. Please use vm_sendline and vm_receive for interactive commands.",
            }
        return self.vm.exec_cmd(cmd, timeout_sec=timeout_sec)

    @mcp_tool
    @ensure_vm()
    @ensure_vm_not_halted()
    def vm_sendline(self, line: str) -> Dict[str, Any]:
        """
        Send a line to the VM console without waiting for a prompt.
        This is a low-level primitive, and more suitable to be used in kernel debugging scenarios 
        (where breakpoints might hit, so you need to send without receiving and then play with GDB).
        Use this tool only when the virtual machine is running.
        """
        try:
            self.vm.sendline(line)
            return {"success": True, "reason": ""}
        except Exception as e:
            return {"success": False, "reason": str(e)}

    @mcp_tool
    @ensure_vm()
    @ensure_vm_not_halted()
    def vm_sendcontrol(self, char: str) -> Dict[str, Any]:
        """
        Send a control character (such as 'c' as Ctrl-C) to the VM console without waiting for a prompt.
        This is a low-level primitive; prefer this for kernel/debug scenarios where
        you need to poke the console regardless of prompt state.
        Use this tool only when the virtual machine is running.
        """
        try:
            # Delegate to the underlying method you already have.
            self.vm.sendcontrol(char)
            return {"success": True, "reason": ""}
        except Exception as e:
            return {"success": False, "reason": str(e)}

    @mcp_tool
    @ensure_vm()
    @ensure_vm_not_halted()
    def vm_receive(self, timeout: int = 1) -> Dict[str, Any]:
        """
        Takes only a timeout as the parameter (in seconds).
        Read text from the VM console until one of these happens:
        1. The prompt "root@syzkaller" appears.
        2. The total wait time reaches `timeout` seconds (prompt not seen).

        Returns:
            {
                "success": bool,      # True if reading succeeded (both "root@syzkaller" appears and timeout are regarded as success).
                "content": str,       # All text read from the console.
                "reason": str         # "Meets prompt", "Timeout", or an error message.
            }
        """
        try:
            # Your existing method returns {"content": str, "reason": "..."}.
            res = self.vm.receive(timeout=timeout)
            # Normalize to include a success flag.
            return {
                "success": True,
                "content": res.get("content", ""),
                "reason": res.get("reason", ""),
            }
        except Exception as e:
            return {
                "success": False,
                "content": "",
                "reason": str(e),
            }

    @mcp_tool
    @ensure_vm()
    @ensure_vm_not_halted()
    def vm_compile_c_and_upload(self, code: str, dest_path: str, flags: Optional[list[str]] = None) -> Dict[str, Any]:
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

    # ---------- GDB-related tools ----------
    @mcp_tool
    @ensure_gdb()
    def gdb_get_vm_state(self) -> Dict[str, Any]:
        """
        Leverage GDB to get the current state of the virtual machine.
        Returns: {"status": str, "note": Optional[Dict]} 
        where status is one of "idle", "running", "stopped", "exited". (If "stopped", note contains details about the stop. Likely a breakpoint hit.)
        """
        return self.gdb.get_state()

    @mcp_tool
    @ensure_gdb()
    def gdb_connect(self, timeout_sec: float = 3.0) -> Dict[str, Any]:
        """
        Make the GDB connected to the virtual machine.
        Once connected, the virtual machine is halted.
        """
        return self.gdb.connect(timeout_sec=timeout_sec)

    @mcp_tool
    @ensure_gdb()
    def gdb_execute(self, cmd: str, timeout_sec: float = 2.0) -> Dict[str, Any]:
        """
        Execute a GDB command and return the result.
        """
        return self.gdb.send(cmd, timeout_sec=timeout_sec)

    @mcp_tool
    @ensure_gdb()
    def gdb_wait_for_stop(self, timeout_sec: float = 5.0) -> Dict[str, Any]:
        """
        Block until a '*stopped' async notify is observed (e.g., breakpoint hit/step/signal).
        Returns {"ok": True, "stop": {...}} or {"ok": False, "error": "timeout"}.
        This is super useful after sendline() or continue_execution() to wait until the VM halts.
        """
        return self.gdb.wait_for_stop(timeout_sec=timeout_sec)
    # ---------- GDB MI execution control ----------

    @mcp_tool
    @ensure_gdb()
    def gdb_continue(self, timeout_sec: float = 2.0) -> Dict[str, Any]:
        """Continue program execution until a breakpoint or program exit."""
        return self.gdb.continue_execution(timeout_sec=timeout_sec)

    @mcp_tool
    @ensure_gdb()
    def gdb_finish(self, timeout_sec: float = 2.0) -> Dict[str, Any]:
        """Run until the current function returns."""
        return self.gdb.finish(timeout_sec=timeout_sec)

    @mcp_tool
    @ensure_gdb()
    def gdb_next(self, timeout_sec: float = 2.0) -> Dict[str, Any]:
        """Step over the next source line."""
        return self.gdb.next(timeout_sec=timeout_sec)

    @mcp_tool
    @ensure_gdb()
    def gdb_step(self, timeout_sec: float = 2.0) -> Dict[str, Any]:
        """Step into the next source line."""
        return self.gdb.step(timeout_sec=timeout_sec)

    @mcp_tool
    @ensure_gdb()
    def gdb_nexti(self, timeout_sec: float = 2.0) -> Dict[str, Any]:
        """Step over the next instruction."""
        return self.gdb.nexti(timeout_sec=timeout_sec)

    @mcp_tool
    @ensure_gdb()
    def gdb_stepi(self, timeout_sec: float = 2.0) -> Dict[str, Any]:
        """Step into the next instruction."""
        return self.gdb.stepi(timeout_sec=timeout_sec)

    @mcp_tool
    @ensure_gdb()
    def gdb_return(self, timeout_sec: float = 2.0) -> Dict[str, Any]:
        """Return from the current function."""
        return self.gdb.return_from_function(timeout_sec=timeout_sec)

    @mcp_tool
    @ensure_gdb()
    def gdb_until(self, locspec: Optional[str] = None, timeout_sec: float = 2.0) -> Dict[str, Any]:
        """Continue execution until the specified location (or next line if not provided)."""
        return self.gdb.until(locspec, timeout_sec=timeout_sec)

    # ---------- GDB MI breakpoints ----------
    @mcp_tool
    @ensure_gdb()
    def gdb_break_insert(self, location: str, condition: Optional[str] = None, timeout_sec: float = 2.0) -> Dict[str, Any]:
        """Insert a new breakpoint at the given location, optionally with a condition."""
        return self.gdb.set_breakpoint(location, condition, timeout_sec=timeout_sec)

    @mcp_tool
    @ensure_gdb()
    def gdb_break_list(self, timeout_sec: float = 2.0) -> Dict[str, Any]:
        """List all breakpoints."""
        return self.gdb.list_breakpoints(timeout_sec=timeout_sec)

    @mcp_tool
    @ensure_gdb()
    def gdb_break_delete(self, number: int, timeout_sec: float = 2.0) -> Dict[str, Any]:
        """Delete a specific breakpoint by number."""
        return self.gdb.delete_breakpoint(number, timeout_sec=timeout_sec)

    @mcp_tool
    @ensure_gdb()
    def gdb_break_enable(self, number: int, timeout_sec: float = 2.0) -> Dict[str, Any]:
        """Enable a specific breakpoint."""
        return self.gdb.enable_breakpoint(number, timeout_sec=timeout_sec)

    @mcp_tool
    @ensure_gdb()
    def gdb_break_disable(self, number: int, timeout_sec: float = 2.0) -> Dict[str, Any]:
        """Disable a specific breakpoint."""
        return self.gdb.disable_breakpoint(number, timeout_sec=timeout_sec)

    # ---------- GDB MI data inspection ----------
    @mcp_tool
    @ensure_gdb()
    def gdb_eval(self, expr: str, timeout_sec: float = 2.0) -> Dict[str, Any]:
        """Evaluate an expression in the current context."""
        return self.gdb.evaluate_expression(expr, timeout_sec=timeout_sec)

    @mcp_tool
    @ensure_gdb()
    def gdb_registers(self, timeout_sec: float = 2.0) -> Dict[str, Any]:
        """List all registers and their values."""
        return self.gdb.get_registers_mi(timeout_sec=timeout_sec)

    @mcp_tool
    @ensure_gdb()
    def gdb_backtrace(self, timeout_sec: float = 2.0) -> Dict[str, Any]:
        """Get the current backtrace (stack frames)."""
        return self.gdb.get_backtrace_mi(timeout_sec=timeout_sec)

    @mcp_tool
    @ensure_gdb()
    def gdb_disasm(self, length: int = 64, timeout_sec: float = 2.0) -> Dict[str, Any]:
        """Disassemble instructions around the current PC."""
        return self.gdb.get_disassembly_mi(length=length, timeout_sec=timeout_sec)

    @mcp_tool
    @ensure_gdb()
    def gdb_read_mem(self, addr: str, fmt: str, size: int, rows: int, cols: int, timeout_sec: float = 2.0) -> Dict[str, Any]:
        """Read memory in the given format."""
        return self.gdb.read_memory_mi(addr, fmt, size, rows, cols, timeout_sec=timeout_sec)

    @mcp_tool
    @ensure_gdb()
    def gdb_read_mem_bytes(self, addr: str, n: int, timeout_sec: float = 2.0) -> Dict[str, Any]:
        """Read raw memory bytes."""
        return self.gdb.read_memory_bytes(addr, n, timeout_sec=timeout_sec)
