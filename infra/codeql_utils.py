# infra/codeql_utils.py
from __future__ import annotations
import csv
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional


class CodeQLCpp:
    """
    Minimal CodeQL wrapper for C/C++ using pack install + CSV decode.

    - Creates a temp QL pack with dependency: codeql/cpp-all: "*"
    - Runs `codeql pack install` to create a lockfile (no --search-path needed)
    - Does NOT modify your query text (you must keep it valid)
    - Decodes BQRS to CSV and returns CSV text (str)
    """

    def __init__(self, db_path: str):
        self.db_path = Path(db_path).resolve()
        if not self.db_path.exists():
            raise FileNotFoundError(
                f"Database path does not exist: {self.db_path}")
        if not (self.db_path / "codeql-database.yml").exists():
            raise ValueError(
                f"Invalid CodeQL database (missing codeql-database.yml): {self.db_path}")
        if not shutil.which("codeql"):
            raise FileNotFoundError(f"CodeQL CLI not found in PATH")

    def run_query_text_csv(
        self,
        query: str,
        timeout: Optional[int] = None,
        keep_temp: bool = False,
        persistent_pack_dir: Optional[str] = None,
        result_set: Optional[str] = "#select",
    ) -> List[List[str]]:
        """
        Run a C/C++ CodeQL query string against this DB and return decoded CSV text.

        :param query: QL source (we do NOT mutate it).
        :param timeout: Optional per-command timeout (seconds).
        :param keep_temp: Keep temp dir for debugging.
        :param persistent_pack_dir: Reuse this dir as pack root to avoid reinstalling each call.
        :param result_set: If provided, pass --result-set=<name> to decoder (e.g. "#select" or custom).
        :return: CSV list of rows (including header).
        """
        temp_mgr = None
        if persistent_pack_dir:
            pack_dir = Path(persistent_pack_dir).resolve()
            pack_dir.mkdir(parents=True, exist_ok=True)
        else:
            temp_mgr = tempfile.TemporaryDirectory()
            pack_dir = Path(temp_mgr.name)

        try:
            # 1) qlpack with C/C++ dependency
            (pack_dir / "qlpack.yml").write_text(
                "name: tmp-cpp-query\n"
                "version: 0.0.0\n"
                "dependencies:\n"
                "  codeql/cpp-all: \"*\"\n",
                encoding="utf-8",
            )

            # 2) install deps -> lock file
            self._run(["pack", "install"], cwd=pack_dir, timeout=timeout)

            # 3) write query EXACTLY as provided
            ql_file = pack_dir / "tmp_query.ql"
            bqrs_file = pack_dir / "tmp_result.bqrs"
            csv_file = pack_dir / "tmp_result.csv"
            ql_file.write_text(query, encoding="utf-8")

            # 4) run query -> bqrs
            self._run(
                ["query", "run", str(
                    ql_file), f"--database={self.db_path}", f"--output={bqrs_file}"],
                timeout=timeout,
            )

            # 5) decode -> content
            decode_cmd = ["bqrs", "decode", "--format=csv",
                          f"--output={csv_file}", str(bqrs_file)]

            decode_cmd.insert(2, f"--result-set={result_set}")
            self._run(decode_cmd, timeout=timeout)

            csv_text = csv_file.read_text(encoding="utf-8")
            return list(csv.reader(csv_text.splitlines()))

        finally:
            if temp_mgr and not keep_temp:
                temp_mgr.cleanup()

    # -------------- internals --------------

    def _run(self, args: List[str], cwd: Optional[Path] = None, timeout: Optional[int] = None) -> str:
        proc = subprocess.run(
            ["codeql"] + args,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or proc.stdout.strip())
        return proc.stdout.strip()
