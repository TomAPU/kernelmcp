# KernelMCP Dev Container Script

This script builds and runs a development container for **KernelMCP** using Docker.
It simplifies managing the `kernelmcp-dev` container with options for port mapping, volume mounts, and persistent or temporary containers.

## Features

* Automatically builds the Docker image (`kernelmcp`) if it does not exist.
* Runs a privileged container named `kernelmcp-dev`.
* Mounts the current working directory into `/kernelmcp` inside the container.
* Optionally mounts an additional folder.
* Supports two modes:

  * **Temporary mode (default):** container runs with `--rm`, automatically removed after exit.
  * **Permanent mode (`--permanent`):** container persists after exit and can be reused.

## Usage

```bash
./run-dev.sh [options]
```

### Options

| Option                   | Description                                                              |
| ------------------------ | ------------------------------------------------------------------------ |
| `-p, --port <host_port>` | Map host `<host_port>` to container port `3125` (default: `3125`).       |
| `-f, --folder <path>`    | Mount an additional host folder into `/additional` inside the container. |
| `-pm, --permanent`       | Run without `--rm`. If the container already exists, it will be reused.  |
| `-h, --help`             | Show usage information.                                                  |

### Behavior

* **If the container already exists:**

  * If running → the script will not start a new one, but tell you how to attach.
  * If stopped → it will be started again (`docker start -ai`).
* **If the container does not exist:** a new one will be created with the given options.

### Examples

Run in default mode (temporary, auto-removed):

```bash
./run-dev.sh
```

Run on a custom port:

```bash
./run-dev.sh --port 4000
```

Run with an extra folder mounted:

```bash
./run-dev.sh -f ./my_data
```

Run in permanent mode:

```bash
./run-dev.sh --permanent
```

---

Do you want me to also include a **section for developers** about how to attach back to the container (`docker exec -it kernelmcp-dev bash`) when it’s running in permanent mode?
