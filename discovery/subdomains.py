"""Registro de subdomínios via CT logs (KL-75 Prompt 2).

O Discovery Worker lê o stream de CT logs 24/7 e, historicamente, **descartava** tudo
que não fosse domínio raiz `.com.br`. Este módulo muda isso: quando um subdomínio
pertence a um domínio raiz **já na base**, o worker **registra a existência** dele
(app./api./staging./…) em vez de descartar — inteligência de infraestrutura sem custo.

**Regras invioláveis:**
- Subdomínios **NUNCA** são escaneados (ético: podem conter ambientes de teste/dados).
- Só a existência é registrada; a listagem completa é feature premium (admin/API).
- Erro no registro **NUNCA** interrompe o stream de CT (fail-safe).
- Lookup do domínio raiz é O(1) via cache em memória (recarregado a cada ciclo).

`classify_subdomain` é **pura/testável**. `DomainCache` e `register_subdomain` isolam o
I/O para o worker chamar de forma resiliente.
"""

from __future__ import annotations

import re
from typing import Dict, Optional

# Prefixo do subdomínio → tipo. Ordem: o 1º match vence (padrões são âncora `^prefixo.`).
SUBDOMAIN_PATTERNS = [
    (r"^app\.", "app"),
    (r"^sistema\.", "app"),
    (r"^plataforma\.", "app"),
    (r"^api\.", "api"),
    (r"^admin\.", "admin"),
    (r"^painel\.", "admin"),
    (r"^backoffice\.", "admin"),
    (r"^staging\.", "staging"),
    (r"^dev\.", "staging"),
    (r"^homolog\.", "staging"),
    (r"^hml\.", "staging"),
    (r"^test\.", "staging"),
    (r"^mail\.", "mail"),
    (r"^webmail\.", "mail"),
    (r"^smtp\.", "mail"),
    (r"^loja\.", "shop"),
    (r"^shop\.", "shop"),
    (r"^store\.", "shop"),
    (r"^docs?\.", "docs"),
    (r"^help\.", "docs"),
    (r"^status\.", "status"),
    (r"^cdn\.", "cdn"),
    (r"^assets\.", "cdn"),
    (r"^static\.", "cdn"),
    (r"^blog\.", "blog"),
    (r"^www\.", "www"),   # registrado mas não conta como subdomínio "interessante"
]
_SUBDOMAIN_RE = [(re.compile(p, re.I), t) for p, t in SUBDOMAIN_PATTERNS]


def classify_subdomain(subdomain: str) -> str:
    """Tipo do subdomínio (app/api/admin/staging/mail/shop/docs/status/cdn/blog/www)
    ou 'outro'. Puro/testável. ``app.hotel.com.br`` → ``app``; ``x.hotel.com.br`` → ``outro``."""
    sub = (subdomain or "").strip().lower()
    for rx, stype in _SUBDOMAIN_RE:
        if rx.match(sub):
            return stype
    return "outro"


class DomainCache:
    """Cache em memória {domínio_raiz: target_id} dos alvos na base. Recarregado a cada
    ciclo do discovery (~30 min). ~36k domínios × ~50 B ≈ 1.8 MB — trivial. Domínios
    novos adicionados no meio do ciclo só aparecem no próximo reload (aceitável)."""

    def __init__(self) -> None:
        self._domains: Dict[str, int] = {}
        self._loaded_at = None

    async def load(self, store, now=None) -> int:
        """Carrega todos os domínios raiz da base. Fail-safe: erro mantém o cache anterior.
        Retorna o tamanho do cache."""
        try:
            rows = await store.get_all_root_domains()
            self._domains = {r["domain"]: r["id"] for r in rows if r.get("domain")}
            self._loaded_at = now
        except Exception as exc:  # noqa: BLE001 - reload é best-effort; mantém o anterior
            print(f"[discovery] cache de domínios: reload falhou ({exc!r})", flush=True)
        return len(self._domains)

    def get(self, domain: str) -> Optional[int]:
        """target_id se o domínio raiz existe na base, senão None."""
        return self._domains.get((domain or "").strip().lower())

    @property
    def size(self) -> int:
        return len(self._domains)


async def register_subdomain(store, cache: DomainCache, root_domain: str,
                             subdomain: str, cert_issuer: Optional[str] = None) -> bool:
    """Registra um subdomínio vinculado a um domínio raiz **já na base**. Fire-and-forget:
    erro é logado e engolido (NUNCA propaga — o stream de CT continua). Retorna True se
    registrou. Ignora `www` (registra o tipo mas não é subdomínio "interessante" — na
    verdade nem grava, para não poluir a contagem). Domínio raiz fora da base → ignora.
    """
    try:
        target_id = cache.get(root_domain)
        if target_id is None:
            return False  # domínio raiz não está na base → ignora
        sub_type = classify_subdomain(subdomain)
        if sub_type == "www":
            return False  # www não conta como subdomínio interessante
        await store.upsert_subdomain(
            target_id=target_id, subdomain=subdomain,
            subdomain_type=sub_type, cert_issuer=cert_issuer)
        return True
    except Exception as exc:  # noqa: BLE001 - NUNCA interrompe o stream de CT
        print(f"[discovery] registro de subdomínio falhou {subdomain}: {exc!r}", flush=True)
        return False


async def process_subdomains(store, cache: DomainCache, sub_map: Dict[str, str],
                             max_items: int = 2000) -> Dict[str, int]:
    """Drena um lote de subdomínios (``{full_subdomain: cert_issuer}``) e registra os que
    pertencem a domínios na base. Cap `max_items` por ciclo (protege o banco; o excedente
    fica no próximo ciclo). Retorna estatísticas. Nunca levanta."""
    from scanner.checks.base import registrable_domain
    stats = {"seen": 0, "registered": 0, "skipped_not_in_base": 0, "www": 0}
    processed = 0
    for full, issuer in (sub_map or {}).items():
        if processed >= max_items:
            break
        processed += 1
        stats["seen"] += 1
        root = registrable_domain(full)
        if cache.get(root) is None:
            stats["skipped_not_in_base"] += 1
            continue
        if classify_subdomain(full) == "www":
            stats["www"] += 1
            continue
        if await register_subdomain(store, cache, root, full, issuer):
            stats["registered"] += 1
    return stats
