#!/usr/bin/env bash
set -euo pipefail

# --- prerequisites ---
command -v uv >/dev/null 2>&1 || { echo "[ERROR] 'uv' not found in PATH." >&2; exit 1; }
command -v codeql >/dev/null 2>&1 || { echo "[ERROR] 'codeql' not found in PATH." >&2; exit 1; }

# --- run uv sync ---
if ! uv sync; then
  echo "[ERROR] 'uv sync' failed." >&2
  exit 1
fi

# --- download CodeQL pack ---
if ! codeql pack download codeql/cpp-all; then
  echo "[ERROR] 'codeql pack download codeql/cpp-all' failed." >&2
  exit 1
fi

echo "[INFO] Done."
