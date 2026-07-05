"""Check 01 — HTTPS ativo (Severidade: CRÍTICA).

Spec: HEAD request na porta 80. Se a porta 80 responde com conteúdo sem
redirecionar (301/302/307/308) para HTTPS, FAIL.

Passing means one of:
  * plain HTTP is refused/closed entirely (only HTTPS is served), or
  * plain HTTP answers with a redirect to an https:// URL.

Failing means plain HTTP serves real content (2xx) or redirects to another
http:// location — i.e. traffic can flow unencrypted.
"""

from __future__ import annotations

import httpx

from .base import CheckResult, Status, Severity, with_scheme

NAME = "HTTPS ativo"


async def check(url: str) -> CheckResult:
    http_url = with_scheme(url, "http")

    try:
        # Do NOT follow redirects: we want to inspect the redirect itself.
        resp = await _head_or_get(http_url)
    except httpx.ConnectError:
        # Port 80 refused the connection -> only HTTPS is reachable. Good.
        return CheckResult(
            name=NAME,
            status=Status.PASS,
            severity=Severity.CRITICA,
            evidence="Porta 80 (HTTP) recusou conexão; apenas HTTPS é servido.",
        )
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        return CheckResult(
            name=NAME,
            status=Status.INCONCLUSO,
            severity=Severity.CRITICA,
            evidence=f"Não foi possível avaliar a porta 80: {exc!r}",
        )

    location = resp.headers.get("location", "")

    if resp.is_redirect:
        if location.lower().startswith("https://"):
            return CheckResult(
                name=NAME,
                status=Status.PASS,
                severity=Severity.CRITICA,
                evidence=(
                    f"HTTP {resp.status_code} redireciona para HTTPS "
                    f"({location})."
                ),
                details={"status_code": resp.status_code, "location": location},
            )
        return CheckResult(
            name=NAME,
            status=Status.FAIL,
            severity=Severity.CRITICA,
            evidence=(
                f"HTTP {resp.status_code} redireciona para destino NÃO-HTTPS "
                f"({location or 'sem Location'})."
            ),
            details={"status_code": resp.status_code, "location": location},
        )

    # Any non-redirect answer on port 80 means content is served over HTTP.
    return CheckResult(
        name=NAME,
        status=Status.FAIL,
        severity=Severity.CRITICA,
        evidence=(
            f"Porta 80 responde HTTP {resp.status_code} sem redirecionar para "
            "HTTPS; tráfego pode trafegar sem criptografia."
        ),
        details={"status_code": resp.status_code},
    )


async def _head_or_get(http_url: str) -> httpx.Response:
    """HEAD first (cheaper); fall back to GET if the server rejects HEAD."""
    from .base import fetch  # local import to keep module import graph flat

    resp = await fetch(http_url, method="HEAD", follow_redirects=False)
    if resp.status_code in (405, 501):
        resp = await fetch(http_url, method="GET", follow_redirects=False)
    return resp
