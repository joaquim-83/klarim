"""Check 29 — Google Safe Browsing (Severidade: CRÍTICA).

Passivo: consulta a API do Google Safe Browsing v4 (gratuita, exige chave em
`GOOGLE_SAFE_BROWSING_KEY`). Site flagado (malware/phishing) → FAIL. Limpo → PASS.
Sem chave ou API indisponível → INCONCLUSO (com nota).
"""

from __future__ import annotations

import json
import os

import httpx

from .base import CheckResult, Status, Severity, with_scheme, USER_AGENT

ORDER = 29
CHECK_ID = "check_29_safe_browsing"
NAME = "Google Safe Browsing"

_ENDPOINT = "https://safebrowsing.googleapis.com/v4/threatMatches:find"
_THREATS = ["MALWARE", "SOCIAL_ENGINEERING", "UNWANTED_SOFTWARE",
            "POTENTIALLY_HARMFUL_APPLICATION"]


async def _query(target: str, key: str) -> httpx.Response:
    payload = {
        "client": {"clientId": "klarim", "clientVersion": "1.0"},
        "threatInfo": {
            "threatTypes": _THREATS,
            "platformTypes": ["ANY_PLATFORM"],
            "threatEntryTypes": ["URL"],
            "threatEntries": [{"url": target}],
        },
    }
    async with httpx.AsyncClient(timeout=10.0, headers={"User-Agent": USER_AGENT}) as client:
        return await client.post(f"{_ENDPOINT}?key={key}", json=payload)


async def check(url: str) -> CheckResult:
    key = os.environ.get("GOOGLE_SAFE_BROWSING_KEY", "").strip()
    if not key:
        return CheckResult(
            name=NAME, status=Status.INCONCLUSO, severity=Severity.CRITICA,
            evidence="Verificação não realizada — GOOGLE_SAFE_BROWSING_KEY não configurada.")

    target = with_scheme(url, "https")
    try:
        resp = await _query(target, key)
    except (httpx.HTTPError, OSError):
        return CheckResult(name=NAME, status=Status.INCONCLUSO, severity=Severity.CRITICA,
                           evidence="Não foi possível consultar o Google Safe Browsing.")

    if resp.status_code != 200:
        return CheckResult(name=NAME, status=Status.INCONCLUSO, severity=Severity.CRITICA,
                           evidence=f"Safe Browsing indisponível (status {resp.status_code}).")

    try:
        data = resp.json()
    except (json.JSONDecodeError, ValueError):
        return CheckResult(name=NAME, status=Status.INCONCLUSO, severity=Severity.CRITICA,
                           evidence="Resposta inesperada do Safe Browsing.")

    matches = data.get("matches") or []
    if matches:
        kinds = ", ".join(sorted({m.get("threatType", "?") for m in matches}))
        return CheckResult(
            name=NAME, status=Status.FAIL, severity=Severity.CRITICA,
            evidence=f"O site está flagado pelo Google como perigoso ({kinds}).",
            details={"matches": kinds})

    return CheckResult(name=NAME, status=Status.PASS, severity=Severity.CRITICA,
                       evidence="O site não está flagado pelo Google Safe Browsing.")
