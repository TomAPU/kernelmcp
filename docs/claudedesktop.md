# Work with Claude Desktop
Claude has a problem with connecting to local HTTP server, so we need [mcp-proxy](https://github.com/sparfenyuk/mcp-proxy) installed on the machine that you run Claude Desktop.
This tool can convert streamable HTTP to a local stdio server, just to make Claude Desktop happy!

## Install mcp-proxy
Follow their document, run:
```bash
pipx install mcp-proxy
```

## Configure in Claude Desktop

Go for Settings -> Developers -> Edit Config, then click and edit the configuration file into:

Then modify the file content like this:
```json
{
  "mcpServers": {
    "mcp-proxy": {
      "command": "mcp-proxy",
      "args": [
        "--transport",
        "streamablehttp",
        "http://127.0.0.1:<port>/mcp"
      ]    }
  }
}
```