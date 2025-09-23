# infra/codeql_utils.py
from __future__ import annotations
import csv
import shutil
import tempfile
import time
import logging
from pathlib import Path
from typing import List, Optional
from .utils import run_cmd


class CodeQLCpp:
    """
    Minimal CodeQL wrapper for C/C++ using pack install + CSV decode.

    - Creates a temp QL pack with dependency: codeql/cpp-all: "*"
    - Runs `codeql pack install` to create a lockfile (no --search-path needed)
    - Does NOT modify your query text (you must keep it valid)
    - Decodes BQRS to CSV and returns CSV text (str)
    """

    def __init__(self, db_path: str, logger: Optional[logging.Logger] = None):
        # ---- minimal logging hook (no signature change) ----
        self.logger = logger
        if not self.logger:
            self.logger = logging.getLogger(__name__)
            self.logger.addHandler(logging.NullHandler())

        self.db_path = Path(db_path).resolve()
        self.logger.debug("Init CodeQLCpp: db_path=%s", self.db_path)
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
        self.logger.debug(
            "run_query_text_csv start (result_set=%s, keep_temp=%s, persistent_pack_dir=%s)",
            result_set, keep_temp, bool(persistent_pack_dir)
        )

        temp_mgr = None
        if persistent_pack_dir:
            pack_dir = Path(persistent_pack_dir).resolve()
            pack_dir.mkdir(parents=True, exist_ok=True)
            self.logger.debug("Using persistent pack_dir: %s", pack_dir)
        else:
            temp_mgr = tempfile.TemporaryDirectory()
            pack_dir = Path(temp_mgr.name)
            self.logger.debug("Using temporary pack_dir: %s", pack_dir)

        try:
            # 1) qlpack with C/C++ dependency
            qlpack_path = pack_dir / "qlpack.yml"
            qlpack_path.write_text(
                "name: tmp-cpp-query\n"
                "version: 0.0.0\n"
                "dependencies:\n"
                "  codeql/cpp-all: \"*\"\n",
                encoding="utf-8",
            )
            self.logger.debug("Wrote qlpack.yml -> %s", qlpack_path)

            # 2) install deps -> lock file
            self._run(["pack", "install"], cwd=pack_dir, timeout=timeout)

            # 3) write query EXACTLY as provided
            ql_file = pack_dir / "tmp_query.ql"
            bqrs_file = pack_dir / "tmp_result.bqrs"
            csv_file = pack_dir / "tmp_result.csv"
            ql_file.write_text(query, encoding="utf-8")
            self.logger.debug("Wrote query -> %s (bytes=%d)",
                              ql_file, ql_file.stat().st_size)

            # 4) run query -> bqrs
            self._run(
                ["query", "run", str(
                    ql_file), f"--database={self.db_path}", f"--output={bqrs_file}"],
                timeout=timeout,
            )
            if not bqrs_file.exists():
                self.logger.error("BQRS not produced: %s", bqrs_file)
                raise RuntimeError(f"BQRS was not produced: {bqrs_file}")
            self.logger.debug("BQRS OK -> %s (bytes=%d)",
                              bqrs_file, bqrs_file.stat().st_size)

            # 5) decode -> content
            decode_cmd = ["bqrs", "decode", "--format=csv",
                          f"--output={csv_file}", str(bqrs_file)]

            decode_cmd.insert(2, f"--result-set={result_set}")
            self._run(decode_cmd, timeout=timeout)

            if not csv_file.exists():
                raise RuntimeError(f"CSV was not produced for: {bqrs_file}")
            csv_text = csv_file.read_text(encoding="utf-8")
            if not csv_text.strip():
                raise RuntimeError("Query returned no results (empty CSV)")

            rows = list(csv.reader(csv_text.splitlines()))
            self.logger.info("Decoded CSV rows=%d", len(rows))
            return rows

        finally:
            if temp_mgr and not keep_temp:
                temp_mgr.cleanup()

    # -------------- internals --------------

    def _run(self, args: List[str], cwd: Optional[Path] = None, timeout: Optional[int] = None) -> str:
        cmd = ["codeql"] + args
        # Delegate actual subprocess execution to infra.utils.run_cmd so behavior
        # (logging, timeout handling, error reporting) is consistent across infra.
        return run_cmd(cmd, logger=self.logger, timeout=timeout, cwd=cwd)
