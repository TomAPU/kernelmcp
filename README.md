# KernelMCP

## Dev Container Script
The description and usage of `rundevcontainer.sh` is in [devcontainer.md](./docs/devcontainer.md)

## Plugin Development
The description of pluggin development in in [plugindev.md](./docs/plugindev.md)

## Usage

### Start the server

```bash
python -m kernelmcp.main --config path/to/config.json --port 3125
```

Options:

* `--config`: Path to JSON config file
* `--port`: HTTP port to bind (default: `3125`)

### Example

```bash
python -m kernelmcp.main -c ./config.json -p 3125
```

Then the mcp server is at `http://ip:port/mcp`