# test_vm_manage.py
import pytest
import os
import sys
import time
import logging
from pathlib import Path

from kernelmcp.infra.vm_manage import VMManage


@pytest.fixture(scope="session")
def vm_logger():
    """A loud logger that prints everything to stdout."""
    logger = logging.getLogger("TEST.VMManage")
    logger.setLevel(logging.DEBUG)

    # Add a single StreamHandler to stdout (avoid duplicates if re-run)
    if not any(isinstance(h, logging.StreamHandler) for h in logger.handlers):
        h = logging.StreamHandler(sys.stdout)
        h.setLevel(logging.DEBUG)
        h.setFormatter(logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
        ))
        logger.addHandler(h)

    # Also make sure root doesn't down-filter our messages
    logging.getLogger().setLevel(logging.DEBUG)
    return logger


def test_vm_lifecycle_and_exec(ezqm_localfolder, tmp_path, vm_logger):
    """Integration: start -> exec command -> upload file -> stop."""
    vm_logger.info("Creating VMManage instance")
    vm = VMManage(ezqmlocalfolder=ezqm_localfolder, logger=vm_logger)

    # Start VM
    t0 = time.time()
    vm_logger.info("Starting VM with timeout=20s ...")
    ok = vm.start_vm(timeout_sec=20)
    vm_logger.info("start_vm finished in %.2fs -> %s", time.time() - t0, ok)
    assert ok, "VM did not start and reach prompt"

    try:
        # Run a simple command
        t0 = time.time()
        cmd = 'echo -n "hello" | base64'
        vm_logger.info("exec_cmd: %r (timeout=10s)", cmd)
        res = vm.exec_cmd(cmd, timeout_sec=10)
        vm_logger.debug("exec_cmd result: %r (%.2fs)", res, time.time() - t0)
        assert res["success"], f"exec_cmd failed: {res}"
        assert "aGVsbG8=" == res["output"]

        # Upload a test file
        testfile = tmp_path / "hello.txt"
        testfile.write_text("hi from host", encoding="utf-8")
        vm_logger.info("Uploading file %s -> /root/hello.txt", testfile)
        up = vm.upload_file(str(testfile), "/root/hello.txt")
        vm_logger.debug("upload_file result: %r", up)
        assert up["success"], f"upload_file failed: {up}"

        # Check the file exists in VM
        t0 = time.time()
        vm_logger.info("Verifying uploaded file content ...")
        chk = vm.exec_cmd("cat /root/hello.txt", timeout_sec=10)
        vm_logger.debug("cat result: %r (%.2fs)", chk, time.time() - t0)
        assert chk["success"] and "hi from host" == chk["output"]

    finally:
        vm_logger.info("Stopping VM ...")
        vm.stop_vm()
        vm_logger.info("VM stopped")


def test_vm_restart(ezqm_localfolder, vm_logger):
    """Check restart logic behaves as expected."""
    vm_logger.info("Creating VMManage instance for restart test")
    vm = VMManage(ezqmlocalfolder=ezqm_localfolder, logger=vm_logger)

    vm_logger.info("Starting VM (timeout=20s)")
    assert vm.start_vm(timeout_sec=20), "start_vm failed"

    vm_logger.info("Restarting VM (timeout=20s)")
    assert vm.restart_vm(timeout_sec=20), "restart_vm failed"

    vm_logger.info("Stopping VM after restart test")
    vm.stop_vm()


def test_vm_exec_timeout_recovery(ezqm_localfolder, vm_logger):
    """Ensure VM can recover from a command timeout and still execute subsequent commands."""
    vm_logger.info("Creating VMManage instance for timeout recovery test")
    vm = VMManage(ezqmlocalfolder=ezqm_localfolder, logger=vm_logger)

    vm_logger.info("Starting VM (timeout=20s)")
    assert vm.start_vm(timeout_sec=20), "start_vm failed"

    try:
        # Step 1: run a long command with short timeout -> expect failure
        cmd = "sleep 100"
        vm_logger.info("exec_cmd: %r (timeout=5s)", cmd)
        res = vm.exec_cmd(cmd, timeout_sec=5)
        vm_logger.debug("exec_cmd result (expected timeout): %r", res)
        assert not res["success"], "Command unexpectedly succeeded, should have timed out"

        # Step 2: run a normal command to verify VM is still responsive
        cmd2 = 'echo -n "hello" | base64'
        vm_logger.info("exec_cmd after timeout: %r (timeout=10s)", cmd2)
        res2 = vm.exec_cmd(cmd2, timeout_sec=10)
        vm_logger.debug("exec_cmd result after timeout: %r", res2)
        assert res2["success"], f"exec_cmd failed after timeout: {res2}"
        assert res2["output"] == "aGVsbG8=", f"Unexpected output: {res2['output']}"

    finally:
        vm_logger.info("Stopping VM after timeout recovery test")
        vm.stop_vm()
        vm_logger.info("VM stopped")


def test_vm_exec_bad_command_recovery(ezqm_localfolder, vm_logger):
    """Ensure VM can recover from a bad command with unmatched quotes and still execute subsequent commands."""
    vm_logger.info("Creating VMManage instance for bad command recovery test")
    vm = VMManage(ezqmlocalfolder=ezqm_localfolder, logger=vm_logger)

    vm_logger.info("Starting VM (timeout=20s)")
    assert vm.start_vm(timeout_sec=20), "start_vm failed"

    try:
        # Step 1: run a bad command -> expect failure
        cmd = "'"
        vm_logger.info("exec_cmd: %r (timeout=2s)", cmd)
        res = vm.exec_cmd(cmd, timeout_sec=2)
        vm_logger.debug("exec_cmd result (expected timeout): %r", res)
        assert not res["success"], "Command unexpectedly succeeded, should have timed out"

        # Step 2: run a normal command to verify VM is still responsive
        cmd2 = 'echo -n "hello" | base64'
        vm_logger.info("exec_cmd after timeout: %r (timeout=10s)", cmd2)
        res2 = vm.exec_cmd(cmd2, timeout_sec=10)
        vm_logger.debug("exec_cmd result after timeout: %r", res2)
        assert res2["success"], f"exec_cmd failed after timeout: {res2}"
        assert res2["output"] == "aGVsbG8=", f"Unexpected output: {res2['output']}"

    finally:
        vm_logger.info("Stopping VM after timeout recovery test")
        vm.stop_vm()
        vm_logger.info("VM stopped")


def test_vm_console_primitives(ezqm_localfolder, vm_logger):
    """Smoke test for low-level console primitives: sendline/sendcontrol/receive."""
    vm_logger.info("Creating VMManage instance for console primitives test")
    vm = VMManage(ezqmlocalfolder=ezqm_localfolder, logger=vm_logger)

    vm_logger.info("Starting VM (timeout=20s)")
    assert vm.start_vm(timeout_sec=20), "start_vm failed"

    try:
        # Send a simple line and verify receive returns structured result
        vm_logger.info("Sending line via sendline()")
        vm.sendline('echo -n "vm-hello"')
        r = vm.receive(timeout=5)
        vm_logger.debug("receive result: %r", r)
        assert isinstance(r, dict)
        assert "content" in r and "reason" in r
        assert r["reason"] == "Meets prompt"
        assert "vm-hello" in r["content"]

        # Send a sleep for 100 seconds, we expect receive to timeout
        vm_logger.info("Sending long sleep command via sendline()")
        vm.sendline("sleep 100")
        r = vm.receive(timeout=1)
        vm_logger.debug("receive result during sleep: %r", r)
        assert isinstance(r, dict)
        assert "content" in r and "reason" in r
        assert r["reason"] == "Timeout"

        # Then send control-C to interrupt it, we expect receive to return promptly
        vm_logger.info("Sending control via sendcontrol()")
        vm.sendcontrol("c")
        r2 = vm.receive(timeout=2)
        vm_logger.debug("receive after control: %r", r2)
        assert isinstance(r2, dict)
        assert "content" in r2 and "reason" in r2
        assert r2["reason"] == "Meets prompt"

    finally:
        vm_logger.info("Stopping VM after console primitives test")
        vm.stop_vm()
        vm_logger.info("VM stopped")
