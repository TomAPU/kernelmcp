#!/usr/bin/env bash
set -euo pipefail
# Make sure that everyone 
USERNAME="$(id -un)"
IMAGE_NAME="kernelmcp-nocmdutil-${USERNAME}"
CONTAINER_NAME="kernelmcp-nocmdutil-devcontainer-${USERNAME}"
HOST_PORT=3125
ADDITIONAL_FOLDER=""
PERMANENT=false
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

usage() {
  cat <<EOF
Usage: $(basename "$0") [options]

Options:
  -p, --port <host_port>     Map host <host_port> to container 3125 (default: 3125)
  -f, --folder <path>        Also mount <path> at /additional inside the container
  -pm, --permanent           Run container without --rm, reuse if it already exists
  -h, --help                 Show this help

Behavior:
  - Builds image '${IMAGE_NAME}' if it doesn't exist.
  - Runs privileged container '${CONTAINER_NAME}'.
  - Mounts \$PWD to /kernelmcp.
  - Optionally mounts (-f) to /additional.
EOF
}

# --- Parse args ---
while [[ $# -gt 0 ]]; do
  case "$1" in
    -p|--port)
      [[ $# -ge 2 ]] || { echo "Error: missing value for $1"; exit 1; }
      HOST_PORT="$2"; shift 2 ;;
    -f|--folder)
      [[ $# -ge 2 ]] || { echo "Error: missing value for $1"; exit 1; }
      ADDITIONAL_FOLDER="$2"; shift 2 ;;
    -pm|--permanent)
      PERMANENT=true; shift ;;
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

# --- Ensure image exists ---
if ! docker image inspect "${IMAGE_NAME}" >/dev/null 2>&1; then
  echo "Image '${IMAGE_NAME}' not found. Building..."
  MY_UID="$(id -u)"
  MY_GID="$(id -g)"
  if [[ -e /dev/kvm ]]; then
    KVM_GID="$(stat -c "%g" /dev/kvm)"
  else
    echo "FAIL: /dev/kvm not found. KVM is required."
    exit 1
  fi
  docker build --no-cache \
    --build-arg USERNAME="${USERNAME}" \
    --build-arg UID="${MY_UID}" \
    --build-arg GID="${MY_GID}" \
    --build-arg KVM_GID="${KVM_GID}" \
    -t "${IMAGE_NAME}" .
fi

# --- If container already exists ---
if docker ps -a --format '{{.Names}}' | grep -Fxq "${CONTAINER_NAME}"; then
  if docker ps --format '{{.Names}}' | grep -Fxq "${CONTAINER_NAME}"; then
    echo "Container '${CONTAINER_NAME}' is already running."
    echo "Attach with: docker exec -it ${CONTAINER_NAME} bash"
    exit 0
  else
    echo "Starting existing container '${CONTAINER_NAME}'..."
    exec docker start -ai "${CONTAINER_NAME}"
  fi
fi

# --- Compose docker run options ---
RUN_OPTS=( --name "${CONTAINER_NAME}" --privileged -it )
$PERMANENT || RUN_OPTS+=( --rm )
RUN_OPTS+=( -p "${HOST_PORT}:3125" )
RUN_OPTS+=( -v "${PWD}:/kernelmcp" )

if [[ -e /dev/kvm ]]; then
  RUN_OPTS+=( --device /dev/kvm --group-add kvm )
fi

if [[ -n "${ADDITIONAL_FOLDER}" ]]; then
  ADD_ABS="$(cd "$(dirname "${ADDITIONAL_FOLDER}")" && pwd)/$(basename "${ADDITIONAL_FOLDER}")"
  RUN_OPTS+=( -v "${ADD_ABS}:/additional" )
fi

# --- Run the container ---
echo "Creating new container '${CONTAINER_NAME}' from image '${IMAGE_NAME}'..."
exec docker run "${RUN_OPTS[@]}" "${IMAGE_NAME}"
