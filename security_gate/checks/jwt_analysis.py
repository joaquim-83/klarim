"""KL-149 — análise de JWTs presentes em cookies / Set-Cookie. Passivo: apenas DECODIFICA (base64)
o header e o payload de tokens já emitidos pelo servidor. **NUNCA forja, assina ou altera tokens.**

Verifica: algoritmo `none` (sem assinatura), ausência de `exp` (expiração) e PII no payload."""
from __future__ import annotations

import base64
import json
from typing import List, Tuple

import httpx

from ..models import Result, Severity, Status

_PII_FIELDS = ("email", "phone", "telefone", "cpf", "cnpj", "address", "endereco", "rg")


def _b64decode(segment: str) -> dict:
    """Decodifica um segmento base64url de JWT → dict. Levanta se não for JSON válido."""
    padded = segment + "=" * (-len(segment) % 4)
    return json.loads(base64.urlsafe_b64decode(padded).decode("utf-8", "replace"))


def _looks_like_jwt(value: str) -> bool:
    return value.startswith("eyJ") and value.count(".") == 2


def _extract_jwts(response: httpx.Response) -> List[Tuple[str, str]]:
    """(token, origem) de cookies e Set-Cookie. Dedup por valor."""
    found: List[Tuple[str, str]] = []
    seen: set = set()

    def _add(value: str, source: str) -> None:
        if value and _looks_like_jwt(value) and value not in seen:
            seen.add(value)
            found.append((value, source))

    try:
        for cookie in response.cookies.jar:
            _add(cookie.value or "", f"cookie:{cookie.name}")
    except Exception:  # noqa: BLE001 - jar pode variar entre versões do httpx
        pass

    headers = response.headers
    set_cookies = (headers.get_list("set-cookie") if hasattr(headers, "get_list")
                   else [v for k, v in headers.multi_items() if k.lower() == "set-cookie"])
    for sc in set_cookies:
        val = sc.split("=", 1)[1].split(";")[0].strip() if "=" in sc else ""
        _add(val, "set-cookie")
    return found


async def check_jwt(client: httpx.AsyncClient, base_url: str, config=None) -> List[Result]:
    try:
        r = await client.get(base_url)
    except httpx.HTTPError as exc:
        return [Result("jwt_error", "jwt", "/", Status.ERROR, Severity.INFO,
                       f"Não foi possível verificar JWTs: {exc!r}")]

    jwts = _extract_jwts(r)
    results: List[Result] = []
    for token, source in jwts:
        parts = token.split(".")
        if len(parts) != 3:
            continue
        try:
            header = _b64decode(parts[0])
            payload = _b64decode(parts[1])
        except Exception:  # noqa: BLE001 - não é JWT decodificável → ignora
            continue
        if str(header.get("alg", "")).lower() == "none":
            results.append(Result("jwt_alg_none", "jwt", source, Status.FAIL, Severity.CRITICAL,
                                  "JWT com algoritmo 'none' (aceito sem assinatura)"))
        if "exp" not in payload:
            results.append(Result("jwt_no_exp", "jwt", source, Status.FAIL, Severity.HIGH,
                                  "JWT sem expiração (claim 'exp' ausente)"))
        pii = [k for k in payload if k.lower() in _PII_FIELDS]
        if pii:
            results.append(Result("jwt_pii", "jwt", source, Status.FAIL, Severity.MEDIUM,
                                  f"JWT carrega PII no payload: {', '.join(sorted(pii))}"))

    if not jwts:
        results.append(Result("jwt_none_found", "jwt", "/", Status.PASS, Severity.INFO,
                              "Nenhum JWT detectado em cookies/headers"))
    elif not results:
        results.append(Result("jwt_ok", "jwt", "/", Status.PASS, Severity.HIGH,
                              "JWTs analisados — sem problemas (alg assinado, exp presente, sem PII)"))
    return results
