"""Check 45 — Informações sensíveis em comentários HTML (Severidade: MÉDIA/ALTA, KL-38).

Passivo: extrai os comentários ``<!-- ... -->`` do HTML servido e procura informação
operacional vazada — credenciais, chaves, IPs internos, paths de sistema, referências a
servidor/banco e TODOs de segurança. Comentários inofensivos (copyright, meta, tracking,
markers de template, condicionais de IE) são filtrados **antes** (evita falso positivo).
"""

from __future__ import annotations

import re
from typing import List, Tuple

import httpx

from .base import CheckResult, Status, Severity, fetch, with_scheme

ORDER = 45
CHECK_ID = "check_45_html_comments"
NAME = "Informações sensíveis em comentários HTML"

_COMMENT_RE = re.compile(r"<!--(.*?)-->", re.DOTALL)

# (regex, motivo, nível) — nível "alta" ou "media".
_SENSITIVE: Tuple[Tuple[re.Pattern, str, str], ...] = (
    (re.compile(r"(?i)(password|passwd|pwd)\s*[:=]"), "possível credencial", "alta"),
    (re.compile(r"(?i)(api[_-]?key|apikey|secret[_-]?key)\s*[:=]"), "possível chave de API", "alta"),
    (re.compile(r"(?i)(token|auth[_-]?token)\s*[:=]"), "possível token de autenticação", "alta"),
    (re.compile(r"(?i)(/var/www/|/home/\w+/|/opt/|[cC]:\\\\)"), "path de sistema exposto", "alta"),
    (re.compile(r"(?i)(server|host|hostname)\s*[:=]\s*\S+"), "referência a servidor interno", "media"),
    (re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"), "endereço IP interno", "media"),
    (re.compile(r"(?i)(db|database|mysql|postgres|mongo)\s*[:=]"), "referência a banco de dados", "media"),
    (re.compile(r"(?i)\bTODO\b.*(?:security|seguran|fix|corrigir|hack|vuln|xss|sql)"), "TODO de segurança não resolvido", "media"),
    (re.compile(r"(?i)\bFIXME\b"), "FIXME não resolvido", "media"),
    (re.compile(r"(?i)\bHACK\b"), "HACK não resolvido", "media"),
    (re.compile(r"(?i)(staging|dev|development)\."), "referência a ambiente não-produção", "media"),
)

_SAFE = (
    re.compile(r"(?i)copyright|license|licen|©|\(c\)"),
    re.compile(r"(?i)viewport|charset|IE=edge"),
    re.compile(r"(?i)google\s*analytics|gtm|facebook|hotjar"),
    re.compile(r"(?i)end\s+(if|header|footer|sidebar|nav|content|section|main|wrapper)"),
    re.compile(r"^\s*\[if\s+"),        # comentário condicional de IE
    re.compile(r"^\s*$"),              # vazio
)


def analyze_comments(html: str) -> List[dict]:
    """Retorna ``[{comment, reason, level}]`` dos comentários sensíveis (não-safe)."""
    findings: List[dict] = []
    for raw in _COMMENT_RE.findall(html or ""):
        text = raw.strip()
        if any(p.search(text) for p in _SAFE):
            continue
        for pat, reason, level in _SENSITIVE:
            if pat.search(text):
                snippet = text if len(text) <= 80 else text[:77] + "..."
                findings.append({"comment": snippet, "reason": reason, "level": level})
                break
    return findings


async def check(url: str) -> CheckResult:
    https_url = with_scheme(url, "https")
    try:
        resp = await fetch(https_url, method="GET", follow_redirects=True)
    except (httpx.HTTPError, OSError) as exc:
        return CheckResult(name=NAME, status=Status.INCONCLUSO, severity=Severity.MEDIA,
                           evidence=f"Falha ao obter o HTML da página: {exc!r}")

    findings = analyze_comments(resp.text or "")
    if not findings:
        return CheckResult(name=NAME, status=Status.PASS, severity=Severity.MEDIA,
                           evidence="Nenhum comentário HTML com informação sensível encontrado.")

    high = any(f["level"] == "alta" for f in findings)
    severity = Severity.ALTA if high else Severity.MEDIA
    n = len(findings)
    listed = "; ".join(f"{f['reason']} (\"{f['comment']}\")" for f in findings[:3])
    return CheckResult(
        name=NAME, status=Status.FAIL, severity=severity,
        evidence=f"{n} comentário(s) HTML com informação sensível: {listed}.",
        details={"findings": findings})
