from __future__ import annotations
import logging
import shutil
import re
from pathlib import Path
from typing import Dict, List, Optional
import fnmatch
import os

from .utils import run_cmd


class SourceView:
    """Lightweight source file viewer/searcher restricted to a code root.

    Provides:
      - get_source(rel_path, start, end)
      - search(rel_path_or_dir, pattern, ...)
      - list_files(rel_dir, pattern, recursive)

    Uses `rg` when available, otherwise falls back to a Python implementation.
    """

    def __init__(self, code_path: str, logger: Optional[logging.Logger] = None):
        self.code_path = Path(code_path).resolve()
        if not self.code_path.exists():
            raise FileNotFoundError(f"code_path not found: {self.code_path}")

        self.logger = logger or logging.getLogger(__name__)
        if not self.logger.handlers:
            self.logger.addHandler(logging.NullHandler())

        # detect ripgrep availability
        self._has_rg = shutil.which("rg") is not None
        if not self._has_rg:
            self.logger.debug("rg not found; falling back to Python search")

    # ---------- helpers ----------
    def _safe_resolve(self, rel: str) -> Path:
        # Normalize and ensure path is inside code_path
        p = (self.code_path / rel).resolve()
        try:
            p.relative_to(self.code_path)
        except Exception:
            raise ValueError("path outside of code root")
        return p

    def _is_binary(self, path: Path) -> bool:
        try:
            with open(path, 'rb') as f:
                chunk = f.read(1024)
                return b'\0' in chunk
        except Exception:
            return True

    # ---------- public API ----------
    def get_source(self, rel_path: str, start: int = 1, end: Optional[int] = None, include_linenos: bool = False) -> Dict:
        p = self._safe_resolve(rel_path)
        if not p.exists() or not p.is_file():
            raise FileNotFoundError(rel_path)

        if self._is_binary(p):
            return {"path": rel_path, "binary": True}

        lines_out: List[Dict] = []
        encoding_used = None
        total_lines = 0
        with open(p, 'rb') as f:
            raw = f.read()
        for enc in ("utf-8", "latin-1"):
            try:
                text = raw.decode(enc)
                encoding_used = enc
                break
            except Exception:
                continue
        if encoding_used is None:
            # as last resort, use surrogateescape-like decode
            text = raw.decode('utf-8', errors='surrogateescape')
            encoding_used = 'surrogateescape'

        all_lines = text.splitlines()
        total_lines = len(all_lines)


        # Interpret start/end as 1-based inclusive indices. Normalize to file bounds.
        start = max(1, start)
        end_idx = total_lines if end is None else end
        end_idx = min(end_idx, total_lines)
        start = min(start, total_lines)

        # no max_lines enforced for now
        truncated = False

        # Build lines list with 1-based lineno
        for idx in range(start, end_idx + 1):
            lines_out.append({"lineno": idx, "text": all_lines[idx - 1]})

        # Build code string; if include_linenos, prefix each line with the lineno
        if include_linenos:
            numbered = [f"{str(item['lineno']).rjust(6)}: {item['text']}" for item in lines_out]
            code_snippet = "\n".join(numbered)
        else:
            code_snippet = "\n".join(item["text"] for item in lines_out)

        return {
            "path": rel_path,
            "start": start,
            "end": end_idx,
            "code": code_snippet,
            "lines": lines_out,
            "truncated": truncated,
            "encoding": encoding_used,
            "total_lines": total_lines,
        }

    def search(self, rel_path: Optional[str], pattern: str, *, regex: bool = False, ignore_case: bool = False, max_results: int = 200, timeout: Optional[int] = None) -> List[Dict]:
        # determine search root
        if rel_path:
            root = self._safe_resolve(rel_path)
            if root.is_file():
                root_dir = root.parent
            else:
                root_dir = root
        else:
            root_dir = self.code_path

        results: List[Dict] = []
        if not self._has_rg:
            raise RuntimeError("rg (ripgrep) not available in PATH")

        if self._has_rg:
            args = ["rg", "--line-number", "--no-heading", "--hidden", "--glob", "!.git"]
            if not regex:
                args.append("-F")
            if ignore_case:
                args.append("-i")
            args += [pattern, str(root_dir)]
            try:
                out = run_cmd(args, logger=self.logger, timeout=timeout)
            except Exception as e:
                self.logger.warning("rg search failed: %s", e)
                out = ""

            for line in out.splitlines():
                # format: path:lineno:match
                parts = line.split(":", 2)
                if len(parts) < 3:
                    continue
                path, lno, match = parts
                try:
                    lno_i = int(lno)
                except Exception:
                    continue
                results.append({"path": os.path.relpath(path, self.code_path), "lineno": lno_i, "match": match})
                if len(results) >= max_results:
                    break
            return results

        # No Python fallback: if ripgrep is not available we raise earlier.
        return results

    def list_files(self, rel_dir: str = ".", *, pattern: Optional[str] = None, recursive: bool = True) -> List[Dict]:
        root = self._safe_resolve(rel_dir)
        if not root.exists():
            raise FileNotFoundError(rel_dir)

        out: List[Dict] = []
        if root.is_file():
            stat = root.stat()
            out.append({"path": os.path.relpath(str(root), self.code_path), "size": stat.st_size})
            return out

        if recursive:
            walker = root.rglob("*")
        else:
            walker = root.iterdir()

        for p in walker:
            if p.is_file():
                rel = os.path.relpath(str(p), self.code_path)
                if pattern and not fnmatch.fnmatch(p.name, pattern):
                    continue
                st = p.stat()
                out.append({"path": rel, "size": st.st_size})

        return out
