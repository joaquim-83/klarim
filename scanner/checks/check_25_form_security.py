"""Check 25 — Formulários inseguros (Severidade: ALTA).

Passivo: parse do HTML e leitura dos `<form>`. Um form (POST) com `action`
`http://` envia dados sem criptografia → FAIL. `action` para domínio registrável
diferente (cross-origin) → FAIL. Sem action / relativo / HTTPS mesmo domínio → PASS.
"""

from __future__ import annotations

from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import httpx

from .base import (CheckResult, Status, Severity, fetch, with_scheme,
                   domain_of, registrable_domain, content_guard)

ORDER = 25
CHECK_ID = "check_25_form_security"
NAME = "Formulários inseguros"


class _FormExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.forms: list[dict] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() != "form":
            return
        adict = {k.lower(): (v or "") for k, v in attrs}
        self.forms.append({"method": adict.get("method", "get").lower().strip(),
                           "action": adict.get("action", "").strip()})


async def check(url: str) -> CheckResult:
    https_url = with_scheme(url, "https")
    try:
        resp = await fetch(https_url, method="GET", follow_redirects=True)
    except (httpx.HTTPError, OSError) as exc:
        return CheckResult(name=NAME, status=Status.INCONCLUSO, severity=Severity.ALTA,
                           evidence=f"Falha ao obter a página: {exc!r}")

    guard = content_guard(resp, NAME, Severity.ALTA)
    if guard:
        return guard

    parser = _FormExtractor()
    try:
        parser.feed(resp.text)
    except Exception:  # noqa: BLE001
        pass

    page_reg = registrable_domain(domain_of(https_url))
    problems: list[dict] = []
    for form in parser.forms:
        if form["method"] != "post":
            continue
        action = form["action"]
        if not action:
            continue  # posta para a própria página (mesmo domínio, HTTPS) → ok
        if action.lower().startswith("http://"):
            problems.append({"action": action, "why": "http"})
            continue
        host = (urlparse(urljoin(https_url, action)).hostname or "").lower()
        if host and registrable_domain(host) != page_reg:
            problems.append({"action": action, "why": "cross-origin"})

    if problems:
        p = problems[0]
        why = ("envia dados sem HTTPS" if p["why"] == "http"
               else "envia dados para outro domínio")
        return CheckResult(
            name=NAME, status=Status.FAIL, severity=Severity.ALTA,
            evidence=f"Formulário {why}: action={p['action']}.",
            details={"problems": problems})

    return CheckResult(name=NAME, status=Status.PASS, severity=Severity.ALTA,
                       evidence="Formulários enviam dados de forma segura (HTTPS, mesmo domínio).")
