"""KL-149 — check de DEPENDÊNCIAS JS com CVE conhecida. Passivo: detecta a versão de bibliotecas
comuns no HTML/JS (URL do bundle, comentário de versão) e compara com uma base LOCAL de versões
vulneráveis — sem API externa. Não é um SCA completo; cobre as libs de front mais exploradas."""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

import httpx

from ..models import Result, Severity, Status

# lib → lista de (faixa_vulnerável, CVE, descrição, severidade). "*" = todas as versões.
KNOWN_VULNERABLE = {
    "jquery": [("<3.5.0", "CVE-2020-11022", "XSS via manipulação de HTML", Severity.HIGH)],
    "lodash": [("<4.17.21", "CVE-2021-23337", "Command injection via template", Severity.HIGH)],
    "angular": [("<1.8.0", "CVE-2022-25869", "XSS (AngularJS legado)", Severity.HIGH)],
    "bootstrap": [("<3.4.1", "CVE-2019-8331", "XSS via data-template", Severity.MEDIUM)],
    "moment": [("*", "CVE-2022-31129", "ReDoS (biblioteca descontinuada)", Severity.MEDIUM)],
    "vue": [("<2.6.11", "CVE-2019-16769", "XSS via SSR", Severity.MEDIUM)],
    "axios": [("<0.21.2", "CVE-2021-3749", "ReDoS via trim", Severity.MEDIUM)],
}

_VERSION_PATTERNS = {
    "jquery": r'jquery[/@.-]v?(\d+\.\d+\.\d+)',
    "lodash": r'lodash[/@.-]v?(\d+\.\d+\.\d+)',
    "angular": r'angular(?:js)?[/@.-]v?(\d+\.\d+\.\d+)',
    "bootstrap": r'bootstrap[/@.-]v?(\d+\.\d+\.\d+)',
    "moment": r'moment[/@.-]v?(\d+\.\d+\.\d+)',
    "vue": r'vue[/@.-]v?(\d+\.\d+\.\d+)',
    "axios": r'axios[/@.-]v?(\d+\.\d+\.\d+)',
}


def _detect_version(lib: str, text: str) -> Optional[str]:
    pattern = _VERSION_PATTERNS.get(lib)
    if not pattern:
        return None
    m = re.search(pattern, text, re.IGNORECASE)
    return m.group(1) if m else None


def _parse(v: str) -> Tuple[int, ...]:
    try:
        return tuple(int(x) for x in v.split("."))
    except (ValueError, AttributeError):
        return (0,)


def _version_matches(version: str, vuln_range: str) -> bool:
    """True se `version` está na faixa vulnerável. Suporta '*' (todas) e '<x.y.z'."""
    if vuln_range == "*":
        return True
    if vuln_range.startswith("<"):
        return _parse(version) < _parse(vuln_range[1:].strip())
    return False


async def check_dependencies(client: httpx.AsyncClient, base_url: str, config=None) -> List[Result]:
    try:
        r = await client.get(base_url)
    except httpx.HTTPError as exc:
        return [Result("deps_error", "dependencies", "/", Status.ERROR, Severity.INFO,
                       f"Não foi possível verificar dependências: {exc!r}")]

    text = r.text
    results: List[Result] = []
    for lib, vulns in KNOWN_VULNERABLE.items():
        version = _detect_version(lib, text)
        if not version:
            continue
        for vuln_range, cve, desc, severity in vulns:
            if _version_matches(version, vuln_range):
                results.append(Result(f"dep_{lib}", "dependencies", "/", Status.FAIL, severity,
                                      f"{lib} {version} — {cve}: {desc}"))
                break   # 1 finding por lib

    if not results:
        results.append(Result("deps_ok", "dependencies", "/", Status.PASS, Severity.HIGH,
                              "Nenhuma dependência JS com CVE conhecida detectada"))
    return results
