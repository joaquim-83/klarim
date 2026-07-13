"""Consulta CNPJ na Receita Federal (KL-55) — CNAEs **oficiais** para os alvos.

Quando o profiler (KL-50) extrai um CNPJ do site, consultamos a Receita (via APIs
públicas gratuitas) para obter o CNAE principal + secundários oficiais. Esses CNAEs
entram como `source='receita'`, `confidence=1.0`, e **nunca** são sobrescritos pela IA.

- **BrasilAPI** (primária, simples) → **ReceitaWS** (fallback, 3/min grátis).
- Cache por CNPJ em disco, **TTL 90 dias** (dados cadastrais mudam raramente).
- **Fail-open / runtime-only:** as APIs podem não estar acessíveis no CI — os testes
  usam mock; qualquer erro retorna ``None`` e o alvo segue só com a classificação da IA.
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Dict, List, Optional

import httpx

from discovery.cnae import derive_section, derive_division, format_cnae

BRASILAPI_URL = "https://brasilapi.com.br/api/cnpj/v1/{cnpj}"
RECEITAWS_URL = "https://receitaws.com.br/v1/cnpj/{cnpj}"
CACHE_DIR = os.path.join(os.environ.get("KLARIM_CACHE_DIR", "/tmp/klarim"), "cnpj")
CACHE_TTL_SECONDS = 90 * 24 * 3600  # 90 dias


def _clean_cnpj(cnpj: str) -> str:
    return re.sub(r"\D", "", cnpj or "")


# --------------------------------------------------------------------------- #
# Cache
# --------------------------------------------------------------------------- #
def _cache_path(cnpj: str) -> str:
    return os.path.join(CACHE_DIR, f"{_clean_cnpj(cnpj)}.json")


def _load_cache(cnpj: str) -> Optional[dict]:
    try:
        p = _cache_path(cnpj)
        if not os.path.exists(p) or time.time() - os.path.getmtime(p) > CACHE_TTL_SECONDS:
            return None
        with open(p, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:  # noqa: BLE001
        return None


def _save_cache(cnpj: str, data: dict) -> None:
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        tmp = _cache_path(cnpj) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)
        os.replace(tmp, _cache_path(cnpj))
    except Exception:  # noqa: BLE001
        pass


# --------------------------------------------------------------------------- #
# Normalização das respostas → formato comum
# --------------------------------------------------------------------------- #
def _normalize_brasilapi(d: dict) -> Optional[dict]:
    if not isinstance(d, dict) or not d.get("cnae_fiscal"):
        return None
    principal = {"code": format_cnae(d.get("cnae_fiscal")),
                 "description": (d.get("cnae_fiscal_descricao") or "").strip()}
    secundarios = []
    for s in (d.get("cnaes_secundarios") or []):
        code = format_cnae(s.get("codigo") or "")
        if code:
            secundarios.append({"code": code, "description": (s.get("descricao") or "").strip()})
    return {"razao_social": d.get("razao_social"), "nome_fantasia": d.get("nome_fantasia"),
            "principal": principal, "secundarios": secundarios}


def _normalize_receitaws(d: dict) -> Optional[dict]:
    if not isinstance(d, dict) or d.get("status") == "ERROR":
        return None
    ap = (d.get("atividade_principal") or [])
    if not ap:
        return None
    principal = {"code": format_cnae(ap[0].get("code") or ""),
                 "description": (ap[0].get("text") or "").strip()}
    if not re.sub(r"\D", "", principal["code"]):
        return None
    secundarios = []
    for s in (d.get("atividades_secundarias") or []):
        code = format_cnae(s.get("code") or "")
        if code and re.sub(r"\D", "", code):
            secundarios.append({"code": code, "description": (s.get("text") or "").strip()})
    return {"razao_social": d.get("nome"), "nome_fantasia": d.get("fantasia"),
            "principal": principal, "secundarios": secundarios}


# --------------------------------------------------------------------------- #
# Fetch + build
# --------------------------------------------------------------------------- #
async def fetch_cnpj(cnpj: str) -> Optional[dict]:
    """Busca os dados cadastrais (com CNAEs) de um CNPJ. BrasilAPI → ReceitaWS.
    Usa cache (90d) e é fail-open (``None`` em qualquer erro)."""
    clean = _clean_cnpj(cnpj)
    if len(clean) != 14:
        return None
    cached = _load_cache(clean)
    if cached is not None:
        return cached

    async with httpx.AsyncClient(timeout=20) as client:
        # 1) BrasilAPI
        try:
            r = await client.get(BRASILAPI_URL.format(cnpj=clean))
            if r.status_code == 200:
                norm = _normalize_brasilapi(r.json())
                if norm:
                    _save_cache(clean, norm)
                    return norm
        except Exception as exc:  # noqa: BLE001
            print(f"[cnpj] BrasilAPI falhou {clean}: {exc!r}", flush=True)
        # 2) ReceitaWS (fallback)
        try:
            r = await client.get(RECEITAWS_URL.format(cnpj=clean))
            if r.status_code == 200:
                norm = _normalize_receitaws(r.json())
                if norm:
                    _save_cache(clean, norm)
                    return norm
        except Exception as exc:  # noqa: BLE001
            print(f"[cnpj] ReceitaWS falhou {clean}: {exc!r}", flush=True)
    return None


def build_receita_classifications(data: dict) -> List[Dict]:
    """Constrói as classificações CNAE (source='receita', confidence 1.0) a partir do
    dado normalizado. Puro/testável. Principal = rank 1; secundários = rank 2..N."""
    if not data:
        return []
    out: List[Dict] = []
    principal = data.get("principal") or {}
    if principal.get("code"):
        out.append(_receita_row(principal, rank=1))
    for i, sec in enumerate(data.get("secundarios") or [], start=2):
        if sec.get("code"):
            out.append(_receita_row(sec, rank=i))
    return out


def _receita_row(cnae: dict, rank: int) -> dict:
    code = cnae["code"]
    return {"cnae_code": code, "cnae_description": cnae.get("description") or None,
            "cnae_section": derive_section(code), "cnae_division": derive_division(code),
            "confidence": 1.0, "source": "receita", "rank": rank}


async def enrich_from_cnpj(cnpj: str, store, target_id: int) -> int:
    """Consulta a Receita e grava os CNAEs oficiais do alvo. Retorna quantos gravou
    (0 se sem CNPJ válido / API fora / sem CNAE). Best-effort — nunca levanta."""
    try:
        data = await fetch_cnpj(cnpj)
        classifications = build_receita_classifications(data or {})
        if classifications:
            await store.upsert_target_classifications(target_id, classifications)
        return len(classifications)
    except Exception as exc:  # noqa: BLE001
        print(f"[cnpj] enrich_from_cnpj erro {cnpj}: {exc!r}", flush=True)
        return 0
