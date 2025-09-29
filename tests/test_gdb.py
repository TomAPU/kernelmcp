# test_gdb.py
import shutil
import subprocess
import time
import socket
from pathlib import Path

import pytest

from kernelmcp.infra import RemoteGdbMI
from kernelmcp.infra.utils import get_unused_port


pytestmark = pytest.mark.skipif(
    not (shutil.which("gdbserver") and shutil.which("gdb")),
    reason="gdb or gdbserver not found in PATH",
)


def _wait_for_port(port: int, timeout: float = 5.0):
    """Wait until a TCP port is accepting connections."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return True
        except OSError:
            time.sleep(0.05)
    return False


@pytest.fixture
def hello_exe(tmp_path: Path):
    """Build a simple hello-world C program, return its path."""
    src = tmp_path / "hello.c"
    src.write_text(
        '#include <stdio.h>\nint main(void){ printf("hi\\n"); fflush(stdout); return 0; }\n',
        encoding="utf-8",
    )
    exe = tmp_path / "hello"
    cc = shutil.which("gcc") or shutil.which("clang")
    if not cc:
        pytest.skip("No C compiler (gcc/clang) found in PATH.")

    res = subprocess.run([cc, str(src), "-o", str(exe)],
                         capture_output=True, text=True)
    if res.returncode != 0:
        pytest.skip(f"Failed to compile test binary: {res.stderr[:400]}")
    return str(exe)


@pytest.fixture
def gdbserver(hello_exe):
    """Run gdbserver serving the hello_exe and yield the process."""
    port = get_unused_port()
    proc = subprocess.Popen(
        ["gdbserver", f":{port}", hello_exe],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if not _wait_for_port(port):
        proc.terminate()
        pytest.skip("gdbserver did not start listening in time")

    yield hello_exe, port

    proc.terminate()
    proc.wait(timeout=2)


def test_gdbserver_connect(gdbserver):
    exe, port = gdbserver
    gdb = RemoteGdbMI(exe, port)

    res = gdb.connect(timeout_sec=5.0)
    assert isinstance(res, dict)

    if res.get("ok"):
        assert gdb.is_connected
        out = gdb.send("info threads", timeout_sec=5.0)
        assert isinstance(out, dict)
    else:
        assert res.get("error") is not None

    gdb.close()


def test_gdb_break_and_print(gdbserver):
    """Real integration: set breakpoint at main, continue, evaluate expression, then run to completion."""
    exe, port = gdbserver
    gdb = RemoteGdbMI(exe, port)
    try:
        # Connect
        cres = gdb.connect(timeout_sec=5.0)
        assert cres.get("ok"), f"connect failed: {cres.get('error')}"

        def _console_text(records):
            # pygdbmi uses type 'console' for CLI output records (not 'output').
            collected = []
            for r in records:
                rtype = r.get("type")
                if rtype in ("console", "output"):
                    payload = r.get("payload")
                    if isinstance(payload, str):
                        collected.append(payload)
            return "\n".join(collected)

        # Break main
        bres = gdb.send("break main", timeout_sec=5.0)
        assert bres["ok"]
        bt = _console_text(bres["records"]).lower()
        assert "break" in bt or "breakpoint" in bt, f"Unexpected breakpoint output: {bt!r}"

        # Continue (hit breakpoint at main)
        cont1 = gdb.send("-exec-continue", timeout_sec=5.0)
        # Expect a 'stopped' notification in records
        stopped_msgs = [r for r in cont1["records"]
                        if r.get("message") == "stopped"]
        assert stopped_msgs, "Did not observe stopped notification after first continue"

        # Evaluate an expression (should get $N = 7) via CLI tunneled print
        pres = gdb.send("print 3+4", timeout_sec=3.0)
        assert pres["ok"], f"print failed: {pres.get('error')}"
        ptxt = _console_text(pres["records"]).lower()
        assert "7" in ptxt, f"Expected '7' in print output, got: {ptxt!r}"

        # Continue to program end; verify final stop reason is program exit (exited / exited-normally)
        cont2 = gdb.send("-exec-continue", timeout_sec=5.0)
        final_stops = [r for r in cont2["records"]
                       if r.get("message") == "stopped"]
        # If no stop yet, issue a no-op to flush pending async notifications
        if not final_stops:
            flush = gdb.send("info threads", timeout_sec=2.0)
            final_stops = [r for r in flush["records"]
                           if r.get("message") == "stopped"]
        assert final_stops, "No final stopped record observed after program run"
        # Inspect reasons
        reasons = []
        for r in final_stops:
            payload = r.get("payload")
            if isinstance(payload, dict):
                reason = payload.get("reason")
                if reason:
                    reasons.append(reason)
        assert any(rsn in ("exited-normally", "exited")
                   for rsn in reasons), f"Unexpected stop reasons: {reasons}"
    finally:
        gdb.close()
