"""Klarim API (FastAPI).

Superfície: semáforo gratuito (`/scan/summary`), relatórios PDF (protegidos por
pagamento) e o fluxo de pagamento PIX via AbacatePay (`/payment/*`, webhook).

Run local:

    uvicorn api.main:app --reload --port 8000
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr
from typing import Any, Optional
from urllib.parse import urlparse, quote

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response, HTMLResponse, RedirectResponse
from pydantic import BaseModel

from scanner import (run_scan, summarize_fails, Severity, ScanReport,
                     ALL_CHECKS, FREE_CHECKS, CHECK_META, FREE_CHECK_MAX_ORDER)
from scanner import __version__ as scanner_version
from scanner.cache import ScanCache
from scanner.checks.base import normalize_url, registrable_domain, domain_of
from scanner.checks.classifications import classify as classify_compliance
from discovery.store import get_target_store
from reporter import generate_executive_pdf, generate_technical_pdf, pdf_filename
from reporter.risk_messages import get_risk_messages, get_risk_summary
from payments import (
    AbacatePayClient,
    AbacatePayError,
    verify_webhook_signature,
    Charge,
    PaymentStatus,
    PRICE_AMOUNT,
    PRICE_DISPLAY,
    PRICING,
    DEFAULT_TIER,
    amount_display,
    mask_email,
    get_store,
    init_store,
)
from notifier import (KlarimMailer, KlarimMailerError, EMAIL_TYPES, unsubscribe_token,
                      verify_resend_signature)
from discovery.alert_worker import send_alert_for_target
from discovery.rescan_worker import rescan_target
from discovery import worker_control
from discovery.ingest import ingest_scan, _fetch_html
from discovery.classifier import classify_sector, classify_by_domain, PRICE_TIERS
from api import health_checks
from api import auth_users
from api import plans


# --------------------------------------------------------------------------- #
# Config (lida do ambiente a cada chamada — facilita testes e overrides)
# --------------------------------------------------------------------------- #

def _api_key() -> str:
    return os.environ.get("ABACATEPAY_API_KEY", "")


def _webhook_secret() -> str:
    return os.environ.get("ABACATEPAY_WEBHOOK_SECRET", "")


def _dev_mode() -> bool:
    return os.environ.get("KLARIM_DEV_MODE", "").lower() == "true"


def _payments_enabled() -> bool:
    return bool(_api_key())


def _free_access() -> bool:
    """PDFs livres quando em modo dev OU quando o pagamento não está configurado.

    (Sem chave da AbacatePay não há como vender — então não faz sentido bloquear;
    o site continua funcional. Basta configurar ABACATEPAY_API_KEY para ativar a
    cobrança.)
    """
    return _dev_mode() or not _payments_enabled()


def _paywall_enabled() -> bool:
    """Gate do resultado web (KL-51 f2). **Default `false`** (pivot freemium): todo
    scan autorizado (e-mail verificado) vê os **48 checks** com detalhe, e não há
    limite de 1 scan/e-mail. Com `PAYWALL_ENABLED=true` volta o gate do KL-27 (15
    grátis + 33 bloqueados, 1 scan/e-mail). O PDF é sempre gratuito."""
    return os.environ.get("PAYWALL_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on")


def _resend_key() -> str:
    return os.environ.get("RESEND_API_KEY", "")


def _email_enabled() -> bool:
    return bool(_resend_key())


def _mailer() -> KlarimMailer:
    return KlarimMailer(_resend_key(), os.environ.get("RESEND_FROM") or None)


# --- Auth do dashboard admin (KL-14) --------------------------------------- #

JWT_ALGO = "HS256"
JWT_TTL_SECONDS = 86400  # 24h

# Prefixos protegidos — exigem Bearer token. O resto é público (scan/summary,
# payment, report, webhooks, recovery, unsubscribe, health, auth/login).
_PROTECTED_PREFIXES = ("/targets", "/scans", "/alerts", "/rescans", "/email", "/payments",
                       "/config", "/discovery", "/admin", "/system", "/analytics",
                       "/leads",  # KL-61: gestão de leads (admin JWT)
                       "/monitoring/admin")  # KL-29: só o /monitoring/admin/* é protegido


def _jwt_secret() -> str:
    return os.environ.get("JWT_SECRET", "")


def _auth_configured() -> bool:
    return bool(os.environ.get("ADMIN_USER") and os.environ.get("ADMIN_PASSWORD") and _jwt_secret())


def _create_token(username: str) -> str:
    import jwt

    now = datetime.now(timezone.utc)
    payload = {"sub": username, "typ": "admin",
               "iat": now, "exp": now + timedelta(seconds=JWT_TTL_SECONDS)}
    return jwt.encode(payload, _jwt_secret(), algorithm=JWT_ALGO)


def _verify_token(token: str) -> dict:
    """Decodifica/valida o JWT do operador (levanta em token inválido/expirado ou se
    o `typ` não é `admin` — impede que um cookie de usuário, assinado com o mesmo
    segredo, seja aceito como admin, KL-51 f3)."""
    import jwt

    payload = jwt.decode(token, _jwt_secret(), algorithms=[JWT_ALGO])
    if payload.get("typ") != "admin":
        raise ValueError("token não é de admin")
    return payload


# Exceções públicas dentro de um prefixo protegido: o webhook da Hostinger (KL-56)
# cai sob `/email` (admin) mas tem auth PRÓPRIA (token da Hostinger), então é público.
_PUBLIC_UNDER_PROTECTED = ("/email/webhook",)


def _is_protected(path: str) -> bool:
    if path in _PUBLIC_UNDER_PROTECTED:
        return False
    return any(path == p or path.startswith(p + "/") for p in _PROTECTED_PREFIXES)


# Cache de scan (Redis). None => sem cache (scans rodam sempre).
_cache: Optional[ScanCache] = None


async def _init_cache() -> None:
    global _cache
    redis_url = os.environ.get("REDIS_URL")
    if not redis_url:
        return
    try:
        import redis.asyncio as aioredis

        client = aioredis.from_url(redis_url, decode_responses=True)
        await client.ping()
        _cache = ScanCache(client)
        print("[cache] Redis conectado — scans cacheados (TTL 1h)", flush=True)
    except Exception as exc:  # noqa: BLE001 - sem cache, mas a API sobe normalmente
        print(f"[cache] Redis indisponível ({exc!r}); sem cache de scan", flush=True)
        _cache = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_store()
    await _init_cache()
    try:
        await get_target_store().ensure_schema()
    except Exception as exc:  # noqa: BLE001 - targets/scans opcionais; API sobe mesmo assim
        print(f"[targets] schema indisponível ({exc!r})", flush=True)
    yield


# Fix de segurança: em produção NÃO expõe Swagger/OpenAPI (mapeariam toda a API
# sem autenticação). Só liga em dev (KLARIM_DEV_MODE=true).
_docs_on = _dev_mode()
app = FastAPI(
    title="Klarim API",
    version="0.1.0",
    description="O alarme que toca antes do ataque — scanner passivo de segurança web.",
    lifespan=lifespan,
    docs_url="/docs" if _docs_on else None,
    redoc_url="/redoc" if _docs_on else None,
    openapi_url="/openapi.json" if _docs_on else None,
)


@app.middleware("http")
async def _admin_auth_mw(request: Request, call_next):
    """Protege as rotas de gestão (KL-14). Rotas públicas passam direto."""
    if request.method != "OPTIONS" and _is_protected(request.url.path):
        auth = request.headers.get("authorization", "")
        token = auth[7:].strip() if auth[:7].lower() == "bearer " else ""
        try:
            if not token:
                raise ValueError("token ausente")
            _verify_token(token)
        except Exception:  # noqa: BLE001 - qualquer falha => 401
            return JSONResponse({"detail": "Não autorizado."}, status_code=401)
    return await call_next(request)


class LoginBody(BaseModel):
    username: str
    password: str


# Rate limit do login por IP (anti brute-force). In-memory basta para o MVP
# single-process; se escalar para múltiplos workers, mover para Redis.
_LOGIN_RL_MAX = 5            # tentativas por janela
_LOGIN_RL_WINDOW = 60        # segundos
_login_attempts: dict = {}   # ip -> [timestamps monotônicos]


def _client_ip(request: Request) -> str:
    """IP real do cliente — o Nginx envia X-Real-IP (senão, o peer da conexão)."""
    xri = request.headers.get("x-real-ip")
    if xri:
        return xri.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def _login_rate_limit(request: Request) -> None:
    # KL-44 auditoria F-02: rate limit distribuído (Redis) com fallback in-memory.
    allowed, retry = await _redis_allow(
        "admin_login", _client_ip(request), _LOGIN_RL_MAX, _LOGIN_RL_WINDOW, _login_attempts)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Muitas tentativas. Tente novamente em {retry}s.",
            headers={"Retry-After": str(retry)},
        )


@app.post("/auth/login", dependencies=[Depends(_login_rate_limit)])
async def auth_login(body: LoginBody) -> dict:
    """Login único do operador (credenciais do .env). Retorna um JWT de 24h."""
    if not _auth_configured():
        raise HTTPException(status_code=503, detail="Autenticação não configurada.")
    admin_user = os.environ.get("ADMIN_USER", "")
    admin_pw = os.environ.get("ADMIN_PASSWORD", "")
    ok = (hmac.compare_digest(body.username, admin_user)
          and hmac.compare_digest(body.password, admin_pw))
    if not ok:
        raise HTTPException(status_code=401, detail="Credenciais inválidas.")
    return {"token": _create_token(body.username), "expires_in": JWT_TTL_SECONDS}


# --------------------------------------------------------------------------- #
# Contas de usuário (KL-51 f3) — signup/login/logout/forgot/reset/me + sites.
# Namespace /account/* (o /auth/login já é o operador/admin). JWT no cookie.
# --------------------------------------------------------------------------- #

_ACCOUNT_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")
_PW_MIN = 8
_PWRESET_TTL = 3600          # 1h
_RESET_CODE_TTL = 3600
# Buckets de fallback in-memory do `_redis_allow` (KL-44 auditoria F-02): o rate limit
# é distribuído via Redis; estes dicts só entram em ação se o Redis estiver indisponível.
_signup_attempts: dict = {}
_forgot_attempts: dict = {}
_reset_attempts: dict = {}
_send_report_attempts: dict = {}


def _mask_email(email: str) -> str:
    """`joao@empresa.com.br` → `j***o@empresa.com.br` (feedback sem vazar o e-mail)."""
    email = (email or "").strip()
    if "@" not in email:
        return email
    local, _, domain = email.partition("@")
    if len(local) <= 2:
        masked = (local[:1] or "*") + "***"
    else:
        masked = local[0] + "***" + local[-1]
    return f"{masked}@{domain}"


def _ip_rate_limit(bucket: dict, key: str, max_hits: int, window: int) -> bool:
    """True se DENTRO do limite (permite); False se excedeu. Janela deslizante.
    Usado como **fallback in-memory** do `_redis_allow` (KL-44 auditoria F-02) e
    diretamente por reset/send_report/eventos."""
    now = time.monotonic()
    q = bucket.setdefault(key, [])
    cutoff = now - window
    while q and q[0] < cutoff:
        q.pop(0)
    if len(q) >= max_hits:
        return False
    q.append(now)
    if len(bucket) > 5000:  # limpeza oportunista
        for k in [k for k, ts in bucket.items() if not ts or ts[-1] < cutoff]:
            bucket.pop(k, None)
    return True


async def _redis_allow(namespace: str, key: str, max_hits: int, window: int,
                       fallback_bucket: dict) -> tuple[bool, int]:
    """Rate limit **distribuído** via Redis (KL-44 auditoria F-02): fixed-window com
    INCR+EXPIRE — compartilhado entre workers e sobrevive a restart/deploy. Retorna
    ``(permitido, retry_after_segundos)``. **Fail-safe:** se o Redis estiver
    indisponível/instável, degrada para o limitador in-memory (`_ip_rate_limit`) — nunca
    desliga o rate limit por falha de infra. Os limites/keys são os já ajustados no
    hardening (só muda o mecanismo de armazenamento)."""
    ident = f"{namespace}:{key}"
    redis = _cache.redis if _cache is not None else None
    if redis is not None:
        try:
            rkey = f"rate:{ident}"
            n = await redis.incr(rkey)
            if n == 1:
                await redis.expire(rkey, window)
            if n > max_hits:
                ttl = await redis.ttl(rkey)
                return False, (ttl if ttl and ttl > 0 else window)
            return True, 0
        except Exception:  # noqa: BLE001 - Redis instável → cai no fallback in-memory
            pass
    allowed = _ip_rate_limit(fallback_bucket, ident, max_hits, window)
    return allowed, (0 if allowed else window)


def _user_public(user: dict) -> dict:
    """Campos seguros do usuário para devolver ao frontend (sem hash)."""
    created = user.get("created_at")
    return {
        "id": user["id"], "email": user["email"], "name": user.get("name"),
        "plan": user.get("plan", "free"), "max_sites": user.get("max_sites", 1),
        "created_at": created.isoformat() if hasattr(created, "isoformat") else created,
    }


def _set_session_cookie(resp: JSONResponse, token: str) -> None:
    resp.set_cookie(
        key=auth_users.USER_COOKIE, value=token, max_age=auth_users.USER_JWT_TTL,
        httponly=True, secure=True, samesite="lax", path="/")


class SignupBody(BaseModel):
    email: str
    password: str
    name: Optional[str] = None
    url: Optional[str] = None   # site recém-escaneado, para vincular no signup


class AccountLoginBody(BaseModel):
    email: str
    password: str


class ForgotBody(BaseModel):
    email: str


class ResetBody(BaseModel):
    email: str
    code: str
    new_password: str


class SiteBody(BaseModel):
    url: str


class UpdateAccountBody(BaseModel):
    name: Optional[str] = None


class ChangePasswordBody(BaseModel):
    current_password: str
    new_password: str


class DeleteAccountBody(BaseModel):
    password: str


async def _resolve_or_create_target(url: str, source: str = "manual") -> Optional[int]:
    """target_id de uma URL: reusa o existente ou registra + enfileira scan."""
    store = get_target_store()
    norm = _norm_scan_url(url)
    existing = await store.get_target_by_url(norm)
    if existing:
        return existing["id"]
    # registra um alvo mínimo e enfileira scan (o worker preenche o resto)
    from urllib.parse import urlparse
    domain = urlparse(norm if "://" in norm else f"https://{norm}").hostname or norm
    try:
        tid = await store.register_target(
            url=norm, domain=domain, platform="", sector="outro",
            price_tier="standard", contact_email=None, source=source, status="discovered")
        await _enqueue_scan(tid, norm, source=source)
        return tid
    except Exception as exc:  # noqa: BLE001
        print(f"[account] resolve target falhou {url}: {exc!r}", flush=True)
        return None


# --- verificação de e-mail no signup direcionado (KL-44 F-03b) --------------- #
# Pending signups (código de 6 dígitos) no Redis (TTL 15min) com fallback in-memory —
# mesmo padrão do rate limit: Redis quando disponível, dict local se o Redis cair.
_pending_signups: dict = {}
_SIGNUP_PENDING_TTL = 900  # 15 min


async def _store_pending_signup(email: str, data: dict) -> None:
    redis = _cache.redis if _cache is not None else None
    if redis is not None:
        try:
            await redis.set(f"signup:pending:{email}", json.dumps(data), ex=_SIGNUP_PENDING_TTL)
            return
        except Exception:  # noqa: BLE001
            pass
    _pending_signups[email] = (data, time.monotonic() + _SIGNUP_PENDING_TTL)


async def _get_pending_signup(email: str) -> Optional[dict]:
    redis = _cache.redis if _cache is not None else None
    if redis is not None:
        try:
            raw = await redis.get(f"signup:pending:{email}")
            return json.loads(raw) if raw else None
        except Exception:  # noqa: BLE001
            pass
    entry = _pending_signups.get(email)
    if not entry:
        return None
    data, exp = entry
    if time.monotonic() > exp:
        _pending_signups.pop(email, None)
        return None
    return data


async def _del_pending_signup(email: str) -> None:
    redis = _cache.redis if _cache is not None else None
    if redis is not None:
        try:
            await redis.delete(f"signup:pending:{email}")
        except Exception:  # noqa: BLE001
            pass
    _pending_signups.pop(email, None)


async def _create_account_record(store, email: str, password_hash: str,
                                 name: Optional[str], url: Optional[str]) -> Optional[dict]:
    """Cria a conta + vincula sites + Pro trial + lead. Retorna o user (ou None se e-mail
    duplicado). Compartilhado pelo signup (e-mail já verificado) e pelo /account/verify."""
    user = await store.create_user(email, password_hash, name=name)
    if user is None:
        return None
    max_sites = int(user.get("max_sites", 1))
    if url:
        tid = await _resolve_or_create_target(url, source="signup")
        if tid:
            await store.link_user_site(user["id"], tid, is_owner=await _email_owns_target(email, tid))
    try:  # histórico: vincula scans anteriores do mesmo e-mail (KL-25) até o limite do plano
        used = await store.count_user_sites(user["id"])
        if used < max_sites:
            for tid in await store.get_targets_scanned_by_email(email, limit=max_sites):
                if used >= max_sites:
                    break
                if await store.link_user_site(user["id"], tid,
                                              is_owner=await _email_owns_target(email, tid)):
                    used += 1
    except Exception as exc:  # noqa: BLE001 - histórico é best-effort
        print(f"[account] vínculo de histórico falhou {email}: {exc!r}", flush=True)
    await store.touch_user_login(user["id"])
    _spawn(_safe_lead(store.set_lead_account(email, user["id"])))          # KL-61
    _spawn(_safe_lead(plans.create_subscription(user["id"], "pro", is_trial=True)))  # KL-44
    return user


def _account_session_response(user: dict) -> JSONResponse:
    resp = JSONResponse({"user": _user_public(user)})
    _set_session_cookie(resp, auth_users.create_user_token(user))
    return resp


@app.post("/account/signup")
async def account_signup(body: SignupBody, request: Request) -> JSONResponse:
    """Cria conta. **KL-44 F-03b:** se o e-mail JÁ foi verificado no scan (KL-25), cria
    direto (sem re-verificar — preserva o fluxo scan→cadastro). Se NÃO foi verificado
    (signup direto via API ou claim de alerta), exige um código de 6 dígitos por e-mail —
    fecha o gap de criar conta com e-mail de terceiro. Rate limit 5/IP/h (Redis, KL-44 F-02)."""
    allowed, retry = await _redis_allow("signup", _client_ip(request), 5, 3600, _signup_attempts)
    if not allowed:
        raise HTTPException(status_code=429, detail="Muitas contas criadas. Tente mais tarde.",
                            headers={"Retry-After": str(retry)})
    email = (body.email or "").lower().strip()
    if not _ACCOUNT_EMAIL_RE.match(email):
        raise HTTPException(status_code=400, detail="E-mail inválido.")
    if len(body.password or "") < _PW_MIN:
        raise HTTPException(status_code=400, detail="A senha precisa ter ao menos 8 caracteres.")
    store = get_target_store()
    if await store.get_user_by_email(email):
        raise HTTPException(status_code=409, detail="Já existe uma conta com este e-mail.")
    pw_hash = auth_users.hash_password(body.password)
    # E-mail já verificado no scan (KL-25) → cria direto.
    try:
        already_verified = await store.email_has_verified_scan(email)
    except Exception:  # noqa: BLE001 - na dúvida, exige verificação
        already_verified = False
    if already_verified:
        user = await _create_account_record(store, email, pw_hash, body.name or None, body.url)
        if user is None:
            raise HTTPException(status_code=409, detail="Já existe uma conta com este e-mail.")
        return _account_session_response(user)
    # E-mail NÃO verificado → exige código (não cria a conta ainda).
    if not _email_enabled():
        raise HTTPException(status_code=503, detail="Verificação de e-mail indisponível no momento.")
    code = f"{secrets.randbelow(900000) + 100000:06d}"  # CSPRNG, 6 dígitos
    await _store_pending_signup(email, {
        "code": code, "password_hash": pw_hash, "name": body.name or None,
        "url": body.url or None, "attempts": 0})
    try:
        await _mailer().send_signup_verification_code(email, code)
    except Exception as exc:  # noqa: BLE001
        print(f"[signup] falha ao enviar código {email}: {exc!r}", flush=True)
        raise HTTPException(status_code=502, detail="Falha ao enviar o código de verificação.") from exc
    return JSONResponse({"status": "verification_sent",
                         "message": "Código de verificação enviado para seu e-mail.",
                         "email": _mask_email(email)})


class VerifySignupBody(BaseModel):
    email: str
    code: str


@app.post("/account/verify")
async def account_verify(body: VerifySignupBody, request: Request) -> JSONResponse:
    """Confirma o código de verificação do signup e cria a conta (KL-44 F-03b). Máximo 3
    tentativas por código; expira em 15 min."""
    allowed, _ = await _redis_allow("signup_verify", _client_ip(request), 10, 600, _reset_attempts)
    if not allowed:
        raise HTTPException(status_code=429, detail="Muitas tentativas. Aguarde alguns minutos.")
    email = (body.email or "").lower().strip()
    pending = await _get_pending_signup(email)
    if not pending:
        raise HTTPException(status_code=400,
                            detail="Código expirado ou inexistente. Refaça o cadastro.")
    if int(pending.get("attempts", 0)) >= 3:
        await _del_pending_signup(email)
        raise HTTPException(status_code=429, detail="Muitas tentativas. Refaça o cadastro.")
    if not hmac.compare_digest(str(body.code or "").strip(), str(pending.get("code"))):
        pending["attempts"] = int(pending.get("attempts", 0)) + 1
        await _store_pending_signup(email, pending)
        raise HTTPException(status_code=400, detail="Código incorreto.")
    store = get_target_store()
    if await store.get_user_by_email(email):
        await _del_pending_signup(email)
        raise HTTPException(status_code=409, detail="Já existe uma conta com este e-mail.")
    user = await _create_account_record(
        store, email, pending["password_hash"], pending.get("name"), pending.get("url"))
    await _del_pending_signup(email)
    if user is None:
        raise HTTPException(status_code=409, detail="Já existe uma conta com este e-mail.")
    return _account_session_response(user)


@app.post("/account/login")
async def account_login(body: AccountLoginBody, request: Request) -> JSONResponse:
    """Login de conta de usuário. Rate limit 10/IP/min (anti brute-force)."""
    allowed, retry = await _redis_allow("user_login", _client_ip(request), 10, 60, _signup_attempts)
    if not allowed:
        raise HTTPException(status_code=429, detail="Muitas tentativas. Aguarde um momento.",
                            headers={"Retry-After": str(retry)})
    email = (body.email or "").lower().strip()
    store = get_target_store()
    user = await store.get_user_by_email(email, with_hash=True)
    if not user or not auth_users.verify_password(body.password, user.get("password_hash", "")):
        raise HTTPException(status_code=401, detail="E-mail ou senha incorretos.")
    if not user.get("is_active", True):
        raise HTTPException(status_code=403, detail="Conta desativada.")
    await store.touch_user_login(user["id"])
    token = auth_users.create_user_token(user)
    resp = JSONResponse({"user": _user_public(user)})
    _set_session_cookie(resp, token)
    return resp


@app.post("/account/logout")
async def account_logout() -> JSONResponse:
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(key=auth_users.USER_COOKIE, path="/")
    return resp


@app.post("/account/forgot")
async def account_forgot(body: ForgotBody, request: Request) -> dict:
    """Envia um código de 6 dígitos para redefinir a senha. Resposta SEMPRE genérica
    (não revela se o e-mail existe). Rate limit 3/e-mail/h. Roda em background."""
    email = (body.email or "").lower().strip()
    generic = {"ok": True, "message": "Se houver uma conta com este e-mail, enviamos um código."}
    if not _ACCOUNT_EMAIL_RE.match(email):
        return generic
    allowed, _ = await _redis_allow("forgot", email, 3, 3600, _forgot_attempts)
    if not allowed:
        return generic  # silencioso — não vaza que passou do limite

    async def _do():
        store = get_target_store()
        user = await store.get_user_by_email(email)
        if not user:
            return  # não existe → nada (resposta já foi genérica)
        code = f"{secrets.randbelow(1_000_000):06d}"
        await store.create_password_reset(email, code, _RESET_CODE_TTL)
        if _email_enabled():
            try:
                await _mailer().send_password_reset_code(email, code)
            except KlarimMailerError as exc:
                print(f"[account] reset e-mail falhou {email}: {exc!r}", flush=True)

    _spawn(_do())
    return generic


@app.post("/account/reset")
async def account_reset(body: ResetBody, request: Request) -> dict:
    """Valida o código e define a nova senha. Rate limit 5/e-mail/10min."""
    email = (body.email or "").lower().strip()
    if not _ip_rate_limit(_reset_attempts, email, 5, 600):
        raise HTTPException(status_code=429, detail="Muitas tentativas. Aguarde alguns minutos.")
    if len(body.new_password or "") < _PW_MIN:
        raise HTTPException(status_code=400, detail="A senha precisa ter ao menos 8 caracteres.")
    store = get_target_store()
    if not await store.verify_password_reset(email, (body.code or "").strip()):
        raise HTTPException(status_code=400, detail="Código inválido ou expirado.")
    ok = await store.set_user_password(email, auth_users.hash_password(body.new_password))
    if not ok:
        raise HTTPException(status_code=400, detail="Não foi possível redefinir a senha.")
    return {"ok": True}


@app.get("/account/me")
async def account_me(request: Request) -> dict:
    user = await auth_users.require_user(request)
    store = get_target_store()
    return {"user": _user_public(user),
            "sites_count": await store.count_user_sites(user["id"])}


@app.put("/account/me")
async def account_update(body: UpdateAccountBody, request: Request) -> dict:
    """Atualiza dados editáveis da conta (KL-57) — hoje só o nome. O e-mail é a
    identidade da conta e não muda por aqui."""
    user = await auth_users.require_user(request)
    name = _sanitize_str((body.name or ""), 120).strip() or None
    await get_target_store().update_user_name(user["id"], name)
    updated = {**user, "name": name}
    return {"ok": True, "user": _user_public(updated)}


@app.post("/account/change-password")
async def account_change_password(body: ChangePasswordBody, request: Request) -> dict:
    """Altera a senha da conta (KL-57): confere a atual, exige a nova ≥ 8 chars. NÃO
    invalida a sessão (o JWT atual continua válido). Rate limit 5/e-mail/10min."""
    user = await auth_users.require_user(request)
    email = (user.get("email") or "").lower().strip()
    if not _ip_rate_limit(_reset_attempts, "change:" + email, 5, 600):
        raise HTTPException(status_code=429, detail="Muitas tentativas. Aguarde alguns minutos.")
    if len(body.new_password or "") < _PW_MIN:
        raise HTTPException(status_code=400, detail="A nova senha precisa ter ao menos 8 caracteres.")
    store = get_target_store()
    full = await store.get_user_by_email(email, with_hash=True)
    if not full or not auth_users.verify_password(body.current_password or "",
                                                  full.get("password_hash", "")):
        raise HTTPException(status_code=401, detail="Senha atual incorreta.")
    await store.set_user_password(email, auth_users.hash_password(body.new_password))
    return {"ok": True}


@app.delete("/account/me")
async def account_delete(body: DeleteAccountBody, request: Request) -> JSONResponse:
    """Exclui a conta (KL-57): confirma por senha, remove o usuário (CASCADE apaga os
    vínculos em `user_sites`) e limpa o cookie de sessão. Os `targets`/`scans`/
    `site_profile` são dados do sistema e **permanecem** (o perfil público segue no ar).
    Envia um e-mail de confirmação em background."""
    user = await auth_users.require_user(request)
    email = (user.get("email") or "").lower().strip()
    store = get_target_store()
    full = await store.get_user_by_email(email, with_hash=True)
    if not full or not auth_users.verify_password(body.password or "",
                                                  full.get("password_hash", "")):
        raise HTTPException(status_code=401, detail="Senha incorreta.")
    await store.delete_user(user["id"])
    if _email_enabled():
        async def _confirm():
            try:
                await _mailer().send_account_deleted(email)
            except Exception as exc:  # noqa: BLE001 - e-mail é best-effort
                print(f"[account] e-mail de exclusão falhou {email}: {exc!r}", flush=True)
        _spawn(_confirm())
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(key=auth_users.USER_COOKIE, path="/")
    return resp


def _semaphore_from_score(score: Optional[int]) -> str:
    """Fallback de semáforo por score (para scans antigos sem a coluna)."""
    if score is None:
        return "amarelo"
    if score >= 90:
        return "verde"
    if score >= 50:
        return "amarelo"
    return "vermelho"


@app.get("/account/scan-history")
async def account_scan_history(request: Request) -> dict:
    """Histórico de consultas do usuário (KL-51 f3 fix): scans que ele solicitou
    (via `scans.scanned_by_email`, KL-25), 1 por URL, mais recente primeiro. Só leitura
    — não conta como site monitorado."""
    user = await auth_users.require_user(request)
    rows = await get_target_store().get_scan_history_for_email(user["email"], limit=20)
    return {"scans": [
        {"id": r["id"], "url": r["url"], "score": r["score"],
         "semaphore": r.get("semaphore") or _semaphore_from_score(r.get("score")),
         "scanned_at": r["scanned_at"].isoformat() if r.get("scanned_at") else None}
        for r in rows]}


# --- sites do usuário ------------------------------------------------------- #

async def _email_owns_target(email: str, target_id: int) -> bool:
    """Propriedade por e-mail: o e-mail da conta bate com o contact_email do alvo."""
    try:
        t = await get_target_store().get_target(target_id)
    except Exception:  # noqa: BLE001
        return False
    ce = (t or {}).get("contact_email") or ""
    return bool(ce) and ce.lower().strip() == (email or "").lower().strip()


@app.get("/account/sites")
async def account_sites(request: Request) -> dict:
    user = await auth_users.require_user(request)
    store = get_target_store()
    return {"sites": await store.list_user_sites(user["id"]),
            "max_sites": user.get("max_sites", 1)}


@app.get("/account/sites/{target_id}")
async def account_site_detail(target_id: int, request: Request) -> dict:
    """Detalhe de um site do usuário: alvo + histórico de score + checks do último
    scan + perfil comercial + CNAEs. 404 se o site não está vinculado à conta."""
    user = await auth_users.require_user(request)
    store = get_target_store()
    link = await store.get_user_site(user["id"], target_id)
    if not link:
        raise HTTPException(status_code=404, detail="Site não encontrado na sua conta.")
    target = await store.get_target(target_id)
    if not target:
        raise HTTPException(status_code=404, detail="Site não encontrado.")
    scans = await store.list_scans(target_id=target_id, limit=12)
    history = [{"score": s.get("score"), "semaphore": s.get("semaphore"),
                "scanned_at": (s.get("scanned_at").isoformat() if s.get("scanned_at") else None)}
               for s in reversed(scans)]
    checks: list = []
    score = target.get("last_scan_score")
    semaphore = None
    fail_count = 0
    if scans:
        full = await store.get_scan(scans[0]["id"])
        cj = (full or {}).get("checks_json") or {}
        if isinstance(cj, dict):
            checks = cj.get("checks") or []
            sc = cj.get("score") or {}
            score = sc.get("score", score)
            semaphore = sc.get("semaphore")
        fail_count = scans[0].get("fail_count") or 0
    profile = await store.get_site_profile(target_id)
    classifications = await store.get_target_classifications(target_id)
    # Selo + posição no ranking do setor (KL-42) — best-effort.
    sector = target.get("sector")
    badge = _score_badge(score)
    ranking = None
    if sector and sector != "outro" and score is not None:
        try:
            pos = await store.get_sector_position(sector, target_id)
            if pos and pos.get("total", 0) > 0:
                pct = min(99, round(100 * (pos["total"] - pos["position"]) / pos["total"]))
                ranking = {"sector": sector, "sector_label": _sector_label(sector),
                           "position": pos["position"], "total": pos["total"],
                           "percentile": pct}
        except Exception:  # noqa: BLE001 - ranking é complementar
            ranking = None
    return {
        "target": {
            "id": target_id, "url": target.get("url"), "domain": target.get("domain"),
            "sector": target.get("sector"), "platform": target.get("platform"),
            "last_scan_at": (target.get("last_scan_at").isoformat()
                             if target.get("last_scan_at") else None),
        },
        "is_owner": bool(link.get("is_owner")),
        "score": score, "semaphore": semaphore, "fail_count": fail_count,
        "badge": badge, "ranking": ranking,
        "history": history, "checks": checks,
        "profile": profile, "classifications": classifications,
    }


async def _effective_plan_limits(user: dict) -> dict:
    """Limites efetivos do plano da conta (KL-44). Usa a assinatura (subscriptions →
    plans) e faz **fallback** para `users.max_sites` se a assinatura não existir ou o
    lookup falhar — preserva o comportamento antigo para contas sem assinatura."""
    try:
        sub = await plans.get_subscription(user["id"])
        return {"max_sites": int(sub["max_sites"]),
                "plan_name": sub.get("plan_name") or sub.get("plan_id"),
                "plan_id": sub.get("plan_id")}
    except Exception as exc:  # noqa: BLE001 - fallback resiliente (nunca bloqueia por erro)
        print(f"[plans] limite efetivo via fallback ({exc!r})", flush=True)
        return {"max_sites": int(user.get("max_sites", 1)),
                "plan_name": user.get("plan", "free"), "plan_id": user.get("plan", "free")}


@app.post("/account/sites")
async def account_add_site(body: SiteBody, request: Request) -> dict:
    user = await auth_users.require_user(request)
    store = get_target_store()
    used = await store.count_user_sites(user["id"])
    limits = await _effective_plan_limits(user)
    if used >= limits["max_sites"]:
        raise HTTPException(
            status_code=403,
            detail=f"Seu plano ({limits['plan_name']}) permite {limits['max_sites']} "
                   "site(s). Faça upgrade para monitorar mais.")
    tid = await _resolve_or_create_target(body.url, source="dashboard")
    if not tid:
        raise HTTPException(status_code=400, detail="Não foi possível analisar esta URL.")
    owner = await _email_owns_target(user["email"], tid)
    await store.link_user_site(user["id"], tid, is_owner=owner)
    # KL-61: marca o lead como tendo monitoramento (fire-and-forget).
    _spawn(_safe_lead(store.set_lead_monitoring(user["email"])))
    return {"ok": True, "target_id": tid, "is_owner": owner}


@app.delete("/account/sites/{target_id}")
async def account_remove_site(target_id: int, request: Request) -> dict:
    user = await auth_users.require_user(request)
    removed = await get_target_store().unlink_user_site(user["id"], target_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Site não encontrado na sua conta.")
    return {"ok": True}


@app.post("/account/sites/{target_id}/claim")
async def account_claim_site(target_id: int, request: Request) -> dict:
    """Reivindica a propriedade de um site: o e-mail da conta precisa bater com o
    contact_email do alvo (verificação por meta tag/DNS fica p/ a fase de perfis)."""
    user = await auth_users.require_user(request)
    store = get_target_store()
    link = await store.get_user_site(user["id"], target_id)
    if not link:
        raise HTTPException(status_code=404, detail="Vincule o site à sua conta primeiro.")
    if not await _email_owns_target(user["email"], target_id):
        raise HTTPException(
            status_code=403,
            detail="Não foi possível confirmar a propriedade: o e-mail da conta não "
                   "corresponde ao contato público do site.")
    await store.set_user_site_owner(user["id"], target_id, True)
    return {"ok": True, "is_owner": True}


@app.get("/")
async def root() -> dict:
    return {
        "name": "Klarim API",
        "scanner_version": scanner_version,
        "endpoints": [
            "/health",
            "/scan/summary?url=",
            "/payment/create (POST)",
            "/payment/status?charge_id=",
            "/webhooks/abacatepay (POST)",
            "/report/executive?url=&charge_id=",
            "/report/technical?url=&charge_id=",
            "/email/test (POST)",
            "/email/send-alert (POST)",
            "/email/send-report (POST)",
            "/recovery/request (POST)",
            "/recovery/validate?token=",
            "/recovery/download?token=&charge_id=&type=",
        ],
        "payments_enabled": _payments_enabled(),
        "email_enabled": _email_enabled(),
        "dev_mode": _dev_mode(),
    }


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/sectors")
async def list_sectors() -> dict:
    """Taxonomia de setores do Klarim (KL-54) — pública, para dropdowns/filtros.
    Não expõe nada sensível: só id/label/macro. `outro` fica fora da listagem."""
    from discovery.sector_taxonomy import SECTOR_TAXONOMY, MACRO_LABELS
    return {
        "sectors": [
            {"id": sid, "label": meta["label"], "macro": meta["macro"]}
            for sid, meta in SECTOR_TAXONOMY.items() if sid != "outro"
        ],
        "macro_sectors": [
            {"id": mid, "label": MACRO_LABELS.get(mid, mid.replace("_", " ").title())}
            for mid in sorted({m["macro"] for m in SECTOR_TAXONOMY.values()})
            if mid != "outro"
        ],
    }


# --------------------------------------------------------------------------- #
# Scan
# --------------------------------------------------------------------------- #

@app.get("/scan")
async def scan_full(url: str = Query(..., description="URL alvo (http/https).")) -> JSONResponse:
    report = await _safe_scan(url)
    return JSONResponse(report.to_dict())


# --------------------------------------------------------------------------- #
# Benchmark (KL-51 f2) — média de score para comparar no resultado. Público.
# --------------------------------------------------------------------------- #

@app.get("/benchmark")
async def api_benchmark_global() -> dict:
    """Média geral de score dos sites brasileiros já escaneados."""
    try:
        data = await get_target_store().global_avg_score()
    except Exception:  # noqa: BLE001 - best-effort; nunca derruba o resultado
        data = {"avg_score": 0, "count": 0}
    return {"scope": "global", "avg_score": data["avg_score"], "count": data["count"]}


@app.get("/benchmark/{sector}")
async def api_benchmark_sector(sector: str) -> dict:
    """Média de score do setor. Cai para o benchmark geral se o setor tem amostra
    pequena (< 5 sites) ou é desconhecido."""
    store = get_target_store()
    try:
        data = await store.sector_avg_score(sector)
        if data["count"] < 5:
            g = await store.global_avg_score()
            return {"scope": "global", "sector": sector, "avg_score": g["avg_score"],
                    "count": g["count"]}
    except Exception:  # noqa: BLE001
        return {"scope": "global", "sector": sector, "avg_score": 0, "count": 0}
    return {"scope": "sector", "sector": sector, "avg_score": data["avg_score"],
            "count": data["count"]}


# --------------------------------------------------------------------------- #
# CNAE (KL-55) — seções/divisões (referência estrutural do IBGE) + benchmark.
# Públicos. `/benchmark/cnae/{division}` (não `/benchmark/{division}`) para não
# colidir com o `/benchmark/{sector}` acima (path de 1 segmento).
# --------------------------------------------------------------------------- #

@app.get("/cnaes/sections")
async def api_cnae_sections() -> dict:
    """As 21 seções CNAE (A–U). Offline (mapa embutido)."""
    from discovery.cnae import sections
    return {"sections": sections()}


@app.get("/cnaes/divisions")
async def api_cnae_divisions() -> dict:
    """As 87 divisões CNAE (2 dígitos → seção). Offline (mapa embutido)."""
    from discovery.cnae import divisions
    return {"divisions": divisions()}


@app.get("/benchmark/cnae/{division}")
async def api_benchmark_cnae(division: str) -> dict:
    """Benchmark de score por divisão CNAE (2 dígitos). Cai para o geral se amostra < 5."""
    store = get_target_store()
    try:
        data = await store.cnae_division_avg_score(division)
        if data["count"] < 5:
            g = await store.global_avg_score()
            return {"scope": "global", "division": division, "avg_score": g["avg_score"],
                    "count": g["count"]}
    except Exception:  # noqa: BLE001
        return {"scope": "global", "division": division, "avg_score": 0, "count": 0}
    return {"scope": "cnae_division", "division": division, "avg_score": data["avg_score"],
            "count": data["count"]}


# --------------------------------------------------------------------------- #
# Perfis públicos SEO (KL-51 f4) — /site/{dominio} no Astro consome estes endpoints.
# Rotas em /public, /og, /notify (NÃO nos prefixos protegidos por JWT admin). O perfil
# NUNCA expõe e-mail de contato nem CNPJ (privacidade). O Klarim avalia a segurança do
# SITE, não do negócio.
# --------------------------------------------------------------------------- #

# Campos do site_profile seguros para exibição pública (sem cnpj/commercial_email/whatsapp).
_PUBLIC_PROFILE_FIELDS = (
    "description", "business_type", "company_name", "tags", "maturity_score",
    "phone", "address", "instagram", "facebook", "linkedin", "youtube", "tiktok",
    "google_maps_url", "logo_url",
)


def _norm_domain(domain: str) -> str:
    d = (domain or "").strip().lower().rstrip("/")
    if "://" in d:
        from urllib.parse import urlparse
        d = urlparse(d).hostname or d
    return d.replace("www.", "", 1) if d.startswith("www.") else d


@app.get("/public/profile/{domain}")
async def public_profile(domain: str) -> dict:
    """Perfil público agregado de um site (1 chamada para o Astro): dados do alvo
    (sem e-mail), perfil comercial (sem cnpj/whatsapp), CNAEs e benchmark do setor."""
    domain = _norm_domain(domain)
    store = get_target_store()
    target = await store.get_target_by_domain(domain)
    if not target:
        return {"status": "not_found", "domain": domain}
    if target.get("status") == "descartado":
        return {"status": "discarded", "domain": domain}
    score = target.get("last_scan_score")
    if score is None:
        return {"status": "not_scanned", "domain": domain}

    tid = target["id"]
    profile = (await store.get_site_profile(tid)) or {}
    # KL-56: landing desligada pelo operador → some (mesmo comportamento de descartado).
    if profile.get("public_visible") is False:
        return {"status": "not_found", "domain": domain}
    classifications = await store.get_target_classifications(tid)
    # semáforo real do último scan (KL-12); fallback por score p/ scans antigos.
    semaphore = _semaphore_from_score(score)
    try:
        recent = await store.list_scans(target_id=tid, limit=1)
        if recent and recent[0].get("semaphore"):
            semaphore = recent[0]["semaphore"]
    except Exception:  # noqa: BLE001
        pass

    sector = target.get("sector")
    benchmark = None
    try:
        b = (await store.sector_avg_score(sector)) if sector and sector != "outro" else None
        if not b or b.get("count", 0) < 5:
            b = await store.global_avg_score()
            benchmark = {"scope": "global", **b}
        else:
            benchmark = {"scope": "sector", "sector": sector, **b}
    except Exception:  # noqa: BLE001
        benchmark = None

    last_at = target.get("last_scan_at")
    return {
        "status": "ok",
        "domain": domain,
        # KL-44 fix (auditoria F-03): NÃO expor o `target.id` (PK interna) no perfil
        # público — o frontend usa o `domain`, não o id; expor ajudava enumeração.
        "target": {
            "url": target.get("url"), "domain": domain,
            "sector": sector, "platform": target.get("platform"),
            "score": score, "semaphore": semaphore,
            "last_scan_at": last_at.isoformat() if last_at else None,
        },
        "profile": {k: profile.get(k) for k in _PUBLIC_PROFILE_FIELDS},
        "classifications": classifications,
        "benchmark": benchmark,
    }


@app.get("/public/sitemap-domains")
async def public_sitemap_domains() -> dict:
    """Domínios com perfil público (para o sitemap.xml gerado pelo Astro)."""
    try:
        rows = await get_target_store().list_public_profile_domains()
    except Exception:  # noqa: BLE001
        rows = []
    return {"domains": [
        {"domain": r["domain"],
         "lastmod": r["last_scan_at"].date().isoformat() if r.get("last_scan_at") else None}
        for r in rows if r.get("domain")]}


# --- og:image dinâmico (SVG → PNG via cairosvg; cairo já vem do WeasyPrint) --- #
_OG_CACHE: dict = {}  # domain -> (png_bytes, expiry_monotonic)
_OG_TTL = 86400
_SEMA_COLOR = {"verde": "#00D26A", "amarelo": "#F0C000", "vermelho": "#F85149"}


def _og_svg(domain: str, score: int, semaphore: str, description: str) -> str:
    from html import escape
    color = _SEMA_COLOR.get(semaphore, "#F0C000")
    desc = (description or "Análise passiva de segurança do site.").strip()
    if len(desc) > 74:
        desc = desc[:71].rstrip() + "…"
    dom = domain if len(domain) <= 34 else domain[:33] + "…"
    return f"""<svg width="1200" height="630" viewBox="0 0 1200 630" xmlns="http://www.w3.org/2000/svg">
  <rect width="1200" height="630" fill="#0D1117"/>
  <rect x="0" y="0" width="1200" height="10" fill="#FF6B35"/>
  <circle cx="76" cy="82" r="15" fill="#FF6B35"/>
  <text x="104" y="94" font-family="sans-serif" font-size="34" font-weight="bold" fill="#E6EDF3" letter-spacing="3">KLA<tspan fill="#FF6B35">R</tspan>IM</text>
  <text x="72" y="240" font-family="sans-serif" font-size="52" font-weight="bold" fill="#E6EDF3">{escape(dom)}</text>
  <text x="72" y="430" font-family="sans-serif" font-size="170" font-weight="bold" fill="{color}">{score}</text>
  <text x="{72 + 105 * len(str(score))}" y="430" font-family="sans-serif" font-size="56" fill="#8B949E">/100</text>
  <circle cx="{130 + 105 * len(str(score))}" cy="388" r="34" fill="{color}"/>
  <text x="72" y="500" font-family="sans-serif" font-size="30" fill="#8B949E">Score de segurança do site</text>
  <text x="72" y="552" font-family="sans-serif" font-size="27" fill="#C9D1D9">{escape(desc)}</text>
  <text x="72" y="602" font-family="sans-serif" font-size="24" fill="#8B949E">48 verificações · klarim.net</text>
</svg>"""


@app.get("/og/{domain}.png")
async def og_image(domain: str) -> Response:
    """og:image (1200x630 PNG) do perfil público. Cache em processo 24h + Cache-Control.
    Fail-open: se o alvo não existe/sem score ou o render falha, cai para o favicon."""
    dom = _norm_domain(domain)
    now = time.monotonic()
    cached = _OG_CACHE.get(dom)
    if cached and cached[1] > now:
        return Response(content=cached[0], media_type="image/png",
                        headers={"Cache-Control": "public, max-age=86400"})
    store = get_target_store()
    target = await store.get_target_by_domain(dom)
    score = (target or {}).get("last_scan_score")
    if not target or score is None or target.get("status") == "descartado":
        return RedirectResponse(url="/favicon.svg", status_code=302)
    profile = (await store.get_site_profile(target["id"])) or {}
    semaphore = _semaphore_from_score(score)
    try:
        recent = await store.list_scans(target_id=target["id"], limit=1)
        if recent and recent[0].get("semaphore"):
            semaphore = recent[0]["semaphore"]
    except Exception:  # noqa: BLE001
        pass
    svg = _og_svg(dom, int(score), semaphore,
                  profile.get("business_type") or profile.get("description") or "")
    try:
        import cairosvg  # lazy: precisa do libcairo (presente na imagem; ausente no CI)
        png = cairosvg.svg2png(bytestring=svg.encode("utf-8"),
                               output_width=1200, output_height=630)
    except Exception as exc:  # noqa: BLE001 - render é best-effort
        print(f"[og] falha ao renderizar {dom}: {exc!r}", flush=True)
        return RedirectResponse(url="/favicon.svg", status_code=302)
    _OG_CACHE[dom] = (png, now + _OG_TTL)
    if len(_OG_CACHE) > 5000:  # limpeza oportunista
        for k, (_, exp) in list(_OG_CACHE.items()):
            if exp <= now:
                _OG_CACHE.pop(k, None)
    return Response(content=png, media_type="image/png",
                    headers={"Cache-Control": "public, max-age=86400"})


# --------------------------------------------------------------------------- #
# Score social (KL-42): widget embeddable + card compartilhável + selo + ranking.
# Tudo público (não está sob prefixo protegido). O widget roda em sites EXTERNOS,
# então /score/ manda Access-Control-Allow-Origin:* (dado público, GET, sem cookie).
# --------------------------------------------------------------------------- #

def _site_base() -> str:
    return os.environ.get("SITE_BASE", "https://klarim.net")


def _sector_label(sector: str) -> str:
    """Rótulo humano do setor (taxonomia KL-54). Fallback: o próprio slug."""
    try:
        from discovery.sector_taxonomy import get_label
        return get_label(sector)
    except Exception:  # noqa: BLE001
        return sector or "outro"


def _score_badge(score: Optional[int]) -> Optional[dict]:
    """Selo derivado do score (KL-42) — sem campo novo no banco. ≥90 Verified,
    ≥80 Approved, <80 sem selo."""
    if score is None:
        return None
    if score >= 90:
        return {"level": "verified", "label": "Klarim Verified", "icon": "⭐"}
    if score >= 80:
        return {"level": "approved", "label": "Klarim Approved", "icon": "✅"}
    return None


async def _public_score_data(domain: str) -> Optional[dict]:
    """Resolve o score público de um domínio (widget/card/score). ``None`` se o site
    não tem score público: inexistente, descartado, landing desligada (KL-56) ou sem
    scan. Mesmo critério de visibilidade de `/public/profile/{domain}`."""
    store = get_target_store()
    target = await store.get_target_by_domain(domain)
    if not target or target.get("status") == "descartado":
        return None
    score = target.get("last_scan_score")
    if score is None:
        return None
    profile = (await store.get_site_profile(target["id"])) or {}
    if profile.get("public_visible") is False:
        return None
    semaphore = _semaphore_from_score(score)
    try:
        recent = await store.list_scans(target_id=target["id"], limit=1)
        if recent and recent[0].get("semaphore"):
            semaphore = recent[0]["semaphore"]
    except Exception:  # noqa: BLE001
        pass
    last_at = target.get("last_scan_at")
    return {
        "domain": domain, "score": int(score), "semaphore": semaphore,
        "badge": _score_badge(int(score)),
        "last_scan": last_at.date().isoformat() if hasattr(last_at, "date") else None,
        "profile_url": f"{_site_base()}/site/{domain}",
    }


@app.get("/score/{domain}")
async def public_score(domain: str) -> JSONResponse:
    """Score público de um site (JSON) — consumido pelo widget embeddable (KL-42).
    CORS liberado (dado público, sem cookie); cache 24h."""
    data = await _public_score_data(_norm_domain(domain))
    headers = {"Cache-Control": "public, max-age=86400",
               "Access-Control-Allow-Origin": "*"}
    if data is None:
        return JSONResponse({"domain": _norm_domain(domain), "score": None,
                             "semaphore": None, "badge": None}, headers=headers)
    return JSONResponse(data, headers=headers)


# JS do widget "Verificado por Klarim" — leve, sem dependência, CSS inline. Gerado
# por domínio (o domínio é embutido); o estilo (inline/card/minimal) é lido em runtime
# do `?style=` do próprio <script src>. O score vem de /api/score (CORS). Beacons de
# impressão/clique via pixel GET (sem CORS).
_WIDGET_JS = r"""(function(){
var D="__DOMAIN__",B="__BASE__",me=document.currentScript;
var style="inline";try{style=(new URL(me.src).searchParams.get("style")||"inline");}catch(e){}
if(["inline","card","minimal"].indexOf(style)<0)style="inline";
var sid="w"+Math.random().toString(36).slice(2,10)+Date.now().toString(36);
function px(ev){try{(new Image()).src=B+"/api/widget/event?e="+ev+"&d="+encodeURIComponent(D)+"&s="+sid+"&t="+Date.now();}catch(e){}}
function col(s){return s==="verde"?"#00D26A":s==="vermelho"?"#F85149":"#F0C000";}
function el(t,css,txt){var e=document.createElement(t);e.style.cssText=css;if(txt!=null)e.textContent=txt;return e;}
function render(d){
var c=col(d.semaphore);
var a=el("a","display:inline-flex;align-items:center;gap:8px;box-sizing:border-box;text-decoration:none;font-family:Arial,Helvetica,sans-serif;background:#0D1117;border:1px solid #30363D;border-radius:10px;color:#E6EDF3;line-height:1.2;");
a.href=d.profile_url+(d.profile_url.indexOf("?")<0?"?":"&")+"utm_source=widget&utm_medium=embed&utm_campaign="+style;
a.target="_blank";a.rel="noopener";
var dot=el("span","width:10px;height:10px;border-radius:50%;flex:0 0 auto;background:"+c+";");
var shield=el("span","font-size:14px;flex:0 0 auto;","🛡️");
if(style==="minimal"){a.style.padding="6px 10px";a.style.fontSize="12px";
a.appendChild(shield);a.appendChild(el("span","font-weight:bold;color:"+c+";",d.score));a.appendChild(el("span","color:#8B949E;","/100"));
}else if(style==="card"){a.style.flexDirection="column";a.style.alignItems="flex-start";a.style.padding="12px 14px";a.style.width="180px";
var top=el("span","display:flex;align-items:center;gap:6px;font-size:12px;color:#8B949E;");top.appendChild(shield);top.appendChild(el("span",null,"Verificado por Klarim"));a.appendChild(top);
var mid=el("span","display:flex;align-items:baseline;gap:5px;margin-top:6px;");mid.appendChild(el("span","font-size:30px;font-weight:bold;color:"+c+";",d.score));mid.appendChild(el("span","color:#8B949E;font-size:13px;","/100"));mid.appendChild(dot);a.appendChild(mid);
a.appendChild(el("span","font-size:11px;color:#8B949E;margin-top:4px;","klarim.net"));
}else{a.style.padding="8px 12px";a.style.fontSize="13px";
a.appendChild(shield);a.appendChild(el("span","color:#E6EDF3;","Verificado por Klarim"));a.appendChild(el("span","font-weight:bold;color:"+c+";","· "+d.score+"/100"));a.appendChild(dot);}
a.addEventListener("click",function(){px("widget_clicked");});
me.parentNode.insertBefore(a,me);px("widget_loaded");}
fetch(B+"/api/score/"+encodeURIComponent(D)).then(function(r){return r.json();}).then(function(d){if(d&&d.score!=null)render(d);}).catch(function(){});
})();"""


@app.get("/widget/event")
async def widget_event(e: str = Query(...), d: str = Query(...),
                       s: str = Query(...)) -> Response:
    """Beacon do widget embeddable (cross-origin via pixel GET → sem CORS). Loga
    impressão (`widget_loaded`) / clique (`widget_clicked`) no funil (KL-21/57)."""
    if e in ("widget_loaded", "widget_clicked") and s and _event_rate_ok(s):
        dom = _norm_domain(d)

        async def _bg():
            try:
                store = get_target_store()
                t = await store.get_target_by_domain(dom)
                await store.log_event(e, s, target_url=dom, target_id=(t or {}).get("id"),
                                      utm_source="widget", utm_medium="embed",
                                      metadata={"domain": dom})
            except Exception as exc:  # noqa: BLE001 - tracking nunca derruba nada
                print(f"[widget] beacon falhou {dom}: {exc!r}", flush=True)

        _spawn(_bg())
    return Response(status_code=204, headers={"Cache-Control": "no-store",
                                              "Access-Control-Allow-Origin": "*"})


@app.get("/widget/{domain}.js")
async def widget_js(domain: str) -> Response:
    """Widget embeddable "Verificado por Klarim" (KL-42). JS leve, CSS inline, o
    domínio é embutido; o estilo vem do `?style=` do próprio <script>. Cache 1h."""
    dom = _norm_domain(domain)
    js = _WIDGET_JS.replace("__DOMAIN__", dom).replace("__BASE__", _site_base())
    return Response(content=js, media_type="application/javascript",
                    headers={"Cache-Control": "public, max-age=3600",
                             "Access-Control-Allow-Origin": "*"})


# --- card compartilhável (SVG → PNG via cairosvg; reusa a infra do og:image) --- #
_CARD_CACHE: dict = {}  # (domain, fmt) -> (png_bytes, expiry_monotonic)


def _card_svg(domain: str, score: int, semaphore: str, fmt: str = "landscape") -> str:
    """Card social com score + semáforo + CTA. `fmt`: square (1080x1080, Instagram) ou
    landscape (1200x630, LinkedIn/Twitter)."""
    from html import escape
    color = _SEMA_COLOR.get(semaphore, "#F0C000")
    dom = domain if len(domain) <= 30 else domain[:29] + "…"
    dom = escape(dom)
    if fmt == "square":
        return f"""<svg width="1080" height="1080" viewBox="0 0 1080 1080" xmlns="http://www.w3.org/2000/svg">
  <rect width="1080" height="1080" fill="#0D1117"/>
  <rect x="0" y="0" width="1080" height="16" fill="#FF6B35"/>
  <circle cx="92" cy="150" r="18" fill="#FF6B35"/>
  <text x="126" y="164" font-family="sans-serif" font-size="44" font-weight="bold" fill="#E6EDF3" letter-spacing="4">KLA<tspan fill="#FF6B35">R</tspan>IM</text>
  <text x="540" y="360" text-anchor="middle" font-family="sans-serif" font-size="54" font-weight="bold" fill="#E6EDF3">{dom}</text>
  <text x="540" y="640" text-anchor="middle" font-family="sans-serif" font-size="240" font-weight="bold" fill="{color}">{score}</text>
  <text x="540" y="720" text-anchor="middle" font-family="sans-serif" font-size="48" fill="#8B949E">/100</text>
  <circle cx="540" cy="792" r="26" fill="{color}"/>
  <text x="540" y="900" text-anchor="middle" font-family="sans-serif" font-size="36" fill="#C9D1D9">Nosso site tem score {score} de segurança.</text>
  <text x="540" y="952" text-anchor="middle" font-family="sans-serif" font-size="36" font-weight="bold" fill="#E6EDF3">E o seu?</text>
  <text x="540" y="1024" text-anchor="middle" font-family="sans-serif" font-size="30" fill="#8B949E">Verifique grátis em klarim.net</text>
</svg>"""
    return f"""<svg width="1200" height="630" viewBox="0 0 1200 630" xmlns="http://www.w3.org/2000/svg">
  <rect width="1200" height="630" fill="#0D1117"/>
  <rect x="0" y="0" width="1200" height="12" fill="#FF6B35"/>
  <circle cx="76" cy="80" r="15" fill="#FF6B35"/>
  <text x="104" y="92" font-family="sans-serif" font-size="34" font-weight="bold" fill="#E6EDF3" letter-spacing="3">KLA<tspan fill="#FF6B35">R</tspan>IM</text>
  <text x="600" y="220" text-anchor="middle" font-family="sans-serif" font-size="48" font-weight="bold" fill="#E6EDF3">{dom}</text>
  <text x="600" y="410" text-anchor="middle" font-family="sans-serif" font-size="170" font-weight="bold" fill="{color}">{score}</text>
  <text x="600" y="470" text-anchor="middle" font-family="sans-serif" font-size="40" fill="#8B949E">/100</text>
  <text x="600" y="540" text-anchor="middle" font-family="sans-serif" font-size="30" fill="#C9D1D9">Nosso site tem score {score} de segurança. E o seu?</text>
  <text x="600" y="592" text-anchor="middle" font-family="sans-serif" font-size="26" fill="#8B949E">Verifique grátis em klarim.net</text>
</svg>"""


@app.get("/card/{domain}.png")
async def card_image(domain: str,
                     format: str = Query("landscape", pattern="^(square|landscape)$")
                     ) -> Response:
    """Card compartilhável (PNG) com o score do site (KL-42). `format`: square
    (1080x1080) ou landscape (1200x630, default). Cache 24h. Fail-open → favicon."""
    dom = _norm_domain(domain)
    fmt = format if format in ("square", "landscape") else "landscape"
    now = time.monotonic()
    key = (dom, fmt)
    cached = _CARD_CACHE.get(key)
    if cached and cached[1] > now:
        return Response(content=cached[0], media_type="image/png",
                        headers={"Cache-Control": "public, max-age=86400"})
    data = await _public_score_data(dom)
    if data is None:
        return RedirectResponse(url="/favicon.svg", status_code=302)
    svg = _card_svg(dom, int(data["score"]), data["semaphore"], fmt)
    w, h = (1080, 1080) if fmt == "square" else (1200, 630)
    try:
        import cairosvg  # lazy: precisa do libcairo (presente na imagem; ausente no CI)
        png = cairosvg.svg2png(bytestring=svg.encode("utf-8"),
                               output_width=w, output_height=h)
    except Exception as exc:  # noqa: BLE001 - render é best-effort
        print(f"[card] falha ao renderizar {dom}: {exc!r}", flush=True)
        return RedirectResponse(url="/favicon.svg", status_code=302)
    _CARD_CACHE[key] = (png, now + _OG_TTL)
    if len(_CARD_CACHE) > 5000:  # limpeza oportunista
        for k, (_, exp) in list(_CARD_CACHE.items()):
            if exp <= now:
                _CARD_CACHE.pop(k, None)
    return Response(content=png, media_type="image/png",
                    headers={"Cache-Control": "public, max-age=86400"})


# --- rankings por setor (páginas públicas SEO no Astro consomem estes) -------- #

@app.get("/ranking")
async def api_ranking_index() -> dict:
    """Setores com ranking público (≥ 5 sites escaneados): contagem, score médio e
    o site top de cada setor (KL-42)."""
    try:
        rows = await get_target_store().ranking_sectors_summary(min_count=5)
    except Exception:  # noqa: BLE001
        rows = []
    sectors = [{
        "sector": r["sector"], "label": _sector_label(r["sector"]),
        "count": int(r["count"]), "avg_score": int(r.get("avg_score") or 0),
        "top_domain": r.get("top_domain"),
    } for r in rows]
    return {"sectors": sectors, "count": len(sectors)}


@app.get("/ranking/{sector}")
async def api_ranking_sector(sector: str,
                             limit: int = Query(20, ge=1, le=100)) -> dict:
    """Top sites por score de segurança num setor (KL-42). Público."""
    store = get_target_store()
    sector = (sector or "").lower().strip()
    try:
        rows = await store.list_sector_ranking(sector, limit)
    except Exception:  # noqa: BLE001
        rows = []
    try:
        avg = await store.sector_avg_score(sector)
    except Exception:  # noqa: BLE001
        avg = {"avg_score": 0, "count": 0}
    sites = []
    for i, r in enumerate(rows, 1):
        sc = int(r["last_scan_score"])
        sites.append({"position": i, "domain": r["domain"], "score": sc,
                      "semaphore": _semaphore_from_score(sc), "badge": _score_badge(sc)})
    return {"sector": sector, "label": _sector_label(sector),
            "avg_score": int(avg.get("avg_score") or 0),
            "count": int(avg.get("count") or 0), "sites": sites}


# --- notificação ao dono (perfil consultado) — rate limit 1/domínio/24h ------ #
class ProfileViewBody(BaseModel):
    domain: str
    utm_campaign: str = ""  # KL-44: origem da visita (anti-loop do e-mail de alerta)


@app.post("/notify/profile-view")
async def notify_profile_view(body: ProfileViewBody) -> dict:
    """Avisa o dono que alguém consultou o perfil público (KL-51 f4). Fire-and-forget:
    responde na hora e envia em background. Rate limit 1/domínio/24h (Redis). Pula alvos
    sem e-mail, descartados, unsubscribed, ou cujo e-mail já é de usuário registrado.

    KL-44: o próprio dono clicando no link do e-mail de alerta (`utm_campaign=alerta*`)
    NÃO gera outra notificação de "perfil consultado" — senão viraria um loop de e-mail
    (alerta → clique → aviso → clique → …)."""
    domain = _norm_domain(body.domain)
    if not domain:
        return {"ok": True, "notified": False}
    # KL-44: visita vinda do e-mail de alerta (o próprio dono) não notifica — anti-loop.
    if (body.utm_campaign or "").startswith("alerta"):
        return {"ok": True, "notified": False}

    async def _do():
        try:
            store = get_target_store()
            target = await store.get_target_by_domain(domain)
            if not target:
                return
            email = (target.get("contact_email") or "").strip()
            status = target.get("status")
            if not email or status in ("descartado", "unsubscribed"):
                return
            # rate limit 1/domínio/24h (Redis SET NX EX); sem Redis, deixa passar.
            if _cache is not None and _cache.redis is not None:
                key = f"notify:{domain}"
                if not await _cache.redis.set(key, "1", nx=True, ex=86400):
                    return  # já notificado nas últimas 24h
            # não notificar se o e-mail já tem conta (o dono já acompanha)
            try:
                if await store.get_user_by_email(email):
                    return
            except Exception:  # noqa: BLE001
                pass
            if not _email_enabled():
                return
            score = target.get("last_scan_score") or 0
            semaphore = _semaphore_from_score(score)
            cta = f"{os.environ.get('SITE_BASE', 'https://klarim.net')}/cadastrar"
            await _mailer().send_profile_view(email, domain, int(score), semaphore, cta,
                                              target_id=target.get("id"))  # KL-62
            print(f"[notify] perfil {domain} consultado → aviso enviado a {email}", flush=True)
        except Exception as exc:  # noqa: BLE001 - nunca derruba nada
            print(f"[notify] profile-view erro {domain}: {exc!r}", flush=True)

    _spawn(_do())
    return {"ok": True}


# --------------------------------------------------------------------------- #
# Verificação de e-mail (código 6 dígitos) antes do scan público — KL-25
# --------------------------------------------------------------------------- #

_SCAN_TOKEN_TTL = 3600  # 1h
_SCAN_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")


def _clean_scan_email(email: str) -> str:
    from discovery.contact import _clean_email
    return _clean_email(email or "")


def _norm_scan_url(url: str) -> str:
    """Normaliza a URL para casar crédito/cache (scheme + host lowercase, sem '/' final)."""
    url = (url or "").strip()
    if not url:
        return ""
    if "://" not in url:
        url = "https://" + url
    p = urlparse(url)
    host = (p.hostname or "").lower()
    if not host:
        return url
    scheme = p.scheme or "https"
    path = (p.path or "").rstrip("/")
    return f"{scheme}://{host}{path}"

# Rate limits in-memory (anti brute-force/spam) — o teto real é o crédito no banco.
_CODE_RL_EMAIL_MAX, _CODE_RL_EMAIL_WIN = 3, 3600      # 3 códigos/e-mail/hora
_CODE_RL_IP_MAX, _CODE_RL_IP_WIN = 5, 3600            # 5 códigos/IP/hora
_VERIFY_RL_MAX, _VERIFY_RL_WIN = 5, 600               # 5 tentativas/e-mail/10min
_code_email_hits: dict = {}
_code_ip_hits: dict = {}
_verify_hits: dict = {}


def _rl_ok(store: dict, key: str, limit: int, window: int) -> bool:
    now = time.monotonic()
    q = store.setdefault(key, [])
    cutoff = now - window
    while q and q[0] < cutoff:
        q.pop(0)
    if len(q) >= limit:
        return False
    q.append(now)
    if len(store) > 10000:  # limpeza oportunista
        for k in [k for k, ts in store.items() if not ts or ts[-1] < cutoff]:
            store.pop(k, None)
    return True


def _scan_token_secret() -> str:
    return os.environ.get("JWT_SECRET", "") or os.environ.get("UNSUBSCRIBE_SECRET", "")


_BONUS_TOKEN_TTL = 30 * 86400  # 30 dias — o e-mail de score 100 pode ser clicado depois


def _make_scan_token(email: str, url: str, full: bool = False, bonus: bool = False,
                     ttl: Optional[int] = None) -> str:
    """Token HMAC-assinado que autoriza 1 scan da URL pelo e-mail.

    ``full=True`` (re-verificação paga, KL-27) autoriza o scan completo de 29 checks
    e serve de bypass de pagamento nos PDFs; ``full=False`` é o scan gratuito (15).
    ``bonus=True`` (KL-31) identifica o link do e-mail de score 100 — o scan completo
    gratuito só roda se o **crédito no banco** existir (o token sozinho não basta).
    """
    payload = {"email": email, "url": url, "full": bool(full), "bonus": bool(bonus),
               "exp": int(time.time()) + (ttl if ttl is not None else _SCAN_TOKEN_TTL)}
    raw = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()
    sig = hmac.new(_scan_token_secret().encode(), raw.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{raw}.{sig}"


def _verify_scan_token(token: str) -> Optional[dict]:
    secret = _scan_token_secret()
    if not token or not secret:
        return None
    try:
        raw, sig = token.rsplit(".", 1)
        expected = hmac.new(secret.encode(), raw.encode(), hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(sig, expected):
            return None
        payload = json.loads(base64.urlsafe_b64decode(raw))
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        return payload
    except Exception:  # noqa: BLE001
        return None


def _is_admin_request(request: Request) -> bool:
    """True se o request traz um JWT de admin válido (bypass do token de scan)."""
    auth = request.headers.get("authorization", "")
    token = auth[7:].strip() if auth[:7].lower() == "bearer " else ""
    if not token:
        return False
    try:
        _verify_token(token)
        return True
    except Exception:  # noqa: BLE001
        return False


# --------------------------------------------------------------------------- #
# Modo demo (Fix pós-KL-27): testar o fluxo completo sem pagamento real.
# Ativado por e-mail (DEMO_EMAIL) e/ou URL (DEMO_URL) — ambos vazios = desligado.
# ⚠️ NÃO aponte DEMO_URL para klarim.net (liberaria relatório completo grátis do
# site real); use um domínio de teste. O código de verificação demo é "000000".
# --------------------------------------------------------------------------- #

DEMO_CODE = "000000"


def _is_demo(email: Optional[str] = None, url: Optional[str] = None) -> bool:
    demo_email = os.environ.get("DEMO_EMAIL", "").strip().lower()
    demo_url = os.environ.get("DEMO_URL", "").strip().lower()
    if email and demo_email and email.strip().lower() == demo_email:
        return True
    if url and demo_url and _norm_scan_url(url).lower().startswith(demo_url):
        return True
    return False


class ScanCodeBody(BaseModel):
    email: str
    url: str


class ScanVerifyBody(BaseModel):
    email: str
    code: str
    url: str


@app.post("/scan/check-credit")
async def scan_check_credit(body: ScanCodeBody) -> dict:
    """Estado do crédito de scan gratuito do e-mail para a URL (sem enviar código)."""
    email = _clean_scan_email(body.email)
    url = _norm_scan_url(body.url)
    if not _SCAN_EMAIL_RE.match(email):
        raise HTTPException(status_code=422, detail="E-mail inválido.")
    credit = await get_target_store().get_scan_credit(email)
    used = int(credit["free_scans_used"]) if credit else 0
    same_url = bool(credit and credit.get("first_scan_url") == url)
    rescan_credits = int(credit.get("rescan_credits") or 0) if credit else 0
    full_scan_credits = int(credit.get("full_scan_credits") or 0) if credit else 0
    # Bônus de score 100 (KL-31): vinculado ao par (e-mail, URL).
    can_full = bool(full_scan_credits > 0 and credit
                    and _norm_scan_url(credit.get("full_scan_url") or "") == url)
    return {"has_free_scan": used == 0, "same_url_scanned": same_url,
            "free_scans_used": used, "rescan_credits": rescan_credits,
            "can_rescan": rescan_credits > 0,
            "full_scan_credits": full_scan_credits, "can_full_scan_free": can_full}


@app.post("/scan/request-code")
async def scan_request_code(body: ScanCodeBody, request: Request) -> dict:
    """Envia um código de 6 dígitos para o e-mail antes de liberar o scan (KL-25)."""
    email = _clean_scan_email(body.email)
    url = _norm_scan_url(body.url)
    if not _SCAN_EMAIL_RE.match(email):
        raise HTTPException(status_code=422, detail="E-mail inválido.")
    if not url:
        raise HTTPException(status_code=422, detail="URL inválida.")

    # Modo demo: não envia e-mail; o código é fixo (DEMO_CODE), sem rate limit.
    if _is_demo(email=email, url=url):
        return {"status": "code_sent", "expires_in": 600, "demo": True}

    if not _email_enabled():
        raise HTTPException(status_code=503, detail="Envio de e-mail não configurado.")

    store = get_target_store()
    credit = await store.get_scan_credit(email)
    has_rescan = bool(credit and int(credit.get("rescan_credits") or 0) > 0)
    # KL-27: quem tem crédito de re-verificação pode pedir um código mesmo já tendo
    # escaneado — é a re-verificação paga (retorno médico), não o scan gratuito.
    # KL-51 f2: com o paywall aberto (default), NÃO há limite de 1 scan/e-mail —
    # a verificação de e-mail continua (captura de lead + anti-bot via rate limit),
    # mas o usuário pode escanear quantos sites quiser.
    if _paywall_enabled() and credit and not has_rescan:
        if credit.get("first_scan_url") == url:
            return {"status": "already_scanned", "message": "Você já escaneou este site."}
        return {"status": "limit_reached",
                "message": "Você já utilizou seu scan gratuito para outro site. "
                           "Para escanear este, adquira o relatório completo."}

    ip = _client_ip(request)
    if not _rl_ok(_code_email_hits, email, _CODE_RL_EMAIL_MAX, _CODE_RL_EMAIL_WIN):
        raise HTTPException(status_code=429, detail="Muitos códigos para este e-mail. Aguarde 1h.",
                            headers={"Retry-After": "3600"})
    if not _rl_ok(_code_ip_hits, ip, _CODE_RL_IP_MAX, _CODE_RL_IP_WIN):
        raise HTTPException(status_code=429, detail="Muitas solicitações. Aguarde 1h.",
                            headers={"Retry-After": "3600"})

    code = f"{secrets.randbelow(900000) + 100000:06d}"  # CSPRNG, 6 dígitos
    await store.create_scan_verification(email, code, url, ttl_minutes=10, ip_address=ip)
    domain = urlparse(url).hostname or url
    try:
        await _mailer().send_verification_code(email, code, domain)
    except KlarimMailerError as exc:
        raise HTTPException(status_code=502, detail=f"Falha ao enviar o código: {exc}") from exc
    return {"status": "code_sent", "expires_in": 600}


@app.post("/scan/verify-code")
async def scan_verify_code(body: ScanVerifyBody) -> dict:
    """Valida o código, consome o scan gratuito e devolve um scan token (1h)."""
    email = _clean_scan_email(body.email)
    url = _norm_scan_url(body.url)
    code = (body.code or "").strip()

    # Modo demo: aceita o código fixo sem consumir crédito (testes repetíveis).
    if _is_demo(email=email, url=url):
        if code != DEMO_CODE:
            return {"status": "invalid", "message": "Código inválido ou expirado."}
        return {"status": "verified", "scan_token": _make_scan_token(email, url, full=False),
                "expires_in": _SCAN_TOKEN_TTL, "demo": True}

    if not _rl_ok(_verify_hits, email, _VERIFY_RL_MAX, _VERIFY_RL_WIN):
        raise HTTPException(status_code=429, detail="Muitas tentativas. Aguarde alguns minutos.",
                            headers={"Retry-After": "600"})
    store = get_target_store()
    if not await store.verify_scan_code(email, code, url):
        return {"status": "invalid", "message": "Código inválido ou expirado."}
    await store.record_free_scan(email, url)  # consome o gratuito
    return {"status": "verified", "scan_token": _make_scan_token(email, url, full=False),
            "expires_in": _SCAN_TOKEN_TTL}


def _evolution_label(old: Optional[int], new: Optional[int]) -> str:
    if old is None or new is None:
        return "first_rescan"
    if new > old:
        return "improved"
    if new < old:
        return "worsened"
    return "unchanged"


class ScanRescanBody(BaseModel):
    email: str
    code: str
    url: str


@app.post("/scan/rescan")
async def scan_rescan(body: ScanRescanBody) -> dict:
    """Re-verificação gratuita pós-compra (retorno médico — KL-27).

    Valida o código, **consome 1 crédito** de re-scan, roda o scan COMPLETO (29) e
    devolve o resultado completo + comparação antes/depois. Também devolve um scan
    token ``full`` para baixar os PDFs atualizados.
    """
    email = _clean_scan_email(body.email)
    url = _norm_scan_url(body.url)
    code = (body.code or "").strip()
    if not _rl_ok(_verify_hits, email, _VERIFY_RL_MAX, _VERIFY_RL_WIN):
        raise HTTPException(status_code=429, detail="Muitas tentativas. Aguarde alguns minutos.",
                            headers={"Retry-After": "600"})
    store = get_target_store()
    if not await store.verify_scan_code(email, code, url):
        return {"status": "invalid", "message": "Código inválido ou expirado."}
    if not await store.consume_rescan_credit(email):
        return {"status": "no_credit",
                "message": "Você não tem re-verificações gratuitas disponíveis."}

    old_score = await store.get_last_scan_score(url)
    report = await _safe_scan(url, full=True, ingest_source="rescan", scanned_by_email=email)
    new_score = report.score.score if report.score else None
    token = _make_scan_token(email, url, full=True)
    payload = _summary_payload(report, full=True)
    payload.update({
        "status": "ok",
        "scan_token": token,
        "comparison": {
            "old_score": old_score,
            "new_score": new_score,
            "delta": ((new_score - old_score)
                      if (old_score is not None and new_score is not None) else None),
            "evolution": _evolution_label(old_score, new_score),
        },
    })
    payload.update(await _full_extras(url, email, None, token))
    return payload


@app.get("/scan/summary")
async def scan_summary(url: str = Query(..., description="URL alvo."),
                       charge_id: Optional[str] = Query(default=None,
                           description="Cobrança paga → resultado COMPLETO (29)."),
                       use_bonus: bool = Query(default=False,
                           description="Consumir o bônus de scan completo (score 100, KL-31)."),
                       request: Request = None) -> dict:
    """Resultado do scan — score + contagens + checks.

    Autorização para o resultado **completo** (29): JWT admin, `charge_id` pago,
    **bônus de score 100** (KL-31, `use_bonus` + crédito no banco) ou scan token
    ``full`` (re-verificação). Sem autorização, devolve o gratuito existente (KL-25)."""
    url = _norm_scan_url(url)
    scanned_by = None
    scan_token = ""
    is_admin = request is not None and _is_admin_request(request)
    full = is_admin
    authorized = is_admin  # pode disparar/mostrar um scan?

    if not is_admin:
        # (1) charge_id pago da MESMA url → completo. Precedência sobre o scan token:
        # depois de pagar, o token GRÁTIS (full=False) ainda fica no sessionStorage e
        # senão mascararia o resultado completo (bug do teste real). (Fix pós-KL-27)
        if charge_id:
            charge = await get_store().get(charge_id)
            if charge and _norm_scan_url(charge.target_url) == url:
                await _refresh_charge(charge)
                if charge.is_paid or _free_access():
                    full = True
                    authorized = True
                    scanned_by = (charge.buyer_email or "").strip() or None

        # (2) senão, scan token. Prioridade dentro do token (KL-31): bônus de score
        # 100 (consome o crédito no banco) → re-verificação (`full`) → básico (15).
        if not authorized:
            scan_token = request.headers.get("x-scan-token", "") if request is not None else ""
            payload = _verify_scan_token(scan_token)
            if payload and _norm_scan_url(payload.get("url", "")) == url:
                email = _clean_scan_email(payload.get("email", "")) or None
                scanned_by = email
                authorized = True
                # Bônus só roda o completo se pedido (botão) E o crédito existir no
                # banco (o token/flag sozinho NÃO basta — consome-se aqui, uso único).
                if (use_bonus and payload.get("bonus") and email
                        and await get_target_store().consume_full_scan_credit(email, url)):
                    full = True
                else:
                    full = bool(payload.get("full"))  # re-verificação → completo

        # (3) usuário logado (KL-51 f3): escanear é ILIMITADO para conta autenticada —
        # sem código de e-mail. O e-mail da conta vira scanned_by (liga o scan à conta,
        # e alimenta o histórico do dashboard). O limite do plano vale só p/ MONITORAR.
        if not authorized and request is not None:
            _user = await auth_users.optional_user(request)
            if _user:
                authorized = True
                scanned_by = (_user.get("email") or "").strip() or None

    # Paywall aberto (KL-51 f2, default): o resultado web mostra os 48 checks com
    # detalhe. `open_all` reflete o flag; `full` só é forçado para o resultado
    # (não muda quem PODE escanear — isso continua exigindo autorização acima).
    open_all = not _paywall_enabled()

    if not authorized:
        # Sem autorização de scan novo: só devolve resultado já existente (o tier
        # segue o paywall — aberto ⇒ completo).
        recent = await get_recent_only(url, full=open_all)
        if recent is None:
            return {"status": "auth_required",
                    "message": "Verifique seu e-mail para escanear este site."}
        data = _summary_payload(recent, full=open_all)
        data.update(await _profile_info(url))
        return data

    # Caminho público gratuito (token, sem admin/charge) — é o que ingere (KL-17).
    # Capturado ANTES de abrir o paywall, senão o `full` mascararia o ingest.
    is_public_free = (not is_admin) and (not full)
    if open_all:
        full = True  # resultado completo (48) para todo scan autorizado

    # Só o caminho público gratuito ingere (KL-17); admin/pago/re-verificação já
    # ingerem no seu próprio fluxo — evita linhas de scan duplicadas (Fix pós-KL-27).
    # Demo (Fix pós-KL-27) é marcado source='demo' para não poluir as métricas reais.
    if _is_demo(email=scanned_by, url=url):
        ingest = "demo"
    elif is_public_free:
        ingest = "public"
    else:
        ingest = None
    report = await _safe_scan(url, full=full, ingest_source=ingest, scanned_by_email=scanned_by)
    data = _summary_payload(report, full=full)
    if full:
        data.update(await _full_extras(url, scanned_by, charge_id, scan_token))
    data.update(await _profile_info(url))
    return data


async def _profile_info(url: str) -> dict:
    """`has_profile` + domínio para o link do perfil público (/site/{dominio}, KL-57).

    `has_profile` é True só se existe `site_profile`, está público (`public_visible`
    não desligado) e o alvo não foi descartado — o mesmo critério de visibilidade de
    `/public/profile/{domain}`. O perfil é gerado em background após o scan (KL-51 f5),
    então na 1ª análise de um site ainda pode não existir (o front mostra "sendo
    gerado")."""
    domain = _norm_domain(url)
    info = {"profile_domain": domain, "has_profile": False}
    try:
        store = get_target_store()
        target = await store.get_target_by_url(_norm_scan_url(url))
        if target and target.get("status") != "descartado":
            prof = await store.get_site_profile(target["id"])
            info["has_profile"] = bool(prof) and prof.get("public_visible") is not False
    except Exception:  # noqa: BLE001 - best-effort; sem perfil o front mostra "sendo gerado"
        pass
    return info


async def _full_extras(url: str, email: Optional[str], charge_id: Optional[str],
                       scan_token: Optional[str]) -> dict:
    """Campos extra do resultado completo (Fix pós-KL-27): links de PDF (com a
    autorização certa) + créditos de re-verificação restantes do comprador."""
    q = f"url={quote(url, safe='')}"
    if charge_id:
        q += f"&charge_id={quote(charge_id, safe='')}"
    elif scan_token:
        q += f"&scan_token={quote(scan_token, safe='')}"
    extras: dict = {"report_urls": {"executive": f"/report/executive?{q}",
                                    "technical": f"/report/technical?{q}"}}
    if email:
        # O próprio e-mail do usuário (pré-preenche a oferta de monitoramento — KL-29).
        extras["contact_email"] = email
        try:
            credit = await get_target_store().get_scan_credit(email)
            extras["rescan_credits"] = int(credit.get("rescan_credits") or 0) if credit else 0
        except Exception:  # noqa: BLE001 - best-effort
            extras["rescan_credits"] = 0
    return extras


# Metadados dos checks para o resultado gratuito (KL-27): nome + tier, sem chamar
# os checks. Os 14 pagos aparecem como `locked` (o visitante vê que existem, não o
# resultado).
_FREE_META = [m for m in CHECK_META if not m["paid"]]
_PAID_META = [m for m in CHECK_META if m["paid"]]


def _technical_content() -> dict:
    """Impacto + correção por check_id (reporter). Import lazy: só o pós-pagamento
    precisa, e evita puxar o WeasyPrint no caminho do resultado gratuito."""
    try:
        from reporter.generator import TECHNICAL
        return TECHNICAL
    except Exception:  # noqa: BLE001 - sem libs nativas → sem detalhe (degrada)
        return {}


def _summary_payload(report: ScanReport, full: bool = False) -> dict:
    """Payload do resultado.

    **Gratuito** (`full=False`): 15 checks com PASS/FAIL (sem detalhe) + os 14 pagos
    **bloqueados** (`status: "locked"`) — NÃO vaza evidência/impacto/correção nem o
    resultado dos pagos. **Completo** (`full=True`, pós-pagamento/re-verificação):
    os 29 com status real e, nos FAILs, `evidence` + `impact` + `fix` (Fix pós-KL-27)."""
    score = report.score
    by_result = {r.check_id: r for r in report.results}
    tech = _technical_content() if full else {}

    def _entry(meta: dict) -> dict:
        cid = meta["check_id"]
        # Pago no tier gratuito é SEMPRE bloqueado (mesmo que o report tenha 29).
        if meta["paid"] and not full:
            return {"check_id": cid, "name": meta["name"], "status": "locked"}
        r = by_result.get(cid)
        status = r.status if r is not None else "INCONCLUSO"
        d = {"check_id": cid, "name": meta["name"], "status": status}
        if full and r is not None and r.status == "FAIL":
            t = tech.get(cid, {})
            d["evidence"] = (r.evidence or None)
            d["impact"] = t.get("impact")
            d["fix"] = t.get("fix")
            if t.get("fix_code"):
                d["fix_code"] = t["fix_code"]
            # Classificação de compliance (KL-34/35) — só no resultado completo/técnico.
            # Carimbada no CheckResult; cai para o mapa por check_id (reports antigos).
            cc = classify_compliance(cid)
            d["owasp"] = getattr(r, "owasp", None) or cc.owasp
            d["cwe"] = getattr(r, "cwe", None) or cc.cwe
            d["lgpd"] = getattr(r, "lgpd", None) or cc.lgpd
        return d

    free_checks = [_entry(m) for m in _FREE_META]
    paid_checks = [_entry(m) for m in _PAID_META]

    return {
        "url": report.url,
        "score": score.score if score else None,
        "semaphore": score.semaphore if score else None,
        "grade_icon": score.grade_icon if score else None,
        # Texto-resumo genérico (categoria de risco), sem detalhar cada falha.
        "risk_summary": get_risk_summary(get_risk_messages(report)),
        "fail_count": score.failed if score else 0,
        "problems": score.failed if score else 0,   # compat com clientes antigos
        "passed": score.passed if score else 0,
        "inconclusive": score.inconclusive if score else 0,
        "free_checks": free_checks,
        "paid_checks": paid_checks,
        "free_count": len(_FREE_META),
        "paid_count": len(_PAID_META),
        "total_checks": len(_FREE_META) + len(_PAID_META),
        "is_full": full,
        "price": PRICE_AMOUNT,
        "price_display": PRICE_DISPLAY,
        "message": (
            "Encaminhe este resumo ao responsável pelo seu site. "
            f"Relatório completo com os {len(_FREE_META) + len(_PAID_META)} pontos "
            "de segurança na versão paga."
        ),
    }


# --------------------------------------------------------------------------- #
# Pagamento (AbacatePay PIX)
# --------------------------------------------------------------------------- #

class PaymentCreateBody(BaseModel):
    url: str
    buyer_email: Optional[str] = None


@app.post("/payment/create")
async def payment_create(body: PaymentCreateBody) -> dict:
    """Cria uma cobrança PIX para liberar o relatório da URL escaneada."""
    buyer_email = (body.buyer_email or "").strip() or None

    # Modo demo (Fix pós-KL-27): cobrança PAID instantânea, sem AbacatePay nem PIX.
    if _is_demo(email=buyer_email, url=body.url):
        charge = Charge(
            charge_id=f"demo_{secrets.token_hex(8)}",
            target_url=body.url, amount_cents=PRICE_AMOUNT,
            status=PaymentStatus.PAID,
            paid_at=datetime.now(timezone.utc).isoformat(),
            buyer_email=buyer_email,
        )
        await get_store().save(charge)
        print(f"[demo] cobrança simulada PAID para {buyer_email} / {body.url}", flush=True)
        return charge.to_public_dict()

    if not _payments_enabled():
        raise HTTPException(status_code=503, detail="Pagamentos não configurados.")

    host = urlparse(body.url if "://" in body.url else "https://" + body.url).hostname or body.url
    amount = PRICE_AMOUNT  # KL-27: preço único R$ 19, independente do setor
    description = f"Relatório de Segurança Klarim - {host}"

    client = AbacatePayClient(_api_key())
    try:
        data = await client.create_pix_charge(amount, description)
    except AbacatePayError as exc:
        raise HTTPException(status_code=502, detail=f"Falha ao criar cobrança: {exc}") from exc

    charge_id = data.get("id")
    if not charge_id:
        raise HTTPException(status_code=502, detail="AbacatePay não retornou o id da cobrança.")

    buyer_email = (body.buyer_email or "").strip() or None
    charge = Charge(
        charge_id=charge_id,
        target_url=body.url,
        amount_cents=amount,
        status=data.get("status", PaymentStatus.PENDING),
        br_code=data.get("brCode"),
        br_code_base64=data.get("brCodeBase64"),
        expires_at=data.get("expiresAt"),
        buyer_email=buyer_email,
        email_status="pending" if buyer_email else None,
    )
    await get_store().save(charge)
    return charge.to_public_dict()


@app.get("/payment/status")
async def payment_status(charge_id: str = Query(..., description="ID da cobrança.")) -> dict:
    """Polling do frontend. Revalida na AbacatePay se ainda pendente."""
    charge = await get_store().get(charge_id)
    if charge is None:
        raise HTTPException(status_code=404, detail="Cobrança não encontrada.")
    await _refresh_charge(charge)
    return {
        "status": charge.status,
        "paid": charge.is_paid,
        "buyer_email": charge.buyer_email,
        "email_status": charge.email_status,
    }


@app.post("/webhooks/abacatepay")
async def webhook_abacatepay(request: Request, webhookSecret: str = Query(default="")) -> dict:
    """Confirmação server-side (redundante ao polling). Valida query secret + HMAC."""
    secret = _webhook_secret()
    raw = await request.body()

    # Camada 1 (obrigatória) — query-string secret que nós controlamos:
    # o endpoint é registrado como .../abacatepay?webhookSecret=<secret>.
    if secret and webhookSecret != secret:
        raise HTTPException(status_code=401, detail="webhookSecret inválido.")

    # Camada 2 (defense-in-depth) — assinatura HMAC-SHA256 no header. A
    # AbacatePay assina com uma chave própria; por isso a verificação só é
    # fatal se explicitamente ativada (ABACATEPAY_HMAC_STRICT=true) com a chave
    # correta em ABACATEPAY_WEBHOOK_SECRET. Caso contrário é apenas registrada.
    sig = request.headers.get("x-webhook-signature", "")
    if secret and sig:
        ok = verify_webhook_signature(secret, raw, sig)
        if not ok and os.environ.get("ABACATEPAY_HMAC_STRICT", "").lower() == "true":
            raise HTTPException(status_code=401, detail="Assinatura HMAC inválida.")

    try:
        payload = json.loads(raw or b"{}")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Payload inválido.") from exc

    event = str(payload.get("event", ""))
    data = payload.get("data") or {}
    charge_id = _extract_charge_id(data)

    if charge_id and (event.endswith(".completed") or event.endswith(".paid")):
        await get_store().mark_status(charge_id, PaymentStatus.PAID)
        charge = await get_store().get(charge_id)
        if charge:
            await _maybe_send_report_email(charge)

    return {"received": True, "charge_id": charge_id, "event": event}


# --------------------------------------------------------------------------- #
# Webhook do Resend — bounces/complaints automáticos (KL-24)
# --------------------------------------------------------------------------- #

def _resend_webhook_secret() -> str:
    return os.environ.get("RESEND_WEBHOOK_SECRET", "")


async def _handle_bounce(store, email: str, message: str) -> None:
    """Bounce permanente: descarta o alvo + adiciona à blocklist + marca no log."""
    email = (email or "").strip().lower()
    if not email:
        return
    await store.discard_target_by_email(email, reason=f"bounced: {message}"[:200])
    await store.block_email(email, reason="bounced")
    print(f"[webhook/resend] bounce {email} — alvo descartado + blocklist", flush=True)


async def _handle_complaint(store, email: str) -> None:
    """Complaint (spam): mais grave que bounce — descadastra + blocklist."""
    email = (email or "").strip().lower()
    if not email:
        return
    await store.mark_unsubscribed(email)
    await store.block_email(email, reason="complained")
    print(f"[webhook/resend] complaint {email} — descadastrado + blocklist", flush=True)


@app.post("/webhooks/resend")
async def webhook_resend(request: Request) -> dict:
    """Recebe eventos do Resend (KL-24): email.bounced / email.complained.

    Valida a assinatura Svix quando `RESEND_WEBHOOK_SECRET` está configurado
    (401 se inválida). Só descarta em bounce **permanente** (soft/transient não
    remove o alvo — pode ser caixa cheia temporária).
    """
    raw = await request.body()
    secret = _resend_webhook_secret()
    if secret and not verify_resend_signature(secret, request.headers, raw):
        raise HTTPException(status_code=401, detail="Assinatura Resend inválida.")

    try:
        payload = json.loads(raw or b"{}")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Payload inválido.") from exc

    evt_type = str(payload.get("type", ""))
    data = payload.get("data") or {}
    email_id = data.get("email_id") or data.get("id")
    recipients = data.get("to") or []
    if isinstance(recipients, str):
        recipients = [recipients]
    store = get_target_store()

    if evt_type == "email.bounced":
        bounce = data.get("bounce") or {}
        btype = str(bounce.get("type", "")).lower()
        # Só é transitório se explicitamente marcado; o resto trata como permanente.
        transient = btype in ("transient", "soft", "temporary", "delivery_delayed")
        message = str(bounce.get("message", "") or bounce.get("subType", ""))
        if not transient:
            if email_id:
                await store.mark_alert_status_by_email_id(email_id, "bounced")
                await store.mark_email_status_by_email_id(email_id, "bounced")  # KL-62
            for addr in recipients:
                await _handle_bounce(store, addr, message)
        else:
            print(f"[webhook/resend] bounce transitório ignorado ({recipients}, {btype})", flush=True)
    elif evt_type == "email.complained":
        if email_id:
            await store.mark_alert_status_by_email_id(email_id, "complained")
            await store.mark_email_status_by_email_id(email_id, "complained")  # KL-62
        for addr in recipients:
            await _handle_complaint(store, addr)
    else:
        print(f"[webhook/resend] evento ignorado: {evt_type}", flush=True)

    return {"received": True, "type": evt_type}


def _extract_charge_id(data: dict) -> Optional[str]:
    """Extrai o id da cobrança do payload do webhook (estrutura pode variar)."""
    if not isinstance(data, dict):
        return None
    for key in ("id", "chargeId"):
        if data.get(key):
            return str(data[key])
    for nested in ("transparent", "charge", "pixQrCode", "billing"):
        obj = data.get(nested)
        if isinstance(obj, dict) and obj.get("id"):
            return str(obj["id"])
    return None


async def _refresh_charge(charge: Charge) -> None:
    """Atualiza o status da cobrança consultando a AbacatePay, se ainda pendente."""
    if charge.is_paid or not _payments_enabled():
        return
    try:
        data = await AbacatePayClient(_api_key()).check_payment(charge.charge_id)
    except AbacatePayError:
        return  # mantém o status atual; o frontend continua o polling
    status = data.get("status", charge.status)
    if status and status != charge.status:
        await get_store().mark_status(charge.charge_id, status)
        charge.status = status
        if charge.is_paid:
            await _maybe_send_report_email(charge)


async def _maybe_send_report_email(charge: Charge) -> None:
    """Pós-pagamento (uma vez): concede o crédito de re-scan (KL-27) e dispara o
    e-mail do relatório completo em background.

    Usa ``report_email_sent`` como marca de idempotência ÚNICA (webhook + polling):
    é setada ANTES de agendar qualquer coisa. Se o envio de e-mail falhar, o cliente
    ainda baixa o PDF no site (fallback) — o erro é apenas registrado.
    """
    if not (charge.is_paid and charge.buyer_email and not charge.report_email_sent):
        return
    # Marca já (idempotência do webhook + polling) e concede o crédito de
    # re-verificação gratuita — independe do e-mail estar configurado.
    await get_store().mark_email_sent(charge.charge_id)
    charge.report_email_sent = True
    _spawn(_grant_rescan_credit(charge.buyer_email))

    if not _email_enabled():
        return
    await get_store().set_email_status(charge.charge_id, "sending")
    charge.email_status = "sending"
    _spawn(_send_report_email_task(charge.charge_id, charge.target_url, charge.buyer_email))


async def _grant_rescan_credit(email: str) -> None:
    """Concede 1 re-verificação gratuita ao comprador (retorno médico — KL-27)."""
    try:
        await get_target_store().grant_rescan_credit(email)
        print(f"[rescan] crédito de re-verificação concedido a {email}", flush=True)
    except Exception as exc:  # noqa: BLE001 - best-effort; não derruba o pagamento
        print(f"[rescan] falha ao conceder crédito a {email}: {exc!r}", flush=True)


# Mantém referência às tasks de background: sem isso o Python pode coletá-las
# (GC) antes de terminarem, matando o envio no meio.
_background_tasks: set = set()


def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def _safe_lead(coro) -> None:
    """Envolve uma coroutine de lead (KL-61) — nunca derruba o fluxo do chamador."""
    try:
        await coro
    except Exception as exc:  # noqa: BLE001
        print(f"[lead] {exc!r}", flush=True)


async def _send_report_email_task(charge_id: str, target_url: str, to_email: str) -> None:
    try:
        # Relatório PAGO → scan COMPLETO (29 checks, KL-27); ingere como 'paid'.
        report = await get_or_scan(target_url, full=True, ingest_source="paid")
        executive = await generate_executive_pdf(report, target_url)
        technical = await generate_technical_pdf(report, target_url)
        score = report.score.score if report.score else 0
        res = await _mailer().send_report(to_email, target_url, score, executive, technical,
                                          email_type="report_delivery", source="payment")  # KL-62
        await get_store().set_email_status(charge_id, "sent")
        print(f"[email] relatório de {charge_id} enviado para {to_email} (id={res.get('email_id')})", flush=True)
    except Exception as exc:  # noqa: BLE001 - falha não deve derrubar nada; há fallback de download
        await get_store().set_email_status(charge_id, "failed")
        print(f"[email] falha ao enviar relatório de {charge_id}: {exc!r}", flush=True)


# --------------------------------------------------------------------------- #
# Relatórios PDF (protegidos por pagamento)
# --------------------------------------------------------------------------- #

def _has_full_scan_token(url: str, scan_token: Optional[str]) -> bool:
    """True se ``scan_token`` é um token de re-verificação (full) válido para a URL
    — autoriza o PDF sem cobrança (KL-27, retorno médico)."""
    if not scan_token:
        return False
    payload = _verify_scan_token(scan_token)
    return bool(payload and payload.get("full")
                and _norm_scan_url(payload.get("url", "")) == _norm_scan_url(url))


@app.get("/report/executive")
async def report_executive(
    url: str = Query(..., description="URL alvo."),
    charge_id: Optional[str] = Query(default=None, description="ID da cobrança paga."),
    scan_token: Optional[str] = Query(default=None, description="Token de re-verificação (full)."),
) -> Response:
    if not _has_full_scan_token(url, scan_token):
        await _require_paid(charge_id)
    report = await _safe_scan(url, full=True)
    pdf = await _safe_pdf(generate_executive_pdf, report, url)
    return _pdf_response(pdf, pdf_filename("executive", url, report.started_at))


@app.get("/report/technical")
async def report_technical(
    url: str = Query(..., description="URL alvo."),
    charge_id: Optional[str] = Query(default=None, description="ID da cobrança paga."),
    scan_token: Optional[str] = Query(default=None, description="Token de re-verificação (full)."),
) -> Response:
    if not _has_full_scan_token(url, scan_token):
        await _require_paid(charge_id)
    report = await _safe_scan(url, full=True)
    pdf = await _safe_pdf(generate_technical_pdf, report, url)
    return _pdf_response(pdf, pdf_filename("technical", url, report.started_at))


class SendReportBody(BaseModel):
    url: str
    email: Optional[str] = None


@app.post("/scan/send-report")
async def scan_send_report(body: SendReportBody, request: Request) -> dict:
    """Envia os 2 PDFs (executivo + técnico) do scan por e-mail (KL-51 f3, fix UX).
    E-mail do corpo ou, se logado e sem e-mail, o da conta. Rate limit 3/e-mail/h.
    O envio roda em background — resposta imediata com o e-mail mascarado."""
    url = _norm_scan_url((body.url or "").strip())
    if not url:
        raise HTTPException(status_code=422, detail="URL inválida.")
    email = (body.email or "").strip().lower()
    if not email and request is not None:
        user = await auth_users.optional_user(request)
        email = ((user or {}).get("email") or "").strip().lower()
    if not email or not _ACCOUNT_EMAIL_RE.match(email):
        raise HTTPException(status_code=422, detail="Informe um e-mail válido.")
    if not _ip_rate_limit(_send_report_attempts, email, 3, 3600):
        raise HTTPException(status_code=429, detail="Muitos envios. Tente novamente mais tarde.")
    if not _email_enabled():
        raise HTTPException(status_code=503, detail="Envio de e-mail indisponível no momento.")

    async def _do():
        try:
            report = await get_or_scan(url, full=True)
            executive = await generate_executive_pdf(report, url)
            technical = await generate_technical_pdf(report, url)
            score = report.score.score if report.score else 0
            res = await _mailer().send_report(email, url, score, executive, technical,
                                              email_type="report_send", source="scan_result")  # KL-62
            print(f"[email] PDFs de {url} enviados para {email} (id={res.get('email_id')})", flush=True)
        except Exception as exc:  # noqa: BLE001 - background; nunca derruba nada
            print(f"[email] falha ao enviar PDFs de {url} para {email}: {exc!r}", flush=True)

    _spawn(_do())
    return {"ok": True, "email": _mask_email(email)}


async def _require_paid(charge_id: Optional[str]) -> None:
    """Exige uma cobrança paga, exceto quando o **paywall está desligado** (KL-51 f2 —
    default; produto freemium, PDF sempre gratuito), em modo dev, ou sem cobrança
    configurada. Com `PAYWALL_ENABLED=true` volta a exigir o pagamento."""
    if not _paywall_enabled() or _free_access():
        return
    if not charge_id:
        raise HTTPException(status_code=402, detail="Pagamento necessário para o relatório completo.")
    charge = await get_store().get(charge_id)
    if charge is None:
        raise HTTPException(status_code=402, detail="Cobrança não encontrada.")
    await _refresh_charge(charge)
    if not charge.is_paid:
        raise HTTPException(status_code=402, detail="Pagamento ainda não confirmado.")


# --------------------------------------------------------------------------- #
# E-mail (Resend)
# --------------------------------------------------------------------------- #

class EmailTestBody(BaseModel):
    to_email: str


class EmailAlertBody(BaseModel):
    to_email: str
    target_url: str


class EmailReportBody(BaseModel):
    to_email: str
    target_url: str
    charge_id: str


# --------------------------------------------------------------------------- #
# Inbox scan@klarim.net (KL-56) — webhook Hostinger Agentic Mail + gestão admin.
# --------------------------------------------------------------------------- #

def _hostinger_token_ok(request: Request) -> bool:
    """Valida o token do webhook (constant-time). Fail-closed: sem
    `HOSTINGER_WEBHOOK_TOKEN` configurado, nada passa. Aceita o token em vários
    lugares (o formato exato do header da Hostinger é confirmado em runtime):
    `Authorization: Bearer`, headers custom comuns, ou `?token=`."""
    expected = os.environ.get("HOSTINGER_WEBHOOK_TOKEN", "")
    if not expected:
        return False
    auth = request.headers.get("authorization", "")
    bearer = auth[7:].strip() if auth[:7].lower() == "bearer " else ""
    candidates = [
        bearer,
        auth.strip(),  # `Authorization: <token>` sem o prefixo Bearer
        request.headers.get("x-webhook-token", ""),
        request.headers.get("x-webhook-secret", ""),
        request.headers.get("webhook-secret", ""),
        request.headers.get("x-hostinger-webhook-token", ""),
        request.headers.get("x-hostinger-token", ""),
        request.headers.get("x-webhook-signature", ""),
        request.headers.get("x-api-key", ""),
        request.query_params.get("token", ""),
        request.query_params.get("secret", ""),
        request.query_params.get("webhookSecret", ""),
    ]
    return any(c and hmac.compare_digest(c, expected) for c in candidates)


def _inbox_dt(val) -> Optional[datetime]:
    """ISO 8601 (com 'Z') → datetime; None se não parsear (received_at é nullable)."""
    if not val or not isinstance(val, str):
        return None
    try:
        return datetime.fromisoformat(val.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_inbox_payload(payload: dict) -> Optional[dict]:
    """Extrai uma mensagem do payload do webhook (KL-56). Suporta o formato da
    AgentMail/Hostinger (`event_type=message.received` + objeto `message`) e formas
    "achatadas" (`from`/`to`/`subject`/`text`/`html`). Retorna o dict pronto para o
    banco, ou None se não for uma mensagem reconhecível (para logar o raw)."""
    if isinstance(payload, list):  # alguns webhooks mandam uma lista de eventos
        payload = payload[0] if payload else {}
    if not isinstance(payload, dict):
        return None
    # Desembrulha wrappers comuns (data/payload/body/email) quando o topo não tem
    # nenhum sinal de mensagem — o formato exato da Hostinger é confirmado em runtime.
    _MSG_KEYS = ("message", "from", "from_address", "sender", "text", "html", "subject")
    if not any(k in payload for k in _MSG_KEYS):
        for wrap in ("data", "payload", "body", "email"):
            if isinstance(payload.get(wrap), dict):
                payload = payload[wrap]
                break
    evt = payload.get("event_type") or payload.get("type") or ""
    # eventos que não são recebimento de mensagem (send/delivery/bounce) → ignora
    if evt and "received" not in evt and "message" not in payload and "from" not in payload:
        return None
    msg = payload.get("message") if isinstance(payload.get("message"), dict) else payload
    raw_from = (msg.get("from") or msg.get("from_address") or msg.get("sender") or "")
    from_name, from_address = parseaddr(raw_from) if raw_from else ("", "")
    from_address = (from_address or raw_from or "").strip()
    to_raw = msg.get("to") or msg.get("to_address") or "scan@klarim.net"
    if isinstance(to_raw, list):
        to_raw = to_raw[0] if to_raw else "scan@klarim.net"
    _, to_addr = parseaddr(str(to_raw))
    text = msg.get("text") or ""
    preview = (msg.get("preview") or msg.get("body_preview") or text or "").strip()[:280]
    body_html = msg.get("html") or msg.get("body_html")
    mid = (msg.get("message_id") or msg.get("id") or payload.get("event_id"))
    if not mid:  # sem id estável → sintetiza p/ o dedup (UNIQUE) funcionar
        seed = f"{from_address}|{msg.get('subject','')}|{msg.get('timestamp','')}"
        mid = "klarim-" + hashlib.sha256(seed.encode("utf-8", "replace")).hexdigest()[:32]
    received = (msg.get("timestamp") or msg.get("date") or msg.get("received_at")
                or msg.get("created_at"))
    if not from_address and not msg.get("subject") and not text and not body_html:
        return None  # nada reconhecível → o chamador loga o raw
    return {
        "message_id": str(mid),
        "from_address": from_address or "(desconhecido)",
        "from_name": (from_name or None),
        "to_address": (to_addr or "scan@klarim.net"),
        "subject": msg.get("subject"),
        "body_preview": preview or None,
        "body_html": body_html,
        "received_at": _inbox_dt(received),
    }


@app.post("/email/webhook")
async def email_webhook(request: Request) -> dict:
    """Recebe e-mails de `scan@klarim.net` (Hostinger Agentic Mail). Auth por token
    próprio (não JWT admin — rota no `_PUBLIC_UNDER_PROTECTED`). Grava em
    `inbox_messages` (dedup por `message_id`)."""
    if not _hostinger_token_ok(request):
        # Diagnóstico (KL-58): loga os NOMES dos headers + chaves de query (nunca os
        # valores/segredos) para descobrir como a Hostinger manda o token — sem isso o
        # 401 é cego. `token_set` confirma se a env está configurada.
        print(f"[inbox] webhook 401 — token_set="
              f"{bool(os.environ.get('HOSTINGER_WEBHOOK_TOKEN'))} "
              f"headers={sorted(request.headers.keys())} "
              f"query={list(request.query_params.keys())}", flush=True)
        return JSONResponse({"detail": "Não autorizado."}, status_code=401)
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001 - corpo inválido; loga o raw e não falha
        raw = (await request.body())[:2000]
        print(f"[inbox] corpo não-JSON: {raw!r}", flush=True)
        return {"ok": True, "parsed": False}

    msg = parse_inbox_payload(payload)
    if not msg:
        # Formato não reconhecido: loga o raw (truncado) p/ adaptar o parser.
        print(f"[inbox] payload não reconhecido: "
              f"{json.dumps(payload, ensure_ascii=False)[:1500]}", flush=True)
        return {"ok": True, "parsed": False}

    try:
        inserted = await get_target_store().insert_inbox_message(msg)
    except Exception as exc:  # noqa: BLE001 - nunca derruba o webhook (Hostinger re-tentaria)
        print(f"[inbox] erro ao gravar ({exc!r})", flush=True)
        return {"ok": True, "stored": False}
    print(f"[inbox] {'nova' if inserted else 'duplicada'} de {msg['from_address']} "
          f"— {msg.get('subject') or '(sem assunto)'}", flush=True)
    return {"ok": True, "stored": bool(inserted)}


_INBOX_BOXES = ("all", "unread", "starred", "archived")


@app.get("/admin/inbox/unread-count")
async def api_inbox_unread_count() -> dict:
    """Contagem de não-lidas (badge do menu). Declarado ANTES de /{msg_id}."""
    try:
        n = await get_target_store().inbox_unread_count()
    except Exception:  # noqa: BLE001
        n = 0
    return {"unread": n}


_INBOX_SOURCES = ("webhook", "contact_form")


@app.get("/admin/inbox")
async def api_inbox_list(
    box: str = Query(default="all"),
    limit: int = Query(default=25, le=200),
    offset: int = Query(default=0, ge=0),
    source: Optional[str] = Query(default=None,
        description="Filtra por origem: webhook (e-mails) | contact_form (formulário)."),
    search: Optional[str] = Query(default=None,
        description="Busca por texto no assunto/remetente/preview."),
) -> dict:
    """Lista mensagens (paginada). `box`: all|unread|starred|archived. `source` (KL-60):
    webhook|contact_form (None = todas). `search`: texto (ILIKE)."""
    if box not in _INBOX_BOXES:
        box = "all"
    src = source if source in _INBOX_SOURCES else None
    q = (search or "").strip() or None
    rows = await get_target_store().list_inbox_messages(box, limit, offset, source=src, search=q)
    return {"count": len(rows), "box": box, "source": src, "search": q, "messages": rows}


@app.get("/admin/inbox/{msg_id}")
async def api_inbox_get(msg_id: int) -> dict:
    """Detalhe (corpo completo). Marca como lida ao abrir."""
    store = get_target_store()
    msg = await store.get_inbox_message(msg_id)
    if msg is None:
        raise HTTPException(status_code=404, detail="Mensagem não encontrada.")
    if not msg.get("is_read"):
        updated = await store.set_inbox_read(msg_id, True)
        if updated:
            msg["is_read"] = True
    return msg


@app.post("/admin/inbox/{msg_id}/read")
async def api_inbox_read(msg_id: int, read: bool = Query(default=True)) -> dict:
    updated = await get_target_store().set_inbox_read(msg_id, read)
    if updated is None:
        raise HTTPException(status_code=404, detail="Mensagem não encontrada.")
    return updated


@app.post("/admin/inbox/{msg_id}/star")
async def api_inbox_star(msg_id: int) -> dict:
    updated = await get_target_store().toggle_inbox_star(msg_id)
    if updated is None:
        raise HTTPException(status_code=404, detail="Mensagem não encontrada.")
    return updated


@app.post("/admin/inbox/{msg_id}/archive")
async def api_inbox_archive(msg_id: int, archived: bool = Query(default=True)) -> dict:
    updated = await get_target_store().set_inbox_archived(msg_id, archived)
    if updated is None:
        raise HTTPException(status_code=404, detail="Mensagem não encontrada.")
    return updated


@app.post("/email/test")
async def email_test(body: EmailTestBody) -> dict:
    """Envia um e-mail de teste (validação da configuração)."""
    _require_email()
    res = await _safe_email(_mailer().send_test(body.to_email))
    return {"sent": True, "email_id": res.get("email_id")}


@app.post("/email/send-alert")
async def email_send_alert(body: EmailAlertBody) -> dict:
    """Escaneia o alvo e envia o alerta gratuito (semáforo)."""
    _require_email()
    report = await _safe_scan(body.target_url)
    s = report.score
    counts = {
        "critica": s.fails_by_severity.get(Severity.CRITICA, 0),
        "alta": s.fails_by_severity.get(Severity.ALTA, 0),
        "media": s.fails_by_severity.get(Severity.MEDIA, 0),
        "baixa": s.fails_by_severity.get(Severity.BAIXA, 0),
    }
    res = await _safe_email(
        _mailer().send_alert(body.to_email, body.target_url, s.score, s.semaphore, s.failed, counts)
    )
    return {"sent": True, "email_id": res.get("email_id"), "score": s.score}


@app.post("/email/send-report")
async def email_send_report(body: EmailReportBody) -> dict:
    """Envia o relatório completo (2 PDFs) — exige cobrança paga."""
    _require_email()
    charge = await get_store().get(body.charge_id)
    if charge is None:
        raise HTTPException(status_code=402, detail="Cobrança não encontrada.")
    await _refresh_charge(charge)
    if not charge.is_paid and not _free_access():
        raise HTTPException(status_code=402, detail="Pagamento não confirmado.")
    report = await _safe_scan(body.target_url)
    executive = await _safe_pdf(generate_executive_pdf, report, body.target_url)
    technical = await _safe_pdf(generate_technical_pdf, report, body.target_url)
    score = report.score.score if report.score else 0
    res = await _safe_email(_mailer().send_report(body.to_email, body.target_url, score, executive, technical))
    return {"sent": True, "email_id": res.get("email_id"), "score": score}


def _require_email() -> None:
    if not _email_enabled():
        raise HTTPException(status_code=503, detail="E-mail não configurado (RESEND_API_KEY).")


async def _safe_email(coro) -> dict:
    try:
        return await coro
    except KlarimMailerError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# --- Formulário de contato do site (público) ------------------------------- #

class ContactBody(BaseModel):
    name: Optional[str] = None
    email: str
    message: str


_CONTACT_RL_MAX = 3          # mensagens por IP por hora
_CONTACT_RL_WINDOW = 3600
_contact_attempts: dict = {}  # ip -> [timestamps monotônicos]
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@app.post("/contact")
async def api_contact(body: ContactBody, request: Request) -> dict:
    """Recebe o formulário de contato do site e encaminha para o time via Resend.
    Público (sem JWT), com sanitização e rate limit de 3/h por IP."""
    email = _sanitize_str((body.email or "").strip(), 200)
    message = _sanitize_str((body.message or "").strip(), 5000)
    name = _sanitize_str((body.name or "").strip(), 200)
    if not email or not _EMAIL_RE.match(email):
        raise HTTPException(status_code=422, detail="E-mail inválido.")
    if not message:
        raise HTTPException(status_code=422, detail="Mensagem obrigatória.")

    # Rate limit por IP (janela deslizante de 1h).
    ip = _client_ip(request)
    now = time.monotonic()
    q = _contact_attempts.setdefault(ip, [])
    cutoff = now - _CONTACT_RL_WINDOW
    while q and q[0] < cutoff:
        q.pop(0)
    if len(q) >= _CONTACT_RL_MAX:
        retry = int(_CONTACT_RL_WINDOW - (now - q[0])) + 1
        raise HTTPException(status_code=429, detail="Muitas mensagens. Tente novamente mais tarde.",
                            headers={"Retry-After": str(retry)})
    q.append(now)
    if len(_contact_attempts) > 5000:
        for k in [k for k, ts in _contact_attempts.items() if not ts or ts[-1] < cutoff]:
            _contact_attempts.pop(k, None)

    # KL-60: grava direto no inbox (fonte de verdade) — a mensagem NUNCA se perde,
    # mesmo se o e-mail via Resend falhar/entrar em loop (mesmo domínio sender/dest).
    from uuid import uuid4
    from html import escape as _html_escape
    try:
        await get_target_store().insert_inbox_message({
            "message_id": f"contact-{uuid4().hex}",
            "from_address": email,
            "from_name": name or None,
            "to_address": "scan@klarim.net",
            "subject": f"Contato via site: {name or email}",
            "body_preview": message[:500],
            "body_html": f"<p>{_html_escape(message).replace(chr(10), '<br>')}</p>",
            "source": "contact_form",
            "received_at": datetime.now(timezone.utc),
        })
    except Exception as exc:  # noqa: BLE001 - se o inbox falhar, ainda tenta o e-mail
        print(f"[contact] falha ao gravar inbox: {exc!r}", flush=True)

    # E-mail via Resend é best-effort (a mensagem já está no inbox do painel).
    if _email_enabled():
        try:
            await _mailer().send_contact(name, email, message)
        except Exception as exc:  # noqa: BLE001
            print(f"[contact] envio de e-mail falhou (msg já no inbox): {exc!r}", flush=True)
    return {"ok": True}


# --------------------------------------------------------------------------- #
# Recuperação de relatórios (token temporário por e-mail)
# --------------------------------------------------------------------------- #

RECOVERY_TTL_HOURS = 24
RECOVERY_RATE_LIMIT = 3  # solicitações por e-mail por hora
_GENERIC_RECOVERY_MSG = (
    "Se existirem relatórios associados a este e-mail, enviaremos um link de acesso."
)


class RecoveryRequestBody(BaseModel):
    email: str


@app.post("/recovery/request")
async def recovery_request(body: RecoveryRequestBody) -> dict:
    """Gera token + envia link — sempre resposta genérica (não revela e-mails)."""
    email = (body.email or "").strip().lower()
    if email and "@" in email and _email_enabled():
        # Rate limit e envio em background para manter a resposta rápida/uniforme.
        _spawn(_recovery_request_task(email))
    return {"message": _GENERIC_RECOVERY_MSG}


async def _recovery_request_task(email: str) -> None:
    try:
        store = get_store()
        if await store.count_recent_recovery_requests(email) >= RECOVERY_RATE_LIMIT:
            print(f"[recovery] rate limit atingido para {mask_email(email)}", flush=True)
            return
        charges = await store.list_paid_charges_by_email(email)
        if not charges:
            return  # nenhum relatório pago -> não envia nada
        token = secrets.token_urlsafe(48)
        expires = (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=RECOVERY_TTL_HOURS))
        await store.create_recovery_token(token, email, expires.isoformat())
        recovery_url = f"https://klarim.net/recuperar/acesso?token={token}"
        res = await _mailer().send_recovery_link(email, recovery_url)
        print(f"[recovery] link enviado para {mask_email(email)} (id={res.get('email_id')})", flush=True)
    except Exception as exc:  # noqa: BLE001 - não deve derrubar nada
        print(f"[recovery] falha para {mask_email(email)}: {exc!r}", flush=True)


@app.get("/recovery/validate")
async def recovery_validate(token: str = Query(...)) -> dict:
    """Valida o token e lista os relatórios pagos do e-mail associado."""
    rt = await get_store().get_valid_recovery_token(token)
    if rt is None:
        return {"valid": False, "error": "Token inválido ou expirado. Solicite um novo link."}
    charges = await get_store().list_paid_charges_by_email(rt.buyer_email)
    reports = [
        {
            "charge_id": c.charge_id,
            "target_url": c.target_url,
            "paid_at": c.paid_at,
            "amount_display": amount_display(c.amount_cents),
        }
        for c in charges
    ]
    return {"valid": True, "email": mask_email(rt.buyer_email), "reports": reports}


@app.get("/recovery/download")
async def recovery_download(
    token: str = Query(...),
    charge_id: str = Query(...),
    type: str = Query("executive"),
) -> Response:
    """Baixa o PDF via token, validando que o charge pertence ao e-mail do token."""
    rt = await get_store().get_valid_recovery_token(token)
    if rt is None:
        raise HTTPException(status_code=401, detail="Token inválido ou expirado.")
    charge = await get_store().get(charge_id)
    if charge is None or charge.buyer_email != rt.buyer_email or not charge.is_paid:
        raise HTTPException(status_code=401, detail="Acesso negado a este relatório.")

    kind = "technical" if type == "technical" else "executive"
    generator = generate_technical_pdf if kind == "technical" else generate_executive_pdf
    report = await _safe_scan(charge.target_url)  # usa o cache do KL-9
    pdf = await _safe_pdf(generator, report, charge.target_url)
    return _pdf_response(pdf, pdf_filename(kind, charge.target_url, report.started_at))


# --------------------------------------------------------------------------- #
# Gestão de alvos / scans (Discovery — KL-11)
# --------------------------------------------------------------------------- #

class TargetAddBody(BaseModel):
    url: str


async def _enqueue_scan(target_id: Optional[int], url: str, source: str = "manual") -> bool:
    """Enfileira {target_id, url, source} na fila de scan (Redis)."""
    if _cache is None or _cache.redis is None:
        return False
    queue = os.environ.get("KLARIM_SCAN_QUEUE", "klarim:scan_queue")
    await _cache.redis.rpush(queue, json.dumps(
        {"target_id": target_id, "url": url, "source": source}))
    return True


@app.get("/targets")
async def api_list_targets(
    status: Optional[str] = Query(default=None),
    platform: Optional[str] = Query(default=None),
    sector: Optional[str] = Query(default=None),
    source: Optional[str] = Query(default=None),
    low_confidence: bool = Query(default=False),
    search: Optional[str] = Query(default=None),
    limit: int = Query(default=50, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict:
    rows = await get_target_store().list_targets(
        status, platform, sector, source, limit, offset,
        low_confidence=low_confidence, search=search)
    return {"count": len(rows), "targets": rows}


@app.get("/targets/stats")
async def api_targets_stats() -> dict:
    return await get_target_store().stats()


@app.get("/admin/dashboard-stats")
async def api_dashboard_stats() -> dict:
    """Totalizadores da home do painel (KL-57): alvos, scans (manual/automatizado),
    perfis/landings, contas, alertas + e-mails não lidos. Protegido pelo JWT admin
    (prefixo `/admin`). Poucas queries agregadas — sem N+1."""
    store = get_target_store()
    summary = await store.dashboard_summary()
    try:
        summary["inbox"] = {"unread": await store.inbox_unread_count()}
    except Exception:  # noqa: BLE001 - inbox é best-effort (tabela pode faltar)
        summary["inbox"] = {"unread": 0}
    return summary


# --------------------------------------------------------------------------- #
# Leads (KL-61) — gestão de leads PQL. Prefixo /leads (admin JWT). classification e
# lead_score são SEMPRE calculados (nunca editados à mão).
# --------------------------------------------------------------------------- #

_LEAD_SORTS = ("lead_score", "last_activity_at", "total_scans", "worst_score")


class LeadUpdateBody(BaseModel):
    tags: Optional[list] = None
    notes: Optional[str] = None
    opted_out: Optional[bool] = None


@app.get("/leads/stats")
async def api_leads_stats() -> dict:
    """Totalizadores de leads: por classificação, com conta/monitoramento, score médio,
    corporativos, multi-scan, top setores, conversão por setor, setores com maior dor,
    taxa PQL, hoje/7 dias (KL-61 + analytics KL-57)."""
    return await get_target_store().lead_stats()


@app.get("/leads/funnel")
async def api_leads_funnel() -> dict:
    """Funil de conversão: e-mail verificado → scan → conta → monitoramento + taxas."""
    return await get_target_store().lead_funnel()


@app.post("/leads/recalculate")
async def api_leads_recalculate() -> dict:
    """Recalcula o score+classificação de TODOS os leads (KL-61). Útil se as regras mudam."""
    n = await get_target_store().recalculate_all_leads()
    return {"ok": True, "recalculated": n}


@app.get("/leads")
async def api_leads_list(
    classification: Optional[str] = Query(default=None),
    sector: Optional[str] = Query(default=None),
    has_account: Optional[bool] = Query(default=None),
    search: Optional[str] = Query(default=None),
    sort: str = Query(default="lead_score"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict:
    """Lista paginada de leads + total + contagem por classificação (KL-61)."""
    cls = classification if classification in ("cold", "warm", "hot", "pql") else None
    srt = sort if sort in _LEAD_SORTS else "lead_score"
    q = (search or "").strip() or None
    return await get_target_store().list_leads(
        classification=cls, sector=(sector or None), has_account=has_account,
        search=q, sort=srt, limit=limit, offset=offset)


@app.get("/leads/{lead_id}")
async def api_lead_get(lead_id: int) -> dict:
    """Detalhe do lead + scans do e-mail + composição do score (KL-61)."""
    lead = await get_target_store().get_lead(lead_id)
    if lead is None:
        raise HTTPException(status_code=404, detail="Lead não encontrado.")
    from api.lead_scoring import score_breakdown
    lead["score_breakdown"] = score_breakdown({
        "total_scans": lead.get("total_scans"),
        "distinct_urls": len(lead.get("urls_scanned") or []),
        "worst_score": lead.get("worst_score"), "has_account": lead.get("has_account"),
        "has_monitoring": lead.get("has_monitoring"),
        "is_corporate_email": lead.get("is_corporate_email"),
        "last_activity_at": lead.get("last_activity_at")})
    return lead


@app.patch("/leads/{lead_id}")
async def api_lead_update(lead_id: int, body: LeadUpdateBody) -> dict:
    """Atualiza campos MANUAIS (tags/notes/opted_out). NÃO permite alterar lead_score
    nem classification (são sempre calculados). Recalcula o score depois."""
    tags = None
    if body.tags is not None:
        tags = [_sanitize_str(str(t), 60) for t in body.tags][:20]
    notes = _sanitize_str(body.notes, 5000) if body.notes is not None else None
    ok = await get_target_store().update_lead(lead_id, tags=tags, notes=notes,
                                              opted_out=body.opted_out)
    if not ok:
        raise HTTPException(status_code=404, detail="Lead não encontrado.")
    return {"ok": True}


@app.get("/targets/{target_id}")
async def api_get_target(target_id: int) -> dict:
    store = get_target_store()
    target = await store.get_target(target_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Alvo não encontrado.")
    # KL-50: anexa o perfil comercial, se houver.
    try:
        target["profile"] = await store.get_site_profile(target_id)
    except Exception:  # noqa: BLE001
        target["profile"] = None
    # KL-55: anexa as classificações CNAE multi-setor.
    try:
        target["classifications"] = await store.get_target_classifications(target_id)
    except Exception:  # noqa: BLE001
        target["classifications"] = []
    return target


@app.get("/targets/{target_id}/classifications")
async def api_target_classifications(target_id: int) -> dict:
    """Classificações CNAE multi-setor de um alvo (KL-55), ordenadas por rank."""
    rows = await get_target_store().get_target_classifications(target_id)
    return {"target_id": target_id, "classifications": rows}


@app.get("/targets/{target_id}/profile")
async def api_target_profile(target_id: int) -> dict:
    """Perfil comercial extraído do site (KL-50)."""
    profile = await get_target_store().get_site_profile(target_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Perfil não encontrado.")
    return profile


@app.get("/targets/{target_id}/payments")
async def api_target_payments(target_id: int) -> dict:
    """Pagamentos vinculados a um alvo (mesma URL) — KL-17."""
    target = await get_target_store().get_target(target_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Alvo não encontrado.")
    charges = await get_store().list_charges_by_url(target["url"])
    return {"count": len(charges), "payments": [_payment_row(c, target_id) for c in charges]}


@app.post("/targets/add")
async def api_targets_add(body: TargetAddBody) -> dict:
    url = normalize_url(body.url)
    domain = registrable_domain(domain_of(url))
    # Classificação inicial pelo domínio; o scan enfileirado refina via HTML.
    sector, tier, confidence = classify_sector(None, url)
    tid = await get_target_store().register_target(
        url, domain, "unknown", sector, tier, None,
        source="manual", status="discovered", confidence=confidence)
    enq = await _enqueue_scan(tid, url, source="manual")
    return {"target_id": tid, "url": url, "domain": domain, "enqueued": enq}


@app.post("/targets/{target_id}/scan")
async def api_targets_scan(target_id: int) -> dict:
    target = await get_target_store().get_target(target_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Alvo não encontrado.")
    enq = await _enqueue_scan(target_id, target["url"], source="admin")
    return {"target_id": target_id, "url": target["url"], "enqueued": enq}


@app.post("/targets/{target_id}/discard")
async def api_targets_discard(target_id: int) -> dict:
    """Marca um alvo como 'descartado' (sai dos ciclos de scan/alerta/re-scan)."""
    store = get_target_store()
    if await store.get_target(target_id) is None:
        raise HTTPException(status_code=404, detail="Alvo não encontrado.")
    await store.update_status(target_id, "descartado")
    return {"target_id": target_id, "status": "descartado"}


@app.get("/scans")
async def api_list_scans(
    target_id: Optional[int] = Query(default=None),
    score_min: Optional[int] = Query(default=None),
    score_max: Optional[int] = Query(default=None),
    source: Optional[str] = Query(default=None),
    limit: int = Query(default=50, le=500),
    offset: int = Query(default=0, ge=0),  # KL-56: paginação real da página Scans
    from_date: Optional[str] = Query(default=None, description="YYYY-MM-DD (inclusive)"),
    to_date: Optional[str] = Query(default=None, description="YYYY-MM-DD (inclusive)"),
    distinct_url: bool = Query(default=False,
        description="Só o scan mais recente de cada URL (atividade recente)."),
) -> dict:
    rows = await get_target_store().list_scans(
        target_id, score_min, score_max, source, limit, distinct_url=distinct_url,
        offset=offset, from_date=from_date, to_date=to_date)
    return {"count": len(rows), "scans": rows}


# Rotas específicas ANTES de /scans/{scan_id} (senão "stats"/"daily" viram id).
@app.get("/scans/stats")
async def api_scans_stats() -> dict:
    return await get_target_store().scan_stats()


@app.get("/scans/daily")
async def api_scans_daily(days: int = Query(default=30, ge=1, le=365)) -> dict:
    return {"series": await get_target_store().scans_daily(days)}


@app.get("/scans/{scan_id}")
async def api_get_scan(scan_id: int) -> dict:
    scan = await get_target_store().get_scan(scan_id)
    if scan is None:
        raise HTTPException(status_code=404, detail="Scan não encontrado.")
    return scan


@app.get("/scans/{scan_id}/report/{kind}")
async def api_scan_report(scan_id: int, kind: str) -> Response:
    """PDF (executivo/técnico) de um scan — via painel admin, sem gating de pagamento."""
    if kind not in ("executive", "technical"):
        raise HTTPException(status_code=404, detail="Tipo inválido.")
    scan = await get_target_store().get_scan(scan_id)
    if scan is None:
        raise HTTPException(status_code=404, detail="Scan não encontrado.")
    url = scan["url"]
    report = await get_or_scan(url)
    fn = generate_executive_pdf if kind == "executive" else generate_technical_pdf
    pdf = await _safe_pdf(fn, report, url)
    return _pdf_response(pdf, pdf_filename(kind, url, report.started_at))


# --------------------------------------------------------------------------- #
# Alertas (Alert Worker — KL-12)
# --------------------------------------------------------------------------- #

@app.get("/alerts")
async def api_list_alerts(
    target_id: Optional[int] = Query(default=None),
    limit: int = Query(default=50, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict:
    rows = await get_target_store().list_alerts(target_id, limit, offset)
    return {"count": len(rows), "alerts": rows}


@app.get("/alerts/stats")
async def api_alerts_stats() -> dict:
    return await get_target_store().alert_stats()


@app.get("/alerts/daily")
async def api_alerts_daily(days: int = Query(default=30, ge=1, le=365)) -> dict:
    return {"series": await get_target_store().alerts_daily(days)}


@app.get("/payments/list")
async def api_payments_list(
    status: Optional[str] = Query(default=None),
    limit: int = Query(default=50, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict:
    charges = await get_store().list_charges(status, limit, offset)
    # KL-17: vincula cada pagamento ao alvo (mesma URL), se existir.
    id_by_url = await get_target_store().map_urls_to_target_ids([c.target_url for c in charges])
    return {"count": len(charges),
            "payments": [_payment_row(c, id_by_url.get(c.target_url)) for c in charges]}


@app.get("/payments/stats")
async def api_payments_stats() -> dict:
    return await get_store().payment_stats()


@app.get("/discovery/status")
async def api_discovery_status() -> dict:
    """Estado do Discovery Worker (Certstream) — publicado no Redis pelo worker."""
    raw = None
    if _cache is not None and _cache.redis is not None:
        try:
            key = os.environ.get("KLARIM_DISCOVERY_STATUS_KEY", "discovery:status")
            raw = await _cache.redis.get(key)
        except Exception:  # noqa: BLE001 - Redis fora do ar
            raw = None
    if raw:
        status = json.loads(raw)
    else:
        status = {"source": {"connected": False, "buffer_size": 0, "total_seen": 0,
                             "total_matched": 0, "last_event_at": None},
                  "source_kind": "ct_poller", "cycles_completed": 0,
                  "last_cycle_at": None, "next_cycle_at": None}
    try:
        status["targets_discovered_today"] = await get_target_store().count_discovered_today()
    except Exception:  # noqa: BLE001 - DB opcional
        status["targets_discovered_today"] = None
    return status


# --------------------------------------------------------------------------- #
# Dashboard operacional (KL-16): status dos workers + health + atividade
# --------------------------------------------------------------------------- #

async def _redis_json(key: str):
    if _cache is None or _cache.redis is None:
        return None
    try:
        raw = await _cache.redis.get(key)
        return json.loads(raw) if raw else None
    except Exception:  # noqa: BLE001
        return None


@app.get("/system/status")
async def api_system_status() -> dict:
    """Status dos workers + health das dependências + métricas de e-mail (KL-16)."""
    store = get_target_store()
    redis = _cache.redis if _cache is not None else None
    disc = await _redis_json(os.environ.get("KLARIM_DISCOVERY_STATUS_KEY", "discovery:status"))
    alert_hb = await _redis_json("worker:alert:status")
    rescan_hb = await _redis_json("worker:rescan:status")
    scan_hb = await _redis_json("worker:scan:status")

    a_stats = await store.alert_stats()
    r_stats = await store.rescan_stats()
    scan_today = await store.scan_today_stats()
    # Fix de divergência: o `last_scan_at` vem do BANCO (MAX(scans.scanned_at)) — a mesma
    # fonte da página Scans do painel —, não do heartbeat do worker (que avança além do
    # banco: scans que não persistem, tempo do enrich pós-scan). Assim MCP == painel.
    db_last_scan = None
    try:
        db_last_scan = await store.last_scan_at()
    except Exception:  # noqa: BLE001 - best-effort
        db_last_scan = None
    eligible = await store.count_rescan_eligible()
    discovered_today = await store.count_discovered_today()
    email = await store.email_metrics()
    # Cota mensal (KL-23 / Resend Pro) — substitui o antigo teto diário.
    monthly_limit = int(os.environ.get("ALERT_MONTHLY_LIMIT", "45000"))
    sent_month = await store.count_proactive_emails_this_month()
    backlog = await store.count_eligible_targets_for_alert()
    usage_pct = round(100.0 * sent_month / monthly_limit, 1) if monthly_limit else 0.0

    queue_size = None
    if redis is not None:
        try:
            queue_size = await redis.llen(os.environ.get("KLARIM_SCAN_QUEUE", "klarim:scan_queue"))
        except Exception:  # noqa: BLE001
            queue_size = None

    deps = await health_checks.run_all(redis)
    ctrl = worker_control.load()  # KL-32: estado de pausa por worker

    def _c(w: str) -> dict:
        n = ctrl.get(w, {})
        return {"enabled": n.get("enabled", True),
                "paused_at": n.get("paused_at"), "paused_by": n.get("paused_by")}

    return {
        "workers": {
            "discovery": {
                "alive": disc is not None,
                **_c("discovery"),
                "last_cycle_at": (disc or {}).get("last_cycle_at"),
                "next_cycle_at": (disc or {}).get("next_cycle_at"),
                "cycles_completed": (disc or {}).get("cycles_completed", 0),
                "source": (disc or {}).get("source"),
                "targets_discovered_today": discovered_today,
            },
            "alert": {
                "alive": alert_hb is not None,
                **_c("alert"),
                "last_cycle_at": (alert_hb or {}).get("last_cycle_at"),
                "next_cycle_at": (alert_hb or {}).get("next_cycle_at"),
                "sent_today": a_stats.get("today", 0),
                "sent_week": a_stats.get("week", 0),
                "sent_month": sent_month,
                "monthly_limit": monthly_limit,
                "backlog": backlog,
                "last_cycle_stats": (alert_hb or {}).get("last_cycle_stats"),
            },
            "rescan": {
                "alive": rescan_hb is not None,
                **_c("rescan"),
                "last_cycle_at": (rescan_hb or {}).get("last_cycle_at"),
                "next_cycle_at": (rescan_hb or {}).get("next_cycle_at"),
                "rescanned_today": r_stats.get("today", 0),
                "eligible": eligible,
                "last_cycle_stats": (rescan_hb or {}).get("last_cycle_stats"),
            },
            "scan": {
                "alive": scan_hb is not None,
                **_c("scan"),
                "queue_size": queue_size,
                "completed_today": scan_today["count"],
                "avg_score_today": scan_today["avg_score"],
                # `last_scan_at` do BANCO (bate com o painel); a hora do heartbeat do
                # worker fica como `worker_last_activity` (liveness) para transparência.
                "last_scan_at": db_last_scan,
                "worker_last_activity": (scan_hb or {}).get("last_scan_at"),
            },
        },
        "dependencies": deps,
        "email_metrics": {
            "sent_today": email["sent_today"],
            "sent_week": email["sent_week"],
            "sent_month": sent_month,
            "monthly_limit": monthly_limit,
            "monthly_usage_pct": f"{usage_pct}%",
            "backlog": backlog,
        },
    }


# --------------------------------------------------------------------------- #
# Controle dos workers (KL-32) — pausa/retoma via painel (mesmo controle do MCP)
# --------------------------------------------------------------------------- #

class WorkerActionBody(BaseModel):
    worker: str  # discovery | alert | rescan | scan | all


@app.post("/admin/workers/pause")
async def api_workers_pause(body: WorkerActionBody) -> dict:
    try:
        data = worker_control.pause(body.worker, by="painel")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"paused": body.worker, "control": data}


@app.post("/admin/workers/resume")
async def api_workers_resume(body: WorkerActionBody) -> dict:
    try:
        data = worker_control.resume(body.worker)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"resumed": body.worker, "control": data}


@app.get("/admin/workers/control")
async def api_workers_control() -> dict:
    return {"control": worker_control.load(), "workers": worker_control.WORKERS}


@app.get("/system/activity")
async def api_system_activity(limit: int = Query(default=50, le=200)) -> dict:
    """Timeline das últimas ações do sistema (scans, alertas, re-scans, pagamentos)."""
    store = get_target_store()
    per = min(limit, 50)
    events = []

    for a in await store.list_alerts(limit=per):
        events.append({"type": "alert", "at": str(a.get("sent_at")),
                       "message": f"alerta enviado para {a.get('contact_email')} "
                                  f"({a.get('url') or 'alvo #' + str(a.get('target_id'))}, score {a.get('score')})"})
    for r in await store.list_rescans(limit=per):
        events.append({"type": "rescan", "at": str(r.get("rescanned_at")),
                       "message": f"re-scan {r.get('url') or 'alvo #' + str(r.get('target_id'))}: "
                                  f"{r.get('old_score')}→{r.get('new_score')} ({r.get('evolution')})"})
    for s in await store.list_scans(limit=per):
        events.append({"type": "scan", "at": str(s.get("scanned_at")),
                       "message": f"scan {s.get('url')} → {s.get('score')}/100 {s.get('semaphore')} "
                                  f"[{s.get('source')}]"})
    # KL-62: e-mails do email_log (discrimina tipo + destino + status/bloqueio).
    try:
        for e in await store.list_email_activity(limit=per):
            st = e.get("status")
            label = EMAIL_TYPES.get(e.get("email_type"), e.get("email_type") or "email")
            if st == "blocked":
                events.append({"type": "email_blocked", "at": str(e.get("sent_at")),
                               "message": f"{label} → {e.get('to_email')} "
                                          f"(bloqueado: {e.get('blocked_reason') or 'blocklist'})"})
            else:
                extra = "" if st == "sent" else f" ({st})"
                events.append({"type": "email", "at": str(e.get("sent_at")),
                               "message": f"{label} → {e.get('to_email')}{extra}"})
    except Exception:  # noqa: BLE001 - best-effort; não derruba a timeline
        pass
    try:
        for c in await get_store().list_charges(limit=per):
            if c.status in PaymentStatus.PAID_STATES:
                events.append({"type": "payment", "at": str(c.paid_at or c.created_at),
                               "message": f"pagamento confirmado: {amount_display(c.amount_cents)} "
                                          f"({c.target_url})"})
    except Exception:  # noqa: BLE001
        pass

    events = [e for e in events if e["at"] and e["at"] != "None"]
    events.sort(key=lambda e: e["at"], reverse=True)
    return {"count": min(len(events), limit), "activity": events[:limit]}


def _bounce_status(rate: float) -> str:
    """Semáforo de bounce (KL-24): ok < 2% · warning 2–4% · critical > 4%."""
    if rate > 4.0:
        return "critical"
    if rate >= 2.0:
        return "warning"
    return "ok"


@app.get("/system/email-health")
async def api_system_email_health() -> dict:
    """Saúde de e-mail (KL-24): bounce rate + status de risco + blocklist.

    `bounced`/`complained` refletem o que o webhook/backfill do Resend marcou no
    `alert_log`. Não distinguimos bounce transitório (não descartamos por isso).
    """
    h = await get_target_store().email_health()
    total = h["total"]
    bounced, complained = h["bounced"], h["complained"]
    rate = round(100.0 * bounced / total, 2) if total else 0.0
    return {
        "total_sent": total,
        "delivered": max(total - bounced - complained, 0),
        "bounced_permanent": bounced,
        "bounced_transient": 0,  # não rastreado (bounces transitórios não descartam)
        "complained": complained,
        "bounce_rate": rate,
        "bounce_status": _bounce_status(rate),
        "blocklist_size": h["blocklist"],
    }


@app.get("/email/log")
async def api_email_log(
    email_type: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    to_email: Optional[str] = Query(default=None),
    source: Optional[str] = Query(default=None),
    limit: int = Query(default=25, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict:
    """Log unificado de e-mails (KL-62, JWT admin) — auditoria de TODOS os envios.

    Filtros: email_type (alert/profile_view/verification_code/…), status
    (sent/bounced/failed/blocked), to_email (parcial), source. Retorna também a
    legenda `types` (email_type → rótulo) para a UI."""
    et = email_type if email_type in EMAIL_TYPES else None
    st = status if status in ("sent", "bounced", "failed", "blocked", "complained") else None
    data = await get_target_store().list_email_log(
        email_type=et, status=st, to_email=(to_email or None),
        source=(source or None), limit=limit, offset=offset)
    return {**data, "types": EMAIL_TYPES}


# --------------------------------------------------------------------------- #
# Tracking da jornada do lead (KL-21): eventos públicos + analytics (JWT)
# --------------------------------------------------------------------------- #

_KNOWN_EVENTS = {
    "page_view", "scan_started", "scan_completed", "result_viewed", "cta_clicked",
    "payment_created", "payment_completed", "report_downloaded", "email_link_clicked",
    # KL-25 — verificação de e-mail antes do scan público
    "code_requested", "code_verified", "code_failed", "scan_limit_reached",
    # KL-29/KL-31 — monitoramento + bônus de score 100
    "score100_full_scan_started", "score100_full_scan_completed",
    "score100_monitoring_offered", "score100_monitoring_accepted",
    # KL-51 f4 — perfis públicos SEO
    "profile_view",
    # KL-57 — perfil no resultado do scan + churn de conta
    "profile_link_clicked", "password_changed", "account_deleted",
    # KL-42 — score social: widget + card + ranking + compartilhamento
    "widget_loaded", "widget_clicked", "widget_copied",
    "card_downloaded", "share_clicked", "ranking_viewed",
}
_EVENT_RL_MAX = 100          # eventos/minuto por sessão
_event_rl: dict = {}         # session_id -> lista de timestamps (janela de 60s)

_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _sanitize_str(value, max_length: int = 500):
    """Remove tags HTML e esquemas perigosos; limita o tamanho. Anti stored-XSS."""
    if not isinstance(value, str):
        return value
    clean = _HTML_TAG_RE.sub("", value)
    for bad in ("javascript:", "data:", "vbscript:"):
        clean = clean.replace(bad, "")
    return clean[:max_length]


def _sanitize_metadata(md, _depth: int = 0):
    """Sanitiza recursivamente as strings de um dict de metadata (profundidade ≤ 4)."""
    if _depth > 4 or not isinstance(md, dict):
        return _sanitize_str(md) if isinstance(md, str) else md
    out = {}
    for k, v in list(md.items())[:50]:  # teto de chaves — payload não infla o banco
        key = _sanitize_str(str(k), 100)
        if isinstance(v, dict):
            out[key] = _sanitize_metadata(v, _depth + 1)
        elif isinstance(v, list):
            out[key] = [_sanitize_str(x) if isinstance(x, str) else x for x in v[:50]]
        else:
            out[key] = _sanitize_str(v)
    return out


def _event_rate_ok(session_id: str) -> bool:
    now = time.monotonic()
    q = _event_rl.setdefault(session_id, [])
    cutoff = now - 60
    while q and q[0] < cutoff:
        q.pop(0)
    if len(q) >= _EVENT_RL_MAX:
        return False
    q.append(now)
    # Limpeza oportunista para o dict não crescer sem limite.
    if len(_event_rl) > 5000:
        for sid in [s for s, ts in _event_rl.items() if not ts or ts[-1] < cutoff]:
            _event_rl.pop(sid, None)
    return True


def _target_id_from_utm(utm_content: Optional[str], given: Optional[int]) -> Optional[int]:
    if given is not None:
        return given
    if utm_content and utm_content.startswith("target_"):
        try:
            return int(utm_content[len("target_"):])
        except ValueError:
            return None
    return None


class EventBody(BaseModel):
    event_type: str
    session_id: str
    target_url: Optional[str] = None
    target_id: Optional[int] = None
    page_url: Optional[str] = None
    referrer: Optional[str] = None
    utm_source: Optional[str] = None
    utm_medium: Optional[str] = None
    utm_campaign: Optional[str] = None
    utm_content: Optional[str] = None
    metadata: Optional[dict] = None


@app.post("/events")
async def api_track_event(body: EventBody) -> dict:
    """Tracking público (sem JWT) — fire-and-forget, gravação em background (KL-21)."""
    if body.event_type not in _KNOWN_EVENTS or not body.session_id:
        return {"ok": True, "recorded": False}
    if not _event_rate_ok(body.session_id):
        return {"ok": True, "recorded": False, "rate_limited": True}
    # Sanitização anti stored-XSS: o dashboard admin renderiza esses campos.
    body.page_url = _sanitize_str(body.page_url)
    body.target_url = _sanitize_str(body.target_url)
    body.referrer = _sanitize_str(body.referrer)
    body.utm_source = _sanitize_str(body.utm_source, 100)
    body.utm_medium = _sanitize_str(body.utm_medium, 100)
    body.utm_campaign = _sanitize_str(body.utm_campaign, 100)
    body.utm_content = _sanitize_str(body.utm_content, 200)
    body.metadata = _sanitize_metadata(body.metadata)
    target_id = _target_id_from_utm(body.utm_content, body.target_id)
    _spawn(_log_event_bg(body, target_id))
    return {"ok": True}


async def _log_event_bg(body: EventBody, target_id: Optional[int]) -> None:
    try:
        await get_target_store().log_event(
            body.event_type, body.session_id, target_url=body.target_url, target_id=target_id,
            page_url=body.page_url, referrer=body.referrer, utm_source=body.utm_source,
            utm_medium=body.utm_medium, utm_campaign=body.utm_campaign,
            utm_content=body.utm_content, metadata=body.metadata)
    except Exception as exc:  # noqa: BLE001 - tracking nunca derruba nada
        print(f"[events] falha ao gravar {body.event_type} ({exc!r})", flush=True)


@app.get("/analytics/funnel")
async def api_analytics_funnel(period: str = Query(default="7d")) -> dict:
    return await get_target_store().analytics_funnel(period)


@app.get("/analytics/public-scans")
async def api_analytics_public_scans() -> dict:
    """Funil do scan público verificado (KL-25): códigos enviados, verificados,
    e-mails distintos, scans gratuitos usados, scans públicos."""
    return await get_target_store().public_scan_stats()


@app.get("/analytics/abandoned")
async def api_analytics_abandoned(period: str = Query(default="7d")) -> dict:
    rows = await get_target_store().analytics_abandoned(period)
    return {"count": len(rows), "abandoned": rows}


@app.get("/analytics/campaigns")
async def api_analytics_campaigns(period: str = Query(default="7d")) -> dict:
    rows = await get_target_store().analytics_campaigns(period)
    return {"count": len(rows), "campaigns": rows}


@app.get("/analytics/pages")
async def api_analytics_pages(period: str = Query(default="7d")) -> dict:
    rows = await get_target_store().analytics_pages(period)
    return {"count": len(rows), "pages": rows}


@app.get("/analytics/events")
async def api_analytics_events(limit: int = Query(default=50, le=500),
                               event_type: Optional[str] = Query(default=None,
                                   description="Filtra por tipo (ex.: profile_view).")) -> dict:
    rows = await get_target_store().analytics_events(limit, event_type=event_type)
    return {"count": len(rows), "events": rows}


@app.get("/config")
async def api_config() -> dict:
    """Parâmetros operacionais em uso (somente leitura — sem segredos)."""
    def _i(name: str, default: str) -> int:
        try:
            return int(os.environ.get(name, default))
        except ValueError:
            return int(default)

    # ALERT_INTERVAL_MINUTES tem precedência; senão deriva de ALERT_INTERVAL_HOURS.
    alert_interval_minutes = _i("ALERT_INTERVAL_MINUTES", "0") or _i("ALERT_INTERVAL_HOURS", "1") * 60

    return {
        "discovery_batch_size": _i("DISCOVERY_BATCH_SIZE", "100"),
        "discovery_interval_minutes": _i("DISCOVERY_INTERVAL_MINUTES", "30"),
        "alert_interval_minutes": alert_interval_minutes,
        # Batch sending (KL-23 / Resend Pro): substitui os antigos tetos hora/dia.
        "alert_batch_size": _i("ALERT_BATCH_SIZE", "50"),
        "alert_batches_per_cycle": _i("ALERT_BATCHES_PER_CYCLE", "4"),
        "alert_batch_pause": _i("ALERT_BATCH_PAUSE", "10"),
        "alert_monthly_limit": _i("ALERT_MONTHLY_LIMIT", "45000"),
        "rescan_interval_hours": _i("RESCAN_INTERVAL_HOURS", "24"),
        "rescan_age_days": _i("RESCAN_AGE_DAYS", "30"),
        "worker_max_scans_per_hour": _i("WORKER_MAX_SCANS_PER_HOUR", "50"),
    }


# --------------------------------------------------------------------------- #
# Fluxo admin integrado (KL-17): escanear + registrar + enviar / reenviar
# --------------------------------------------------------------------------- #

class ScanAndReportBody(BaseModel):
    url: str
    send_email: bool = False
    email_to: Optional[str] = None
    email_type: str = "alert"  # 'alert' | 'report'


class ResendAlertBody(BaseModel):
    target_id: int


class SendReportBody(BaseModel):
    target_id: int
    email_to: Optional[str] = None


class ResendPaymentBody(BaseModel):
    charge_id: str


def _severity_counts(report: ScanReport) -> dict:
    sev = report.score.fails_by_severity if report.score else {}
    return {
        "critica": sev.get(Severity.CRITICA, 0), "alta": sev.get(Severity.ALTA, 0),
        "media": sev.get(Severity.MEDIA, 0), "baixa": sev.get(Severity.BAIXA, 0),
    }


async def _send_alert_to(url: str, report: ScanReport, to_email: str,
                         target_id: Optional[int] = None) -> Optional[str]:
    s = report.score
    res = await _mailer().send_alert(
        to_email, url, s.score if s else 0, s.semaphore if s else "vermelho",
        s.failed if s else 0, _severity_counts(report),
        risk_messages=get_risk_messages(report), target_id=target_id,
        email_type="admin_alert", source="admin")  # KL-62: tag do canal admin
    return res.get("email_id")


async def _send_report_to(url: str, report: ScanReport, to_email: str) -> Optional[str]:
    executive = await _safe_pdf(generate_executive_pdf, report, url)
    technical = await _safe_pdf(generate_technical_pdf, report, url)
    score = report.score.score if report.score else 0
    res = await _mailer().send_report(to_email, url, score, executive, technical,
                                      email_type="admin_report", source="admin")  # KL-62
    return res.get("email_id")


@app.post("/admin/scan-and-report")
async def api_admin_scan_and_report(body: ScanAndReportBody) -> dict:
    """Escaneia (cache ou fresh) → registra no banco (source='admin') → opcionalmente
    envia alerta/relatório. Tudo num request (JWT)."""
    url = normalize_url(body.url)
    report = await _safe_scan(url)  # sem auto-ingest; ingerimos abaixo com os ids
    meta = await ingest_scan(get_target_store(), url, report, source="admin")
    # KL-51 f5: gera o perfil completo (profiler + IA + CNAE) em background.
    if meta.get("target_id"):
        from scanner.enrichment import enrich_profile
        _spawn(enrich_profile(get_target_store(), meta["target_id"], url,
                              report.score.score if report.score else None))
    s = report.score
    risk_messages = get_risk_messages(report)
    result = {
        "target_id": meta["target_id"], "scan_id": meta["scan_id"], "url": url,
        "score": s.score if s else None, "semaphore": s.semaphore if s else None,
        "checks": report.to_dict().get("results", []),
        "pass_count": s.passed if s else 0, "fail_count": s.failed if s else 0,
        "inconclusive": s.inconclusive if s else 0,
        "severity_counts": _severity_counts(report),
        "risk_summary": get_risk_summary(risk_messages),
        "risk_messages": risk_messages,
        "platform": meta["platform"], "sector": meta["sector"],
        "contact_email": meta["contact_email"],
        "email_sent": False, "email_id": None,
    }
    if body.send_email:
        to_email = (body.email_to or meta["contact_email"] or "").strip()
        if not to_email:
            result["email_error"] = "Sem e-mail de destino."
        elif not _email_enabled():
            result["email_error"] = "Envio de e-mail não configurado."
        else:
            try:
                if body.email_type == "report":
                    result["email_id"] = await _send_report_to(url, report, to_email)
                else:
                    result["email_id"] = await _send_alert_to(url, report, to_email, meta["target_id"])
                result["email_sent"] = True
                result["email_to"] = to_email
            except (KlarimMailerError, HTTPException) as exc:
                result["email_error"] = str(getattr(exc, "detail", exc))
    return result


@app.post("/admin/resend-alert")
async def api_admin_resend_alert(body: ResendAlertBody) -> dict:
    """Reenvia o alerta de um alvo (ignora throttle/janela — ação manual)."""
    _require_email()
    store = get_target_store()
    target = await store.get_target(body.target_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Alvo não encontrado.")
    if not target.get("contact_email"):
        raise HTTPException(status_code=400, detail="Alvo sem e-mail de contato.")
    print(f"[alert] reenvio manual (bypass cota mensal) alvo {body.target_id} "
          f"{target.get('url')}", flush=True)
    try:
        email_id = await send_alert_for_target(store, _mailer(), target)
    except KlarimMailerError as exc:
        raise HTTPException(status_code=502, detail=f"Falha no envio: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"target_id": body.target_id, "email": target["contact_email"],
            "email_id": email_id, "sent": True}


@app.post("/admin/send-report")
async def api_admin_send_report(body: SendReportBody) -> dict:
    """Envia os 2 PDFs para o e-mail do contato (ou `email_to`)."""
    _require_email()
    store = get_target_store()
    target = await store.get_target(body.target_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Alvo não encontrado.")
    to_email = (body.email_to or target.get("contact_email") or "").strip()
    if not to_email:
        raise HTTPException(status_code=400, detail="Sem e-mail de destino.")
    report = await _safe_scan(target["url"])
    try:
        email_id = await _send_report_to(target["url"], report, to_email)
    except KlarimMailerError as exc:
        raise HTTPException(status_code=502, detail=f"Falha no envio: {exc}") from exc
    return {"target_id": body.target_id, "email": to_email, "email_id": email_id, "sent": True}


@app.post("/admin/resend-payment")
async def api_admin_resend_payment(body: ResendPaymentBody) -> dict:
    """Reenvia o relatório pago de uma cobrança (mesmo caminho do pós-pagamento)."""
    _require_email()
    charge = await get_store().get(body.charge_id)
    if charge is None:
        raise HTTPException(status_code=404, detail="Cobrança não encontrada.")
    if not charge.buyer_email:
        raise HTTPException(status_code=400, detail="Cobrança sem e-mail do comprador.")
    await get_store().set_email_status(charge.charge_id, "sending")
    _spawn(_send_report_email_task(charge.charge_id, charge.target_url, charge.buyer_email))
    return {"charge_id": charge.charge_id, "email": charge.buyer_email, "queued": True}


@app.post("/admin/clean-emails")
async def api_admin_clean_emails() -> dict:
    """Limpa e-mails sujos já no banco (URL-encoded, espaços, lixo). Aplica
    `_clean_email` a cada alvo com e-mail: conserta os que mudaram e ficaram
    válidos; descarta os irrecuperáveis (formato inválido após limpar)."""
    from discovery.contact import _clean_email

    store = get_target_store()
    rows = await store.list_target_emails()
    cleaned, discarded, examples = 0, 0, []
    for r in rows:
        raw = r.get("contact_email") or ""
        clean = _clean_email(raw)
        if clean == raw:
            continue
        if clean and _EMAIL_RE.match(clean):
            await store.update_target_email(r["id"], clean)
            cleaned += 1
            if len(examples) < 10:
                examples.append({"id": r["id"], "from": raw, "to": clean})
        else:
            await store.update_status(r["id"], "descartado")
            discarded += 1
    print(f"[admin] clean-emails: {cleaned} corrigidos, {discarded} descartados "
          f"de {len(rows)}", flush=True)
    return {"total": len(rows), "cleaned": cleaned, "discarded": discarded,
            "examples": examples}


@app.post("/admin/process-bounces")
async def api_admin_process_bounces(limit: int = Query(default=1000, le=5000)) -> dict:
    """Backfill de bounces (KL-24): checa no Resend o status de cada alerta enviado
    e descarta/bloqueia os que bouncaram permanentemente.

    Concorrência limitada (não estoura o rate limit do Resend). Idempotente — rodar
    de novo só reprocessa o que ainda está 'sent'.
    """
    _require_email()
    store = get_target_store()
    mailer = _mailer()
    # KL-62: lê do email_log unificado (superset — cobre verificação/perfil/relatório/…),
    # não só o alert_log. Marca ambos (alert_log + email_log) por email_id.
    alerts = await store.get_sent_emails_for_bounce_check(limit=limit)

    sem = asyncio.Semaphore(8)
    result = {"processed": 0, "bounced": 0, "delivered": 0, "unknown": 0}

    async def _check(alert: dict) -> None:
        async with sem:
            event = await mailer.get_email_event(alert["email_id"])
        result["processed"] += 1
        if event in ("bounced", "bounce"):
            await store.mark_alert_status_by_email_id(alert["email_id"], "bounced")
            await store.mark_email_status_by_email_id(alert["email_id"], "bounced")
            await _handle_bounce(store, alert.get("contact_email", ""), "backfill")
            result["bounced"] += 1
        elif event in ("complained", "complaint"):
            await store.mark_alert_status_by_email_id(alert["email_id"], "complained")
            await store.mark_email_status_by_email_id(alert["email_id"], "complained")
            await _handle_complaint(store, alert.get("contact_email", ""))
        elif event is None:
            result["unknown"] += 1
        else:
            result["delivered"] += 1

    await asyncio.gather(*[_check(a) for a in alerts])
    print(f"[admin] process-bounces: {result}", flush=True)
    return {"candidates": len(alerts), **result}


# --------------------------------------------------------------------------- #
# Reclassificação de setor (refino do KL-11)
# --------------------------------------------------------------------------- #

# Estado do job de reclassificação por HTML (em memória — um operador só).
_reclassify_status: dict = {"running": False, "processed": 0, "changed": 0, "total": 0}


@app.post("/admin/reclassify-domains")
async def api_reclassify_domains() -> dict:
    """Reclassifica TODOS os alvos só pela pista do domínio (instantâneo, sem HTTP).

    Só atualiza quando o domínio dá uma pista confiável (≥ 0.9) — nunca rebaixa
    uma classificação existente para 'outro'. Os sem pista ficam como estão e são
    refinados via `/admin/reclassify-all` (fetch) ou no próximo re-scan.
    """
    store = get_target_store()
    rows = await store.all_targets_for_reclassify()
    updates, changed, skipped, by_sector = [], 0, 0, {}
    for r in rows:
        if r.get("classification_source") == "manual":
            skipped += 1
            print(f"[reclassify] pulando target {r['id']} (classificação manual)", flush=True)
            continue
        res = classify_by_domain(r["url"])
        if not res:
            continue  # sem pista no domínio → mantém a classificação atual
        sector, confidence = res
        tier = PRICE_TIERS.get(sector, "standard")
        updates.append((sector, tier, confidence, r["id"]))
        by_sector[sector] = by_sector.get(sector, 0) + 1
        if sector != (r.get("sector") or "outro"):
            changed += 1
    await store.bulk_update_classification(updates)
    print(f"[reclassify] domínios: {len(rows)} avaliados, {len(updates)} com pista, "
          f"{changed} alterados, {skipped} manuais preservados", flush=True)
    return {"processed": len(rows), "updated": len(updates), "changed": changed,
            "skipped_manual": skipped, "by_sector": by_sector}


async def _reclassify_all_task() -> None:
    """Reclassifica cada alvo buscando o HTML (rate limit 1/s). Roda em background."""
    store = get_target_store()
    rows = await store.all_targets_for_reclassify()
    _reclassify_status.update(running=True, processed=0, changed=0, total=len(rows))
    print(f"[reclassify] fetch: iniciando {len(rows)} alvos", flush=True)
    changed = 0
    for i, r in enumerate(rows, 1):
        try:
            if r.get("classification_source") == "manual":
                print(f"[reclassify] pulando target {r['id']} (classificação manual)", flush=True)
            else:
                html = await _fetch_html(r["url"])
                sector, tier, confidence = classify_sector(html, r["url"])
                await store.update_classification(r["id"], sector, tier, confidence)
                if sector != (r.get("sector") or "outro"):
                    changed += 1
                await asyncio.sleep(1.0)  # rate limit: 1 fetch/segundo (só quando busca)
        except Exception as exc:  # noqa: BLE001 - um alvo ruim não derruba o job
            print(f"[reclassify] falha em {r.get('url')}: {exc!r}", flush=True)
        _reclassify_status.update(processed=i, changed=changed)
        if i % 50 == 0:
            print(f"[reclassify] {i}/{len(rows)} processados, {changed} alterados", flush=True)
    _reclassify_status["running"] = False
    print(f"[reclassify] concluído: {len(rows)} processados, {changed} alterados", flush=True)


@app.post("/admin/reclassify-all")
async def api_reclassify_all() -> dict:
    """Dispara a reclassificação por HTML (background). Idempotente: não reinicia
    se já estiver rodando."""
    if _reclassify_status["running"]:
        return {"started": False, "reason": "já em execução", **_reclassify_status}
    _spawn(_reclassify_all_task())
    return {"started": True}


@app.get("/admin/reclassify-status")
async def api_reclassify_status() -> dict:
    return dict(_reclassify_status)


# --------------------------------------------------------------------------- #
# Classificação manual pelo operador (source='manual', confiança 1.0)
# --------------------------------------------------------------------------- #

_VALID_SECTORS = set(PRICE_TIERS)          # 11 setores + 'outro'
_VALID_TIERS = set(PRICING)                # basic | standard | professional | enterprise


class ClassifyBody(BaseModel):
    sector: str
    price_tier: Optional[str] = None


class ClassifyBatchBody(BaseModel):
    target_ids: list[int]
    sector: str
    price_tier: Optional[str] = None


def _resolve_classification(sector: str, price_tier: Optional[str]) -> tuple[str, str]:
    """Valida o setor, deriva o tier do PRICE_TIERS se omitido, e valida o tier."""
    if sector not in _VALID_SECTORS:
        raise HTTPException(status_code=422, detail=f"Setor inválido: {sector}")
    tier = price_tier or PRICE_TIERS.get(sector, "standard")
    if tier not in _VALID_TIERS:
        raise HTTPException(status_code=422, detail=f"Faixa de preço inválida: {tier}")
    return sector, tier


@app.patch("/targets/{target_id}/classify")
async def api_target_classify(target_id: int, body: ClassifyBody) -> dict:
    """Classifica manualmente um alvo (source='manual', confiança 1.0). O tier é
    derivado do setor se não vier no corpo."""
    sector, tier = _resolve_classification(body.sector, body.price_tier)
    updated = await get_target_store().manual_classify(target_id, sector, tier)
    if updated is None:
        raise HTTPException(status_code=404, detail="Alvo não encontrado.")
    return updated


@app.post("/admin/classify-batch")
async def api_classify_batch(body: ClassifyBatchBody) -> dict:
    """Classificação manual em massa. Retorna quantos alvos foram atualizados."""
    sector, tier = _resolve_classification(body.sector, body.price_tier)
    updated = await get_target_store().manual_classify_batch(body.target_ids, sector, tier)
    return {"updated": updated, "sector": sector, "price_tier": tier}


# Status válidos de um alvo (edição manual no painel).
_VALID_STATUSES = {"discovered", "scanned", "alerted", "converted",
                   "sem_contato", "descartado", "unsubscribed"}


class StatusBody(BaseModel):
    status: str


class EmailBody(BaseModel):
    contact_email: str


@app.patch("/targets/{target_id}/status")
async def api_target_update_status(target_id: int, body: StatusBody) -> dict:
    """Edição manual do status de um alvo pelo operador. Retorna o alvo atualizado."""
    if body.status not in _VALID_STATUSES:
        raise HTTPException(status_code=422, detail=f"Status inválido: {body.status}")
    updated = await get_target_store().update_target_status(target_id, body.status)
    if updated is None:
        raise HTTPException(status_code=404, detail="Alvo não encontrado.")
    return updated


@app.patch("/targets/{target_id}/email")
async def api_target_update_email(target_id: int, body: EmailBody) -> dict:
    """Edição manual do e-mail de contato. Alvo 'sem_contato' que ganha e-mail
    volta para 'discovered' (pode ser escaneado/alertado). Retorna o alvo."""
    from discovery.contact import _clean_email

    email = _clean_email(body.contact_email or "")  # URL-decode + tira espaços/lixo
    if not email or not _EMAIL_RE.match(email):
        raise HTTPException(status_code=422, detail="E-mail inválido.")
    updated = await get_target_store().update_target_email(target_id, email)
    if updated is None:
        raise HTTPException(status_code=404, detail="Alvo não encontrado.")
    return updated


# --- Gestão da landing pública pelo operador (KL-56) ----------------------- #

class ProfileEditBody(BaseModel):
    description: Optional[str] = None
    business_type: Optional[str] = None
    company_name: Optional[str] = None
    tags: Optional[Any] = None  # lista OU string "a, b, c" (o store normaliza)


class VisibilityBody(BaseModel):
    visible: bool


@app.put("/targets/{target_id}/profile")
async def api_update_profile(target_id: int, body: ProfileEditBody) -> dict:
    """Edição manual do perfil da landing (description/business_type/tags/company_name).
    Marca `edited_by_admin=TRUE` — o enrich automático passa a preservar esses campos."""
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(status_code=422, detail="Nenhum campo para atualizar.")
    updated = await get_target_store().update_site_profile_fields(target_id, fields)
    if updated is None:
        raise HTTPException(status_code=404, detail="Perfil não encontrado para o alvo.")
    return updated


@app.patch("/targets/{target_id}/profile/visibility")
async def api_profile_visibility(target_id: int, body: VisibilityBody) -> dict:
    """Liga/desliga a landing pública (`/site/{dominio}`). Desligada: some do site e
    do sitemap (mesmo comportamento de descartado)."""
    updated = await get_target_store().set_profile_visibility(target_id, body.visible)
    if updated is None:
        raise HTTPException(status_code=404, detail="Perfil não encontrado para o alvo.")
    return {"target_id": target_id, "public_visible": bool(body.visible)}


def _payment_row(charge, target_id: Optional[int] = None) -> dict:
    """Payload de pagamento para o painel admin (mascarando o e-mail do comprador)."""
    return {
        "charge_id": charge.charge_id,
        "target_url": charge.target_url,
        "target_id": target_id,
        "amount_cents": charge.amount_cents,
        "amount_display": amount_display(charge.amount_cents),
        "status": charge.status,
        "paid": charge.is_paid,
        "created_at": charge.created_at,
        "paid_at": charge.paid_at,
        "buyer_email": mask_email(charge.buyer_email) if charge.buyer_email else None,
        "report_email_sent": charge.report_email_sent,
        "email_status": charge.email_status,
    }


@app.post("/targets/{target_id}/alert")
async def api_target_alert(target_id: int) -> dict:
    """Dispara o alerta manualmente (ignora throttle e janela de 30 dias)."""
    _require_email()
    store = get_target_store()
    target = await store.get_target(target_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Alvo não encontrado.")
    if not target.get("contact_email"):
        raise HTTPException(status_code=400, detail="Alvo sem e-mail de contato.")
    print(f"[alert] envio manual (bypass cota mensal) alvo {target_id} "
          f"{target.get('url')}", flush=True)
    try:
        email_id = await send_alert_for_target(store, _mailer(), target)
    except KlarimMailerError as exc:
        raise HTTPException(status_code=502, detail=f"Falha no envio: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"target_id": target_id, "email": target["contact_email"], "email_id": email_id, "sent": True}


@app.get("/unsubscribe")
async def api_unsubscribe(
    email: str = Query(...),
    token: str = Query(...),
) -> HTMLResponse:
    """Descadastro via link do rodapé do alerta (token HMAC do e-mail)."""
    secret = os.environ.get("UNSUBSCRIBE_SECRET")
    ok = bool(secret) and hmac.compare_digest(token, unsubscribe_token(email, secret))
    if not ok:
        return HTMLResponse(_unsubscribe_html(email, success=False), status_code=400)
    await get_target_store().mark_unsubscribed(email)
    return HTMLResponse(_unsubscribe_html(email, success=True))


def _unsubscribe_html(email: str, success: bool) -> str:
    from html import escape

    if success:
        title, msg = "Descadastro concluído", (
            f"O endereço <strong>{escape(email)}</strong> não receberá mais alertas do Klarim.")
    else:
        title, msg = "Link inválido", (
            "Este link de descadastro é inválido ou expirou. "
            "Se preferir, escreva para <a href=\"mailto:scan@klarim.net\" "
            "style=\"color:#FF6B35\">scan@klarim.net</a>.")
    return (
        "<!DOCTYPE html><html lang=\"pt-BR\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">"
        f"<title>{title} — Klarim</title></head>"
        "<body style=\"margin:0;background:#0D1117;font-family:Arial,sans-serif;color:#E6EDF3\">"
        "<div style=\"max-width:520px;margin:64px auto;padding:32px;background:#161B22;"
        "border:1px solid #30363D;border-radius:12px;text-align:center\">"
        "<div style=\"font-size:24px;font-weight:bold;letter-spacing:2px;margin-bottom:16px\">"
        "KLA<span style=\"color:#FF6B35\">R</span>IM</div>"
        f"<h2 style=\"color:#FF6B35;font-size:20px\">{title}</h2>"
        f"<p style=\"color:#8B949E;font-size:15px;line-height:1.6\">{msg}</p>"
        "</div></body></html>"
    )


# --------------------------------------------------------------------------- #
# Sites monitorados (KL-29) — selo de segurança para score 100
# --------------------------------------------------------------------------- #

_monitor_hits: dict = {}


def _monitor_secret() -> str:
    return os.environ.get("JWT_SECRET", "") or os.environ.get("UNSUBSCRIBE_SECRET", "")


def _monitor_removal_token(domain: str) -> str:
    """Token HMAC (idempotente) para o link de remoção do rodapé dos e-mails."""
    return hmac.new(_monitor_secret().encode(), f"remove:{domain}".encode(),
                    hashlib.sha256).hexdigest()[:32]


def _favicon_url(domain: str) -> str:
    return f"https://{domain}/favicon.ico"


def _public_monitored(site: dict) -> dict:
    """Payload público de um site monitorado — sem e-mail, target_id ou token."""
    return {
        "domain": site.get("domain"),
        "display_name": site.get("display_name") or site.get("domain"),
        "logo_url": site.get("logo_url") or _favicon_url(site.get("domain", "")),
        "url": site.get("url"),
        "score": site.get("last_check_score") if site.get("last_check_score") is not None else 100,
        "last_check_at": site.get("last_check_at"),
        "verified_since": site.get("approved_at"),
    }


class MonitorOfferBody(BaseModel):
    url: str
    email: str
    charge_id: Optional[str] = None


async def _authorized_for_url(request: Optional[Request], url: str,
                              charge_id: Optional[str]) -> bool:
    """Quem pode ofertar monitoramento de uma URL: admin, ou quem comprovadamente
    fez o scan COMPLETO dela (scan token `full` ou cobrança paga). Evita alguém
    listar/monitorar o site de terceiros com o próprio e-mail (KL-29)."""
    if request is not None and _is_admin_request(request):
        return True
    token = request.headers.get("x-scan-token", "") if request is not None else ""
    payload = _verify_scan_token(token)
    if payload and payload.get("full") and _norm_scan_url(payload.get("url", "")) == url:
        return True
    if charge_id:
        charge = await get_store().get(charge_id)
        if charge and _norm_scan_url(charge.target_url) == url:
            await _refresh_charge(charge)
            if charge.is_paid or _free_access():
                return True
    return False


class MonitorApproveBody(BaseModel):
    token: str
    display_name: Optional[str] = None


@app.post("/monitoring/offer")
async def monitoring_offer(body: MonitorOfferBody, request: Request = None) -> dict:
    """Oferta de monitoramento (KL-29). Público, mas só para uma URL cujo scan
    COMPLETO recente é **score 100** (o servidor confere — não confia no cliente).
    Cria/reusa o registro `pending` e devolve o `approval_token` para a confirmação."""
    url = _norm_scan_url(body.url)
    email = _clean_scan_email(body.email)
    if not url or not _SCAN_EMAIL_RE.match(email):
        raise HTTPException(status_code=422, detail="URL ou e-mail inválido.")

    ip = _client_ip(request) if request is not None else "?"
    if not _rl_ok(_monitor_hits, ip, 10, 3600):
        raise HTTPException(status_code=429, detail="Muitas solicitações. Aguarde.",
                            headers={"Retry-After": "3600"})

    # Só quem comprovadamente fez o scan completo da URL pode ofertá-la (anti-abuso).
    if not await _authorized_for_url(request, url, body.charge_id):
        raise HTTPException(
            status_code=403,
            detail="Faça o scan completo deste site para ativar o monitoramento.")

    # Confere o score 100 num scan COMPLETO recente (sem reescanear).
    report = await get_recent_only(url, full=True)
    score = report.score.score if (report and report.score) else None
    if score != 100:
        raise HTTPException(
            status_code=409,
            detail="O monitoramento é oferecido apenas a sites com score 100 num scan completo recente.")

    domain = registrable_domain(domain_of(url))
    store = get_target_store()
    target = await store.get_target_by_url(url)
    token = secrets.token_urlsafe(48)
    site = await store.upsert_monitoring_offer(
        domain=domain, url=url, contact_email=email, approval_token=token,
        target_id=(target or {}).get("id"), score=score)
    if site is None:
        raise HTTPException(status_code=500, detail="Falha ao registrar o monitoramento.")
    if site["status"] in ("active", "suspended"):
        return {"status": site["status"], "domain": domain, "already": True}
    return {"status": "pending", "domain": domain, "approval_token": site["approval_token"]}


@app.get("/monitoring/status")
async def monitoring_status(token: str = Query(...)) -> dict:
    """Estado de uma oferta pelo token (para a página de aprovação)."""
    site = await get_target_store().get_monitored_by_token(token)
    if site is None:
        return {"valid": False}
    return {"valid": True, "domain": site["domain"], "status": site["status"],
            "display_name": site.get("display_name"),
            "score": site.get("last_check_score")}


@app.post("/monitoring/approve")
async def monitoring_approve(body: MonitorApproveBody) -> dict:
    """Confirma o monitoramento (uso único do token): marca `active`, captura o
    favicon como logo e salva o nome da empresa (opcional)."""
    store = get_target_store()
    domain_row = await store.get_monitored_by_token(body.token)
    logo = _favicon_url(domain_row["domain"]) if domain_row else None
    site = await store.approve_monitored_site(
        body.token, display_name=body.display_name, logo_url=logo)
    if site is None:
        raise HTTPException(status_code=404, detail="Link inválido, expirado ou já usado.")
    return {"status": "active", "domain": site["domain"],
            "display_name": site.get("display_name")}


@app.get("/monitoring/remove")
async def monitoring_remove(domain: str = Query(...), token: str = Query(...)) -> Response:
    """Link de remoção (rodapé dos e-mails de monitoramento). HMAC por domínio."""
    domain = (domain or "").strip().lower()
    ok = bool(_monitor_secret()) and hmac.compare_digest(token, _monitor_removal_token(domain))
    if ok:
        await get_target_store().remove_monitored_site_by_domain(domain)
    return HTMLResponse(_unsubscribe_html_generic(
        "Removido dos Sites Monitorados" if ok else "Link inválido",
        (f"O site <strong>{domain}</strong> foi removido da seção Sites Monitorados."
         if ok else "Este link de remoção é inválido.")))


def _unsubscribe_html_generic(title: str, msg: str) -> str:
    return (
        "<!DOCTYPE html><html lang=\"pt-BR\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">"
        f"<title>{title} — Klarim</title></head>"
        "<body style=\"margin:0;background:#0D1117;font-family:Arial,sans-serif;color:#E6EDF3\">"
        "<div style=\"max-width:520px;margin:64px auto;padding:32px;background:#161B22;"
        "border:1px solid #30363D;border-radius:12px;text-align:center\">"
        "<div style=\"font-size:24px;font-weight:bold;letter-spacing:2px;margin-bottom:16px\">"
        "KLA<span style=\"color:#FF6B35\">R</span>IM</div>"
        f"<h2 style=\"color:#FF6B35;font-size:20px\">{title}</h2>"
        f"<p style=\"color:#8B949E;font-size:15px;line-height:1.6\">{msg}</p>"
        "</div></body></html>")


@app.get("/monitoring/sites")
async def monitoring_sites() -> dict:
    """Lista pública dos sites monitorados `active` (sem dados sensíveis)."""
    try:
        rows = await get_target_store().get_active_monitored_sites()
    except Exception:  # noqa: BLE001
        rows = []
    return {"sites": [_public_monitored(s) for s in rows], "total": len(rows)}


# --- gestão (admin, prefixo /monitoring/admin protegido pelo middleware) ---- #

class MonitorAdminStatusBody(BaseModel):
    status: str
    reason: Optional[str] = None


@app.get("/monitoring/admin/list")
async def monitoring_admin_list(status: Optional[str] = Query(default=None)) -> dict:
    rows = await get_target_store().list_monitored_sites(status=status)
    return {"count": len(rows), "sites": rows}


@app.get("/monitoring/admin/stats")
async def monitoring_admin_stats() -> dict:
    return await get_target_store().monitored_stats()


@app.post("/monitoring/admin/{site_id}/status")
async def monitoring_admin_set_status(site_id: int, body: MonitorAdminStatusBody) -> dict:
    if body.status not in ("pending", "active", "suspended", "removed"):
        raise HTTPException(status_code=422, detail=f"Status inválido: {body.status}")
    site = await get_target_store().set_monitored_status(site_id, body.status, body.reason)
    if site is None:
        raise HTTPException(status_code=404, detail="Site monitorado não encontrado.")
    return site


# --------------------------------------------------------------------------- #
# KL-44 — Planos, assinaturas e trial (admin) + assinatura do usuário (público)
# --------------------------------------------------------------------------- #

class PlanEditBody(BaseModel):
    name: Optional[str] = None
    price_monthly: Optional[int] = None
    price_yearly: Optional[int] = None
    max_sites: Optional[int] = None
    scan_frequency: Optional[str] = None
    vigilia_ssl: Optional[bool] = None
    vigilia_domain: Optional[bool] = None
    vigilia_score: Optional[bool] = None
    vigilia_email: Optional[bool] = None
    vigilia_reputation: Optional[bool] = None
    vigilia_changes: Optional[bool] = None
    vigilia_phishing: Optional[bool] = None
    vigilia_uptime: Optional[bool] = None
    uptime_interval_minutes: Optional[int] = None
    bulletin_frequency: Optional[str] = None
    action_plan_limit: Optional[int] = None
    history_months: Optional[int] = None
    competitor_slots: Optional[int] = None
    lgpd_full: Optional[bool] = None
    widget_type: Optional[str] = None
    pdf_report_frequency: Optional[str] = None
    export_enabled: Optional[bool] = None
    api_enabled: Optional[bool] = None
    is_active: Optional[bool] = None


class SubPlanBody(BaseModel):
    plan_id: str
    reason: Optional[str] = None


class SubTrialBody(BaseModel):
    days: int


class SubStatusBody(BaseModel):
    status: str
    reason: Optional[str] = None


class SubBulkBody(BaseModel):
    account_ids: list[int]
    action: str  # 'change_plan' | 'extend_trial' | 'change_status'
    plan_id: Optional[str] = None
    days: Optional[int] = None
    status: Optional[str] = None
    reason: Optional[str] = None


@app.get("/admin/plans")
async def api_admin_plans() -> dict:
    return {"plans": await plans.get_plans()}


@app.get("/admin/plans/{plan_id}")
async def api_admin_plan(plan_id: str) -> dict:
    p = await plans.get_plan(plan_id)
    if not p:
        raise HTTPException(status_code=404, detail="Plano não encontrado.")
    return p


@app.put("/admin/plans/{plan_id}")
async def api_admin_plan_update(plan_id: str, body: PlanEditBody) -> dict:
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    p = await get_target_store().update_plan(plan_id, fields)
    if not p:
        raise HTTPException(status_code=404, detail="Plano não encontrado.")
    return p


# ⚠️ /stats e /bulk são declarados ANTES de /{account_id} (senão "stats" viraria id).
@app.get("/admin/subscriptions/stats")
async def api_admin_sub_stats() -> dict:
    return await plans.get_subscription_stats()


@app.get("/admin/subscriptions")
async def api_admin_subscriptions(
    plan_id: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    search: Optional[str] = Query(default=None),
    limit: int = Query(default=25, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict:
    rows = await plans.list_subscribers(plan_id=plan_id, status=status, search=search,
                                        limit=limit, offset=offset)
    return {"subscribers": rows}


@app.post("/admin/subscriptions/bulk")
async def api_admin_sub_bulk(body: SubBulkBody) -> dict:
    results = []
    for aid in body.account_ids:
        try:
            if body.action == "change_plan" and body.plan_id:
                await plans.change_plan(aid, body.plan_id, changed_by="admin", reason=body.reason)
            elif body.action == "extend_trial" and body.days:
                await plans.extend_trial(aid, int(body.days), changed_by="admin")
            elif body.action == "change_status" and body.status:
                await plans.set_status(aid, body.status, changed_by="admin", reason=body.reason)
            else:
                raise ValueError("ação inválida ou parâmetros faltando")
            results.append({"account_id": aid, "ok": True})
        except Exception as exc:  # noqa: BLE001 - um alvo ruim não derruba o lote
            results.append({"account_id": aid, "ok": False, "error": str(exc)})
    return {"results": results, "applied": len([r for r in results if r["ok"]])}


@app.get("/admin/subscriptions/{account_id}")
async def api_admin_subscription(account_id: int) -> dict:
    return await plans.get_subscription(account_id)


@app.get("/admin/subscriptions/{account_id}/history")
async def api_admin_sub_history(account_id: int) -> dict:
    return {"history": await get_target_store().list_subscription_history(account_id)}


@app.patch("/admin/subscriptions/{account_id}/plan")
async def api_admin_sub_change_plan(account_id: int, body: SubPlanBody) -> dict:
    if not await plans.get_plan(body.plan_id):
        raise HTTPException(status_code=400, detail="Plano inválido.")
    await plans.change_plan(account_id, body.plan_id, changed_by="admin", reason=body.reason)
    return await plans.get_subscription(account_id)


@app.patch("/admin/subscriptions/{account_id}/trial")
async def api_admin_sub_extend_trial(account_id: int, body: SubTrialBody) -> dict:
    await plans.extend_trial(account_id, int(body.days), changed_by="admin")
    return await plans.get_subscription(account_id)


@app.patch("/admin/subscriptions/{account_id}/status")
async def api_admin_sub_status(account_id: int, body: SubStatusBody) -> dict:
    await plans.set_status(account_id, body.status, changed_by="admin", reason=body.reason)
    return await plans.get_subscription(account_id)


@app.get("/account/subscription")
async def account_subscription(request: Request) -> dict:
    """Assinatura da conta logada (dashboard do usuário — usado no P6)."""
    user = await auth_users.require_user(request)
    return await plans.get_subscription(user["id"])


@app.get("/admin/clients")
async def admin_clients() -> dict:
    """Gestão de Clientes (KL-51 f3 fix): contas de usuário + os sites monitorados de
    cada uma (via `user_sites`). Protegido pelo middleware admin (prefixo `/admin`)."""
    clients = await get_target_store().list_users_with_sites()
    active = sum(1 for c in clients if c.get("is_active"))
    total_sites = sum(len(c.get("sites") or []) for c in clients)
    return {"clients": clients, "total": len(clients),
            "active": active, "total_sites": total_sites}


# --------------------------------------------------------------------------- #
# Re-scan / evolução (Re-scan Worker — KL-13)
# --------------------------------------------------------------------------- #

@app.get("/rescans")
async def api_list_rescans(
    target_id: Optional[int] = Query(default=None),
    evolution: Optional[str] = Query(default=None),
    limit: int = Query(default=50, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict:
    rows = await get_target_store().list_rescans(target_id, evolution, limit, offset)
    return {"count": len(rows), "rescans": rows}


@app.get("/rescans/stats")
async def api_rescans_stats() -> dict:
    return await get_target_store().rescan_stats()


@app.post("/targets/{target_id}/rescan")
async def api_target_rescan(target_id: int) -> dict:
    """Força o re-scan de um alvo (ignora a janela de 30 dias e o throttle) e envia
    o e-mail de evolução se houver e-mail de contato."""
    store = get_target_store()
    target = await store.get_target(target_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Alvo não encontrado.")
    # Enriquecemos com o semáforo do scan anterior (para o registro de evolução).
    if target.get("last_scan_id"):
        prev = await store.get_scan(target["last_scan_id"])
        if prev is not None:
            target["old_semaphore"] = prev.get("semaphore")
    send_email = _email_enabled() and bool(target.get("contact_email"))
    mailer = _mailer() if send_email else None
    try:
        res = await rescan_target(store, mailer, _cache, target, send_email=send_email)
    except KlarimMailerError as exc:
        raise HTTPException(status_code=502, detail=f"Falha no envio: {exc}") from exc
    return res


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _pdf_response(pdf: bytes, filename: str) -> Response:
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


def _tier_ok(report: ScanReport, full: bool) -> bool:
    """Um report cacheado/do-banco serve o tier pedido? (KL-27)

    O completo exige os 29 checks; o gratuito basta ter os 15 (um scan completo
    também satisfaz o gratuito). Evita servir um scan de 15 checks como se fosse
    o relatório pago de 29 — e vice-versa.
    """
    n = len(report.results)
    return n >= len(ALL_CHECKS) if full else n >= len(FREE_CHECKS)


async def get_or_scan(url: str, full: bool = True, ingest_source: Optional[str] = None,
                      scanned_by_email: Optional[str] = None) -> ScanReport:
    """Retorna o scan do tier reusando o dado mais recente; só escaneia em último caso.

    Prioridade (fix — carregamento rápido pelo link do e-mail):
      1. **Cache Redis** (KL-9, por tier KL-27) — instantâneo.
      2. **Tabela `scans`** (Postgres) — reconstrói o `ScanReport` de um scan < 1h
         **do tier certo** e reaquece o cache, sem reescanear.
      3. **Scan novo** — só se não houver nada recente compatível; se ``ingest_source``,
         grava alvo+scan no Postgres em background (KL-17).
    """
    if _cache is not None:
        cached = await _cache.get(url, full)
        if cached is not None and _tier_ok(cached, full):
            return cached

    # 2. Scan recente no banco → reconstrói e reaquece o cache (sem reescanear).
    try:
        checks = await get_target_store().get_recent_scan_checks(url, 60)
    except Exception as exc:  # noqa: BLE001 - banco opcional; cai no scan novo
        checks = None
        print(f"[get_or_scan] lookup no banco falhou ({exc!r})", flush=True)
    if checks:
        try:
            report = ScanReport.from_dict(checks)
            if _tier_ok(report, full):
                if _cache is not None:
                    await _cache.set(url, report, full)
                return report
        except Exception as exc:  # noqa: BLE001 - checks_json corrompido → reescaneia
            print(f"[get_or_scan] from_dict falhou ({exc!r}); reescaneando", flush=True)

    # 3. Scan novo (do tier pedido).
    report = await run_scan(url, full=full)
    if _cache is not None:
        await _cache.set(url, report, full)
    if ingest_source:
        _spawn(_ingest_scan_bg(url, report, ingest_source, scanned_by_email))
    return report


async def get_recent_only(url: str, full: bool = False) -> Optional[ScanReport]:
    """Retorna um scan RECENTE do tier (cache Redis ou banco < 1h) SEM reescanear.
    Usado no /scan/summary quando não há token — nunca dispara scan novo (KL-25)."""
    if _cache is not None:
        cached = await _cache.get(url, full)
        if cached is not None and _tier_ok(cached, full):
            return cached
    try:
        checks = await get_target_store().get_recent_scan_checks(url, 60)
    except Exception:  # noqa: BLE001
        checks = None
    if checks:
        try:
            report = ScanReport.from_dict(checks)
            if not _tier_ok(report, full):
                return None
            if _cache is not None:
                await _cache.set(url, report, full)
            return report
        except Exception:  # noqa: BLE001
            return None
    return None


async def _ingest_scan_bg(url: str, report: ScanReport, source: str,
                          scanned_by_email: Optional[str] = None) -> None:
    try:
        meta = await ingest_scan(get_target_store(), url, report, source, scanned_by_email)
        print(f"[ingest] {url} registrado no banco (source={source})", flush=True)
        # KL-51 f5: TODO scan gera perfil completo (profiler + IA + CNAE), não só o worker.
        # Já estamos em background (este bg roda depois da resposta do scan) — enriquece
        # inline aqui, mesmo módulo que o scan worker usa.
        tid = (meta or {}).get("target_id")
        score = report.score.score if report.score else None
        if tid:
            from scanner.enrichment import enrich_profile
            await enrich_profile(get_target_store(), tid, url, score)
        # KL-61: captura o lead (e-mail verificado). Já em background — best-effort.
        if scanned_by_email:
            try:
                store = get_target_store()
                t = await store.get_target(tid) if tid else None
                await store.upsert_scan_lead(
                    scanned_by_email, url, score,
                    sector=(t or {}).get("sector"), platform=(t or {}).get("platform"))
            except Exception as exc:  # noqa: BLE001 - lead nunca derruba o ingest
                print(f"[lead] upsert falhou {scanned_by_email} ({exc!r})", flush=True)
    except Exception as exc:  # noqa: BLE001 - ingestão é best-effort, não quebra o scan
        print(f"[ingest] falha ao registrar {url} ({exc!r})", flush=True)


async def _safe_scan(url: str, full: bool = True, ingest_source: Optional[str] = None,
                     scanned_by_email: Optional[str] = None):
    try:
        return await get_or_scan(url, full, ingest_source, scanned_by_email)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"URL inválida: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Falha na varredura: {exc!r}") from exc


async def _safe_pdf(fn, report, url: str) -> bytes:
    try:
        return await fn(report, url)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Falha ao gerar PDF: {exc!r}") from exc


# --------------------------------------------------------------------------- #
# Servidor MCP (KL-18) — operar o Klarim via Claude. SSE em /mcp/sse, autenticado
# pela MCPAuthMiddleware (MCP_API_KEY, fail-closed). Modelo Traka: middleware ASGI
# envolvendo o mcp_app. Opcional: se o pacote `mcp` faltar, a API sobe sem o MCP.
# --------------------------------------------------------------------------- #
try:
    from mcp_server.server import mcp_app
    from mcp_server.auth import MCPAuthMiddleware

    app.mount("/mcp", MCPAuthMiddleware(mcp_app))
    print("[mcp] servidor MCP montado em /mcp/sse (SSE, auth por MCP_API_KEY)", flush=True)
except Exception as exc:  # noqa: BLE001 - MCP é opcional; a API sobe mesmo assim
    print(f"[mcp] não montado ({exc!r})", flush=True)
