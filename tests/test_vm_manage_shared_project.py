# test_vm_manage_shared_project.py
import json
import logging
import os
import socket
import textwrap
from pathlib import Path

from kernelmcp.infra.vm_manage import VMManage


def _make_shared_project(tmp_path: Path) -> Path:
    project_dir = tmp_path / "shared-project"
    project_dir.mkdir()

    (project_dir / "support.txt").write_text("shared-data", encoding="utf-8")

    localconf = {
        "gdbport": 31337,
        "sshport": 31338,
        "nested": {
            "serialport": 31339,
        },
        "vmlinux": "/bin/true",
    }
    (project_dir / "ezqmlocal.json").write_text(
        json.dumps(localconf, indent=2),
        encoding="utf-8",
    )
    return project_dir


def _install_fake_ezqm(tmp_path: Path) -> Path:
    bindir = tmp_path / "bin"
    bindir.mkdir()

    ezqm_path = bindir / "ezqm"
    ezqm_path.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env python3
            import json
            import socket
            import sys
            from pathlib import Path


            def iter_ports(node):
                if isinstance(node, dict):
                    for key, value in node.items():
                        if isinstance(value, int) and value > 0 and key.lower().endswith("port"):
                            yield value
                        else:
                            yield from iter_ports(value)
                elif isinstance(node, list):
                    for item in node:
                        yield from iter_ports(item)


            with open(Path.cwd() / "ezqmlocal.json", "r", encoding="utf-8") as f:
                conf = json.load(f)

            listeners = []
            try:
                for port in iter_ports(conf):
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    sock.bind(("127.0.0.1", port))
                    sock.listen(1)
                    listeners.append(sock)
            except OSError as exc:
                print(f"bind failed: {exc}", file=sys.stderr, flush=True)
                raise SystemExit(1)

            print("root@syzkaller", flush=True)
            for _line in sys.stdin:
                print("root@syzkaller", flush=True)
            """
        ),
        encoding="utf-8",
    )
    ezqm_path.chmod(0o755)
    return bindir


def test_vm_manage_creates_isolated_runtime_dirs(tmp_path):
    shared_project = _make_shared_project(tmp_path)
    logger = logging.getLogger("test_vm_manage_shared_project")

    vm1 = VMManage(ezqmlocalfolder=str(shared_project), logger=logger)
    vm2 = VMManage(ezqmlocalfolder=str(shared_project), logger=logger)

    assert vm1.project_dir != shared_project
    assert vm2.project_dir != shared_project
    assert vm1.project_dir != vm2.project_dir

    assert (vm1.project_dir / "support.txt").exists()
    assert (vm2.project_dir / "support.txt").exists()

    assert vm1.localconf["gdbport"] != 31337
    assert vm2.localconf["gdbport"] != 31337
    assert vm1.localconf["gdbport"] != vm2.localconf["gdbport"]
    assert vm1.localconf["sshport"] != vm2.localconf["sshport"]
    assert vm1.localconf["nested"]["serialport"] != vm2.localconf["nested"]["serialport"]

    assert Path(vm1.vmoutput) == vm1.project_dir / "vmoutput"
    assert Path(vm2.vmoutput) == vm2.project_dir / "vmoutput"


def test_two_vm_instances_can_start_from_same_shared_project(tmp_path, monkeypatch):
    shared_project = _make_shared_project(tmp_path)
    bindir = _install_fake_ezqm(tmp_path)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")

    logger = logging.getLogger("test_vm_manage_shared_project")
    vm1 = VMManage(ezqmlocalfolder=str(shared_project), logger=logger)
    vm2 = VMManage(ezqmlocalfolder=str(shared_project), logger=logger)

    try:
        assert vm1.start_vm(timeout_sec=5)
        assert vm2.start_vm(timeout_sec=5)

        with socket.create_connection(("127.0.0.1", vm1.localconf["gdbport"]), timeout=1):
            pass
        with socket.create_connection(("127.0.0.1", vm2.localconf["gdbport"]), timeout=1):
            pass
    finally:
        vm1.stop_vm()
        vm2.stop_vm()
