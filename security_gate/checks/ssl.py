"""KL-141 — check de SSL/TLS: validade do certificado + protocolo TLS.

Reusa o `scanner.tls_analyzer.get_tls_info` (UM handshake, cacheado, já testado) em vez de
reimplementar o socket TLS — e a lista `WEAK_PROTOCOLS`. O engine passa o `client` só por
uniformidade da assinatura; o handshake TLS é feito por fora do httpx (nível de socket)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

import httpx

from scanner.tls_analyzer import WEAK_PROTOCOLS, get_tls_info

from ..models import Result, Severity, Status


def _hostname(base_url: str) -> str:
    return base_url.split("//")[-1].split("/")[0].split(":")[0]


def _days_left(not_after) -> Optional[int]:
    """Dias até a expiração do cert. `not_after` pode ser aware ou naive (assume UTC)."""
    if not isinstance(not_after, datetime):
        return None
    if not_after.tzinfo is None:
        not_after = not_after.replace(tzinfo=timezone.utc)
    return (not_after - datetime.now(timezone.utc)).days


async def check_ssl(client: httpx.AsyncClient, base_url: str, config=None) -> List[Result]:
    """Certificado (expiração/validade) + protocolo TLS negociado. `config.ssl_min_days` define a
    janela de aviso (HIGH) antes da expiração; ≤7 dias é sempre CRÍTICO."""
    min_days = getattr(config, "ssl_min_days", None) or 14
    host = _hostname(base_url)
    results: List[Result] = []
    try:
        info = await get_tls_info(host)
    except Exception as exc:  # noqa: BLE001 - handshake nunca derruba o gate
        return [Result("ssl_error", "ssl", "/", Status.ERROR, Severity.INFO,
                       f"Erro ao verificar SSL: {exc!r}")]

    if not info.get("ok"):
        return [Result("ssl_error", "ssl", "/", Status.ERROR, Severity.INFO,
                       f"Erro ao verificar SSL: {info.get('error')}")]

    cert = info.get("cert") or {}
    days = _days_left(cert.get("not_after"))
    verified = info.get("verified", True)

    # --- Validade do certificado --- #
    if days is not None and days <= 0:
        results.append(Result("ssl_expired", "ssl", "/", Status.FAIL, Severity.CRITICAL,
                              f"Certificado EXPIRADO (há {abs(days)} dia(s))"))
    elif not verified:
        results.append(Result("ssl_invalid", "ssl", "/", Status.FAIL, Severity.CRITICAL,
                              f"Certificado inválido: {info.get('verify_error') or 'falha na verificação'}"))
    elif days is None:
        results.append(Result("ssl_error", "ssl", "/", Status.ERROR, Severity.INFO,
                              "Não foi possível ler a validade do certificado"))
    elif days <= 7:
        results.append(Result("ssl_expiring", "ssl", "/", Status.FAIL, Severity.CRITICAL,
                              f"Certificado expira em {days} dia(s)"))
    elif days <= min_days:
        results.append(Result("ssl_expiring", "ssl", "/", Status.FAIL, Severity.HIGH,
                              f"Certificado expira em {days} dia(s)"))
    else:
        results.append(Result("ssl_valid", "ssl", "/", Status.PASS, Severity.CRITICAL,
                              f"Certificado válido ({days} dia(s) restantes)"))

    # --- Protocolo TLS negociado --- #
    protocol = info.get("protocol")
    if protocol in WEAK_PROTOCOLS:
        results.append(Result("ssl_protocol", "ssl", "/", Status.FAIL, Severity.CRITICAL,
                              f"Protocolo inseguro: {protocol}"))
    else:
        results.append(Result("ssl_protocol", "ssl", "/", Status.PASS, Severity.HIGH,
                              f"Protocolo: {protocol}"))

    return results
