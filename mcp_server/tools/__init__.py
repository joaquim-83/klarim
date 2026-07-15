"""Tools do MCP do Klarim. Importar este pacote registra as tools no `mcp`
(via `@mcp.tool()`). Organizadas por domínio (modelo Traka)."""

from . import (system, targets, scans, alerts, payments, analytics,  # noqa: F401
               monitoring, workers, inbox, leads, subscriptions, vigilia)
