"""KL-84 — Taxonomia aberta de setores (endpoints admin). 5 rotas admin-only sob `/admin/sectors`
(prefixo `/admin` já protegido pelo middleware admin JWT). Fluxo: a IA propõe setores novos
(status='proposed'); o admin aprova (aparece em /setores), faz merge num existente ou rejeita
(sites voltam p/ 'outro'). Toda reclassificação preserva `classification_source='manual'`.

Datas/slugs sempre parametrizados. A lógica de banco vive em `discovery/store.py` (métodos de
setor); aqui só orquestração + validação de entrada.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from discovery.store import get_target_store
from discovery.sector_classification import sanitize_slug

router = APIRouter(prefix="/admin/sectors")

_VALID_MACROS = {
    "alimentacao", "saude", "beleza", "comercio", "servicos", "imoveis", "automotivo",
    "educacao", "turismo", "eventos", "industria", "transporte", "tecnologia",
    "financeiro", "institucional", "outro",
}


def _clean_slug(slug: str) -> str:
    s = sanitize_slug(slug)
    if not s:
        raise HTTPException(422, "Slug inválido.")
    return s


class ApproveBody(BaseModel):
    label: Optional[str] = None
    macro_sector: Optional[str] = None


class MergeBody(BaseModel):
    merge_into: str


@router.get("")
async def list_sectors_admin(status: str = Query("all")) -> Dict[str, Any]:
    """Taxonomia completa + setores emergentes (propostos). `status`: all | proposed |
    official | approved | rejected | merged. Devolve `emerging` (propostos, p/ curadoria) e
    `taxonomy` (official+approved, a taxonomia viva) + contadores."""
    store = get_target_store()
    stats = await store.sector_taxonomy_stats()
    emerging = await store.list_sectors(["proposed"])
    if status == "all":
        taxonomy = await store.list_sectors(["official", "approved"])
    else:
        valid = {"proposed", "official", "approved", "rejected", "merged"}
        if status not in valid:
            raise HTTPException(422, "status inválido.")
        taxonomy = await store.list_sectors([status])
    return {"stats": stats, "emerging": emerging, "taxonomy": taxonomy}


@router.get("/{slug}/examples")
async def sector_examples_admin(slug: str, limit: int = Query(5, ge=1, le=20)) -> Dict[str, Any]:
    """Domínios de exemplo de um setor (ajuda o admin a decidir aprovar/merge/rejeitar)."""
    store = get_target_store()
    slug = _clean_slug(slug)
    sector = await store.get_sector(slug)
    if not sector:
        raise HTTPException(404, "Setor não encontrado.")
    examples = await store.sector_examples(slug, limit)
    return {"sector": sector, "examples": examples}


def _admin_subject(request: Request) -> str:
    """Extrai o `sub` do JWT admin (já validado pelo middleware) p/ o audit trail."""
    try:
        import api.main as _m
        auth = request.headers.get("authorization", "")
        token = auth[7:].strip() if auth[:7].lower() == "bearer " else ""
        return str(_m._verify_token(token).get("sub") or "admin")
    except Exception:  # noqa: BLE001
        return "admin"


@router.post("/{slug}/approve")
async def approve_sector_admin(slug: str, body: ApproveBody, request: Request) -> Dict[str, Any]:
    """Aprova um setor proposto → status='approved' (passa a aparecer em /setores)."""
    store = get_target_store()
    slug = _clean_slug(slug)
    macro = (body.macro_sector or "").strip().lower() or None
    if macro and macro not in _VALID_MACROS:
        raise HTTPException(422, "macro_sector inválido.")
    who = _admin_subject(request)
    row = await store.approve_sector(slug, (body.label or "").strip() or None, macro, who)
    if not row:
        raise HTTPException(404, "Setor proposto não encontrado (ou já resolvido).")
    return {"ok": True, "sector": row}


@router.post("/{slug}/merge")
async def merge_sector_admin(slug: str, body: MergeBody) -> Dict[str, Any]:
    """Merge de um setor proposto num destino existente. Reclassifica os sites (exceto
    `manual`) e devolve quantos foram movidos."""
    store = get_target_store()
    slug = _clean_slug(slug)
    dest = sanitize_slug(body.merge_into)
    if not dest:
        raise HTTPException(422, "merge_into inválido.")
    if dest == slug:
        raise HTTPException(422, "Não é possível fazer merge de um setor nele mesmo.")
    target = await store.get_sector(dest)
    if not target or target.get("status") not in ("official", "approved"):
        raise HTTPException(422, "Destino inválido: precisa ser um setor official/approved.")
    res = await store.merge_sector(slug, dest)
    if not res:
        raise HTTPException(404, "Setor proposto não encontrado (ou já resolvido).")
    return {"ok": True, "merged_into": dest, **res}


@router.post("/{slug}/reject")
async def reject_sector_admin(slug: str) -> Dict[str, Any]:
    """Rejeita um setor proposto → status='rejected'. Os sites voltam para 'outro'
    (exceto os `manual`)."""
    store = get_target_store()
    slug = _clean_slug(slug)
    res = await store.reject_sector(slug)
    if not res:
        raise HTTPException(404, "Setor proposto não encontrado (ou já resolvido).")
    return {"ok": True, **res}
