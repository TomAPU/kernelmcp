import subprocess
import os
import socket
import shutil
from pathlib import Path
import pytest

from kernelmcp.infra.utils import run_cmd
from kernelmcp.infra.utils import get_unused_port
from kernelmcp.infra.utils import compile_c_code


@pytest.fixture(scope="module", autouse=False)
def require_gcc():
    if shutil.which("gcc") is None:
        pytest.skip("gcc not installed")


def test_run_cmd_success(monkeypatch, tmp_path: Path):
    # Real execution: run echo in the provided cwd and check stdout is returned
    out = run_cmd(["echo", "hello"], logger=None, timeout=3, cwd=tmp_path)
    assert out == "hello"


def test_run_cmd_nonzero_exit(monkeypatch):
    # Real execution: run a shell command that prints to stderr and exits non-zero
    cmd = ["sh", "-c", "printf 'something went wrong' 1>&2; exit 2"]
    with pytest.raises(RuntimeError) as ei:
        run_cmd(cmd)
    assert "something went wrong" in str(ei.value)


def test_run_cmd_timeout(monkeypatch):
    def fake_run(cmd, cwd=None, capture_output=None, text=None, timeout=None):
        raise subprocess.TimeoutExpired(cmd, timeout)

    monkeypatch.setattr("kernelmcp.infra.utils.subprocess.run", fake_run)

    with pytest.raises(subprocess.TimeoutExpired):
        run_cmd(["sleep"], timeout=1)


def test_run_cmd_real_execution():
    """
    Integration-style test: actually run a trivial command to ensure run_cmd
    executes real subprocesses when not mocked.
    """
    out = run_cmd(["echo", "it-works"])  # simple, portable on Linux containers
    assert out.strip() == "it-works"


def test_get_unused_port():
    port = get_unused_port()
    assert isinstance(port, int)
    assert 0 < port <= 65535

    # Try binding to the returned port -- this should succeed because
    # get_unused_port uses a short-lived socket and closed it, so the
    # port should be available for immediate reuse.
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", port))
    finally:
        s.close()


def test_compile_c_code_success(require_gcc):
    code = r"""
    #include <stdio.h>
    int main() {
        printf("ok\n");
        return 0;
    }
    """
    res = compile_c_code(logger=None, code=code)
    assert res["success"] is True
    bin_path = Path(res["binary_path"])
    assert bin_path.exists()
    # run binary
    output = subprocess.check_output([str(bin_path)], text=True).strip()
    assert output == "ok"
    # cleanup
    os.remove(bin_path)


def test_compile_c_code_with_flags(require_gcc):
    code = r"""
    #include <stdio.h>
    int main() {
        puts("flag");
        return 0;
    }
    """
    res = compile_c_code(logger=None, code=code, flags=["-O2"])
    assert res["success"] is True
    bin_path = Path(res["binary_path"])
    assert bin_path.exists()
    # run binary
    output = subprocess.check_output([str(bin_path)], text=True).strip()
    assert output == "flag"
    # cleanup
    os.remove(bin_path)


def test_compile_c_code_failure(require_gcc):
    # broken code, missing semicolon
    code = r"""
    int main() {
        return 0
    """
    res = compile_c_code(logger=None, code=code)
    assert res["success"] is False
    assert res["binary_path"] is None
    assert "error" in res["reason"].lower()
