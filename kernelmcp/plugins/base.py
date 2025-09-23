import logging
from typing import Optional
from abc import ABC, abstractmethod
from fastmcp import FastMCP
from kernelmcp.config import Config
from functools import wraps


def mcp_tool(func):
    func._is_mcp_tool = True
    return func


def limit_list(max_items=50):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            result = func(*args, **kwargs)
            if isinstance(result, list):
                return result[:max_items]
            return result
        return wrapper
    return decorator

class McpPlugin(ABC):
    logger: logging.Logger
    config: Config
    mcp: FastMCP

    @property
    @abstractmethod
    def NAME(self) -> str:
        """Name of the plugin. Must be overridden in subclass."""
        pass

    def __init__(self, config: Config, mcp: FastMCP, logger: Optional[logging.Logger] = None) -> None:
        self.config = config
        self.mcp = mcp
        self.logger = logger or logging.getLogger(__name__)
        if not self.logger.handlers:
            self.logger.addHandler(logging.NullHandler())

        for attr_name in dir(self):
            method = getattr(self, attr_name)
            if callable(method) and getattr(method, "_is_mcp_tool", False):
                self.logger.info(
                    f"Registering tool: {attr_name} for Plugin: {self.NAME}")
                mcp.tool()(method)

        self._post_init()

    def _post_init(self) -> None:
        """Post-initialization steps."""
        pass
