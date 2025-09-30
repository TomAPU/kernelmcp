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

    res = subprocess.run([cc, str(src), "-g", "-O0", "-o", str(exe)],
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
    try:
        proc.wait(timeout=2)
    except Exception:
        proc.kill()


def _notify_stops(records):
    """Extract MI notify 'stopped' records (list of dict)."""
    return [r for r in (records or []) if r.get("type") == "notify" and r.get("message") == "stopped"]


def _stop_reasons(stopped_records):
    """Collect 'reason' fields from stopped notify payloads."""
    reasons = []
    for r in stopped_records:
        payload = r.get("payload")
        if isinstance(payload, dict):
            rsn = payload.get("reason")
            if rsn:
                reasons.append(rsn)
    return reasons


def test_gdbserver_connect(gdbserver):
    exe, port = gdbserver
    gdb = RemoteGdbMI(exe, port)

    try:
        res = gdb.connect(timeout_sec=5.0)
        assert isinstance(res, dict)
        if res.get("ok"):
            assert gdb.is_connected
            # After remote connect, target should be stopped; backtrace should work.
            bt = gdb.get_backtrace_mi(timeout_sec=5.0)
            assert isinstance(bt, dict)
            assert bt.get("ok"), f"backtrace failed: {bt.get('error')}"
        else:
            assert res.get("error") is not None
    finally:
        gdb.close()


def test_gdb_break_and_eval_then_exit(gdbserver):
    """Integration: break at main -> continue -> MI evaluate -> continue to program exit."""
    exe, port = gdbserver
    gdb = RemoteGdbMI(exe, port)

    try:
        # Connect
        cres = gdb.connect(timeout_sec=5.0)
        assert cres.get("ok"), f"connect failed: {cres.get('error')}"

        # Set a breakpoint at main (MI)
        bres = gdb.set_breakpoint("main", timeout_sec=5.0)
        assert bres["ok"], f"break main failed: {bres.get('error')}"

        # Continue; expect to stop at the breakpoint
        cont1 = gdb.continue_execution(timeout_sec=5.0)
        assert cont1["ok"], f"continue failed: {cont1.get('error')}"
        stops = _notify_stops(cont1["records"])
        # Some GDBs may not flush notify immediately; poke once to flush.
        if not stops:
            flush = gdb.get_backtrace_mi(timeout_sec=2.0)
            stops = _notify_stops(flush["records"])
        assert stops, "No 'stopped' notification after continue to breakpoint"
        reasons = _stop_reasons(stops)
        # Accept either 'breakpoint-hit' or a generic 'stopped' without reason (toolchains vary)
        assert ("breakpoint-hit" in reasons) or (len(reasons) ==
                                                 0), f"Unexpected stop reasons at main: {reasons}"

        # Evaluate an expression using MI (no CLI parsing)
        pres = gdb.evaluate_expression("3+4", timeout_sec=3.0)
        assert pres["ok"], f"evaluate failed: {pres.get('error')}"
        # Find the 'result' record with value
        value = None
        for r in pres["records"]:
            if r.get("type") == "result" and r.get("message") == "done":
                payload = r.get("payload")
                if isinstance(payload, dict) and "value" in payload:
                    value = payload["value"]
                    break
        assert value is not None, f"MI did not return a value: {pres['records']}"
        assert value.strip() in ("7", "0x7"), f"Expected 7, got {value!r}"

        # Continue to program end; expect exited/exited-normally
        cont2 = gdb.continue_execution(timeout_sec=5.0)
        assert cont2["ok"], f"final continue failed: {cont2.get('error')}"
        final_stops = _notify_stops(cont2["records"])
        if not final_stops:
            # Nudge GDB to deliver pending notifications
            flush = gdb.get_backtrace_mi(timeout_sec=2.0)
            final_stops = _notify_stops(flush["records"])
        assert final_stops, "No final 'stopped' record observed after program run"
        final_reasons = _stop_reasons(final_stops)
        assert any(r in ("exited-normally", "exited")
                   for r in final_reasons), f"Unexpected final reasons: {final_reasons}"
    finally:
        gdb.close()
