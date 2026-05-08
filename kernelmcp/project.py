import argparse
import logging
import os
import re
import sys
from typing import Optional
from kernelmcp.infra.utils import run_cmd
import shutil
import json
from pathlib import Path


def initialize_ezqm_project(project_path: str, linux_path: str, logger: logging.Logger) -> None:
    """
    Initialize EZQM local environment inside the project folder.

    Steps:
      1. cd to project folder
      2. run 'ezcf --init-local <linux_path>'
      3. run 'ezqm -b'
      4. run 'ezcf -l -r' and move snapshot_file into project folder
      5. run 'ezcf -l -u snapshot_file <new_path>'
    """
    project_path = os.path.abspath(project_path)
    linux_path = os.path.abspath(linux_path)

    if not os.path.isdir(project_path):
        logger.error("Project folder not found: %s", project_path)
        return
    if not os.path.isdir(linux_path):
        logger.error("Linux source folder not found: %s", linux_path)
        return

    logger.info("Initializing EZQM local environment in %s", project_path)

    # Step 1: ezcf --init-local
    init_cmd = ["ezcf", "--init-local", linux_path]
    logger.info("Running ezcf initialization: %s", " ".join(init_cmd))
    run_cmd(init_cmd, logger=logger, cwd=project_path)
    
    # Step 1.5 for kernelCTF image we set qemu command template
    # 3584 is the real RAM size, but it's too big. 1024 is good enough for triaging 
    custom_qemu_cmd = ["ezcf", "-l", "-u", "qemucmd", "{qemu_binary} -m 1024 -gdb tcp::{gdbport} -monitor tcp::{qemuport},server,nowait -smp 2 -display none -chardev stdio,id=char0,logfile={outputfile},signal=off -serial chardev:char0 -no-reboot -device virtio-rng-pci -enable-kvm -cpu host,migratable=on -device virtio-net-pci,netdev=net0 -nographic -snapshot -netdev user,id=net0,restrict=on,hostfwd=tcp:127.0.0.1:{sshport}-:22 -drive file={diskimage} -kernel {bzImage} -append 'nokaslr console=ttyS0 root=/dev/sda rw kasan_multi_shot=1 printk.synchronous=1 net.ifnames=0 biosdevname=0'"]
    logger.info("Setting custom qemu command for kernelCTF image")
    run_cmd(custom_qemu_cmd, logger=logger, cwd=project_path)

    # Step 2: ezqm -b
    build_cmd = ["ezqm", "-b"]
    logger.info("Running ezqm build: %s", " ".join(build_cmd))
    run_cmd(build_cmd, logger=logger, cwd=project_path)

    # Step 3: ezcf -l -r to read snapshot_file
    list_cmd = ["ezcf", "-l", "-r"]
    logger.info("Retrieving snapshot information: %s", " ".join(list_cmd))
    result = run_cmd(list_cmd, logger=logger, cwd=project_path)

    try:
        info = json.loads(result)
        snapshot_file = info.get("snapshot_file")
    except Exception as e:
        logger.error("Failed to parse ezcf -l -r output as JSON: %s", e)
        return

    if not snapshot_file or not os.path.isfile(snapshot_file):
        logger.error("snapshot_file not found or invalid: %s", snapshot_file)
        return

    # Step 4: move snapshot file into project folder
    new_snapshot_path = os.path.join(
        project_path, os.path.basename(snapshot_file))
    try:
        shutil.move(snapshot_file, new_snapshot_path)
        logger.info("Moved snapshot_file → %s", new_snapshot_path)
    except Exception as e:
        logger.error("Failed to move snapshot_file: %s", e)
        return

    # Step 5: ezcf -l -u snapshot_file <new_path>
    update_cmd = ["ezcf", "-l", "-u", "snapshot_file", new_snapshot_path]
    logger.info("Updating snapshot_file path: %s", " ".join(update_cmd))
    run_cmd(update_cmd, logger=logger, cwd=project_path)

    logger.info("EZQM project fully initialized with updated snapshot path.")


def generate_cqdb(linux_path: str, project_path: str, logger: logging.Logger) -> None:
    """
    Generate a CodeQuery database (linux.cqdb) from a compiled Linux source tree.

    Steps:
      1. Check if .config mentions 'clang'
      2. Run 'make (LLVM=1 if clang) COMPILED_SOURCE=1 cscope tags'
      3. Run 'cqmakedb -s linux.cqdb -c cscope.out -t tags -p'
      4. Clean up temporary files and move linux.cqdb to project folder
    """
    # 1. Detect clang
    use_llvm = "clang" in open(os.path.join(
        linux_path, ".config"), "r", encoding="utf-8").read()

    if use_llvm:
        logger.info("Detected 'clang' in .config — enabling LLVM=1 for make.")
    else:
        logger.info("No 'clang' detected — building without LLVM=1.")

    # 2. make cscope + tags
    make_cmd = ["make"]
    if use_llvm:
        make_cmd.append("LLVM=1")
    nproc = os.cpu_count()
    make_cmd += ["COMPILED_SOURCE=1", "cscope", "tags", f"-j{nproc}"]

    logger.info("Running make: %s", " ".join(make_cmd))
    run_cmd(make_cmd, logger=logger, cwd=linux_path)

    # 3. cqmakedb
    cqdb_file = os.path.join(project_path, "linux.cqdb")
    cq_cmd = [
        "cqmakedb",
        "-s", str(cqdb_file),
        "-c", "cscope.out",
        "-t", "tags",
        "-p"
    ]
    logger.info("Running cqmakedb to generate %s", cqdb_file)
    run_cmd(cq_cmd, logger=logger, cwd=linux_path)

    # 4. Cleanup temporary files
    for fname in ["cscope.out", "tags"]:
        f = os.path.join(linux_path, fname)
        if os.path.exists(f):
            try:
                os.remove(f)
                logger.debug("Removed temporary file: %s", f)
            except Exception as e:
                logger.warning("Failed to remove %s: %s", f, e)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kernelmcp-project",
        description="KernelMCP project utilities",
    )
    parser.add_argument(
        "-l",
        "--linux",
        required=True,
        help="Linux source folder with compiled kernel",
    )
    parser.add_argument(
        "-n",
        "--name",
        required=True,
        help="Project name.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )
    return parser


def validate_linux_folder(linux_path: str) -> tuple[bool, Optional[str]]:
    """
    Check 1) It's a folder
          2) It contains a Makefile
          3) It contains a .config file
          4) It contains a vmlinux file
    """
    if not os.path.isdir(linux_path):
        return "Linux path is not a directory"

    required_files = ["Makefile", ".config", "vmlinux"]
    missing_files = [f for f in required_files if not os.path.isfile(
        os.path.join(linux_path, f))]
    if missing_files:
        return False, f"Missing required files: {', '.join(missing_files)}, are you sure this is a compiled Linux source folder?"
    return True, None


def main() -> Optional[argparse.Namespace]:
    """Parse arguments and perform a minimal validation.

    Returns the parsed args for downstream use. This is a lightweight
    skeleton meant to be extended with project-specific logic later.
    """
    parser = build_parser()
    args = parser.parse_args()

    logger = logging.getLogger("KernelMCP.project")
    if not logger.handlers:
        ch = logging.StreamHandler()
        ch.setLevel(logging.DEBUG if args.verbose else logging.INFO)
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        ch.setFormatter(formatter)
        logger.addHandler(ch)
    logger.setLevel(logging.DEBUG if args.verbose else logging.INFO)

    linux_path = os.path.abspath(args.linux)
    is_valid, error_message = validate_linux_folder(linux_path)
    if not is_valid:
        logger.error("Invalid Linux source folder: %s", error_message)
    else:
        logger.info("Using Linux source folder: %s", linux_path)

    project_name = args.name
    if any(c in project_name for c in [".", "/", "\\"]):
        logger.error("Project name contains forbidden characters ('.' or '/')")
        sys.exit(2)
    logger.info("Project name: %s", project_name)

    # Create project folder
    project_path = os.path.abspath(os.path.join("project", project_name))
    if not os.path.exists("project"):
        os.makedirs("project")
    if os.path.exists(project_path):
        logger.error(
            "Project folder 'project/%s' already exists. Please delete it before re-running.", project_name)
        sys.exit(3)
    os.makedirs(project_path)
    logger.info("Created project folder: project/%s", project_name)

    # Initialize EZQM project
    initialize_ezqm_project(project_path, linux_path, logger)
    # Generate CodeQuery database
    generate_cqdb(linux_path, project_path, logger)
    logger.info("Project setup complete.")

    # Create config.json in project folder
    config_path = os.path.join(project_path, "config.json")
    config_data = {
        "db_path": os.path.join(project_path, "linux.cqdb"),
        "code_path": linux_path,
        "ezqmlocalfolder": project_path,
    }

    try:
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=2)
        logger.info("Created config.json at %s", config_path)
    except Exception as e:
        logger.error("Failed to create config.json: %s", e)


if __name__ == "__main__":
    main()
