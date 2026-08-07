"""Tools MCP do Security Gate (KL-151 Prompt 2/4) — visão do OPERADOR sobre o produto.

O Gate é um produto para devs externos (contas, API keys, planos, projetos, runs). Estas tools
dão ao operador leitura/gestão dos projetos e runs de QUALQUER conta (admin). A criação de projeto
extrai o domínio da URL. Não expõem valores de API key (só metadados)."""
from __future__ import annotations

from typing import Optional

from mcp_server._base import mcp, _guard, _store


def _ser(row: Optional[dict]) -> Optional[dict]:
    """Datetimes → ISO (o retorno da tool vira JSON)."""
    if not row:
        return row
    out = dict(row)
    for k in ("created_at", "verified_at", "accepted_at", "expires_at", "last_used_at",
              "revoked_at", "gate_trial_started_at", "gate_trial_ends_at"):
        v = out.get(k)
        if hasattr(v, "isoformat"):
            out[k] = v.isoformat()
    return out


@mcp.tool()
async def list_gate_projects(account_id: Optional[int] = None, limit: int = 100) -> dict:
    """Lista projetos do Security Gate. Sem `account_id` = de TODAS as contas (admin); com
    `account_id` = só daquela conta. Retorna `{projects: [...]}` (domínio, verificado, método)."""
    async def _impl():
        rows = await _store().admin_list_gate_projects(account_id=account_id, limit=limit)
        return {"projects": [_ser(r) for r in rows]}

    return await _guard(_impl)


@mcp.tool()
async def get_gate_project(project_id: int) -> dict:
    """Detalhe de um projeto do Gate (domínio, verificação, dono/convite, config)."""
    async def _impl():
        row = await _store().get_gate_project_by_id(project_id)
        return _ser(row) if row else {"error": "projeto não encontrado", "id": project_id}

    return await _guard(_impl)


@mcp.tool()
async def create_gate_project(name: str, url: str, account_id: int) -> dict:
    """Cria um projeto do Gate para uma conta (o domínio é extraído da URL). Nasce NÃO verificado —
    o dev precisa verificar o domínio (ou aceitar um convite) antes de escanear."""
    async def _impl():
        from api.gate import _extract_domain
        domain = _extract_domain(url)
        if not domain:
            return {"error": "URL inválida", "url": url}
        row = await _store().create_gate_project(account_id, name=(name or domain),
                                                 url=url, domain=domain)
        if row is None:
            return {"error": "a conta já tem um projeto para este domínio", "domain": domain}
        return _ser(row)

    return await _guard(_impl)


@mcp.tool()
async def list_gate_runs(project_id: Optional[int] = None, account_id: Optional[int] = None,
                         limit: int = 10) -> dict:
    """Lista runs do Gate (sumário, sem os `results`). Filtros opcionais por `project_id`/
    `account_id` (ambos None = todos). Mais recente 1º."""
    async def _impl():
        rows = await _store().list_gate_runs(account_id=account_id, project_id=project_id, limit=limit)
        return {"runs": [_ser(r) for r in rows]}

    return await _guard(_impl)


@mcp.tool()
async def get_gate_run(run_id: int) -> dict:
    """Detalhe de um run do Gate (score, passed, checks rodados/bloqueados, findings, metadados)."""
    async def _impl():
        row = await _store().get_gate_run(run_id, account_id=None)   # None = visão admin
        return _ser(row) if row else {"error": "run não encontrado", "id": run_id}

    return await _guard(_impl)
