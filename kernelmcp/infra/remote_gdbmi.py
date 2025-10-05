# Adapted from pwno-mcp
# Original source: https://github.com/pwno-io/pwno-mcp/blob/main/pwnomcp/gdb/controller.py
from __future__ import annotations
import logging
from typing import Dict, List, Optional, Union, Tuple, Any

from pygdbmi.gdbcontroller import GdbController


class RemoteGdbMI:
    """
    Kernel-friendly GDB/MI wrapper for remote TCP debugging.
    """

    def __init__(
        self,
        binary_path: str,
        port: Union[int, str],
        *,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.logger = logger or logging.getLogger(__name__)
        if not self.logger.handlers:
            self.logger.addHandler(logging.NullHandler())

        self.binary_path = binary_path
        self.port = str(port)
        self.hostport = f"127.0.0.1:{self.port}"
        self._connected = False
        self._state = "idle"

        self.gdb = GdbController(
            command=["gdb", "--nx", "--quiet", "--interpreter=mi2"])

        # Load symbols
        res = self.set_file(self.binary_path)
        if not res["ok"]:
            raise RuntimeError(
                f"Failed to load symbols: {res['error'] or 'unknown error'}")
        # Default settings
        for mi in ("-gdb-set pagination off", "-gdb-set confirm off", "-gdb-set mi-async on"):
            self._mi(mi, timeout_sec=2.0)

    # ---------- Primary API (unchanged) ----------

    def send(self, cmd: str, timeout_sec: float = 2.0) -> Dict[str, Any]:
        """Execute CLI command."""
        mi_cmd = f'-interpreter-exec console "{_escape_quotes(cmd)}"'
        return self._mi(mi_cmd, timeout_sec)

    def connect(self, timeout_sec: float = 3.0) -> Dict[str, Any]:
        """Connect to remote gdbserver at host:port."""
        if self._connected:
            raise RuntimeError("Already connected")
        res = self._mi(
            f"-target-select remote {self.hostport}", timeout_sec)
        if res["ok"]:
            self._connected, self._state = True, "stopped"
        else:
            self._connected = False
        return res

    def disconnect(self, timeout_sec: float = 2.0) -> Dict[str, Any]:
        res = self._mi("-target-detach", timeout_sec)
        self._connected, self._state = False, "idle"
        return res

    @property
    def is_connected(self) -> bool:
        return self._connected

    def close(self) -> None:
        try:
            if self._connected:
                self.disconnect()
        except Exception as e:
            self.logger.error(f"Error disconnecting: {e}")
        try:
            self.gdb.exit()
        except Exception as e:
            self.logger.error(f"Error exiting gdb: {e}")

    # ---------- Extra helpers ----------
    # fmt: off
    def continue_execution(self, *, timeout_sec: float = 2.0): return self._mi("-exec-continue", timeout_sec)
    def finish(self, *, timeout_sec: float = 2.0): return self._mi("-exec-finish", timeout_sec)
    def next(self, *, timeout_sec: float = 2.0): return self._mi("-exec-next", timeout_sec)
    def step(self, *, timeout_sec: float = 2.0): return self._mi("-exec-step", timeout_sec)
    def nexti(self, *, timeout_sec: float = 2.0): return self._mi("-exec-next-instruction", timeout_sec)
    def stepi(self, *, timeout_sec: float = 2.0): return self._mi("-exec-step-instruction", timeout_sec)
    def return_from_function(self, *, timeout_sec: float = 2.0): return self._mi("-exec-return", timeout_sec)
    def until(self, locspec: Optional[str] = None, *, timeout_sec: float = 2.0):
        return self._mi("-exec-until" if not locspec else f"-exec-until {locspec}", timeout_sec)

    def set_breakpoint(self, location: str, condition: Optional[str] = None, *, timeout_sec: float = 2.0):
        mi = f"-break-insert {location}" if not condition else f'-break-insert -c "{_escape_quotes(condition)}" {location}'
        return self._mi(mi, timeout_sec)
    def list_breakpoints(self, *, timeout_sec: float = 2.0): return self._mi("-break-list", timeout_sec)
    def delete_breakpoint(self, number: int, *, timeout_sec: float = 2.0): return self._mi(f"-break-delete {number}", timeout_sec)
    def enable_breakpoint(self, number: int, *, timeout_sec: float = 2.0): return self._mi(f"-break-enable {number}", timeout_sec)
    def disable_breakpoint(self, number: int, *, timeout_sec: float = 2.0): return self._mi(f"-break-disable {number}", timeout_sec)

    def evaluate_expression(self, expr: str, *, timeout_sec: float = 2.0):
        return self._mi(f'-data-evaluate-expression "{_escape_quotes(expr)}"', timeout_sec)
    def get_registers_mi(self, *, timeout_sec: float = 2.0): return self._mi("-data-list-register-values x", timeout_sec)
    def get_backtrace_mi(self, *, timeout_sec: float = 2.0): return self._mi("-stack-list-frames", timeout_sec)
    def get_disassembly_mi(self, length: int = 64, *, timeout_sec: float = 2.0):
        return self._mi(f"-data-disassemble -s $pc -e $pc+{length} -- 1", timeout_sec)
    def read_memory_mi(self, addr: str, fmt: str, size: int, rows: int, cols: int, *, timeout_sec: float = 2.0):
        return self._mi(f"-data-read-memory {addr} {fmt} {size} {rows} {cols}", timeout_sec)
    def read_memory_bytes(self, addr: str, n: int, *, timeout_sec: float = 2.0):
        return self._mi(f"-data-read-memory-bytes {addr} {n}", timeout_sec)
    def set_file(self, filepath: str, *, timeout_sec: float = 5.0):
        return self._mi(f"-file-exec-and-symbols {filepath}", timeout_sec)
    # fmt: on
    # ---------- Internals ----------

    def _mi(self, mi_cmd: str, timeout_sec: float) -> Dict[str, Any]:
        """Core send wrapper with logging + state tracking."""
        try:
            out = self.gdb.write(mi_cmd, timeout_sec=timeout_sec)
            self._ingest_state(out)
            ok, err = _evaluate_success(out)
            self.logger.debug(
                "cmd=%r, ok=%s, error=%s, records=%s", mi_cmd, ok, err, out)
            return {"ok": ok, "records": out, "error": err}
        except Exception as e:
            self.logger.error("_mi() error for %r: %s", mi_cmd, e)
            return {"ok": False, "records": [], "error": str(e)}

    def _ingest_state(self, records: List[Dict[str, Any]]) -> None:
        for r in records or []:
            if r.get("type") == "notify":
                if r.get("message") == "running":
                    self._state = "running"
                elif r["message"] == "stopped":
                    self._state = "stopped"
                elif r["message"] == "thread-group-exited":
                    self._state = "exited"

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


# ---------- Helpers ----------

def _evaluate_success(records: List[Dict]) -> Tuple[bool, Optional[str]]:
    ok, err, saw_result, saw_notify = False, None, False, False
    for r in records or []:
        t, msg, payload = r.get("type"), r.get("message"), r.get("payload")
        if t == "result":
            saw_result = True
            if msg in {"done", "connected", "running"}:
                ok = True
            elif msg == "error":
                err = payload.get("msg") if isinstance(
                    payload, dict) else str(payload or "error")
        elif t == "notify" and msg in {"running", "stopped", "thread-group-added"}:
            saw_notify = True
    if not saw_result:
        ok = True if saw_notify or not records else ok
    return ok, err


def _escape_quotes(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', r'\"')
