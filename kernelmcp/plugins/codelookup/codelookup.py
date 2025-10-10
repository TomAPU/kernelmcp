from kernelmcp.plugins import McpPlugin, mcp_tool, limit_list
from kernelmcp.infra import CodeQuerySearch
from typing import List, Dict, Any, Callable
from functools import wraps


def ensure_db():
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            self = args[0]
            if not hasattr(self, 'db') or self.db is None:
                raise RuntimeError(
                    f"{self.NAME} plugin not properly initialized with CodeQuerySearch. Please check 'db_path' in config.")
            return func(*args, **kwargs)
        return wrapper
    return decorator


class CodeLookup(McpPlugin):
    @property
    def NAME(self) -> str:
        return "CodeLookup"

    def _post_init(self) -> None:
        db_path = self.config.data.get("db_path", "")
        if not db_path:
            self.logger.warning(
                f"{self.NAME} plugin: 'db_path' not set in config, some features may not work.")
            self.db = None
            return

        self.logger.info(f"{self.NAME} plugin using db_path: {db_path}")
        self.db = CodeQuerySearch(db_path)

    @mcp_tool
    @ensure_db()
    @limit_list(max_items=50)
    def source_get_definition(self, symbol: str) -> List[Dict]:
        """
        Get the definition of a symbol from the code database.
        This can be used to query functions, structs, macros, variables, etc.
        """
        return self.db.get_definition(symbol)

    @mcp_tool
    @ensure_db()
    @limit_list(max_items=50)
    def source_get_reference(self, symbol: str) -> List[Dict]:
        """
        Get the references of a symbol from the code database.
        This can be used to query functions, structs, macros, variables, etc.
        """
        return self.db.get_reference(symbol)

    @mcp_tool
    @ensure_db()
    @limit_list(max_items=50)
    def source_get_function_range(self, symbol: str) -> List[Dict]:
        """
        Get the filename and line range of a function definition from the code database.
        This can be used be comnbined with the get_source tool to retrieve the function's source code.
        """
        return self.db.get_function_range(symbol)
