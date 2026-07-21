"""Check 10 — Arquivos sensíveis expostos (Severidade: CRÍTICA).

Spec: tenta GET em ``.env``, ``.git/config``, ``wp-config.php.bak`` e
``debug.log``.

The hard part is avoiding false positives: many hosts (SPAs, custom 404s)
answer *every* path with a 200 and an HTML page. A path only counts as exposed
when the response is 200, is **not** an HTML page, and its body matches a
content signature specific to that file. This keeps the CRITICA finding honest.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin

import httpx

from .base import (
    CheckResult,
    Status,
    Severity,
    fetch,
    base_url,
    looks_like_html,
)

ORDER = 10
CHECK_ID = "check_10_sensitive"
NAME = "Arquivos sensíveis expostos"


def _sig_env(text: str) -> bool:
    # dotenv: KEY=VALUE lines, often APP_/DB_/SECRET/AWS_ prefixes.
    if re.search(r"(?m)^\s*[A-Z0-9_]{2,}\s*=", text):
        return True
    return bool(re.search(r"(APP_|DB_|SECRET|AWS_|API_KEY|PASSWORD)", text))


def _sig_git_config(text: str) -> bool:
    return "[core]" in text or "[remote" in text or "repositoryformatversion" in text


def _sig_wp_bak(text: str) -> bool:
    low = text.lower()
    return "<?php" in low or "db_password" in low or "define(" in low


def _sig_debug_log(text: str) -> bool:
    # A log file: timestamps / stack traces / level markers, and plaintext.
    return bool(
        re.search(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}", text)
        or re.search(r"(?i)(error|warning|notice|stack trace|exception)", text)
    )


# path -> (signature predicate, human description)
_TARGETS = [
    (".env", _sig_env, "variáveis de ambiente/segredos"),
    (".git/config", _sig_git_config, "configuração de repositório Git"),
    ("wp-config.php.bak", _sig_wp_bak, "backup de config do WordPress"),
    ("debug.log", _sig_debug_log, "log de depuração"),
]


async def check(url: str) -> CheckResult:
    root = base_url(url) + "/"
    exposed: list[dict] = []
    probed: list[str] = []
    responded = 0  # quantas sondas obtiveram resposta HTTP (não exceção)

    for path, signature, desc in _TARGETS:
        target = urljoin(root, path)
        probed.append(target)
        try:
            resp = await fetch(target, method="GET", follow_redirects=False)
        except (httpx.HTTPError, OSError):
            continue
        responded += 1

        if resp.status_code != 200:
            continue
        if looks_like_html(resp):
            # SPA / custom-error HTML fallback, not the real file.
            continue
        body = resp.text[:4096]
        if signature(body):
            exposed.append({"path": path, "url": target, "what": desc})

    # Nenhuma sonda respondeu (site inacessível) → não dá para afirmar PASS.
    if responded == 0:
        return CheckResult(
            name=NAME,
            status=Status.INCONCLUSO,
            severity=Severity.CRITICA,
            evidence="Não foi possível acessar o conteúdo para verificação.",
            details={"probed": probed},
        )

    if exposed:
        listing = ", ".join(f"{e['path']} ({e['what']})" for e in exposed)
        return CheckResult(
            name=NAME,
            status=Status.FAIL,
            severity=Severity.CRITICA,
            evidence=f"Arquivo(s) sensível(is) acessível(is) publicamente: {listing}.",
            details={"exposed": exposed, "probed": probed},
        )

    return CheckResult(
        name=NAME,
        status=Status.PASS,
        severity=Severity.CRITICA,
        evidence=(
            f"Nenhum arquivo sensível exposto ({len(probed)} caminho(s) testado(s))."
        ),
        details={"probed": probed},
    )
