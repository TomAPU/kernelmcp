#!/usr/bin/env bash
set -euo pipefail

USERNAME="$(id -un)"
FULL_IMAGE_NAME="kernelmcp-superweaken-full-${USERNAME}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FULL_DOCKERFILE="${SCRIPT_DIR}/Dockerfile.full"
REPO_REF="$(git -C "${SCRIPT_DIR}" rev-parse HEAD 2>/dev/null || true)"
BUILDX_BUILDER="kernelmcp-superweaken-builder-${USERNAME}"

usage() {
  cat <<EOF
Usage: $(basename "$0") [options]

Options:
  -r, --repo-ref <ref>   Override git ref/commit (default: current HEAD)
  -h, --help             Show this help message

Behavior:
  - Builds '${FULL_IMAGE_NAME}' directly from '${FULL_DOCKERFILE}' using Docker Buildx in privileged mode.
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

MY_UID="$(id -u)"
MY_GID="$(id -g)"
if [[ -e /dev/kvm ]]; then
  KVM_GID="$(stat -c "%g" /dev/kvm)"
else
  echo "FAIL: /dev/kvm not found. KVM is required."
  exit 1
fi

ensure_buildx_available() {
  if docker buildx version >/dev/null 2>&1; then
    return
  fi

  echo "Docker Buildx CLI not active. Installing via 'docker buildx install'..."
  docker buildx install >/dev/null
}

ensure_buildx_builder() {
    echo "Ensuring privileged Buildx builder '${BUILDX_BUILDER}' ..."

    # If builder exists, remove it to recreate with correct privileged config
    if docker buildx inspect "${BUILDX_BUILDER}" >/dev/null 2>&1; then
        echo "Removing existing builder '${BUILDX_BUILDER}' because it may not be privileged..."
        docker buildx rm "${BUILDX_BUILDER}" >/dev/null 2>&1 || true
    fi

    echo "Creating Buildx builder '${BUILDX_BUILDER}' with REAL privileged worker + entitlements..."

    docker buildx create \
    --name "${BUILDX_BUILDER}" \
    --driver docker-container \
    --buildkitd-flags '--allow-insecure-entitlement security.insecure --allow-insecure-entitlement network.host' \
    --use >/dev/null

    echo "Builder '${BUILDX_BUILDER}' created. Inspecting..."
    docker buildx inspect "${BUILDX_BUILDER}"
}


build_full_image() {
  echo "Building '${FULL_IMAGE_NAME}' from '${FULL_DOCKERFILE}' via Buildx (privileged)..."
  docker buildx build \
    --builder "${BUILDX_BUILDER}" \
    --allow security.insecure \
    --load \
    --build-arg USERNAME="${USERNAME}" \
    --build-arg UID="${MY_UID}" \
    --build-arg GID="${MY_GID}" \
    --build-arg KVM_GID="${KVM_GID}" \
    --build-arg REPO_REF="${REPO_REF}" \
    -t "${FULL_IMAGE_NAME}" \
    -f "${FULL_DOCKERFILE}" \
    "${SCRIPT_DIR}"
}


ensure_buildx_available
ensure_buildx_builder
build_full_image

echo "Done. Built image '${FULL_IMAGE_NAME}'."
