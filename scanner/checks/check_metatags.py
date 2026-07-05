"""Check 12 — Meta tags default (Severidade: BAIXA).

Spec: verifica se a meta description (ou title/generator) contém fingerprints
de framework (CRA, Next.js, etc.).

Leftover default metadata leaks the underlying platform and signals a site that
was shipped without hardening. This is a low-severity, informational finding —
it aids an attacker's reconnaissance but is not itself exploitable.
"""

from __future__ import annotations

import re

import httpx

from .base import CheckResult, Status, Severity, fetch, with_scheme

NAME = "Meta tags default"

# Framework/platform fingerprints commonly left in default templates.
_FINGERPRINTS = [
    (r"web site created using create-react-app", "Create React App (default)"),
    (r"create-react-app", "Create React App"),
    (r"react app", "React App (title default)"),
    (r"\bnext\.js\b", "Next.js"),
    (r"nuxt\.js", "Nuxt.js"),
    (r"vite \+ ", "Vite (default template)"),
    (r"content=\"WordPress", "WordPress (generator)"),
    (r"powered by wordpress", "WordPress"),
    (r"gatsby", "Gatsby"),
    (r"vue\.js app", "Vue.js (default)"),
    (r"document title", "template placeholder"),
    (r"lorem ipsum", "conteúdo placeholder (Lorem Ipsum)"),
]

_META_DESC_RE = re.compile(
    r"""<meta[^>]+name\s*=\s*['"]description['"][^>]*content\s*=\s*['"]([^'"]*)['"]""",
    re.IGNORECASE,
)
_META_GEN_RE = re.compile(
    r"""<meta[^>]+name\s*=\s*['"]generator['"][^>]*content\s*=\s*['"]([^'"]*)['"]""",
    re.IGNORECASE,
)
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


async def check(url: str) -> CheckResult:
    https_url = with_scheme(url, "https")
    try:
        resp = await fetch(https_url, method="GET", follow_redirects=True)
    except (httpx.HTTPError, OSError) as exc:
        return CheckResult(
            name=NAME,
            status=Status.INCONCLUSO,
            severity=Severity.BAIXA,
            evidence=f"Falha ao obter o HTML da página: {exc!r}",
        )

    html = resp.text
    description = _first(_META_DESC_RE, html)
    generator = _first(_META_GEN_RE, html)
    title = _first(_TITLE_RE, html)

    haystack = " | ".join(
        part for part in (description, generator, title) if part
    )
    haystack_l = haystack.lower()
    # Also scan the generator meta tag by its raw form for WordPress etc.
    raw_l = html[:8192].lower()

    matches = []
    for pattern, label in _FINGERPRINTS:
        if re.search(pattern, haystack_l) or re.search(pattern, raw_l):
            matches.append(label)

    if matches:
        # Deduplicate preserving order.
        seen = []
        for m in matches:
            if m not in seen:
                seen.append(m)
        return CheckResult(
            name=NAME,
            status=Status.FAIL,
            severity=Severity.BAIXA,
            evidence=(
                "Fingerprint de framework/plataforma exposto em meta tags: "
                + ", ".join(seen)
                + (f". (meta description: '{description}')" if description else ".")
            ),
            details={
                "matches": seen,
                "description": description,
                "generator": generator,
                "title": title,
            },
        )

    return CheckResult(
        name=NAME,
        status=Status.PASS,
        severity=Severity.BAIXA,
        evidence=(
            "Nenhum fingerprint de framework default nas meta tags"
            + (f" (description: '{description}')." if description else ".")
        ),
        details={"description": description, "generator": generator, "title": title},
    )


def _first(regex: re.Pattern, text: str) -> str:
    m = regex.search(text)
    return (m.group(1).strip() if m else "")[:300]
