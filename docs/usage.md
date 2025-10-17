# Usage
This document describes only about `kernelmcp.main`. 
Go [quicstart.md ](./quicstart.md ) for more information about how to prepare this Json.

## Start the server

```bash
python -m kernelmcp.main --config path/to/config.json --port 3125
```

Options:

* `--config`: Path to JSON config file
* `--port`: HTTP port to bind (default: `3125`)

## Example

```bash
python -m kernelmcp.main -c ./config.json -p 3125
```

Then the mcp server is at `http://ip:port/mcp`