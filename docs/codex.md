# Work with Codex
(Assume you have Codex setup).

## Prepare prompts
Create `~/.codex/AGENTS.md`, copy the [kernelmcp prompt](sampleprompt.md) (or your own prompt) there

## Configure the Codex being able to connect to our MCP server

### Install mcp-proxy
Follow this [mcp-proxy installation](./mcpproxy.md)

### Edit configuration
Create  (if not exist) `~/.codex/config.toml`. 
Add this
```
[mcp_servers.kernelmcp]
command = "mcp-proxy"
args = ["--transport","streamablehttp","http://127.0.0.1:<port number>/mcp"]
```

## Run
First create an empty folder and cd into it (to avoid codex reading things inside your current folder and cause confusion)

```bash
mkdit emptyfolder && cd emptyfolder
# Ask question via command line
codex "Analyze how to reach drivers/tty/n_gsm.c:2588 inside function gsm_dlci_config Generate C based PoC, test it on vm, using GDB to debug step by step to verify your PoC"
```
