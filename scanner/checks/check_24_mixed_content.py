"""Check 24 — Mixed content (Severidade: MÉDIA).

Passivo: GET na página HTTPS e parse do HTML. Recursos carregados via `http://`
(script/css/img/iframe/video/audio/source/embed/object) num site HTTPS podem ser
interceptados e trocados por código malicioso. Ignora `http://localhost` e `data:`.
"""

from __future__ import annotations

from html.parser import HTMLParser
from urllib.parse import urljoin

import httpx

from .base import CheckResult, Status, Severity, fetch, with_scheme

ORDER = 24
CHECK_ID = "check_24_mixed_content"
NAME = "Mixed content (recursos HTTP em página HTTPS)"

# tag -> atributo que carrega o recurso
_RESOURCE_ATTR = {
    "script": "src", "img": "src", "iframe": "src", "video": "src",
    "audio": "src", "source": "src", "embed": "src",
    "link": "href", "object": "data",
}


class _ResourceExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.urls: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        attr = _RESOURCE_ATTR.get(tag.lower())
        if not attr:
            return
        adict = {k.lower(): (v or "") for k, v in attrs}
        # <link> só conta como recurso carregado quando é stylesheet/preload/etc.
        if tag.lower() == "link":
            rel = adict.get("rel", "").lower()
            if not any(r in rel for r in ("stylesheet", "preload", "icon", "prefetch")):
                return
        val = adict.get(attr, "").strip()
        if val:
            self.urls.append(val)


def _is_insecure(u: str) -> bool:
    low = u.lower()
    if not low.startswith("http://"):
        return False
    host = low[len("http://"):].split("/", 1)[0]
    return not (host.startswith("localhost") or host.startswith("127.0.0.1"))


async def check(url: str) -> CheckResult:
    https_url = with_scheme(url, "https")
    try:
        resp = await fetch(https_url, method="GET", follow_redirects=True)
    except (httpx.HTTPError, OSError) as exc:
        return CheckResult(name=NAME, status=Status.INCONCLUSO, severity=Severity.MEDIA,
                           evidence=f"Falha ao obter a página: {exc!r}")

    parser = _ResourceExtractor()
    try:
        parser.feed(resp.text)
    except Exception:  # noqa: BLE001 - HTML malformado não derruba o check
        pass

    insecure = []
    for raw in parser.urls:
        absu = urljoin(https_url, raw)
        if _is_insecure(absu) and absu not in insecure:
            insecure.append(absu)

    if insecure:
        names = ", ".join(u.rsplit("/", 1)[-1] or u for u in insecure[:3])
        extra = f" (+{len(insecure) - 3})" if len(insecure) > 3 else ""
        return CheckResult(
            name=NAME, status=Status.FAIL, severity=Severity.MEDIA,
            evidence=f"{len(insecure)} recurso(s) carregado(s) via HTTP inseguro: {names}{extra}.",
            details={"insecure": insecure[:20]})

    return CheckResult(name=NAME, status=Status.PASS, severity=Severity.MEDIA,
                       evidence="Nenhum recurso carregado via HTTP inseguro.")
