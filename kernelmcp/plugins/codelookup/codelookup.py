from kernelmcp.plugins import McpPlugin, mcp_tool, limit_list
from kernelmcp.infra import CodeQuerySearch
from typing import List, Dict, Any, Callable


class _NullDB:
    """Minimal null-object: any method logs warning and returns []"""

    def __init__(self, logger, plugin_name: str):
        self._logger = logger
        self._name = plugin_name

    def __getattr__(self, name: str) -> Callable[..., List[Dict]]:
        def _missing(*args, **kwargs):
            if self._logger:
                self._logger.error(f"{self._name} plugin: db not initialized.")
            return []
        return _missing


class CodeLookup(McpPlugin):
    @property
    def NAME(self) -> str:
        return "CodeLookup"

    def _post_init(self) -> None:
        db_path = self.config.data.get("db_path", "")
        if not db_path:
            self.logger.warning(
                f"{self.NAME} plugin: 'db_path' not set in config, some features may not work.")
            self.db = _NullDB(self.logger, self.NAME)
            return
        self.logger.info(f"{self.NAME} plugin using db_path: {db_path}")
        self.db = CodeQuerySearch(db_path)

    @mcp_tool
    @limit_list(max_items=50)
    def get_definition(self, symbol: str) -> List[Dict]:
        """
        Get the definition of a symbol from the code database.
        """
        return self.db.get_definition(symbol)

    @mcp_tool
    @limit_list(max_items=50)
    def get_reference(self, symbol: str) -> List[Dict]:
        """
        Get the references of a symbol from the code database.
        """
        return self.db.get_reference(symbol)

    @mcp_tool
    @limit_list(max_items=50)
    def get_function_range(self, symbol: str) -> List[Dict]:
        """
        Get the filename and line range of a function definition from the code database.
        """
        return self.db.get_function_range(symbol)
