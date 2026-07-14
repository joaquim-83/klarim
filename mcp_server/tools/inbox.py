"""Tools MCP do inbox scan@klarim.net — busca de mensagens (webhook + formulário)."""

from __future__ import annotations

from typing import Optional

from mcp_server._base import mcp, _guard, _store


@mcp.tool()
async def search_inbox(query: Optional[str] = None, source: Optional[str] = None,
                       unread_only: bool = False, limit: int = 25) -> dict:
    """Busca mensagens no inbox (scan@klarim.net): `query` (texto no assunto/remetente/
    preview), `source` (`webhook` = e-mails da Hostinger | `contact_form` = formulário
    do site), `unread_only`. Retorna as mensagens (sem o corpo HTML) + total de não-lidas."""
    async def _impl():
        store = _store()
        box = "unread" if unread_only else "all"
        src = source if source in ("webhook", "contact_form") else None
        q = (query or "").strip() or None
        rows = await store.list_inbox_messages(box=box, limit=min(limit, 200),
                                               source=src, search=q)
        unread = await store.inbox_unread_count()
        return {"count": len(rows), "messages": rows, "unread_total": unread}

    return await _guard(_impl)
