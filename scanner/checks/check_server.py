"""Check 08 — Server header exposto (Severidade: MÉDIA).

Spec: se o header ``Server`` revela versão (ex.: ``Apache/2.4.41``), FAIL.
A bare product name (e.g. ``nginx``, ``cloudflare``) is fine; a version number
or an OS build string is an information-disclosure FAIL. We also flag verbose
``X-Powered-By`` values (e.g. ``PHP/8.1.2``) as part of the same finding.
"""

from __future__ import annotations

import re

import httpx

from .base import CheckResult, Status, Severity, fetch, with_scheme

ORDER = 8
CHECK_ID = "check_08_server"
NAME = "Server header exposto"

# A version number: "Apache/2.4.41", "nginx/1.18.0", "Microsoft-IIS/10.0".
_VERSION_RE = re.compile(r"/\s*\d+(\.\d+)*")
# OS / build disclosure inside parentheses: "(Ubuntu)", "(Debian)", "(CentOS)".
_OS_RE = re.compile(r"\((?:ubuntu|debian|centos|red hat|win\d+|unix)", re.IGNORECASE)


async def check(url: str) -> CheckResult:
    https_url = with_scheme(url, "https")
    try:
        resp = await fetch(https_url, method="GET", follow_redirects=True)
    except (httpx.HTTPError, OSError) as exc:
        return CheckResult(
            name=NAME,
            status=Status.INCONCLUSO,
            severity=Severity.MEDIA,
            evidence=f"Falha ao obter resposta HTTPS: {exc!r}",
        )

    server = resp.headers.get("server", "")
    powered_by = resp.headers.get("x-powered-by", "")

    leaks = []
    if server and (_VERSION_RE.search(server) or _OS_RE.search(server)):
        leaks.append(f"Server: '{server}'")
    if powered_by:
        # X-Powered-By almost always leaks a stack/version; flag it verbosely.
        leaks.append(f"X-Powered-By: '{powered_by}'")

    if leaks:
        return CheckResult(
            name=NAME,
            status=Status.FAIL,
            severity=Severity.MEDIA,
            evidence="Versão/tecnologia exposta em header(s): " + "; ".join(leaks) + ".",
            details={"server": server, "x_powered_by": powered_by},
        )

    if server:
        return CheckResult(
            name=NAME,
            status=Status.PASS,
            severity=Severity.MEDIA,
            evidence=f"Header Server presente sem versão exposta: '{server}'.",
            details={"server": server},
        )

    return CheckResult(
        name=NAME,
        status=Status.PASS,
        severity=Severity.MEDIA,
        evidence="Header Server ausente (nenhuma versão exposta).",
        details={"server": None},
    )
