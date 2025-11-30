#!/usr/bin/env bash
set -euo pipefail

USERNAME="$(id -un)"
BASE_IMAGE_NAME="kernelmcp-${USERNAME}"
FULL_IMAGE_NAME="kernelmcp-full-${USERNAME}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FULL_DOCKERFILE="${SCRIPT_DIR}/Dockerfile.full"
REPO_REF="$(git -C "${SCRIPT_DIR}" rev-parse HEAD 2>/dev/null || true)"

usage() {
  cat <<EOF
Usage: $(basename "$0") [options]

Options:
  -r, --repo-ref <ref>   Override git ref/commit (default: current HEAD)
  -h, --help             Show this help message

Behavior:
  - Ensures image '${BASE_IMAGE_NAME}' exists (builds identical to rundevcontainer.sh if missing).
  - Builds '${FULL_IMAGE_NAME}' from '${FULL_DOCKERFILE}' using the repo currently checked out on disk.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -r|--repo-ref)
      [[ $# -ge 2 ]] || { echo "Error: missing value for $1"; exit 1; }
      REPO_REF="$2"; shift 2 ;;
    -h|--help)
      usage; exit 0 ;;
    *)
      echo "Unknown option: $1"; usage; exit 1 ;;
  esac
done

[[ -n "${REPO_REF}" ]] || { echo "Error: unable to determine repo ref. Use --repo-ref."; exit 1; }
[[ -f "${FULL_DOCKERFILE}" ]] || { echo "Error: ${FULL_DOCKERFILE} not found."; exit 1; }

export DOCKER_BUILDKIT=1

ensure_base_image() {
  if docker image inspect "${BASE_IMAGE_NAME}" >/dev/null 2>&1; then
    return
  fi

  echo "Image '${BASE_IMAGE_NAME}' not found. Building..."
  local my_uid my_gid kvm_gid
  my_uid="$(id -u)"
  my_gid="$(id -g)"
  if [[ -e /dev/kvm ]]; then
    kvm_gid="$(stat -c "%g" /dev/kvm)"
  else
    echo "FAIL: /dev/kvm not found. KVM is required."
    exit 1
  fi

  docker build --no-cache \
    --build-arg USERNAME="${USERNAME}" \
    --build-arg UID="${my_uid}" \
    --build-arg GID="${my_gid}" \
    --build-arg KVM_GID="${kvm_gid}" \
    -t "${BASE_IMAGE_NAME}" \
    "${SCRIPT_DIR}"
}

build_full_image() {
  echo "Building '${FULL_IMAGE_NAME}' from '${BASE_IMAGE_NAME}'..."
  docker build \
    --build-arg BASE_IMAGE="${BASE_IMAGE_NAME}" \
    --build-arg USERNAME="${USERNAME}" \
    --build-arg REPO_REF="${REPO_REF}" \
    -t "${FULL_IMAGE_NAME}" \
    -f "${FULL_DOCKERFILE}" \
    "${SCRIPT_DIR}"
}

ensure_base_image
build_full_image

echo "Done. Built image '${FULL_IMAGE_NAME}'."
