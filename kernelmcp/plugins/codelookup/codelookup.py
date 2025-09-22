from kernelmcp.plugins import McpPlugin, mcp_tool
from kernelmcp.infra import CodeQuerySearch
from typing import List, Dict


class CodeLookup(McpPlugin):
    @property
    def NAME(self) -> str:
        return "CodeLookup"

    def _post_init(self) -> None:
        db_path = self.config.data.get("db_path", "")
        if not db_path:
            self.logger.warning(
                f"{self.NAME} plugin: 'db_path' not set in config, some features may not work.")
            return
        self.logger.info(f"{self.NAME} plugin using db_path: {db_path}")
        self.db = CodeQuerySearch(db_path)

    @mcp_tool
    def get_definition(self, symbol: str) -> List[Dict]:
        """
        Get the definition of a symbol from the code database.
        """
        if not hasattr(self, 'db'):
            self.logger.error(f"{self.NAME} plugin: db not initialized.")
            return []
        return self.db.get_definition(symbol)
