import argparse
import inspect
import logging
import os
import importlib
from importlib import import_module
from typing import List, Type

from fastmcp import FastMCP
from kernelmcp.config import Config
from kernelmcp.plugins import McpPlugin
import kernelmcp.plugins as plugins_pkg
from kernelmcp.plugins import McpPlugin, __path__ as plugins_path, __name__ as plugins_pkg


def discover_plugins() -> List[Type[McpPlugin]]:
    """Discover McpPlugin subclasses under kernelmcp.plugins.<plugin>/<plugin>.py"""
    found: List[Type[McpPlugin]] = []
    logger = logging.getLogger("KernelMCP")

    plugins_dir = plugins_path[0]  # kernelmcp/plugins
    for name in os.listdir(plugins_dir):
        subdir = os.path.join(plugins_dir, name)
        if not os.path.isdir(subdir):
            continue

        module_name = f"{plugins_pkg}.{name}.{name}"
        try:
            module = importlib.import_module(module_name)
        except Exception as e:
            logger.warning(
                f"Failed to import plugin module {module_name}: {e}")
            continue

        # Find McpPlugin subclasses in the module
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if issubclass(obj, McpPlugin) and obj is not McpPlugin:
                found.append(obj)

    return found


def main():
    parser = argparse.ArgumentParser(
        prog="kernelmcp", description="Kernel MCP server")
    parser.add_argument(
        "-c",
        "--config",
        help="Path to JSON config file",
    )
    parser.add_argument(
        "-p",
        "--port",
        type=int,
        default=3125,
        help="HTTP port to bind (default: 3125)",
    )
    args = parser.parse_args()

    config = Config(args.config)
    mcp = FastMCP(name="Kernel MCP")

    logger = logging.getLogger("KernelMCP")
    logger.setLevel(logging.DEBUG)
    ch = logging.StreamHandler()
    ch.setLevel(logging.DEBUG)
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    ch.setFormatter(formatter)
    if not logger.handlers:
        logger.addHandler(ch)

    # Discover and load plugins
    plugins = discover_plugins()
    if not plugins:
        logger.warning("No plugins discovered under kernelmcp.plugins")
    for plugin_cls in plugins:
        try:
            plugin_instance = plugin_cls(config, mcp, logger)
            logger.info(f"Loaded plugin: {plugin_cls.__name__}")
        except Exception as e:
            logger.error(
                f"Failed to initialize plugin {plugin_cls.__name__}: {e}")

    mcp.run(transport="http", host="0.0.0.0", port=args.port)


if __name__ == "__main__":
    main()
