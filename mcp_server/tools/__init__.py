"""Tools do MCP do Klarim. Importar este pacote registra as 25 tools no `mcp`
(via `@mcp.tool()`). Organizadas por domínio (modelo Traka)."""

from . import system, targets, scans, alerts, payments, analytics  # noqa: F401
