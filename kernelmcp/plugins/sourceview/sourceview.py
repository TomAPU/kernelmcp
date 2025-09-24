from typing import Dict, List, Callable, Any, Optional

from kernelmcp.plugins import McpPlugin, mcp_tool
from kernelmcp.infra import SourceView


class _NullSV:
    """Null-object for SourceView: logs and returns minimal responses."""

    def __init__(self, logger, name: str):
        self._logger = logger
        self._name = name

    def get_source(self, *args, **kwargs) -> Dict:
        if self._logger:
            self._logger.error(
                f"{self._name} plugin: code_path not configured; cannot retrieve source.")
        return {}


class SourceViewPlugin(McpPlugin):
    @property
    def NAME(self) -> str:
        return "SourceView"

    def _post_init(self) -> None:
        code_path = self.config.data.get("code_path", "")
        self.code_path = code_path
        if not code_path:
            self.logger.warning(
                f"{self.NAME} plugin: 'code_path' not set in config; source retrieval disabled.")
            self.sv = _NullSV(self.logger, self.NAME)
            return

        self.logger.info(f"{self.NAME} plugin using code_path: {code_path}")
        self.sv: SourceView = SourceView(code_path, logger=self.logger)

    @mcp_tool
    def get_source(self, source_path: str, start: int, end: int) -> Dict:
        """
        Retrieve source code from a file between start and end lines (1-based, inclusive).
        """
        return self.sv.get_source(source_path, start=start, end=end, include_linenos=False)

    @mcp_tool
    def get_source_folder(self) -> str:
        """
        Return the root of the source code folder path.
        """
        return self.code_path

    @mcp_tool
    def search(self, pattern: str, regex: bool, root_dir: Optional[str] = None) -> List[Dict]:
        """
        Search for a text pattern in the source code.
        Returns file path, line number, and the matching line.
        If `root_dir` is None, searches from the root of the source code folder.
        If `regex` is False, performs a literal search. 
        If `regex` is True, treats `pattern` as a regex.
        """
        try:
            return self.sv.search(root_dir, pattern, regex=regex, ignore_case=False, max_results=100)
        except Exception as e:
            self.logger.error(f"{self.NAME} search failed: {e}")
            return []

    @mcp_tool
    def list_files(self, rel_dir: str = ".") -> List[Dict]:
        """
        List files under a directory (relative to source root).
        Shows file path and size in bytes.
        """
        try:
            return self.sv.list_files(rel_dir, pattern=None, recursive=True)
        except Exception as e:
            self.logger.error(f"{self.NAME} list_files failed: {e}")
            return []
