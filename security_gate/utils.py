"""KL-147 — utilidades compartilhadas do Security Gate.

`matches_spa_fingerprint` é usado por `checks/exposure.py` E `checks/api_security.py` para
distinguir um **fallback de SPA** (app que devolve o mesmo `index.html` — 200 — para QUALQUER
path) de um arquivo/endpoint REALMENTE exposto. O engine captura o fingerprint num "probe de
controle" (HEAD num path que não existe, KL-147) e o passa aos checks."""
from __future__ import annotations

import httpx


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
    # Fallback quando o servidor não envia ETag: mesmo Content-Type + mesmo Content-Length.
    resp_ct = (response.headers.get("content-type") or "").split(";")[0].strip()
    resp_cl = response.headers.get("content-length")
    fp_ct = fingerprint.get("content_type")
    fp_cl = fingerprint.get("content_length")
    if resp_ct and fp_ct and resp_ct == fp_ct and resp_cl and fp_cl and resp_cl == fp_cl:
        return True
    return False
