"""OAuth 2.1 + PKCE para o MCP do Klarim (KL-63) — o Klarim é o próprio
authorization server (single-tenant, operador único).

Implementa:
- **RFC 9728** (Protected Resource Metadata) + **RFC 8414** (Authorization Server
  Metadata) — builders servidos pela API em `/.well-known/oauth-*`.
- **RFC 7591** (Dynamic Client Registration) — `POST /mcp/register` (público, rate
  limit).
- **Authorization Code + PKCE S256** — `GET/POST /mcp/authorize` (página de login com
  a senha admin) → code → `POST /mcp/token` troca code por access/refresh JWT.
- **Refresh token com rotação**.

**Segurança:** PKCE S256 obrigatório, `redirect_uri` casada com a registrada (anti open
redirect), code one-time (60s), rate limit no register/authorize, senha admin em
constant-time, valores escapados no HTML (anti-XSS). O **token estático** (`MCP_API_KEY`)
segue válido como fallback (middleware) — este módulo não o remove.

Roda **no mesmo processo** da API (reusa o Redis já conectado em `api.main._cache` e o
`_redis_allow`). Sem Redis, o fluxo OAuth degrada para erro 503 (mas o token estático
continua funcionando).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import os
import secrets
import time
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlencode, urlparse

import jwt
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

# --- config ---------------------------------------------------------------- #

def _issuer() -> str:
    return os.environ.get("MCP_ISSUER", "https://klarim.net").rstrip("/")

def _resource() -> str:
    return f"{_issuer()}/mcp/sse"

SCOPE = "mcp:admin"
ACCESS_TTL = 3600            # 1h
REFRESH_TTL = 30 * 86400     # 30 dias
CODE_TTL = 60                # 60s
CLIENT_TTL = 30 * 86400      # 30 dias
_JWT_ALGO = "HS256"

# fallback in-memory dos rate limits (o _redis_allow usa Redis quando disponível).
_register_rl: dict = {}
_authorize_rl: dict = {}


def _jwt_secret() -> str:
    """MCP_JWT_SECRET (preferível) ou JWT_SECRET (fallback)."""
    return os.environ.get("MCP_JWT_SECRET") or os.environ.get("JWT_SECRET", "")


def _redis():
    """Reusa o Redis já conectado pela API (mesmo processo). None se indisponível."""
    try:
        from api import main as _m
        return _m._cache.redis if _m._cache is not None else None
    except Exception:  # noqa: BLE001
        return None


def _client_ip(request: Request) -> str:
    xri = request.headers.get("x-real-ip")
    if xri:
        return xri.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def _parse_form(request: Request) -> Dict[str, str]:
    """Parse `application/x-www-form-urlencoded` sem depender de python-multipart."""
    raw = await request.body()
    parsed = parse_qs(raw.decode("utf-8", "ignore"), keep_blank_values=True)
    return {k: v[0] for k, v in parsed.items() if v}


# --- metadata (RFC 9728 / 8414) -------------------------------------------- #

def protected_resource_metadata() -> Dict[str, Any]:
    return {
        "resource": _resource(),
        "authorization_servers": [_issuer()],
        "bearer_methods_supported": ["header"],
        "scopes_supported": [SCOPE],
    }


def authorization_server_metadata() -> Dict[str, Any]:
    iss = _issuer()
    return {
        "issuer": iss,
        "authorization_endpoint": f"{iss}/mcp/authorize",
        "token_endpoint": f"{iss}/mcp/token",
        "registration_endpoint": f"{iss}/mcp/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "scopes_supported": [SCOPE],
    }


def www_authenticate_header() -> str:
    return f'Bearer resource_metadata="{_issuer()}/.well-known/oauth-protected-resource"'


# --- PKCE + JWT ------------------------------------------------------------ #

def _b64url_no_pad(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def verify_pkce(code_verifier: str, code_challenge: str) -> bool:
    """PKCE S256: `code_challenge == base64url(sha256(code_verifier))` (sem padding)."""
    if not code_verifier or not code_challenge:
        return False
    expected = _b64url_no_pad(hashlib.sha256(code_verifier.encode("ascii")).digest())
    return hmac.compare_digest(expected, code_challenge)


def looks_like_jwt(token: str) -> bool:
    return token.count(".") == 2 and len(token) > 20


def mint_access_token() -> str:
    now = int(time.time())
    payload = {
        "sub": "admin", "iss": _issuer(), "aud": _resource(), "scope": SCOPE,
        "typ": "mcp_access", "iat": now, "exp": now + ACCESS_TTL,
    }
    return jwt.encode(payload, _jwt_secret(), algorithm=_JWT_ALGO)


def validate_access_token(token: str) -> bool:
    """True se for um access token OAuth válido (assinatura/iss/aud/exp/typ/scope)."""
    secret = _jwt_secret()
    if not secret:
        return False
    try:
        payload = jwt.decode(token, secret, algorithms=[_JWT_ALGO],
                             audience=_resource(), issuer=_issuer())
    except Exception:  # noqa: BLE001 - qualquer falha de validação → inválido
        return False
    return payload.get("typ") == "mcp_access" and payload.get("scope") == SCOPE


# --- redirect_uri (anti open redirect) ------------------------------------- #

def valid_redirect_uri(uri: str) -> bool:
    """Aceita só HTTPS (qualquer host) ou HTTP em loopback (localhost/127.0.0.1/::1) —
    o padrão dos clientes MCP. Rejeita javascript:/data:/http remoto."""
    if not uri or not isinstance(uri, str):
        return False
    try:
        p = urlparse(uri)
    except Exception:  # noqa: BLE001
        return False
    if p.scheme == "https":
        return bool(p.netloc)
    if p.scheme == "http":
        return (p.hostname or "") in ("localhost", "127.0.0.1", "::1")
    return False


# --- Dynamic Client Registration (RFC 7591) -------------------------------- #

async def register(request: Request) -> Response:
    from api import main as _m
    ip = _client_ip(request)
    allowed, _ = await _m._redis_allow("mcp_register", ip, 5, 3600, _register_rl)
    if not allowed:
        return JSONResponse({"error": "temporarily_unavailable",
                             "error_description": "rate limit"}, status_code=429)
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return JSONResponse({"error": "invalid_client_metadata"}, status_code=400)
    redirect_uris = body.get("redirect_uris")
    if (not isinstance(redirect_uris, list) or not redirect_uris
            or not all(valid_redirect_uri(u) for u in redirect_uris)):
        return JSONResponse({"error": "invalid_redirect_uri"}, status_code=400)
    r = _redis()
    if r is None:
        return JSONResponse({"error": "temporarily_unavailable",
                             "error_description": "registration store indisponível"},
                            status_code=503)
    client_id = secrets.token_hex(16)
    reg = {
        "client_id": client_id,
        "client_name": str(body.get("client_name") or "MCP Client")[:120],
        "redirect_uris": redirect_uris[:10],
        "grant_types": body.get("grant_types") or ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
        "created_at": int(time.time()),
    }
    await r.set(f"mcp:client:{client_id}", json.dumps(reg), ex=CLIENT_TTL)
    return JSONResponse(reg, status_code=201)


async def _get_client(client_id: str) -> Optional[Dict[str, Any]]:
    r = _redis()
    if r is None or not client_id:
        return None
    try:
        raw = await r.get(f"mcp:client:{client_id}")
    except Exception:  # noqa: BLE001
        return None
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:  # noqa: BLE001
        return None


# --- Authorization endpoint ------------------------------------------------ #

def _authz_params(src: Dict[str, str]) -> Dict[str, str]:
    return {
        "response_type": (src.get("response_type") or "").strip(),
        "client_id": (src.get("client_id") or "").strip(),
        "redirect_uri": (src.get("redirect_uri") or "").strip(),
        "code_challenge": (src.get("code_challenge") or "").strip(),
        "code_challenge_method": (src.get("code_challenge_method") or "").strip(),
        "state": (src.get("state") or "").strip(),
        "scope": (src.get("scope") or SCOPE).strip() or SCOPE,
    }


async def _validate_authz(p: Dict[str, str]) -> Optional[str]:
    """Valida os params do authorize. Retorna mensagem de erro ou None se ok."""
    if p["response_type"] != "code":
        return "response_type deve ser 'code'."
    if p["code_challenge_method"] != "S256":
        return "code_challenge_method deve ser 'S256'."
    if not p["code_challenge"]:
        return "code_challenge é obrigatório (PKCE)."
    if not p["state"]:
        return "state é obrigatório."
    client = await _get_client(p["client_id"])
    if client is None:
        return "client_id inválido ou não registrado."
    if p["redirect_uri"] not in (client.get("redirect_uris") or []):
        return "redirect_uri não corresponde ao registrado."
    return None


async def authorize(request: Request) -> Response:
    # GET → params na query; POST → params + senha no corpo do form (lido uma vez).
    form: Dict[str, str] = {} if request.method == "GET" else await _parse_form(request)
    src = dict(request.query_params) if request.method == "GET" else form
    p = _authz_params(src)
    err = await _validate_authz(p)
    if err:
        return _error_page(err)
    client = await _get_client(p["client_id"])
    client_name = (client or {}).get("client_name") or "MCP Client"

    if request.method == "GET":
        return _login_page(p, client_name)

    # POST — valida a senha do admin (single-tenant), com rate limit anti-brute-force.
    from api import main as _m
    allowed, _ = await _m._redis_allow("mcp_authorize", _client_ip(request), 5, 60,
                                       _authorize_rl)
    if not allowed:
        return _login_page(p, client_name,
                           error="Muitas tentativas. Aguarde um minuto.", status=429)
    admin_pw = os.environ.get("ADMIN_PASSWORD", "")
    password = form.get("password", "")
    if not admin_pw or not hmac.compare_digest(password, admin_pw):
        return _login_page(p, client_name, error="Senha incorreta.", status=401)

    # OK → gera authorization code (one-time, 60s).
    r = _redis()
    if r is None:
        return _error_page("Serviço de autorização indisponível.", status=503)
    code = secrets.token_hex(32)
    await r.set(f"mcp:auth_code:{code}", json.dumps({
        "client_id": p["client_id"], "redirect_uri": p["redirect_uri"],
        "code_challenge": p["code_challenge"], "scope": p["scope"],
        "created_at": int(time.time())}), ex=CODE_TTL)
    sep = "&" if "?" in p["redirect_uri"] else "?"
    location = f"{p['redirect_uri']}{sep}{urlencode({'code': code, 'state': p['state']})}"
    return RedirectResponse(location, status_code=302)


# --- Token endpoint -------------------------------------------------------- #

async def token(request: Request) -> Response:
    form = await _parse_form(request)
    grant = form.get("grant_type", "")
    if grant == "authorization_code":
        return await _token_auth_code(form)
    if grant == "refresh_token":
        return await _token_refresh(form)
    return _token_error("unsupported_grant_type")


def _token_error(code: str, desc: str = "", status: int = 400) -> Response:
    body = {"error": code}
    if desc:
        body["error_description"] = desc
    return JSONResponse(body, status_code=status, headers={"Cache-Control": "no-store"})


def _token_ok(access: str, refresh: str) -> Response:
    return JSONResponse({
        "access_token": access, "token_type": "Bearer", "expires_in": ACCESS_TTL,
        "refresh_token": refresh, "scope": SCOPE,
    }, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})


async def _token_auth_code(form: Dict[str, str]) -> Response:
    code = form.get("code", "")
    client_id = form.get("client_id", "")
    redirect_uri = form.get("redirect_uri", "")
    code_verifier = form.get("code_verifier", "")
    if not (code and client_id and redirect_uri and code_verifier):
        return _token_error("invalid_request", "parâmetros faltando")
    r = _redis()
    if r is None:
        return _token_error("temporarily_unavailable", "store indisponível", 503)
    key = f"mcp:auth_code:{code}"
    raw = await r.get(key)
    await r.delete(key)  # one-time use: consome sempre (mesmo se inválido)
    if not raw:
        return _token_error("invalid_grant", "code inválido ou expirado")
    saved = json.loads(raw)
    if not hmac.compare_digest(saved.get("client_id", ""), client_id):
        return _token_error("invalid_grant", "client_id não confere")
    if saved.get("redirect_uri") != redirect_uri:
        return _token_error("invalid_grant", "redirect_uri não confere")
    if not verify_pkce(code_verifier, saved.get("code_challenge", "")):
        return _token_error("invalid_grant", "PKCE falhou")
    refresh = await _issue_refresh(client_id)
    return _token_ok(mint_access_token(), refresh)


async def _token_refresh(form: Dict[str, str]) -> Response:
    refresh = form.get("refresh_token", "")
    client_id = form.get("client_id", "")
    if not (refresh and client_id):
        return _token_error("invalid_request", "parâmetros faltando")
    r = _redis()
    if r is None:
        return _token_error("temporarily_unavailable", "store indisponível", 503)
    key = f"mcp:refresh:{refresh}"
    raw = await r.get(key)
    if not raw:
        return _token_error("invalid_grant", "refresh token inválido ou expirado")
    saved = json.loads(raw)
    if not hmac.compare_digest(saved.get("client_id", ""), client_id):
        return _token_error("invalid_grant", "client_id não confere")
    await r.delete(key)  # rotação: invalida o refresh antigo
    new_refresh = await _issue_refresh(client_id)
    return _token_ok(mint_access_token(), new_refresh)


async def _issue_refresh(client_id: str) -> str:
    r = _redis()
    tok = secrets.token_hex(64)
    if r is not None:
        await r.set(f"mcp:refresh:{tok}", json.dumps({
            "client_id": client_id, "scope": SCOPE, "created_at": int(time.time())}),
            ex=REFRESH_TTL)
    return tok


# --- páginas HTML ---------------------------------------------------------- #

def _shell(title: str, inner: str, status: int = 200) -> HTMLResponse:
    page = f"""<!DOCTYPE html><html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="robots" content="noindex,nofollow"><title>{html.escape(title)}</title></head>
<body style="margin:0;background:#0D1117;color:#E6EDF3;font-family:Arial,Helvetica,sans-serif;">
<div style="max-width:420px;margin:8vh auto;padding:28px;background:#161B22;border:1px solid #30363D;border-radius:12px;">
<div style="font-size:24px;font-weight:800;letter-spacing:3px;margin-bottom:6px;">KLA<span style="color:#FF6B35;">R</span>IM</div>
{inner}
</div></body></html>"""
    return HTMLResponse(page, status_code=status)


def _login_page(p: Dict[str, str], client_name: str, error: str = "",
                status: int = 200) -> HTMLResponse:
    hidden = "".join(
        f'<input type="hidden" name="{html.escape(k)}" value="{html.escape(p[k])}">'
        for k in ("response_type", "client_id", "redirect_uri", "code_challenge",
                  "code_challenge_method", "state", "scope"))
    err_html = (f'<div style="color:#F85149;font-size:13px;margin:8px 0;">{html.escape(error)}</div>'
                if error else "")
    inner = f"""
<div style="color:#8B949E;font-size:13px;margin-bottom:18px;">Autorizar acesso ao painel de administração</div>
<div style="font-size:14px;margin-bottom:14px;">O aplicativo <strong>{html.escape(client_name)}</strong> quer acessar o MCP do Klarim (escopo <code>mcp:admin</code>).</div>
{err_html}
<form method="POST" action="/mcp/authorize">
{hidden}
<label style="display:block;font-size:12px;color:#8B949E;margin-bottom:6px;">Senha do administrador</label>
<input type="password" name="password" autocomplete="current-password" autofocus required
 style="width:100%;box-sizing:border-box;padding:12px;background:#0D1117;border:1px solid #30363D;border-radius:8px;color:#E6EDF3;font-size:15px;">
<button type="submit" style="width:100%;margin-top:16px;padding:13px;background:#FF6B35;color:#0D1117;border:0;border-radius:8px;font-size:15px;font-weight:bold;cursor:pointer;">Autorizar</button>
</form>
<div style="color:#484F58;font-size:11px;margin-top:16px;">Você está autorizando um cliente a operar o Klarim em seu nome. Só continue se reconhece este aplicativo.</div>
"""
    return _shell("Autorizar — Klarim MCP", inner, status=status)


def _error_page(message: str, status: int = 400) -> HTMLResponse:
    inner = (f'<div style="color:#F85149;font-size:15px;margin-top:12px;">{html.escape(message)}</div>'
             '<div style="color:#8B949E;font-size:12px;margin-top:12px;">Erro de autorização OAuth.</div>')
    return _shell("Erro — Klarim MCP", inner, status=status)
