#!/usr/bin/env bash
set -euo pipefail

# --- Prerequisites ---
command -v uv >/dev/null 2>&1 || { echo "[ERROR] 'uv' not found in PATH." >&2; exit 1; }
command -v codeql >/dev/null 2>&1 || { echo "[ERROR] 'codeql' not found in PATH." >&2; exit 1; }

# --- Run uv sync ---
if ! uv sync; then
  echo "[ERROR] 'uv sync' failed." >&2
  exit 1
fi

# --- Download CodeQL pack ---
if ! codeql pack download codeql/cpp-all; then
  echo "[ERROR] 'codeql pack download codeql/cpp-all' failed." >&2
  exit 1
fi

# --- Syzkaller image creation ---
mkdir -p tools/
mkdir -p tools/diskimage

if [[ ! -f tools/diskimage/IMAGE_CREATED ]]; then
  echo "[INFO] Creating syzkaller image..."
(
  destdir="$(pwd)/tools/diskimage"
  mkdir -p "$destdir"

  tmpdir="$(mktemp -d)"
  trap 'sudo rm -rf "$tmpdir"' EXIT

  (
    cd "$tmpdir"
    if [[ ! -f create-image.sh ]]; then
      wget https://raw.githubusercontent.com/google/syzkaller/master/tools/create-image.sh
    fi
    chmod +x create-image.sh
    ./create-image.sh -d bullseye --feature full
    touch IMAGE_CREATED

    # Unify the image name and SSH key name 
    mv bullseye.img diskimage.img
    mv bullseye.id_rsa sshkey.id_rsa
    mv bullseye.id_rsa.pub sshkey.id_rsa.pub

    # Copy results back to tools/diskimage
    cp diskimage.img sshkey.id_rsa sshkey.id_rsa.pub "$destdir/"
    cp IMAGE_CREATED "$destdir/"
  )
)
else
  echo "[INFO] Syzkaller image already exists. Skipping creation."
fi

# --- syz-logparser download ---
# From https://github.com/TomAPU/syz-logparser/releases/download/v0.2/syz-logparser-x86_64-linux
# tools/syz-logparser/syz-logparser
if [[ ! -f tools/syz-logparser/syz-logparser ]]; then
  echo "[INFO] Downloading syz-logparser..."
  mkdir -p tools/syz-logparser
  (
    cd tools/syz-logparser
    wget https://github.com/TomAPU/syz-logparser/releases/download/v0.2/syz-logparser-x86_64-linux
    mv syz-logparser-x86_64-linux syz-logparser
    chmod +x syz-logparser
  )
else
  echo "[INFO] syz-logparser already exists. Skipping download."
fi


# --- Setup ezqm ---
echo "[INFO] Setting up ezqm..."
mkdir -p tools/memsnapshots


# Activate Python virtual environment
if [[ ! -f .venv/bin/activate ]]; then
  echo "[ERROR] Python virtual environment not found at .venv/bin/activate" >&2
  exit 1
fi

source .venv/bin/activate

# Ensure ezcf is available
if ! command -v ezcf >/dev/null 2>&1; then
  echo "[ERROR] 'ezcf' not found in PATH (is ezqm installed in this venv?)." >&2
  deactivate || true
  exit 1
fi

# Check if diskimage is already configured in ezqm
if ! ezcf -g -r | grep -q "snapshot"; then
  echo "[INFO] ezqm diskimage not configured. Setting it up..."

  # Get absolute paths for image and SSH key
  IMAGE_ABS_PATH="$(realpath tools/diskimage/diskimage.img)"
  SSHKEY_ABS_PATH="$(realpath tools/diskimage/sshkey.id_rsa)"
  SNAPSHOTFOLDER_ABS_PATH="$(realpath tools/memsnapshots)"

  # Update ezqm global settings
  ezcf -g -u diskimage "${IMAGE_ABS_PATH}"
  ezcf -g -u sshkey "${SSHKEY_ABS_PATH}"
  ezcf -g -u snapshotfolder "${SNAPSHOTFOLDER_ABS_PATH}"

  echo "[INFO] ezqm configuration completed."
else
  echo "[INFO] ezqm already configured. Skipping setup."
fi

# Deactivate the virtual environment safely
deactivate || true

echo "[INFO] Done."
