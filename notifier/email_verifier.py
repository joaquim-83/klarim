"""KL-110 — verificação de deliverability de e-mail (Camada 0 local + Camada 1 Reoon).

⚠️ **KL-145 — o Reoon saiu do fluxo de ENVIO.** A decisão de envio agora é `is_safe_to_send`
(3 filtros locais: sintaxe + MX + blocklist). A Camada 1 (Reoon Power) classificava ~97% dos
servidores BR como `unknown` e travava o volume em 2-8/ciclo; segue neste módulo só como
enriquecimento em background (usada pelos scripts de limpeza e pelo monitoramento de saldo do
`/system/status`), NUNCA no `alert_worker`. A Camada 0 (sintaxe + MX) passou a ser O filtro.

O bounce rate dos senders cold (6-8% hard) vinha de e-mails ruins entrando na fila de
alerta sem validação. O circuit breaker (KL-108) é REATIVO — pausa o sender DEPOIS do
bounce prejudicar a reputação. Este módulo é PREVENTIVO: verifica se a caixa existe
antes de enviar.

Dois níveis:
  • **Camada 0 (local, custo zero):** sintaxe/normalização (email-validator, com fallback
    regex se a lib faltar), domínios descartáveis (reusa a lista curada do KL-85), MX
    (dnspython, cache Redis 24h por domínio) e flag role-based (reusa `ROLE_BASED_PREFIXES`).
  • **Camada 1 (API Reoon, pago):** verifica se a INBOX existe + catch-all + disabled +
    inbox_full + spamtrap. Modo `quick` (~0.5s) ou `power` (2-60s). Semáforo de 5 chamadas
    simultâneas (restrição da API). Fallback fail-open: API fora → status `unknown` (nunca
    bloqueia o pipeline).

Regras de segurança:
  • `REOON_API_KEY` vem só do ambiente (`.env` da VM), nunca do código/frontend/logs.
  • Cache Redis usa **SHA-256** do e-mail normalizado (o e-mail em claro nunca vira chave).
  • Sem `REOON_API_KEY` → roda só a Camada 0 (o profiler/worker não quebram por falta de key).
  • Tudo é fail-open: qualquer erro de infra degrada para "envie sem bloquear" + log.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from typing import Optional

import httpx

# Reuso de listas curadas já existentes (evita nova dependência / fetch de rede no boot).
from api.disposable_emails import DISPOSABLE_EMAIL_DOMAINS
from discovery.alert_scoring import ROLE_BASED_PREFIXES

REOON_BASE_URL = "https://emailverifier.reoon.com/api/v1"
# A API Reoon limita chamadas simultâneas — semáforo global do processo (máx 5 threads).
_MAX_CONCURRENT_REOON = int(os.environ.get("REOON_MAX_CONCURRENCY", "5"))
_reoon_semaphore = asyncio.Semaphore(_MAX_CONCURRENT_REOON)

# Status DEFINITIVOS (cache 60d) vs. TRANSITÓRIOS (cache 7d — a caixa pode mudar).
_DEFINITIVE_STATUSES = frozenset({"safe", "valid", "invalid", "disabled", "disposable", "spamtrap"})
_TRANSIENT_STATUSES = frozenset({"catch_all", "inbox_full", "unknown", "role"})
# Status que NUNCA devem receber e-mail (blocklist permanente).
BLOCK_STATUSES = frozenset({"invalid", "disabled", "disposable", "spamtrap"})

# KL-145 — a DECISÃO DE ENVIO deixou de depender do status Reoon. O Reoon classificava ~97% dos
# servidores BR como `unknown` (inútil como filtro) e a regra binária por-status do KL-137 barrava
# quase tudo → 2-8 envios/dia. Agora o envio é decidido por 3 filtros LOCAIS (sintaxe + MX +
# blocklist — ver `is_safe_to_send` abaixo). O status de verificação (`SENDABLE_STATUSES` do KL-137)
# NÃO decide mais envio; o Reoon segue no módulo só como enriquecimento em background (scripts).

_CACHE_TTL_DEFINITIVE = 60 * 24 * 3600   # 60 dias
_CACHE_TTL_TRANSIENT = 7 * 24 * 3600     # 7 dias

# Fallback de sintaxe se `email-validator` não estiver instalado (mantém testes/dev funcionando).
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


@dataclass
class VerifyResult:
    """Resultado de verificação. `status`/`reason` seguem o contrato do KL-110.

    status (local):  valid | invalid | disposable
    status (power):  safe | invalid | disabled | disposable | inbox_full | catch_all |
                     role | spamtrap | unknown
    """
    status: str
    reason: str
    is_role_based: bool = False
    cached: bool = False
    score: Optional[int] = None       # overall_score (0-100) no modo power
    source: str = "local"             # local | reoon | cache | fallback
    catch_all: bool = False

    def to_cache(self) -> dict:
        return {"status": self.status, "reason": self.reason,
                "is_role_based": self.is_role_based, "score": self.score,
                "source": self.source, "catch_all": self.catch_all}


def _cache_key(email: str) -> str:
    h = hashlib.sha256(email.strip().lower().encode()).hexdigest()[:16]
    return f"email_verify:{h}"


def _domain_of(email: str) -> str:
    return (email or "").rsplit("@", 1)[-1].strip().lower()


def _local_of(email: str) -> str:
    return (email or "").split("@", 1)[0].strip().lower()


def is_role_based(email: str) -> bool:
    """Prefixo de caixa de função (contato@, info@, sac@…) — reusa a lista do lead scoring."""
    return _local_of(email) in ROLE_BASED_PREFIXES


# --------------------------------------------------------------------------- #
# Camada 0 — filtragem local (custo zero)
# --------------------------------------------------------------------------- #

def _validate_syntax(email: str) -> Optional[str]:
    """Normaliza + valida sintaxe. Devolve o e-mail NORMALIZADO (lowercase) ou None.

    Usa `email-validator` (RFC 5322 + IDN) quando disponível; senão cai num regex simples.
    NÃO faz lookup de MX aqui (fazemos com cache à parte)."""
    e = (email or "").strip()
    if not e or "@" not in e:
        return None
    try:
        import email_validator
        v = email_validator.validate_email(e, check_deliverability=False)
        return v.normalized.lower()
    except ImportError:
        return e.lower() if _EMAIL_RE.match(e) else None
    except Exception:  # noqa: BLE001 - EmailNotValidError e afins
        return None


def _resolve_mx_sync(domain: str) -> str:
    """'ok' | 'no_mx' | 'unknown'. Nunca levanta (mesma semântica do discovery.contact)."""
    try:
        import dns.resolver  # dnspython (já é dependência)
    except ImportError:
        return "unknown"
    try:
        answers = dns.resolver.resolve(domain, "MX", lifetime=5)
        # NULL MX (RFC 7505): um único registro "." significa "não recebe e-mail".
        hosts = [str(r.exchange).rstrip(".") for r in answers]
        if not hosts or hosts == [""]:
            return "no_mx"
        return "ok"
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        return "no_mx"
    except Exception:  # noqa: BLE001 - timeout/NoNameservers → incerto, fail-open
        return "unknown"


async def _has_mx(domain: str, redis=None) -> tuple[bool, bool]:
    """(has_mx, cached). Cache Redis por domínio 24h. Fail-open: 'unknown' conta como has_mx
    (não rejeita por falha de DNS/infra); só 'no_mx' definitivo rejeita."""
    if not domain:
        return False, False
    key = f"email_verify_mx:{domain}"
    if redis is not None:
        try:
            v = await redis.get(key)
            if v is not None:
                return v == "1", True
        except Exception:  # noqa: BLE001
            pass
    status = await asyncio.to_thread(_resolve_mx_sync, domain)
    has = status != "no_mx"
    if redis is not None and status in ("ok", "no_mx"):  # não cacheia 'unknown' (transitório)
        try:
            await redis.set(key, "1" if has else "0", ex=24 * 3600)
        except Exception:  # noqa: BLE001
            pass
    return has, False


async def verify_local(email: str, redis=None) -> VerifyResult:
    """Camada 0: sintaxe → descartável → MX → flag role-based. Sem API, custo zero."""
    normalized = _validate_syntax(email)
    if not normalized:
        return VerifyResult("invalid", "syntax", is_role_based(email), source="local")
    role = is_role_based(normalized)
    domain = _domain_of(normalized)
    if domain in DISPOSABLE_EMAIL_DOMAINS:
        return VerifyResult("disposable", "disposable_domain", role, source="local")
    has_mx, cached = await _has_mx(domain, redis)
    if not has_mx:
        return VerifyResult("invalid", "no_mx", role, cached=cached, source="local")
    return VerifyResult("valid", "ok", role, cached=cached, source="local")


# --------------------------------------------------------------------------- #
# Camada 1 — API Reoon
# --------------------------------------------------------------------------- #

def _map_reoon_status(raw: str) -> str:
    """Mapeia o status da Reoon para o nosso `email_verify_status`."""
    raw = (raw or "").strip().lower()
    if raw == "role_account":
        return "role"
    known = {"safe", "valid", "invalid", "disabled", "disposable",
             "inbox_full", "catch_all", "spamtrap", "unknown"}
    return raw if raw in known else "unknown"


def parse_reoon_response(data: dict, mode: str = "power") -> VerifyResult:
    """Função PURA: JSON da Reoon → VerifyResult. Testável sem rede.

    KL-128: num servidor **catch-all** (`is_catch_all=true`) o Reoon costuma devolver
    `safe`/`valid` porque o servidor aceita QUALQUER caixa no SMTP-check — não é confiável.
    Rebaixamos `safe`/`valid` + `is_catch_all` → `catch_all` (que passa pelo gate de score),
    atacando na origem o bounce de "catch-all disfarçado de safe"."""
    status = _map_reoon_status(data.get("status", "unknown"))
    score = data.get("overall_score")
    try:
        score = int(score) if score is not None else None
    except (TypeError, ValueError):
        score = None
    role = bool(data.get("is_role_account")) or status == "role"
    catch_all = bool(data.get("is_catch_all")) or status == "catch_all"
    if catch_all and status in ("safe", "valid"):
        status = "catch_all"  # catch-all server: safe do Reoon não é confiável

    reason = f"reoon_{mode}"
    return VerifyResult(status, reason, is_role_based=role, score=score,
                        source="reoon", catch_all=catch_all)


async def verify_reoon(email: str, mode: str = "power", api_key: Optional[str] = None,
                       client: Optional[httpx.AsyncClient] = None) -> VerifyResult:
    """Chama a API Reoon (`quick` ou `power`). Levanta em erro de rede/HTTP (o chamador
    trata via `verify_api`, que faz fallback). Semáforo global de 5 chamadas simultâneas."""
    key = api_key if api_key is not None else os.environ.get("REOON_API_KEY")
    if not key:
        raise RuntimeError("REOON_API_KEY ausente")
    timeout = 90.0 if mode == "power" else 10.0
    params = {"email": email, "key": key, "mode": mode}
    async with _reoon_semaphore:
        if client is not None:
            resp = await client.get(f"{REOON_BASE_URL}/verify", params=params, timeout=timeout)
        else:
            async with httpx.AsyncClient() as c:
                resp = await c.get(f"{REOON_BASE_URL}/verify", params=params, timeout=timeout)
    resp.raise_for_status()
    return parse_reoon_response(resp.json(), mode)


async def verify_api(email: str, mode: str = "power", api_key: Optional[str] = None,
                     client: Optional[httpx.AsyncClient] = None,
                     role_hint: bool = False) -> VerifyResult:
    """Camada 1 com fallback fail-open: se a Reoon estiver fora/timeout, devolve `unknown`
    (NÃO bloqueia o pipeline — o e-mail passou pela Camada 0 e pelo MX)."""
    try:
        res = await verify_reoon(email, mode, api_key, client)
        res.is_role_based = res.is_role_based or role_hint
        return res
    except Exception as exc:  # noqa: BLE001 - timeout/HTTP/no-key → fail-open
        print(f"[email_verifier] Reoon indisponível ({type(exc).__name__}); "
              f"seguindo sem verificar {_mask(email)}", flush=True)
        return VerifyResult("unknown", "api_unavailable", is_role_based=role_hint,
                            source="fallback")


async def check_balance(api_key: Optional[str] = None,
                        client: Optional[httpx.AsyncClient] = None) -> Optional[int]:
    """Saldo da conta Reoon (créditos). None se não der para ler."""
    key = api_key if api_key is not None else os.environ.get("REOON_API_KEY")
    if not key:
        return None
    try:
        url = f"{REOON_BASE_URL}/check-account-balance/"
        if client is not None:
            resp = await client.get(url, params={"key": key}, timeout=10.0)
        else:
            async with httpx.AsyncClient() as c:
                resp = await c.get(url, params={"key": key}, timeout=10.0)
        resp.raise_for_status()
        data = resp.json()
        for k in ("available_credits", "credits", "instant_credits", "balance"):
            if data.get(k) is not None:
                return int(data[k])
    except Exception as exc:  # noqa: BLE001
        print(f"[email_verifier] saldo Reoon indisponível: {type(exc).__name__}", flush=True)
    return None


# --------------------------------------------------------------------------- #
# Pipeline principal + cache
# --------------------------------------------------------------------------- #

async def _get_cached(email: str, redis=None) -> Optional[VerifyResult]:
    if redis is None:
        return None
    try:
        raw = await redis.get(_cache_key(email))
        if not raw:
            return None
        d = json.loads(raw)
        return VerifyResult(d["status"], d.get("reason", "cache"),
                            is_role_based=d.get("is_role_based", False),
                            cached=True, score=d.get("score"),
                            source="cache", catch_all=d.get("catch_all", False))
    except Exception:  # noqa: BLE001
        return None


async def _cache_result(email: str, result: VerifyResult, redis=None) -> None:
    if redis is None or result.source == "fallback":
        return  # não cacheia falha de infra (transitória por natureza)
    ttl = _CACHE_TTL_DEFINITIVE if result.status in _DEFINITIVE_STATUSES else _CACHE_TTL_TRANSIENT
    try:
        await redis.set(_cache_key(email), json.dumps(result.to_cache()), ex=ttl)
        if result.catch_all:  # domínio catch-all: cacheia o domínio 7d (todos os e-mails são)
            await redis.set(f"email_verify_domain:{_domain_of(email)}", "catch_all",
                            ex=_CACHE_TTL_TRANSIENT)
    except Exception:  # noqa: BLE001
        pass


async def verify_email(email: str, mode: str = "power", skip_api: bool = False,
                       redis=None, api_key: Optional[str] = None,
                       client: Optional[httpx.AsyncClient] = None) -> VerifyResult:
    """Pipeline completo: cache → Camada 0 → (cache domínio catch-all) → Camada 1.

    `skip_api=True` roda só a Camada 0 (profiler em modo rápido). Sem `REOON_API_KEY`
    configurada, também roda só a Camada 0 (nunca quebra por falta de key)."""
    e = (email or "").strip().lower()
    key = api_key if api_key is not None else os.environ.get("REOON_API_KEY")

    cached = await _get_cached(e, redis)
    if cached is not None:
        return cached

    local = await verify_local(e, redis)
    if local.status != "valid":
        await _cache_result(e, local, redis)
        return local

    if skip_api or not key:
        return local  # Camada 0 apenas

    # Domínio já conhecido como catch-all → não gasta crédito reverificando a inbox.
    if redis is not None:
        try:
            if await redis.get(f"email_verify_domain:{_domain_of(e)}") == "catch_all":
                return VerifyResult("catch_all", "domain_catch_all", local.is_role_based,
                                    cached=True, source="cache", catch_all=True)
        except Exception:  # noqa: BLE001
            pass

    api_result = await verify_api(e, mode, key, client, role_hint=local.is_role_based)
    await _cache_result(e, api_result, redis)
    return api_result


# --------------------------------------------------------------------------- #
# KL-145 — decisão de envio DESACOPLADA do Reoon: 3 filtros locais
# --------------------------------------------------------------------------- #

def _is_valid_syntax(email: str) -> bool:
    """Filtro 1 — sintaxe válida (reusa a normalização RFC da Camada 0)."""
    return _validate_syntax(email) is not None


async def _email_has_mx(email: str, redis=None) -> bool:
    """Filtro 2 — o domínio do e-mail tem registro MX? (cache Redis 24h/domínio, Camada 0).
    Fail-open: DNS incerto ('unknown') conta como MX presente; só 'no_mx' definitivo rejeita."""
    domain = _domain_of(_validate_syntax(email) or email)
    if not domain:
        return False
    has, _ = await _has_mx(domain, redis)
    return has


async def _is_blocklisted(email: str, store) -> bool:
    """Filtro 3 — e-mail na blocklist aprendente (cada bounce, via webhook, entra aqui).
    Sem store → False (fail-open: falha de infra nunca bloqueia o envio)."""
    if store is None:
        return False
    try:
        return await store.is_email_blocked(email)
    except Exception:  # noqa: BLE001 - erro de banco não deve bloquear envio
        return False


async def is_safe_to_send(email: str, redis=None, store=None) -> bool:
    """KL-145 — 3 filtros LOCAIS, sem API externa, sem gates, sem score.

      1. Sintaxe válida
      2. Domínio tem MX
      3. Não está na blocklist

    Tudo que passa nos 3 → ENVIA. Substitui a regra binária por-status do KL-137 (que dependia da
    verificação Reoon e travava o volume em 2-8/ciclo). O Reoon consumiu 10 cards e ~5.000 créditos
    sem melhorar o bounce (97% dos servidores BR = `unknown`); a blocklist aprendente (alimentada
    pelo webhook de bounce) faz o trabalho de aprender quem não recebe."""
    if not _is_valid_syntax(email):
        return False
    if not await _email_has_mx(email, redis):
        return False
    if await _is_blocklisted(email, store):
        return False
    return True


def _mask(email: str) -> str:
    email = email or ""
    if "@" not in email:
        return "***"
    local, _, dom = email.partition("@")
    return f"{local[:1]}***@{dom}"
