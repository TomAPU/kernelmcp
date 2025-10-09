# Adapted from pwno-mcp
# Original source: https://github.com/pwno-io/pwno-mcp/blob/main/pwnomcp/gdb/controller.py
from __future__ import annotations
import logging
import copy
import threading
import time
from typing import Dict, List, Optional, Union, Tuple, Any, Callable

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

        # ---- internal async/event machinery (minimal, additive) ----
        self._io_lock = threading.RLock()
        self._cv = threading.Condition()
        self._bg_stop = threading.Event()
        self._event_callback: Optional[Callable[[Dict[str, Any]], None]] = None
        self._last_stop: Optional[Dict[str, Any]] = None

        self.gdb = GdbController(
            command=["gdb", "--quiet", "--interpreter=mi2"])

        # Load symbols
        res = self.set_file(self.binary_path)
        if not res["ok"]:
            raise RuntimeError(
                f"Failed to load symbols: {res['error'] or 'unknown error'}")
        # Default settings
        for mi in ("-gdb-set pagination off", "-gdb-set confirm off", "-gdb-set mi-async on"):
            self._mi(mi, timeout_sec=2.0)

        # start background poller to consume async MI notifications (e.g., *stopped)
        self._bg_thread = threading.Thread(
            target=self._bg_loop, name="RemoteGdbMI-bg", daemon=True
        )
        self._bg_thread.start()

    # ---------- Primary API (unchanged) ----------

    def send(self, cmd: str, timeout_sec: float = 2.0) -> Dict[str, Any]:
        """Execute CLI command."""
        return self._mi(cmd, timeout_sec)

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

    def get_state(self) -> str:
        """
        Get current state: "idle", "running", "stopped", "exited"
        """
        return self._state

    @property
    def is_connected(self) -> bool:
        return self._connected

    def close(self) -> None:
        try:
            self._bg_stop.set()
            try:
                if getattr(self, "_bg_thread", None):
                    self._bg_thread.join(timeout=1.0)
            except Exception as e:
                self.logger.error(f"Error stopping background thread: {e}")
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

    # ---------- New: wait_for_stop ----------
    def wait_for_stop(self, *, timeout_sec: Optional[float] = None) -> Dict[str, Any]:
        """
        Block until a '*stopped' async notify is observed (e.g., breakpoint hit/step/signal).
        Returns {"ok": True, "stop": {...}} or {"ok": False, "error": "timeout"}.
        """
        deadline = (time.monotonic() + timeout_sec) if timeout_sec is not None else None
        with self._cv:
            if self._state == "stopped" and self._last_stop is not None:
                return {"ok": True, "stop": copy.deepcopy(self._last_stop)}
            while True:
                remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
                if remaining == 0.0:
                    return {"ok": False, "error": "timeout"}
                self._cv.wait(timeout=remaining)
                if self._state == "stopped" and self._last_stop is not None:
                    return {"ok": True, "stop": copy.deepcopy(self._last_stop)}
                if deadline is not None and time.monotonic() >= deadline:
                    return {"ok": False, "error": "timeout"}

    # ---------- New: optional event callback ----------
    def set_event_callback(self, callback: Optional[Callable[[Dict[str, Any]], None]]) -> None:
        """
        Set a callback to receive async notify events.
        The callback will be invoked with a dict like:
          {"message": "stopped", "stop": {...}} |
          {"message": "running"} |
          {"message": "thread-group-exited", "payload": {...}}
        """
        self._event_callback = callback

    # ---------- Internals ----------

    def _mi(self, mi_cmd: str, timeout_sec: float) -> Dict[str, Any]:
        """Core send wrapper with logging + state tracking."""
        try:
            with self._io_lock:
                out = self.gdb.write(mi_cmd, timeout_sec=timeout_sec)
            self._ingest_state(out)
            ok, err = _evaluate_success(out)
            response = ""
            for r in out:
                if "payload" in r and isinstance(r.get("payload"), str):
                    response += r["payload"]
            self.logger.debug(
                "cmd=%r, ok=%s, error=%s, records=%s", mi_cmd, ok, err, out)
            return {"ok": ok, "response": response, "error": err}
        except Exception as e:
            self.logger.error("_mi() error for %r: %s", mi_cmd, e)
            return {"ok": False, "response": "", "error": str(e)}

    def _ingest_state(self, records: List[Dict[str, Any]]) -> None:
        for r in records or []:
            if r.get("type") == "notify":
                msg = r.get("message")
                if msg == "running":
                    with self._cv:
                        self._state = "running"
                        self._cv.notify_all()
                    self._fire_event({"message": "running"})
                elif msg == "stopped":
                    stop = _parse_stopped_record(r)
                    with self._cv:
                        self._state = "stopped"
                        self._last_stop = stop
                        self._cv.notify_all()
                    self._fire_event({"message": "stopped", "stop": stop})
                elif msg == "thread-group-exited":
                    with self._cv:
                        self._state = "exited"
                        self._cv.notify_all()
                    self._fire_event({"message": "thread-group-exited", "payload": r.get("payload")})

    def _fire_event(self, evt: Dict[str, Any]) -> None:
        cb = self._event_callback
        if cb:
            try:
                cb(copy.deepcopy(evt))
            except Exception as e:
                self.logger.error(f"event callback error: {e}")

    def _bg_loop(self) -> None:
        """Background poller for async MI notifications."""
        while not self._bg_stop.is_set():
            try:
                with self._io_lock:
                    out = self.gdb.get_gdb_response(timeout_sec=0.1, raise_error_on_timeout=False)
                if out:
                    self._ingest_state(out)
            except Exception as e:
                self.logger.error(f"Background poll error: {e}")
                time.sleep(0.1)

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


def _parse_stopped_record(rec: Dict[str, Any]) -> Dict[str, Any]:
    """Extract useful fields from a '*stopped' notify record."""
    p = rec.get("payload") or {}
    stop: Dict[str, Any] = {
        "reason": p.get("reason"),
        "thread_id": p.get("thread-id"),
        "stopped_threads": p.get("stopped-threads"),
        "core": p.get("core"),
    }
    if "bkptno" in p:
        try:
            bkptno = int(p.get("bkptno"))
        except Exception:
            bkptno = p.get("bkptno")
        stop["breakpoint"] = {"number": bkptno, "locno": p.get("locno")}
    if isinstance(p.get("frame"), dict):
        stop["frame"] = p["frame"]
    if "signal-name" in p or "signal-meaning" in p:
        stop["signal"] = {"name": p.get("signal-name"), "meaning": p.get("signal-meaning")}
    return stop
