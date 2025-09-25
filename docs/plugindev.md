# Plugin Development

Kernel MCP supports a **plugin discovery mechanism** where new functionality can be added simply by dropping a plugin into the `kernelmcp/plugins` directory.

## Plugin Discovery
Plugins are discovered dynamically with the following rules:

1. The server scans the `kernelmcp/plugins` directory for subdirectories.  
2. Each subdirectory is expected to contain a module named the same as the folder, e.g.:  
   - `plugins/codelookup/codelookup.py`
3. Inside that module, any class that **inherits from `McpPlugin`** is automatically registered.  


## Plugin Convention
- Simple plugin convention:  
  - Each plugin lives in `kernelmcp/plugins/<plugin_name>/<plugin_name>.py`
  - Each plugin file must define at least one subclass of `McpPlugin`

##  Structure

```
kernelmcp/
├── config.py
├── main.py
├── plugins/
│   ├── base.py
│   ├── codelookup/  # Plugin here
│   │   └── codelookup.py
│   └── **init**.py

```







## Writing a Plugin

1. Create a new folder under `kernelmcp/plugins/`
2. Inside it, create a Python file with the same name as the folder
3. Define a class that inherits from `McpPlugin`
4. The class will automatically be discovered and initialized when the server runs

Example plugin layout:
```

kernelmcp/plugins/hello/hello.py


```python
from kernelmcp.plugins import McpPlugin, mcp_tool,limit_list

class HelloPlugin(McpPlugin):

    def _post_init(self) -> None:
        """Post-initialization steps."""
        pass
    @mcp_tool
    def add(a,b):
        return a+b
    # Limit the number of list result via limit_list decorator (default value: 50)
    @mcp_tool
    @limit_list
    def give_me_numers(n):
        return list(range(n))

````


