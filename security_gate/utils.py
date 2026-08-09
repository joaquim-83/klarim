"""KL-147 — utilidades compartilhadas do Security Gate.

`matches_spa_fingerprint` é usado por `checks/exposure.py` E `checks/api_security.py` para
distinguir um **fallback de SPA** (app que devolve o mesmo `index.html` — 200 — para QUALQUER
path) de um arquivo/endpoint REALMENTE exposto. O engine captura o fingerprint num "probe de
controle" (HEAD num path que não existe, KL-147) e o passa aos checks."""
from __future__ import annotations

import re
from typing import List

import httpx

# --------------------------------------------------------------------------- #
# Extração de URLs de <script src> mesma-origem (compartilhado por credentials + infrastructure)
# --------------------------------------------------------------------------- #
_SCRIPT_SRC_RE = re.compile(r'<script[^>]+src=["\']([^"\']+)["\']', re.IGNORECASE)


def _origin(base_url: str) -> str:
    return "/".join(base_url.split("/")[:3])   # https://host


def _host(base_url: str) -> str:
    parts = base_url.split("/")
    return parts[2] if len(parts) > 2 else ""


def _extract_js_urls(html: str, base_url: str) -> List[str]:
    """URLs de TODOS os `<script src>` da MESMA origem (CDN de terceiros é ignorado)."""
    host, origin = _host(base_url), _origin(base_url)
    urls: List[str] = []
    for m in _SCRIPT_SRC_RE.finditer(html):
        src = m.group(1)
        if src.startswith("//"):
            src = "https:" + src
        elif src.startswith("/"):
            src = origin + src
        elif not src.startswith("http"):
            src = origin + "/" + src
        if host and host in src.split("/")[2] and src not in urls:
            urls.append(src)
    return urls


def matches_spa_fingerprint(response: httpx.Response, fingerprint: dict | None) -> bool:
    """True se o `response` casa o fingerprint do probe de controle (⇒ é fallback de SPA).

    Um SPA que serve o mesmo `index.html` para todo path produz o MESMO ETag (hash do corpo) —
    ou, sem ETag, o mesmo Content-Type + Content-Length. Comparar com o probe distingue o fallback
    (200 para tudo, inofensivo) de um `.env`/`/admin`/`/swagger` de verdade exposto.

    Cobre a lacuna do `_is_spa_fallback_nonhtml` (KL-141 P3), que só pegava extensões não-HTML
    listadas — este comparador pega paths SEM extensão (`/admin`, `/swagger`) e extensões novas."""
    if not fingerprint:
        return False
    # ETag é o comparador mais confiável (identidade do corpo servido).
    resp_etag = response.headers.get("etag")
    fp_etag = fingerprint.get("etag")
    if resp_etag and fp_etag and resp_etag == fp_etag:
        return True
    # KL-160 — fallback por Last-Modified quando o ETag é removido pelo proxy (o Cloudflare tira o
    # ETag → o KL-147 ficava sem comparador e reportava /adminer, /_profiler etc. como exposição).
    # Um SPA serve o MESMO index.html (mesmo Last-Modified) para todo path; + Content-Type HTML
    # confirma que é o fallback, não um endpoint real (que teria JSON/binário ou outro timestamp).
    resp_ct = (response.headers.get("content-type") or "").split(";")[0].strip()
    resp_lm = response.headers.get("last-modified")
    fp_lm = fingerprint.get("last_modified")
    if resp_lm and fp_lm and resp_lm == fp_lm and "text/html" in resp_ct:
        return True
    # Fallback quando o servidor não envia ETag: mesmo Content-Type + mesmo Content-Length.
    resp_cl = response.headers.get("content-length")
    fp_ct = fingerprint.get("content_type")
    fp_cl = fingerprint.get("content_length")
    if resp_ct and fp_ct and resp_ct == fp_ct and resp_cl and fp_cl and resp_cl == fp_cl:
        return True
    return False
