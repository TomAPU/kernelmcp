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
        cmd = "echo hello"
        vm_logger.info("exec_cmd: %r (timeout=10s)", cmd)
        res = vm.exec_cmd(cmd, timeout_sec=10)
        vm_logger.debug("exec_cmd result: %r (%.2fs)", res, time.time() - t0)
        assert res["success"], f"exec_cmd failed: {res}"
        assert "hello" in res["output"]

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
        assert chk["success"] and "hi from host" in chk["output"]

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
