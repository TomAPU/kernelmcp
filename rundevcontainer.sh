#!/usr/bin/env bash
set -euo pipefail

IMAGE_NAME="kernelmcp"
CONTAINER_NAME="kernelmcp-dev"
HOST_PORT=3125
ADDITIONAL_FOLDER=""
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

usage() {
  cat <<EOF
Usage: $(basename "$0") [options]

Options:
  -p, --port <host_port>     Map host <host_port> to container 3125 (default: 3125)
  -f, --folder <path>        Also mount <path> at /additional inside the container
  -h, --help                 Show this help

Behavior:
  - Builds image '${IMAGE_NAME}' if it doesn't exist.
  - Runs privileged container '${CONTAINER_NAME}'.
  - Mounts \$PWD to /kernelmcp.
  - Optionally mounts (-f) to /additional.
EOF
}

# --- Parse args (support short and long) ---
while [[ $# -gt 0 ]]; do
  case "$1" in
    -p|--port)
      [[ $# -ge 2 ]] || { echo "Error: missing value for $1"; exit 1; }
      HOST_PORT="$2"; shift 2 ;;
    -f|--folder)
      [[ $# -ge 2 ]] || { echo "Error: missing value for $1"; exit 1; }
      ADDITIONAL_FOLDER="$2"; shift 2 ;;
    -h|--help)
      usage; exit 0 ;;
    *)
      echo "Unknown option: $1"; usage; exit 1 ;;
  esac
done

# --- Validate inputs ---
if [[ -n "${ADDITIONAL_FOLDER}" && ! -e "${ADDITIONAL_FOLDER}" ]]; then
  echo "Error: additional folder does not exist: ${ADDITIONAL_FOLDER}"
  exit 1
fi

# --- Ensure image exists (build if missing) ---
if ! docker image inspect "${IMAGE_NAME}" >/dev/null 2>&1; then
  echo "Image '${IMAGE_NAME}' not found. Building..."
  USERNAME="$(id -un)"
  MY_UID="$(id -u)"
  MY_GID="$(id -g)"
  docker build --no-cache \
    --build-arg USERNAME="${USERNAME}" \
    --build-arg UID="${MY_UID}" \
    --build-arg GID="${MY_GID}" \
    -t "${IMAGE_NAME}" .
fi

# --- Remove any existing container with the same name ---
if docker ps -a --format '{{.Names}}' | grep -Fxq "${CONTAINER_NAME}"; then
  echo "Removing existing container '${CONTAINER_NAME}'..."
  docker rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true
fi

# --- Compose docker run options ---
RUN_OPTS=( --name "${CONTAINER_NAME}" --privileged --rm -it )
RUN_OPTS+=( -p "${HOST_PORT}:3125" )
RUN_OPTS+=( -v "${PWD}:/kernelmcp" )

# Pass through KVM device if present (helps nested virtualization)
if [[ -e /dev/kvm ]]; then
  RUN_OPTS+=( --device /dev/kvm )
fi

# Optional additional mount
if [[ -n "${ADDITIONAL_FOLDER}" ]]; then
  # Resolve to absolute path for safety
  ADD_ABS="$(cd "$(dirname "${ADDITIONAL_FOLDER}")" && pwd)/$(basename "${ADDITIONAL_FOLDER}")"
  RUN_OPTS+=( -v "${ADD_ABS}:/additional" )
fi

# --- Run the container ---
echo "Starting container '${CONTAINER_NAME}' from image '${IMAGE_NAME}'..."
echo "  Port mapping: host ${HOST_PORT} -> container 3125"
echo "  Mounts: ${PWD} -> /kernelmcp"
if [[ -n "${ADDITIONAL_FOLDER}" ]]; then
  echo "          ${ADDITIONAL_FOLDER} -> /additional"
fi

exec docker run "${RUN_OPTS[@]}" "${IMAGE_NAME}"
