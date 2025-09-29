from __future__ import annotations
import logging
from typing import Dict, List, Optional, Union

from pygdbmi.gdbcontroller import GdbController


class RemoteGdbMI:
    """
    Minimal wrapper around pygdbmi for remote-GDB analysis.

    - Accepts a local symbols path (e.g., vmlinux or an executable) and a remote TCP port
      (connects to 127.0.0.1:<port>)
    - GDB (mi2) is started and symbols are loaded at initialization time
    - send(): accepts traditional GDB CLI (e.g., 'bt', 'break main') or MI ('-exec-continue')
      Returns a dict: { "ok": bool, "records": list[dict], "error": Optional[str] }
    - connect(force=False): connect to remote target via '-target-select remote 127.0.0.1:<port>'
      * if already connected and force=False -> no-op
      * if force=True -> detach if needed, then reconnect
    - disconnect(): detach from remote target via '-target-detach'
    """

    def __init__(self, symbols_path: str, port: Union[int, str], *, logger: Optional[logging.Logger] = None) -> None:
        self.logger = logger or logging.getLogger(__name__)
        if not self.logger.handlers:
            self.logger.addHandler(logging.NullHandler())

        self.symbols_path = symbols_path
        self.port = str(port)
        self.hostport = f"127.0.0.1:{self.port}"
        self._connected = False

        # Spawn GDB (MI2) using the built-in defaults
        args = ["gdb", "--nx", "--quiet", "--interpreter=mi2"]
        self.gdb = GdbController(command=args)

        # Load symbols
        # MI: -file-exec-and-symbols <path>
        self._bootstrap_symbols()

        # Optional QoL gdb settings (MI form)
        # Disable pagination, confirmations to avoid interactive prompts
        self._safe_mi("gdb-set pagination off")
        self._safe_mi("gdb-set confirm off")

    # ---------- Public API ----------

    def send(self, cmd: str, *, timeout_sec: float = 2.0) -> Dict:
        """
        Send a command to GDB.

        If cmd starts with '-', treat as MI command (e.g., '-exec-continue').
        Otherwise treat as traditional GDB CLI and tunnel via:
            -interpreter-exec console "<cmd>"

        Returns:
            {
              "ok": bool,
              "records": List[dict],   # pygdbmi-parsed records
              "error": Optional[str],
            }
        """
        try:
            if not cmd or not isinstance(cmd, str):
                return {"ok": False, "records": [], "error": "invalid command"}

            if cmd.lstrip().startswith("-"):
                # MI directly
                out = self.gdb.write(cmd, timeout_sec=timeout_sec)
            else:
                # Traditional CLI via MI
                # NB: properly quote the command for -interpreter-exec console
                tunneled = f'-interpreter-exec console "{_escape_quotes(cmd)}"'
                out = self.gdb.write(tunneled, timeout_sec=timeout_sec)

            ok, err = _evaluate_success(out)
            return {"ok": ok, "records": out, "error": err}
        except Exception as e:
            self.logger.debug("send() error for cmd=%r: %s", cmd, e)
            return {"ok": False, "records": [], "error": str(e)}

    def connect(self, *, force: bool = False, timeout_sec: float = 3.0) -> Dict:
        """
        Connect to the pre-specified remote target (127.0.0.1:<port>).

        - If already connected and force=False, it's a no-op and returns ok=True.
        - If force=True, it will try to detach first (ignore errors), then reconnect.
        """
        if self._connected and not force:
            return {"ok": True, "records": [], "error": None}

        if force and self._connected:
            try:
                self.disconnect(timeout_sec=timeout_sec)
            except Exception:
                # ignore detach errors on force
                pass

        # MI: -target-select remote <host:port>
        res = self.send(
            f"-target-select remote {self.hostport}", timeout_sec=timeout_sec)
        if res["ok"]:
            self._connected = True
        else:
            self._connected = False
        return res

    def disconnect(self, *, timeout_sec: float = 2.0) -> Dict:
        """
        Detach from the remote target.

        Uses MI: -target-detach
        """
        res = self.send("-target-detach", timeout_sec=timeout_sec)
        # Even if detach fails due to not connected, treat as disconnected afterwards
        self._connected = False
        return res

    @property
    def is_connected(self) -> bool:
        return self._connected

    def close(self) -> None:
        """Terminate GDB process."""
        try:
            # Try to detach politely if connected
            if self._connected:
                self.disconnect()
        except Exception:
            pass
        try:
            self.gdb.exit()
        except Exception:
            pass

    # ---------- Internals ----------

    def _bootstrap_symbols(self) -> None:
        res = self.send(
            f"-file-exec-and-symbols {self.symbols_path}", timeout_sec=5.0)
        if not res["ok"]:
            # Surface a clear error early
            raise RuntimeError(
                f"Failed to load symbols: {res['error'] or 'unknown error'}")

    def _safe_mi(self, mi_cmd: str) -> None:
        """Fire-and-forget MI settings; swallow errors."""
        try:
            if not mi_cmd.startswith("-"):
                mi_cmd = f"-{mi_cmd}"
            self.gdb.write(mi_cmd, timeout_sec=1.0)
        except Exception:
            pass

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


# ---------- Helpers ----------

def _evaluate_success(records: List[Dict]) -> tuple[bool, Optional[str]]:
    """
    Heuristic success detection:
    - If we saw any 'result' record with message in {'done', 'connected'} => success
    - If any 'result' record with message == 'error' => failure with msg
    - Else: if any async 'stopped' etc. w/o error -> still consider ok (many CLI cmds produce stream only)
    """
    ok = False
    err = None
    saw_any = False

    for r in records or []:
        rtype = r.get("type")
        msg = r.get("message")
        payload = r.get("payload")

        if rtype == "result":
            saw_any = True
            if msg in ("done", "connected"):
                ok = True
            elif msg == "error":
                ok = False
                # payload can be a string or dict with 'msg'
                if isinstance(payload, dict):
                    err = payload.get("msg") or str(payload)
                else:
                    err = str(payload) if payload is not None else "error"
                # keep scanning to gather more context, but error dominates
        # Streams ('~','@','&') and async ('*','=')
        # don't flip ok to True unless we saw a positive 'result', but we keep them for context.

    # If there was no explicit result record, treat as ok (console-only output is common)
    if not saw_any:
        ok = True
    return ok, err


def _escape_quotes(s: str) -> str:
    # Escape embedded double quotes for -interpreter-exec console "..."
    return s.replace('\\', '\\\\').replace('"', r'\"')
