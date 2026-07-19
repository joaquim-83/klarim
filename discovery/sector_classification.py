"""KL-84 — fluxo de classificação de setor com taxonomia ABERTA. Resolve sinônimo → consulta a
tabela `sectors` → segue merged / cria proposto / fallback 'outro'. Retorna o slug canônico
para gravar no target. Testável com FakeStore.
"""

from __future__ import annotations

import re
from typing import Any, Dict

from discovery.sector_synonyms import resolve_synonym

_SLUG_RE = re.compile(r"[^a-z0-9_]")
_VALID_MACROS = {
    "alimentacao", "saude", "beleza", "comercio", "servicos", "imoveis", "automotivo",
    "educacao", "turismo", "eventos", "industria", "transporte", "tecnologia",
    "financeiro", "institucional", "outro",
}


def sanitize_slug(slug: str) -> str:
    """Slug seguro: minúsculo, espaços/hífens → `_`, só [a-z0-9_], máx 50 chars."""
    s = (slug or "").strip().lower().replace(" ", "_").replace("-", "_")
    s = _SLUG_RE.sub("", s)
    return s[:50]


async def process_classification(store, ai_result: Dict[str, Any]) -> Dict[str, Any]:
    """Resolve o setor final da IA. Retorna {"sector": slug, "confidence": float, "action": str}.
    `action` ∈ {existing, merged, proposed, fallback}. NÃO grava no target (o chamador faz)."""
    raw = ai_result.get("sector") or "outro"
    slug = resolve_synonym(raw)              # 1. sinônimo antes de tudo
    is_new = bool(ai_result.get("is_new_sector"))
    conf = float(ai_result.get("sector_confidence") or 0.5)

    if not slug or slug == "outro":
        return {"sector": "outro", "confidence": conf, "action": "fallback"}

    existing = await store.get_sector(slug)
    if existing:
        status = existing.get("status")
        if status == "merged" and existing.get("merged_into"):
            slug = existing["merged_into"]
            action = "merged"
        elif status == "rejected":
            return {"sector": "outro", "confidence": conf, "action": "fallback"}
        else:
            action = "existing"
        try:
            await store.increment_sector_count(slug)
        except Exception:  # noqa: BLE001 - contador é best-effort
            pass
        return {"sector": slug, "confidence": conf, "action": action}

    if is_new:
        slug = sanitize_slug(slug)
        if not slug:
            return {"sector": "outro", "confidence": conf, "action": "fallback"}
        macro = (ai_result.get("macro_sector_suggestion") or "outro").strip().lower()
        if macro not in _VALID_MACROS:
            macro = "outro"
        try:
            await store.create_proposed_sector(slug, ai_result.get("sector_label") or slug, macro)
            await store.increment_sector_count(slug)
        except Exception:  # noqa: BLE001
            return {"sector": "outro", "confidence": conf, "action": "fallback"}
        return {"sector": slug, "confidence": conf, "action": "proposed"}

    # IA retornou um slug desconhecido sem marcar is_new → trata como 'outro'.
    return {"sector": "outro", "confidence": conf, "action": "fallback"}
