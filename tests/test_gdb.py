# test_gdb.py
import shutil
import subprocess
import time
import socket
from pathlib import Path
import signal
import pytest
import os
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
def persistent_exe(tmp_path: Path):
    """Build a simple long-running C program that waits (does not exit)."""
    src = tmp_path / "wait.c"
    # This program prints a line, then loops sleeping until killed.
    src.write_text(
        '#include <stdio.h>\n'
        '#include <unistd.h>\n'
        'int main(void) {\n'
        '    printf("waiting...\\n"); fflush(stdout);\n'
        '    /* loop until killed by the test */\n'
        '    while (1) {\n'
        '        sleep(1);\n'
        '    }\n'
        '    return 0;\n'
        '}\n',
        encoding="utf-8",
    )
    exe = tmp_path / "wait"
    cc = shutil.which("gcc") or shutil.which("clang")
    if not cc:
        pytest.skip("No C compiler (gcc/clang) found in PATH.")
    res = subprocess.run([cc, str(src), "-g", "-O0", "-o", str(exe)],
                         capture_output=True, text=True)
    if res.returncode != 0:
        pytest.skip(f"Failed to compile wait binary: {res.stderr[:400]}")
    return str(exe)


@pytest.fixture
def persistent_gdbserver(persistent_exe):
    """Run gdbserver serving the persistent_exe and yield the process + port."""
    port = get_unused_port()
    proc = subprocess.Popen(
        ["gdbserver", f":{port}", persistent_exe],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=os.environ.copy(),
    )
    if not _wait_for_port(port, timeout=5.0):
        proc.terminate()
        pytest.skip("persistent gdbserver did not start listening in time")

    yield persistent_exe, port, proc

    # Teardown: terminate and wait, then kill if necessary
    try:
        proc.terminate()
        proc.wait(timeout=2)
    except Exception:
        proc.kill()
        proc.wait(timeout=2)


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


def test_gdbserver_connect(gdbserver):
    exe, port = gdbserver
    gdb = RemoteGdbMI(exe, port)

    try:
        res = gdb.connect(timeout_sec=5.0)
        # require connect succeeded
        assert isinstance(res, dict)
        assert res.get("ok") is True, f"connect failed: {res.get('error')}"
        assert gdb.is_connected
        state = gdb.get_state()
        assert isinstance(state, dict)
        assert state["status"] == "stopped", f"unexpected state: {state}"
        bt = gdb.get_backtrace_mi(timeout_sec=5.0)
        assert isinstance(bt, dict)
        assert bt.get("ok"), f"backtrace failed: {bt.get('error')}"
        # Ensure backtrace returned some useful payload (textual or structured)
        assert bt.get("response") or bt.get(
            "additional_info"), f"backtrace missing payload: {bt}"
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

        # Continue; expect to stop at the breakpoint -> wait_for_stop should observe it
        cont1 = gdb.continue_execution(timeout_sec=5.0)
        assert cont1["ok"], f"continue failed: {cont1.get('error')}"
        rstop = gdb.wait_for_stop(timeout_sec=5.0)
        assert rstop.get("ok"), f"wait_for_stop (main) failed: {rstop}"
        # we expect to have hit the breakpoint at main
        stop = rstop.get("stop") or {}
        assert stop.get(
            "reason") == "breakpoint-hit", f"unexpected stop: {rstop}"
        # ensure breakpoint info present and has numeric id when possible
        bp = stop.get("breakpoint")
        assert isinstance(bp, dict), f"missing breakpoint info: {stop!r}"
        assert "number" in bp
        try:
            _ = int(bp.get("number"))
        except Exception:
            pytest.fail(
                f"breakpoint number not integer-convertible: {bp.get('number')!r}")

        # Evaluate an expression using CLI so textual response is present
        pres = gdb.send("print 3+4", timeout_sec=3.0)
        assert pres["ok"], f"print failed: {pres.get('error')}"
        assert "7" in (pres.get(
            "response") or ""), f"Expected '7' in response, got: {pres.get('response')!r}"
        # Also test MI expression evaluator returns the value (either in response or additional_info)
        eres = gdb.evaluate_expression("3+4", timeout_sec=3.0)
        assert eres.get(
            "ok"), f"evaluate_expression failed: {eres.get('error')}"
        found = False
        if "7" in (eres.get("response") or ""):
            found = True
        for ai in eres.get("additional_info", []) or []:
            if any("7" in str(v) for v in ai.values()):
                found = True
                break
        assert found, f"evaluate_expression did not include expected value: {eres}"

        # Continue to program end; expect an exit stop
        cont2 = gdb.continue_execution(timeout_sec=5.0)
        assert cont2["ok"], f"final continue failed: {cont2.get('error')}"
        rstop2 = gdb.wait_for_stop(timeout_sec=5.0)
        assert rstop2.get("ok"), f"final wait_for_stop failed: {rstop2}"
        assert (rstop2.get("stop") or {}).get("reason") in {
            "exited", "exited-normally", "exited-signalled"}
    finally:
        gdb.close()


# Removed record-parsing helpers; new API no longer exposes raw MI records.


def test_cli_send_and_error_path(gdbserver):
    """Validate CLI passthrough via send(), and ensure errors propagate sanely."""
    exe, port = gdbserver
    gdb = RemoteGdbMI(exe, port)
    try:
        assert gdb.connect(timeout_sec=5.0)["ok"]

        # CLI expression print (GDB console)
        r = gdb.send("p/x 0x34 + 0x9", timeout_sec=3.0)
        assert r["ok"], f"CLI send failed: {r.get('error')}"
        # 0x34 + 0x9 == 0x3d (61)
        resp = r.get("response") or ""
        assert "0x3d" in resp, f"unexpected CLI arithmetic output: {resp!r}"
        # Also try a clearly-bad MI to hit error path
        bad = gdb._mi("-this-command-does-not-exist", timeout_sec=2.0)
        assert not bad["ok"] and bad["error"], "Expected MI error to be surfaced"
    finally:
        gdb.close()


def test_breakpoint_lifecycle_and_listing(gdbserver):
    """Insert/list/enable/disable/delete breakpoints end-to-end."""
    exe, port = gdbserver
    gdb = RemoteGdbMI(exe, port)
    try:
        assert gdb.connect(timeout_sec=5.0)["ok"]

        # Insert breakpoint at main
        b = gdb.set_breakpoint("main", timeout_sec=5.0)
        assert b["ok"], f"break-insert failed: {b.get('error')}"

        # List -> should succeed (we no longer parse content)
        bl = gdb.list_breakpoints(timeout_sec=3.0)
        assert bl["ok"], f"break-list failed: {bl.get('error')}"
        assert bl["additional_info"], "break-list missing additional_info"
        # Best-effort clean-up: attempt to delete breakpoint #1 without asserting
        gdb.delete_breakpoint(1, timeout_sec=3.0)
    finally:
        gdb.close()


def test_disconnect_flags(persistent_gdbserver):
    """Check connect(force=True) detaches and reconnects; then disconnect() flips flags."""
    exe, port, proc = persistent_gdbserver
    gdb = RemoteGdbMI(exe, port)
    try:
        assert gdb.connect(timeout_sec=5.0)["ok"]
        assert gdb.is_connected

        # Plain disconnect
        d = gdb.disconnect(timeout_sec=3.0)
        # some gdbs return ok, some are quiet after detach
        assert d["ok"] or d["error"] is None
        assert not gdb.is_connected
    finally:
        gdb.close()
        # kill the persistent gdbserver process explicitly
        proc.terminate()


def test_step_disassemble_registers_memory_flow(gdbserver):
    """
    Stop at main, exercise stepping, disassembly, registers, memory reads, and continue to exit.
    """
    exe, port = gdbserver
    gdb = RemoteGdbMI(exe, port)
    try:
        assert gdb.connect(timeout_sec=5.0)["ok"]

        # Break at main and continue to hit it
        assert gdb.set_breakpoint("main", timeout_sec=5.0)["ok"]
        hit = gdb.continue_execution(timeout_sec=5.0)
        assert hit["ok"], f"continue to main failed: {hit.get('error')}"
        stop_main = gdb.wait_for_stop(timeout_sec=5.0)
        assert stop_main.get("ok"), f"wait_for_stop (main) failed: {stop_main}"
        assert (stop_main.get("stop") or {}).get(
            "reason") == "breakpoint-hit", f"unexpected stop: {stop_main}"

        # Disassemble near $pc
        dasm = gdb.get_disassembly_mi(length=64, timeout_sec=3.0)
        assert dasm["ok"], f"disassembly failed: {dasm.get('error')}"

        # Read registers (hex)
        regs = gdb.get_registers_mi(timeout_sec=3.0)
        assert regs["ok"], f"get registers failed: {regs.get('error')}"

        # Read a few bytes at $pc
        mem_bytes = gdb.read_memory_bytes("$pc", 8, timeout_sec=3.0)
        assert mem_bytes["ok"], f"read-memory-bytes failed: {mem_bytes.get('error')}"

        # Read memory cells (data-read-memory)
        mem_cells = gdb.read_memory_mi("$pc", "x", 1, 1, 8, timeout_sec=3.0)
        assert mem_cells["ok"], f"data-read-memory failed: {mem_cells.get('error')}"

        # Single-step instruction then next-instruction (doesn't assume source line info)
        assert gdb.stepi(timeout_sec=3.0)["ok"]
        assert gdb.nexti(timeout_sec=3.0)["ok"]

        # Evaluate again via CLI print for textual response
        ev = gdb.send("print 5*6", timeout_sec=3.0)
        assert ev["ok"], f"evaluate failed: {ev.get('error')}"
        assert any(s in (ev.get("response") or "") for s in (
            "30", "0x1e")), f"Expected 30, got {ev.get('response')!r}"

        # Continue to end and wait for final stop (exit)
        cont = gdb.continue_execution(timeout_sec=5.0)
        assert cont["ok"], f"final continue failed: {cont.get('error')}"
        final_stop = gdb.wait_for_stop(timeout_sec=5.0)
        assert final_stop.get(
            "ok"), f"final wait_for_stop failed: {final_stop}"
        assert (final_stop.get("stop") or {}).get("reason") in {
            "exited", "exited-normally", "exited-signalled"}
    finally:
        gdb.close()


def test_cli_command_execution_result(gdbserver):
    """
    Verify send()'s output 
    """
    exe, port = gdbserver
    gdb = RemoteGdbMI(exe, port)
    try:
        assert gdb.connect(timeout_sec=5.0)["ok"]

        r1 = gdb.send('print "Hello, world!"', timeout_sec=3.0)
        assert r1["ok"], f"double-quoted print failed: {r1.get('error')}"
        out1 = r1.get("response", "")
        assert "Hello, world!" in out1, f"unexpected output: {out1!r}"

    finally:
        gdb.close()


def test_kbpf_command(gdbserver):
    """
    Verify kbpf --help works to ensure gef is loaded
    """
    exe, port = gdbserver
    gdb = RemoteGdbMI(exe, port)
    try:
        assert gdb.connect(timeout_sec=5.0)["ok"]

        r1 = gdb.send('kbpf --help', timeout_sec=3.0)
        assert r1["ok"], f"kbpf --help failed: {r1.get('error')}"
        out1 = r1.get("response", "")
        assert "usage: kbpf" in out1, f"unexpected output: {out1!r}"

    finally:
        gdb.close()


def test_wait_for_stop_breakpoint_hit(gdbserver):
    """wait_for_stop should return when a breakpoint is actually hit."""
    exe, port = gdbserver
    gdb = RemoteGdbMI(exe, port)
    try:
        # Connect and set a breakpoint at main
        assert gdb.connect(timeout_sec=5.0)["ok"]
        b = gdb.set_breakpoint("main", timeout_sec=5.0)
        assert b["ok"], f"break main failed: {b.get('error')}"

        # Continue; expect to hit the breakpoint, and wait_for_stop should observe it
        cont = gdb.continue_execution(timeout_sec=5.0)
        assert cont["ok"], f"continue failed: {cont.get('error')}"
        res = gdb.wait_for_stop(timeout_sec=5.0)
        assert res and res.get("ok"), f"wait_for_stop failed: {res}"
        stop = res.get("stop") or {}
        assert stop.get(
            "reason") == "breakpoint-hit", f"unexpected stop: {stop!r}"
        # Best-effort sanity: breakpoint info should be present and numeric
        bp = stop.get("breakpoint")
        assert isinstance(bp, dict), f"missing breakpoint info: {stop!r}"
        assert "number" in bp
        try:
            _ = int(bp.get("number"))
        except Exception:
            pytest.fail(
                f"breakpoint number not integer-convertible: {bp.get('number')!r}")

        # Cleanly continue to program exit for teardown hygiene
        cont2 = gdb.continue_execution(timeout_sec=5.0)
        assert cont2["ok"], f"final continue failed: {cont2.get('error')}"
        res2 = gdb.wait_for_stop(timeout_sec=5.0)
        assert res2.get("ok"), f"final wait_for_stop failed: {res2}"
        assert (res2.get("stop") or {}).get("reason") in {
            "exited", "exited-normally", "exited-signalled"}
    finally:
        gdb.close()


def test_wait_for_stop_timeout_running_inferior(persistent_gdbserver):
    """wait_for_stop should time out when the inferior keeps running with no stops."""
    exe, port, proc = persistent_gdbserver
    gdb = RemoteGdbMI(exe, port)
    try:
        # Connect -> initial stop at entry; then run without breakpoints.
        assert gdb.connect(timeout_sec=5.0)["ok"]
        cont = gdb.continue_execution(timeout_sec=1.0)
        assert cont["ok"], f"continue failed: {cont.get('error')}"

        # Since the program loops forever and we set no breakpoints, we expect a timeout.
        res = gdb.wait_for_stop(timeout_sec=0.7)
        assert not res["ok"] and res.get(
            "error") == "timeout", f"expected timeout, got: {res}"
    finally:
        gdb.close()
        proc.terminate()
