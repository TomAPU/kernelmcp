# Kernel MCP

Kernel MCP is a lightweight MCP (Modular Control Platform) server built with [FastMCP](https://pypi.org/project/fastmcp/).  
It supports a **plugin discovery mechanism** where new functionality can be added simply by dropping a plugin into the `kernelmcp/plugins` directory.

---

## Features
- Automatic discovery and loading of plugins
- Simple plugin convention:  
  - Each plugin lives in `kernelmcp/plugins/<plugin_name>/<plugin_name>.py`
  - Each plugin file must define at least one subclass of `McpPlugin`
- Configurable server options via command-line arguments
- Logging with detailed debug information

---

## Project Structure
```

kernelmcp/
├── config.py
├── main.py
├── plugins/
│   ├── base.py
│   ├── codelookup/
│   │   └── codelookup.py
│   └── **init**.py

```

---

## Plugin Discovery
Plugins are discovered dynamically with the following rules:

1. The server scans the `kernelmcp/plugins` directory for subdirectories.  
2. Each subdirectory is expected to contain a module named the same as the folder, e.g.:  
   - `plugins/codelookup/codelookup.py`
3. Inside that module, any class that **inherits from `McpPlugin`** is automatically registered.  

Example plugin layout:
```

kernelmcp/plugins/hello/hello.py


```python
from kernelmcp.plugins import McpPlugin, mcp_tool

class HelloPlugin(McpPlugin):

    def _post_init(self) -> None:
        """Post-initialization steps."""
        pass
    @mcp_tool
    def add(a,b):
        return a+b

````


---

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

---

## Logging

The server logs detailed messages to the console, including:

* Plugin discovery results
* Successful plugin initialization
* Errors if a plugin fails to load

---

## Writing a Plugin

1. Create a new folder under `kernelmcp/plugins/`
2. Inside it, create a Python file with the same name as the folder
3. Define a class that inherits from `McpPlugin`
4. The class will automatically be discovered and initialized when the server runs

---
