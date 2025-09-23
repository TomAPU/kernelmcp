import subprocess
from pathlib import Path

import pytest

from kernelmcp.infra.utils import run_cmd


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
