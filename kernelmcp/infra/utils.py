from __future__ import annotations
import uuid
import os
import subprocess
import time
import logging
from pathlib import Path
from typing import List, Optional
import shlex
import socket


def run_cmd(
    cmd: List[str],
    logger: Optional[logging.Logger] = None,
    timeout: Optional[int] = None,
    cwd: Optional[Path] = None,
) -> str:
    """
    Run a command via subprocess.run and return stdout (str).

    - `cmd` should be a list of program and args (no shell).
    - `timeout` forwards to subprocess.run and will raise subprocess.TimeoutExpired.
    - On non-zero exit we raise RuntimeError with stderr/stdout content.
    - Logging mirrors the style used across the infra package.
    """
    if logger is None:
        logger = logging.getLogger(__name__)
        logger.addHandler(logging.NullHandler())

    cwd_display = str(cwd) if cwd else None
    logger.debug("Exec: %s (cwd=%s, timeout=%s)",
                 shlex.join(cmd), cwd_display, timeout)
    t0 = time.time()
    proc = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    dt = time.time() - t0

    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        logger.error("Non-zero exit rc=%d (%.2fs)\n---- stderr/stdout ----\n%s",
                     proc.returncode, dt, err)
        raise RuntimeError(err)

    out = (proc.stdout or "").strip()
    logger.debug("OK rc=0 (%.2fs), stdout_lines=%d", dt, len(out.splitlines()))
    return out


def get_unused_port(host: str = "127.0.0.1") -> int:
    """
    Return an unused TCP port on the local machine bound to `host`.

    This function opens a temporary socket bound to (host, 0) so the OS
    assigns a free port, reads that port number, then closes the socket and
    returns the port. Note that there is a small race window between closing
    the socket and the caller actually binding to the port, but this is the
    standard portable approach for finding an ephemeral free port.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        # Allow immediate reuse (not strictly necessary here) and bind to port 0
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((host, 0))
        return s.getsockname()[1]


def compile_c_code(
    logger: Optional[logging.Logger],
    code: str,
    flags: Optional[List[str]] = None,
) -> dict:
    """
    Compile given C code into a binary using gcc.

    - Creates a random subdir under /tmp.
    - Writes code into 'prog.c'.
    - Invokes gcc with optional `flags`.
    - Deletes the .c file after compilation, leaving only the binary.
    - Returns dict with {"success": bool, "binary_path": str|None, "reason": str}.
    """
    if logger is None:
        logger = logging.getLogger(__name__)
        logger.addHandler(logging.NullHandler())

    flags = flags or []
    tmpdir = Path("/tmp") / f"prog_{uuid.uuid4().hex}"
    tmpdir.mkdir(parents=True, exist_ok=False)
    src = tmpdir / "prog.c"
    out = tmpdir / "prog.bin"

    try:
        src.write_text(code, encoding="utf-8")
        cmd = ["gcc", str(src), "-o", str(out)] + flags
        try:
            run_cmd(cmd, logger=logger, cwd=tmpdir)
        except RuntimeError as e:
            return {"success": False, "binary_path": None, "reason": str(e)}
        finally:
            try:
                os.remove(src)
            except FileNotFoundError:
                pass

        return {"success": True, "binary_path": str(out), "reason": ""}
    except Exception as e:
        return {"success": False, "binary_path": None, "reason": str(e)}


def find_project_root(marker_dir: str = "tools") -> Path:
    """Locate the project root directory based on this infra package location.

    The project root is defined as the first ancestor directory of this file
    that contains a subdirectory named ``marker_dir`` (defaults to ``"tools"``).

    This is intentionally independent of the current working directory and
    works reliably regardless of where callers import from.
    """
    here = Path(__file__).resolve()
    start_dir = here.parent
    for parent in (start_dir, *start_dir.parents):
        if (parent / marker_dir).is_dir():
            return parent
    raise RuntimeError(
        f"Could not locate project root (no '{marker_dir}' directory found above {here})"
    )

