# Quick Start

## Premise

You need a Linux source code directory **with a compiled kernel**, i.e., `vmlinux` and `bzImage` must already be generated.

## Build and Run the Docker Container

This script builds the Docker image and runs a container, mounting your Linux source directory to `/additional` inside the container.
The Docker image will be named `kernelmcp-<your username>`, and the container will be named `kernelmcp-devcontainer-<your username>`.

```bash
./rundevcontainer.sh -f <Linux source> -pm -p <port>
```

* `-pm` keeps the container permanently (i.e., it won’t be removed after exit).
* `-p <port>` maps the specified host port to the container’s port.

## Initialization

Inside the container, run:

```bash
cd /kernelmcp/
./install.sh
source .venv/bin/activate
```

## Create a Project

```bash
python -m kernelmcp.project -l /additional/ -n <project name>
```

This command creates a project directory under `./project/<project name>`
and generates a `config.json` file inside it.

## Run

```bash
python -m kernelmcp.main -c <path to config.json>
```

> **Note:** The container’s port `3125` is mapped to the host port specified with the `-p` option in `rundevcontainer.sh`, so you don’t need to change the port option in `kernelmcp.main`.

The MCP server will then be available at:
`http://127.0.0.1:<port>/mcp`
