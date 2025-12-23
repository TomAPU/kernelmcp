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
        gdboutput: str = "",
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
        # Lock to protect GDB I/O and _ingest_state function
        self._io_lock = threading.Lock()
        # Condition for efficient wait_for_stop change
        self.stop_condition = threading.Condition()
        # Lock to protect internal _state, last_stop, connected
        # Inside this lock, do NOT perform any complex tasks, just read/write state and unlock
        self._state_lock = threading.Lock()
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

        # Disable colors
        self.send("gef config gef.disable_color True", timeout_sec=2.0)

        # GDB logging
        if gdboutput:
            self.send(f"set logging file {gdboutput}", timeout_sec=2.0)
            self.send("set logging overwrite on", timeout_sec=2.0)
            self.send("set trace-commands on", timeout_sec=2.0)
            self.send("set logging on", timeout_sec=2.0)

        # Start background poller to consume async MI notifications (e.g., *stopped)
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
        ok = res["ok"]
        with self._state_lock:
            if ok:
                self._connected, self._state, self._last_stop = True, "stopped", None
            else:
                self._connected, self._state, self._last_stop = False, "idle", None
        with self.stop_condition:
            self.stop_condition.notify_all()
        return res

    def disconnect(self, timeout_sec: float = 2.0) -> Dict[str, Any]:
        res = self._mi("-target-detach", timeout_sec)
        with self._state_lock:
            self._connected, self._state, self._last_stop = False, "idle", None
        with self.stop_condition:
            self.stop_condition.notify_all()
        return res

    def get_state(self) -> Dict[str, Any]:
        """
        Get current state: "idle", "running", "stopped", "exited"
        """
        with self._state_lock:
            state = self._state
            if state == "stopped":
                return {"status": state, "note": copy.deepcopy(self._last_stop)}
            return {"status": state, "note": None}

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

    def wait_for_stop(self, *, timeout_sec: Optional[float] = None) -> Dict[str, Any]:
        """
        Block until a '*stopped' async notify is observed (e.g., breakpoint hit/step/signal).
        Returns {"ok": True, "stop": {...}} or {"ok": False, "error": "timeout"}.
        """

        def check_state():
            """
            Check if state is already stopped/exited.
            If so, return True, {...} (which is the return message for wait_for_stop).
            Otherwise return False, state_string.
            """
            with self._state_lock:
                if self._state == "stopped":
                    return True, {"ok": True, "stop": copy.deepcopy(self._last_stop)}
                if self._state == "exited":
                    return True, {"ok": False, "error": "target exited"}
                if self._state == "idle":
                    return True, {"ok": False, "error": "target idle, we may be disconnected from the target"}
                return False, self._state

        with self.stop_condition:
            # Check if already stopped, if so return immediately
            ok_to_return, state_or_msg = check_state()
            if ok_to_return:
                return state_or_msg
            # Ok we have to wait for a stop condition
            ret = self.stop_condition.wait(timeout=timeout_sec)
            if ret:
                ok_to_return, state_or_msg = check_state()
                if ok_to_return:
                    return state_or_msg
                self.logger.error(
                    "wait_for_stop: state should be stopped but end up being state=%r", self._state)
                return {"ok": False, "error": "state should be stopped/exited but reported as %r" % self._state}
            else:
                return {"ok": False, "error": "timeout"}

    # ---------- Internals ----------

    def _mi(self, mi_cmd: str, timeout_sec: float) -> Dict[str, Any]:
        """Core send wrapper with logging + state tracking."""
        try:
            with self._io_lock:
                out = self.gdb.write(mi_cmd, timeout_sec=timeout_sec)
                self._ingest_state(out)
            ok, err = _evaluate_success(out)
            response = ""
            additional_info = []
            for r in out:
                if not r.get("payload"):
                    continue
                if isinstance(r.get("payload"), str):
                    response += r["payload"]
                elif isinstance(r.get("payload"), dict):
                    additional_info.append(copy.deepcopy(r["payload"]))
                else:
                    self.logger.error(
                        "Unexpected payload type for _mi payload: %r", r)
            self.logger.debug(
                "cmd=%r, ok=%s, error=%s, records=%s additional_info=%s", mi_cmd, ok, err, out, additional_info)
            return {"ok": ok, "response": response, "error": err, "additional_info": additional_info}
        except Exception as e:
            self.logger.error("_mi() error for %r: %s", mi_cmd, e)
            return {"ok": False, "response": "", "error": str(e)}

    def _ingest_state(self, records: List[Dict[str, Any]]) -> None:
        for r in records or []:
            if r.get("type") == "notify":
                msg = r.get("message")
                if msg == "running":
                    with self._state_lock:
                        self._state = "running"
                elif msg == "stopped":
                    stop = _parse_stopped_record(r)
                    _connected = True
                    state = "stopped"
                    # List from https://sourceware.org/gdb/current/onlinedocs/gdb.html/GDB_002fMI-Async-Records.html
                    if "reason" in stop and stop["reason"] in ("exited-normally", "exited", "exited-signalled"):
                        _connected = False
                        state = "exited"
                    with self.stop_condition:
                        with self._state_lock:
                            self._state = state
                            self._last_stop = stop
                            self._connected = _connected
                        self.stop_condition.notify_all()
                elif msg == "thread-group-exited":
                    with self.stop_condition:
                        with self._state_lock:
                            self._state = "exited"
                            self._last_stop = None
                            self._connected = False
                        self.stop_condition.notify_all()
                elif msg in {"thread-group-added", "cmd-param-changed"}:
                    # Ignore for now
                    pass
                else:
                    self.logger.error(
                        "Unhandled notify message: %r", msg)

    def _bg_loop(self) -> None:
        """Background poller for async MI notifications."""
        while not self._bg_stop.is_set():
            try:
                with self._io_lock:
                    out = self.gdb.get_gdb_response(
                        timeout_sec=0.1, raise_error_on_timeout=False)
                    if out:
                        self._ingest_state(out)
            except Exception as e:
                self.logger.error(f"Background poll error: {e}")
            # Avoid busy loop
            time.sleep(0.5)

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
        stop["signal"] = {"name": p.get(
            "signal-name"), "meaning": p.get("signal-meaning")}
    return stop
