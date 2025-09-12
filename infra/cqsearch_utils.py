# infra/cqsearch_utils.py
from __future__ import annotations
import logging
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple


@dataclass
class CqHit:
    name: str
    path: str
    line: int
    preview: str
    kind: str     # "function_or_macro" | "class_or_struct" | "reference" | "call"
    source: str   # e.g., "p=2", "p=3", "p=1", "p=8"


class CodeQuerySearch:
    """
    Strict wrapper for CodeQuery's `cqsearch`:

    - Definitions: exact `-p 2` (function/macro), exact `-p 3` (class/struct)
    - References: exact `-p 1` (symbol) minus definitions; add exact `-p 8` (calls) if requested
    - Kinds depend solely on the `-p` mode; no heuristics.
    """

    def __init__(self, db_path: str, logger: Optional[logging.Logger] = None):
        self.logger = logger or logging.getLogger(__name__)
        if not self.logger.handlers:
            self.logger.addHandler(logging.NullHandler())

        self.db_path = Path(db_path).resolve()
        if not self.db_path.exists():
            raise FileNotFoundError(f"CodeQuery DB not found: {self.db_path}")
        if not shutil.which("cqsearch"):
            raise FileNotFoundError("cqsearch CLI not found in PATH")

    # ---------------- Public API ----------------

    def get_definition(
        self,
        symbol: str,
        *,
        path_filter: Optional[str] = None,
        timeout: Optional[int] = None,
    ) -> List[Dict]:
        """Strict definitions: p=2 (function/macro) ∪ p=3 (class/struct). All exact-match."""
        self._check_symbol(symbol)
        hits: List[CqHit] = []
        hits += self._search(symbol, p=2,
                             path_filter=path_filter, timeout=timeout)
        hits += self._search(symbol, p=3,
                             path_filter=path_filter, timeout=timeout)

        # Label kinds by p-mode only
        for h in hits:
            if h.source == "p=2":
                h.kind = "function_or_macro"
            elif h.source == "p=3":
                h.kind = "class_or_struct"

        return [self._hit_to_json(h) for h in self._dedup(hits)]

    def get_reference(
        self,
        symbol: str,
        *,
        path_filter: Optional[str] = None,
        timeout: Optional[int] = None,
        include_calls: bool = True,
    ) -> List[Dict]:
        """
        Strict references:
          refs = (p=1 exact) \\ defs(p=2|p=3 exact)
          calls (if include_calls=True) = p=8 exact
        """
        self._check_symbol(symbol)

        # 1) collect defs to subtract
        defs = []
        defs += self._search(symbol, p=2,
                             path_filter=path_filter, timeout=timeout)
        defs += self._search(symbol, p=3,
                             path_filter=path_filter, timeout=timeout)
        def_locs = {(h.path, h.line) for h in defs}

        # 2) generic symbol appearances
        refs = []
        sym_hits = self._search(
            symbol, p=1, path_filter=path_filter, timeout=timeout)
        for h in sym_hits:
            if (h.path, h.line) in def_locs:
                continue
            h.kind = "reference"
            refs.append(h)

        # 3) optional calls
        if include_calls:
            calls = self._search(
                symbol, p=8, path_filter=path_filter, timeout=timeout)
            for h in calls:
                h.kind = "call"
            refs.extend(calls)

        return [self._hit_to_json(h) for h in self._dedup(refs)]

    # ---------------- Internals ----------------

    def _run(self, args: List[str], timeout: Optional[int] = None) -> str:
        cmd = ["cqsearch"] + args
        self.logger.debug("Exec: %s", " ".join(cmd))
        t0 = time.time()
        proc = subprocess.run(cmd, capture_output=True,
                              text=True, timeout=timeout)
        dt = time.time() - t0
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip()
            raise RuntimeError(
                f"cqsearch rc={proc.returncode} ({dt:.2f}s)\n{err}")
        return (proc.stdout or "").rstrip("\n")

    def _search(
        self,
        term: str,
        *,
        p: int,
        path_filter: Optional[str],
        timeout: Optional[int],
    ) -> List[CqHit]:
        # exact (-e), full path (-u), unlimited preview (-l 0)
        args = ["-s", str(self.db_path), "-p", str(p),
                "-t", term, "-e", "-u", "-l", "0"]
        if path_filter:
            args += ["-b", path_filter]

        out = self._run(args, timeout=timeout)
        if not out.strip():
            return []

        hits: List[CqHit] = []
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            name = parts[0].strip() or term
            file_and_line = parts[1].strip()
            preview = parts[2] if len(parts) > 2 else ""

            fpath, lno = self._split_file_line(file_and_line)
            if fpath is None or lno is None:
                continue

            hits.append(CqHit(
                name=name,
                path=fpath,
                line=lno,
                preview=preview,
                kind="reference" if p == 1 else (
                    "call" if p == 8 else "class_or_struct" if p == 3 else "function_or_macro" if p == 2 else "reference"),
                source=f"p={p}"
            ))
        return hits

    @staticmethod
    def _split_file_line(file_and_line: str) -> Tuple[Optional[str], Optional[int]]:
        if ":" not in file_and_line:
            return None, None
        f, l = file_and_line.rsplit(":", 1)
        try:
            return f, int(l)
        except ValueError:
            return None, None

    @staticmethod
    def _check_symbol(s: str) -> None:
        if not s or any(ch.isspace() for ch in s):
            raise ValueError("symbol must be a non-empty, space-free token")

    @staticmethod
    def _hit_to_json(h: CqHit) -> Dict:
        return {
            "name": h.name,
            "path": h.path,
            "line": h.line,
            "preview": h.preview,
            "kind": h.kind,
            "source": h.source
        }

    @staticmethod
    def _dedup(hits: List[CqHit]) -> List[CqHit]:
        seen = set()
        out = []
        for h in hits:
            key = (h.name, h.path, h.line, h.source)
            if key in seen:
                continue
            seen.add(key)
            out.append(h)
        return out
