import argparse
import logging
import os
import re
import sys
from typing import Optional


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


if __name__ == "__main__":
    main()
