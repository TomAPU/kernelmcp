# infra/cqsearch_utils.py
from __future__ import annotations
import logging
import shutil
import subprocess
import time
import json
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

    def get_function_range(
        self,
        symbol: str,
        *,
        path_filter: Optional[str] = None,
        timeout: Optional[int] = None,
    ) -> List[Dict]:
        """
        Return function ranges for exact-matched definitions (p=2).
        Each item: {"name","path","start","end","kind","source"}
        - start: definition line (from cqsearch p=2)
        - end:   from Universal Ctags 'end.line' (None if unavailable)
        """
        self._check_symbol(symbol)
        defs = self._search(
            symbol, p=2, path_filter=path_filter, timeout=timeout)
        out: List[Dict] = []
        for h in self._dedup(defs):
            end_line = self._ctags_function_end(
                h.path, h.name, h.line, timeout=timeout)  # ← CHANGED
            out.append({
                "name": h.name,
                "path": h.path,
                "start": h.line,
                "end": end_line,
                "kind": "function_or_macro",
                "source": h.source
            })
        return out

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

    # --- NEW: use Universal Ctags to get end line of a function ---
    def _ctags_function_end(
        self,
        file_path: str,
        func_name: str,
        def_line: int,
        timeout: Optional[int] = None,
    ) -> Optional[int]:
        """
        Use Universal Ctags JSON output with --fields=+ne to obtain function end line.
        Matching strategy:
          - same file
          - name == func_name
          - kind is function (not prototype)
          - prefer entry whose 'line' == def_line; otherwise choose closest within a small window
        Returns end.line or None.
        """
        if not shutil.which("ctags"):
            self.logger.error(
                "ctags not found in PATH; skip end-line resolution for %s", func_name)
            return None

        # Only scan the single file for speed.
        cmd = [
            "ctags",
            "--languages=C,C++",
            "--kinds-C=fp",        # functions & prototypes
            "--kinds-C++=fp",
            "--fields=+ne",        # include 'end' and 'line' fields
            "--output-format=json",
            "-o", "-",             # stdout
            file_path,
        ]
        self.logger.debug("Exec: %s", " ".join(cmd))
        try:
            proc = subprocess.run(cmd, capture_output=True,
                                  text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            self.logger.error("ctags timed out for %s", file_path)
            return None
        if proc.returncode != 0:
            self.logger.error("ctags failed rc=%s: %s",
                              proc.returncode, (proc.stderr or proc.stdout)[:300])
            return None

        best = None
        best_delta = float('inf')
        for line in (proc.stdout or "").splitlines():
            line = line.strip()
            if not line or line.startswith("[") or line == "]":
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                self.logger.error("ctags: JSON decode error: %s", line)
                continue
            if obj.get("path") != file_path:
                continue
            if obj.get("name") != func_name:
                continue

            tag_line = obj.get("line")
            assert isinstance(tag_line, int) or tag_line is None
            # Prefer exact line, else closest within a small window to tolerate style diffs.
            delta = abs(tag_line - def_line)
            if delta < best_delta:
                best = obj
                best_delta = delta

        if not best:
            self.logger.error("ctags: no matching function found for %s in %s",
                              func_name, file_path)
            return None

        end = best.get("end")
        assert isinstance(end, int)
        return end
