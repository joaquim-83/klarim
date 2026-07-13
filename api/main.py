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
from typing import Optional
from urllib.parse import urlparse, quote

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response, HTMLResponse
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
from notifier import KlarimMailer, KlarimMailerError, unsubscribe_token, verify_resend_signature
from discovery.alert_worker import send_alert_for_target
from discovery.rescan_worker import rescan_target
from discovery import worker_control
from discovery.ingest import ingest_scan, _fetch_html
from discovery.classifier import classify_sector, classify_by_domain, PRICE_TIERS
from api import health_checks
from api import auth_users


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


def _is_protected(path: str) -> bool:
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
    ip = _client_ip(request)
    now = time.monotonic()
    q = _login_attempts.setdefault(ip, [])
    cutoff = now - _LOGIN_RL_WINDOW
    while q and q[0] < cutoff:
        q.pop(0)
    if len(q) >= _LOGIN_RL_MAX:
        retry = int(_LOGIN_RL_WINDOW - (now - q[0])) + 1
        raise HTTPException(
            status_code=429,
            detail=f"Muitas tentativas. Tente novamente em {retry}s.",
            headers={"Retry-After": str(retry)},
        )
    q.append(now)
    if len(_login_attempts) > 5000:  # limpeza oportunista (não cresce sem limite)
        for k in [k for k, ts in _login_attempts.items() if not ts or ts[-1] < cutoff]:
            _login_attempts.pop(k, None)


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
# rate limits in-memory (MVP single-process; mover p/ Redis se escalar)
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
    """True se DENTRO do limite (permite); False se excedeu. Janela deslizante."""
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


def _user_public(user: dict) -> dict:
    """Campos seguros do usuário para devolver ao frontend (sem hash)."""
    return {
        "id": user["id"], "email": user["email"], "name": user.get("name"),
        "plan": user.get("plan", "free"), "max_sites": user.get("max_sites", 1),
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


@app.post("/account/signup")
async def account_signup(body: SignupBody, request: Request) -> JSONResponse:
    """Cria uma conta (o e-mail já foi verificado no fluxo de scan, KL-25). Vincula
    automaticamente o site recém-escaneado. Rate limit 5/IP/h."""
    if not _ip_rate_limit(_signup_attempts, _client_ip(request), 5, 3600):
        raise HTTPException(status_code=429, detail="Muitas contas criadas. Tente mais tarde.")
    email = (body.email or "").lower().strip()
    if not _ACCOUNT_EMAIL_RE.match(email):
        raise HTTPException(status_code=400, detail="E-mail inválido.")
    if len(body.password or "") < _PW_MIN:
        raise HTTPException(status_code=400, detail="A senha precisa ter ao menos 8 caracteres.")
    store = get_target_store()
    user = await store.create_user(email, auth_users.hash_password(body.password),
                                   name=(body.name or None))
    if user is None:
        raise HTTPException(status_code=409, detail="Já existe uma conta com este e-mail.")
    max_sites = int(user.get("max_sites", 1))
    # vincula o site recém-escaneado (best-effort)
    if body.url:
        tid = await _resolve_or_create_target(body.url, source="signup")
        if tid:
            owner = await _email_owns_target(email, tid)
            await store.link_user_site(user["id"], tid, is_owner=owner)
    # histórico: vincula scans anteriores do mesmo e-mail (KL-25) até o limite do plano
    try:
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
    token = auth_users.create_user_token(user)
    resp = JSONResponse({"user": _user_public(user)})
    _set_session_cookie(resp, token)
    return resp


@app.post("/account/login")
async def account_login(body: AccountLoginBody, request: Request) -> JSONResponse:
    """Login de conta de usuário. Rate limit 10/IP/min (anti brute-force)."""
    if not _ip_rate_limit(_signup_attempts, "login:" + _client_ip(request), 10, 60):
        raise HTTPException(status_code=429, detail="Muitas tentativas. Aguarde um momento.")
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
    if not _ip_rate_limit(_forgot_attempts, email, 3, 3600):
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
    return {
        "target": {
            "id": target_id, "url": target.get("url"), "domain": target.get("domain"),
            "sector": target.get("sector"), "platform": target.get("platform"),
            "last_scan_at": (target.get("last_scan_at").isoformat()
                             if target.get("last_scan_at") else None),
        },
        "is_owner": bool(link.get("is_owner")),
        "score": score, "semaphore": semaphore, "fail_count": fail_count,
        "history": history, "checks": checks,
        "profile": profile, "classifications": classifications,
    }


@app.post("/account/sites")
async def account_add_site(body: SiteBody, request: Request) -> dict:
    user = await auth_users.require_user(request)
    store = get_target_store()
    used = await store.count_user_sites(user["id"])
    if used >= int(user.get("max_sites", 1)):
        raise HTTPException(
            status_code=403,
            detail=f"Seu plano permite {user.get('max_sites', 1)} site(s). "
                   "Faça upgrade para monitorar mais.")
    tid = await _resolve_or_create_target(body.url, source="dashboard")
    if not tid:
        raise HTTPException(status_code=400, detail="Não foi possível analisar esta URL.")
    owner = await _email_owns_target(user["email"], tid)
    await store.link_user_site(user["id"], tid, is_owner=owner)
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
        return _summary_payload(recent, full=open_all)

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
    return data


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
            for addr in recipients:
                await _handle_bounce(store, addr, message)
        else:
            print(f"[webhook/resend] bounce transitório ignorado ({recipients}, {btype})", flush=True)
    elif evt_type == "email.complained":
        if email_id:
            await store.mark_alert_status_by_email_id(email_id, "complained")
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


async def _send_report_email_task(charge_id: str, target_url: str, to_email: str) -> None:
    try:
        # Relatório PAGO → scan COMPLETO (29 checks, KL-27); ingere como 'paid'.
        report = await get_or_scan(target_url, full=True, ingest_source="paid")
        executive = await generate_executive_pdf(report, target_url)
        technical = await generate_technical_pdf(report, target_url)
        score = report.score.score if report.score else 0
        res = await _mailer().send_report(to_email, target_url, score, executive, technical)
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
            res = await _mailer().send_report(email, url, score, executive, technical)
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

    _require_email()
    await _safe_email(_mailer().send_contact(name, email, message))
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
    distinct_url: bool = Query(default=False,
        description="Só o scan mais recente de cada URL (atividade recente)."),
) -> dict:
    rows = await get_target_store().list_scans(
        target_id, score_min, score_max, source, limit, distinct_url=distinct_url)
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
                "last_scan_at": (scan_hb or {}).get("last_scan_at"),
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
async def api_analytics_events(limit: int = Query(default=50, le=500)) -> dict:
    rows = await get_target_store().analytics_events(limit)
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
        risk_messages=get_risk_messages(report), target_id=target_id)
    return res.get("email_id")


async def _send_report_to(url: str, report: ScanReport, to_email: str) -> Optional[str]:
    executive = await _safe_pdf(generate_executive_pdf, report, url)
    technical = await _safe_pdf(generate_technical_pdf, report, url)
    score = report.score.score if report.score else 0
    res = await _mailer().send_report(to_email, url, score, executive, technical)
    return res.get("email_id")


@app.post("/admin/scan-and-report")
async def api_admin_scan_and_report(body: ScanAndReportBody) -> dict:
    """Escaneia (cache ou fresh) → registra no banco (source='admin') → opcionalmente
    envia alerta/relatório. Tudo num request (JWT)."""
    url = normalize_url(body.url)
    report = await _safe_scan(url)  # sem auto-ingest; ingerimos abaixo com os ids
    meta = await ingest_scan(get_target_store(), url, report, source="admin")
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
    alerts = await store.get_sent_alerts_for_bounce_check(limit=limit)

    sem = asyncio.Semaphore(8)
    result = {"processed": 0, "bounced": 0, "delivered": 0, "unknown": 0}

    async def _check(alert: dict) -> None:
        async with sem:
            event = await mailer.get_email_event(alert["email_id"])
        result["processed"] += 1
        if event in ("bounced", "bounce"):
            await store.mark_alert_status_by_email_id(alert["email_id"], "bounced")
            await _handle_bounce(store, alert.get("contact_email", ""), "backfill")
            result["bounced"] += 1
        elif event in ("complained", "complaint"):
            await store.mark_alert_status_by_email_id(alert["email_id"], "complained")
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
        await ingest_scan(get_target_store(), url, report, source, scanned_by_email)
        print(f"[ingest] {url} registrado no banco (source={source})", flush=True)
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
