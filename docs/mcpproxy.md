# MCP Proxy
Some MCP clients don't support HTTP based MCP well, so we need [mcp-proxy](https://github.com/sparfenyuk/mcp-proxy) to convert HTTP to stdio to make them happy.

## Install mcp-proxy
Run 
```bash
pip3 install pipx
pipx install mcp-proxy
```
Once succeed, you should be able to execute `mcp-proxy` command.

## Example of converting

```bash
mcp-proxy --transport streamablehttp http://127.0.0.1:3125/mcp
```