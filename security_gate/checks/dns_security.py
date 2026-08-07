"""KL-149 — checks de DNS: DNSSEC (assinatura da zona, anti-spoofing) e CAA (quais CAs podem
emitir certificado). Passivo: apenas consultas DNS de leitura (dnspython). As chamadas bloqueantes
rodam em thread (`asyncio.to_thread`) p/ não travar o event loop; os helpers `_query_*` são o
ponto de mock nos testes."""
from __future__ import annotations

import asyncio
from typing import List

from ..models import Result, Severity, Status
from ..utils import _host


def _query_dnskey(hostname: str) -> bool:
    """True se a zona tem DNSKEY (DNSSEC). Levanta NoAnswer/NXDOMAIN se não tiver. Bloqueante."""
    import dns.flags
    import dns.resolver
    resolver = dns.resolver.Resolver()
    resolver.use_edns(0, dns.flags.DO, 4096)   # consulta DNSSEC-aware
    answers = resolver.resolve(hostname, "DNSKEY")
    return bool(list(answers))


def _query_caa(hostname: str) -> List[str]:
    """Registros CAA da zona (lista de strings). NoAnswer se não houver. Bloqueante."""
    import dns.resolver
    return [str(r) for r in dns.resolver.resolve(hostname, "CAA")]


async def check_dns_security(client, base_url: str, config=None) -> List[Result]:
    hostname = _host(base_url).split(":")[0]
    results: List[Result] = []

    # --- DNSSEC ---
    try:
        await asyncio.to_thread(_query_dnskey, hostname)
        results.append(Result("dnssec_ok", "dns", hostname, Status.PASS, Severity.MEDIUM,
                              "DNSSEC configurado (zona assinada)"))
    except Exception as exc:  # noqa: BLE001 - NoAnswer/NXDOMAIN → não configurado; resto → sem sinal
        name = type(exc).__name__
        if name in ("NoAnswer", "NXDOMAIN"):
            results.append(Result("dnssec_missing", "dns", hostname, Status.FAIL, Severity.MEDIUM,
                                  "DNSSEC não configurado (spoofing de DNS possível)"))
        else:
            results.append(Result("dnssec_error", "dns", hostname, Status.ERROR, Severity.INFO,
                                  f"Não foi possível verificar DNSSEC ({name})"))

    # --- CAA ---
    try:
        records = await asyncio.to_thread(_query_caa, hostname)
        results.append(Result("caa_ok", "dns", hostname, Status.PASS, Severity.MEDIUM,
                              f"CAA configurado ({len(records)} registro(s))"))
    except Exception as exc:  # noqa: BLE001
        if type(exc).__name__ in ("NoAnswer", "NXDOMAIN"):
            results.append(Result("caa_missing", "dns", hostname, Status.FAIL, Severity.MEDIUM,
                                  "Sem registros CAA (qualquer CA pode emitir certificado)"))
        # erro de infra (timeout/servfail) → sem sinal (não polui o relatório)

    return results
