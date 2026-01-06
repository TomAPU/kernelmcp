from __future__ import annotations
import logging
import json
import tempfile
import subprocess
from pathlib import Path
from typing import Optional, List, Any

from .utils import find_project_root, run_cmd


class SyzLogparser:
    """Skeleton SyzLogparser.

    Initialized with a `logger` and raw syzlog `content` (string).
    Resolves the absolute path to the syz-log parser helper binary under
    ``tools/syz-logparser/syz-logparser`` and stores it on
    ``self.syzloggerpath``.
    """

    def __init__(self, logger: Optional[logging.Logger] = None, content: str = "", timeout: int = 10):
        self.content = content
        self.logger = logger or logging.getLogger(__name__)
        if not self.logger.handlers:
            self.logger.addHandler(logging.NullHandler())
        self.timeout = timeout

        # Discover project root via shared infra.utils helper.
        self.project_root = find_project_root()
        # Absolute path to tools/syz-logparser/syz-logparser
        self.syzloggerpath = (self.project_root / "tools" / "syz-logparser" / "syz-logparser").resolve()
        self.logger.debug("Initialized SyzLogparser with syzloggerpath=%s", self.syzloggerpath)
        if not self.syzloggerpath.is_file():
            raise FileNotFoundError(f"syz-logparser binary not found at {self.syzloggerpath}")


    def parse(self) -> List[Any]:
        """
        Run syz-logparser on the current content and return a JSON list.
        Returns [] on any error / timeout / invalid output.
        """

        def fail(msg: str, *args: Any) -> List[Any]:
            self.logger.error(msg, *args)
            return []

        try:
            with tempfile.TemporaryDirectory() as td:
                tmp_path = Path(td) / "kernel.log"
                tmp_path.write_text(self.content or "", encoding="utf-8", errors="ignore")

                cmd = [str(self.syzloggerpath), "-json", str(tmp_path)]
                self.logger.debug("Running syz-logparser: %s", cmd)

                try:
                    out = run_cmd(cmd, logger=self.logger, timeout=self.timeout)
                except subprocess.TimeoutExpired:
                    return fail("syz-logparser timed out after %s seconds", self.timeout)

                try:
                    data = json.loads(out or "[]")
                except json.JSONDecodeError:
                    return fail("Failed to decode syz-logparser JSON output")

                if not isinstance(data, list):
                    return fail("syz-logparser JSON output is not a list: %r", data)

                return data

        except Exception:
            # Any unexpected error: keep traceback for debugging, still return [].
            self.logger.exception("syz-logparser parse failed unexpectedly")
            return []
