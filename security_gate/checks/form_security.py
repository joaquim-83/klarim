"""KL-149 — check de FORM ACTION HIJACKING. Passivo: 1 GET na homepage; se algum `<form action>`
aponta para um domínio EXTERNO (absoluto e fora do host), credenciais digitadas poderiam vazar."""
from __future__ import annotations

import re
from typing import List

import httpx

from ..models import Result, Severity, Status
from ..utils import _host

_FORM_ACTION_RE = re.compile(r'<form[^>]+action=["\']([^"\']+)["\']', re.IGNORECASE)


async def check_form_security(client: httpx.AsyncClient, base_url: str, config=None) -> List[Result]:
    try:
        r = await client.get(base_url)
    except httpx.HTTPError as exc:
        return [Result("forms_error", "forms", "/", Status.ERROR, Severity.INFO,
                       f"Não foi possível verificar forms: {exc!r}")]

    hostname = _host(base_url)
    results: List[Result] = []
    for action in _FORM_ACTION_RE.findall(r.text):
        act = action.strip()
        # Só actions ABSOLUTAS (http/https/protocol-relative) podem ir p/ outro domínio.
        if act.startswith(("http://", "https://", "//")) and hostname and hostname not in act:
            results.append(Result("form_external", "forms", act[:80], Status.FAIL,
                                  Severity.CRITICAL,
                                  f"Form aponta para domínio externo: {act[:60]}"))

    if not results:
        results.append(Result("forms_ok", "forms", "/", Status.PASS, Severity.HIGH,
                              "Forms apontam para o próprio domínio (ou são relativos)"))
    return results
