"""Klarim API (FastAPI).

Superfície: semáforo gratuito (`/scan/summary`), relatórios PDF (protegidos por
pagamento) e o fluxo de pagamento PIX via AbacatePay (`/payment/*`, webhook).

Run local:

    uvicorn api.main:app --reload --port 8000
"""

from __future__ import annotations

import asyncio
import hmac
import json
import os
import secrets
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response, HTMLResponse
from pydantic import BaseModel

from scanner import run_scan, summarize_fails, Severity, ScanReport
from scanner import __version__ as scanner_version
from scanner.cache import ScanCache
from scanner.checks.base import normalize_url, registrable_domain, domain_of
from discovery.store import get_target_store
from reporter import generate_executive_pdf, generate_technical_pdf, pdf_filename
from reporter.risk_messages import get_risk_messages, get_risk_summary
from payments import (
    AbacatePayClient,
    AbacatePayError,
    verify_webhook_signature,
    Charge,
    PaymentStatus,
    PRICING,
    DEFAULT_TIER,
    amount_display,
    mask_email,
    get_store,
    init_store,
)
from notifier import KlarimMailer, KlarimMailerError, unsubscribe_token
from discovery.alert_worker import send_alert_for_target
from discovery.rescan_worker import rescan_target
from discovery.ingest import ingest_scan, _fetch_html
from discovery.classifier import classify_sector, classify_by_domain, PRICE_TIERS
from api import health_checks


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
                       "/config", "/discovery", "/admin", "/system", "/analytics")


def _jwt_secret() -> str:
    return os.environ.get("JWT_SECRET", "")


def _auth_configured() -> bool:
    return bool(os.environ.get("ADMIN_USER") and os.environ.get("ADMIN_PASSWORD") and _jwt_secret())


def _create_token(username: str) -> str:
    import jwt

    now = datetime.now(timezone.utc)
    payload = {"sub": username, "iat": now, "exp": now + timedelta(seconds=JWT_TTL_SECONDS)}
    return jwt.encode(payload, _jwt_secret(), algorithm=JWT_ALGO)


def _verify_token(token: str) -> dict:
    """Decodifica/valida o JWT (levanta em token inválido/expirado)."""
    import jwt

    return jwt.decode(token, _jwt_secret(), algorithms=[JWT_ALGO])


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


app = FastAPI(
    title="Klarim API",
    version="0.1.0",
    description="O alarme que toca antes do ataque — scanner passivo de segurança web.",
    lifespan=lifespan,
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


@app.post("/auth/login")
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


# --------------------------------------------------------------------------- #
# Scan
# --------------------------------------------------------------------------- #

@app.get("/scan")
async def scan_full(url: str = Query(..., description="URL alvo (http/https).")) -> JSONResponse:
    report = await _safe_scan(url)
    return JSONResponse(report.to_dict())


@app.get("/scan/summary")
async def scan_summary(url: str = Query(..., description="URL alvo.")) -> dict:
    """Semáforo executivo gratuito — score + contagens, sem detalhe por check."""
    # KL-17: scan público grava o alvo + scan no banco (background, source='public').
    report = await _safe_scan(url, ingest_source="public")
    score = report.score
    sev = score.fails_by_severity if score else {}
    risk_messages = get_risk_messages(report)
    return {
        "url": report.url,
        "score": score.score if score else None,
        "semaphore": score.semaphore if score else None,
        "grade_icon": score.grade_icon if score else None,
        "summary": summarize_fails(report.results),
        "risk_summary": get_risk_summary(risk_messages),
        "risk_messages": risk_messages,
        "problems": score.failed if score else 0,
        "passed": score.passed if score else 0,
        "inconclusive": score.inconclusive if score else 0,
        "severity_counts": {
            "critica": sev.get(Severity.CRITICA, 0),
            "alta": sev.get(Severity.ALTA, 0),
            "media": sev.get(Severity.MEDIA, 0),
            "baixa": sev.get(Severity.BAIXA, 0),
        },
        "price": PRICING[DEFAULT_TIER],
        "price_display": amount_display(PRICING[DEFAULT_TIER]),
        "message": (
            "Encaminhe este resumo ao responsável pelo seu site. "
            "Relatório técnico completo disponível na versão paga."
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
    if not _payments_enabled():
        raise HTTPException(status_code=503, detail="Pagamentos não configurados.")

    host = urlparse(body.url if "://" in body.url else "https://" + body.url).hostname or body.url
    amount = PRICING[DEFAULT_TIER]
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
    """Dispara (uma vez) o e-mail do relatório após o pagamento, em background.

    Marca ``report_email_sent`` ANTES de agendar para evitar duplicação
    (webhook + polling). Se o envio falhar, o cliente ainda baixa o PDF no site
    (fallback) — o erro é apenas registrado.
    """
    if not (charge.is_paid and charge.buyer_email and not charge.report_email_sent):
        return
    if not _email_enabled():
        return
    # Marca "enviado" (idempotência) + "sending" ANTES de agendar, para evitar
    # disparo duplicado (webhook + polling) e dar feedback imediato ao frontend.
    await get_store().mark_email_sent(charge.charge_id)
    await get_store().set_email_status(charge.charge_id, "sending")
    charge.report_email_sent = True
    charge.email_status = "sending"
    _spawn(_send_report_email_task(charge.charge_id, charge.target_url, charge.buyer_email))


# Mantém referência às tasks de background: sem isso o Python pode coletá-las
# (GC) antes de terminarem, matando o envio no meio.
_background_tasks: set = set()


def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def _send_report_email_task(charge_id: str, target_url: str, to_email: str) -> None:
    try:
        report = await get_or_scan(target_url)  # cache hit -> instantâneo
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

@app.get("/report/executive")
async def report_executive(
    url: str = Query(..., description="URL alvo."),
    charge_id: Optional[str] = Query(default=None, description="ID da cobrança paga."),
) -> Response:
    await _require_paid(charge_id)
    report = await _safe_scan(url)
    pdf = await _safe_pdf(generate_executive_pdf, report, url)
    return _pdf_response(pdf, pdf_filename("executive", url, report.started_at))


@app.get("/report/technical")
async def report_technical(
    url: str = Query(..., description="URL alvo."),
    charge_id: Optional[str] = Query(default=None, description="ID da cobrança paga."),
) -> Response:
    await _require_paid(charge_id)
    report = await _safe_scan(url)
    pdf = await _safe_pdf(generate_technical_pdf, report, url)
    return _pdf_response(pdf, pdf_filename("technical", url, report.started_at))


async def _require_paid(charge_id: Optional[str]) -> None:
    """Exige uma cobrança paga, exceto em modo dev / pagamentos desativados."""
    if _free_access():
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
    limit: int = Query(default=50, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict:
    rows = await get_target_store().list_targets(
        status, platform, sector, source, limit, offset, low_confidence=low_confidence)
    return {"count": len(rows), "targets": rows}


@app.get("/targets/stats")
async def api_targets_stats() -> dict:
    return await get_target_store().stats()


@app.get("/targets/{target_id}")
async def api_get_target(target_id: int) -> dict:
    target = await get_target_store().get_target(target_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Alvo não encontrado.")
    return target


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
) -> dict:
    rows = await get_target_store().list_scans(target_id, score_min, score_max, source, limit)
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
    max_day = int(os.environ.get("MAX_ALERTS_PER_DAY", "90"))

    queue_size = None
    if redis is not None:
        try:
            queue_size = await redis.llen(os.environ.get("KLARIM_SCAN_QUEUE", "klarim:scan_queue"))
        except Exception:  # noqa: BLE001
            queue_size = None

    deps = await health_checks.run_all(redis)

    return {
        "workers": {
            "discovery": {
                "alive": disc is not None,
                "last_cycle_at": (disc or {}).get("last_cycle_at"),
                "next_cycle_at": (disc or {}).get("next_cycle_at"),
                "cycles_completed": (disc or {}).get("cycles_completed", 0),
                "source": (disc or {}).get("source"),
                "targets_discovered_today": discovered_today,
            },
            "alert": {
                "alive": alert_hb is not None,
                "last_cycle_at": (alert_hb or {}).get("last_cycle_at"),
                "next_cycle_at": (alert_hb or {}).get("next_cycle_at"),
                "sent_today": a_stats.get("today", 0),
                "sent_week": a_stats.get("week", 0),
                "throttle_limit": max_day,
                "last_cycle_stats": (alert_hb or {}).get("last_cycle_stats"),
            },
            "rescan": {
                "alive": rescan_hb is not None,
                "last_cycle_at": (rescan_hb or {}).get("last_cycle_at"),
                "next_cycle_at": (rescan_hb or {}).get("next_cycle_at"),
                "rescanned_today": r_stats.get("today", 0),
                "eligible": eligible,
                "last_cycle_stats": (rescan_hb or {}).get("last_cycle_stats"),
            },
            "scan": {
                "alive": scan_hb is not None,
                "queue_size": queue_size,
                "completed_today": scan_today["count"],
                "avg_score_today": scan_today["avg_score"],
                "last_scan_at": (scan_hb or {}).get("last_scan_at"),
            },
        },
        "dependencies": deps,
        "email_metrics": {**email, "throttle_used": f"{email['sent_today']}/{max_day}"},
    }


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


# --------------------------------------------------------------------------- #
# Tracking da jornada do lead (KL-21): eventos públicos + analytics (JWT)
# --------------------------------------------------------------------------- #

_KNOWN_EVENTS = {
    "page_view", "scan_started", "scan_completed", "result_viewed", "cta_clicked",
    "payment_created", "payment_completed", "report_downloaded", "email_link_clicked",
}
_EVENT_RL_MAX = 100          # eventos/minuto por sessão
_event_rl: dict = {}         # session_id -> lista de timestamps (janela de 60s)


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
        "max_alerts_per_cycle": _i("MAX_ALERTS_PER_CYCLE", "4"),
        "max_alerts_per_hour": _i("MAX_ALERTS_PER_HOUR", "8"),
        "max_alerts_per_day": _i("MAX_ALERTS_PER_DAY", "90"),
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
            "Se preferir, escreva para <a href=\"mailto:seguranca@klarim.net\" "
            "style=\"color:#FF6B35\">seguranca@klarim.net</a>.")
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


async def get_or_scan(url: str, ingest_source: Optional[str] = None) -> ScanReport:
    """Retorna o scan reusando o dado mais recente; só escaneia em último caso.

    Prioridade (fix — carregamento rápido pelo link do e-mail):
      1. **Cache Redis** (KL-9) — instantâneo.
      2. **Tabela `scans`** (Postgres) — reconstrói o `ScanReport` de um scan < 1h
         e reaquece o cache, sem reescanear.
      3. **Scan novo** — só se não houver nada recente; se ``ingest_source``,
         grava alvo+scan no Postgres em background (KL-17).
    """
    if _cache is not None:
        cached = await _cache.get(url)
        if cached is not None:
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
            if _cache is not None:
                await _cache.set(url, report)
            return report
        except Exception as exc:  # noqa: BLE001 - checks_json corrompido → reescaneia
            print(f"[get_or_scan] from_dict falhou ({exc!r}); reescaneando", flush=True)

    # 3. Scan novo.
    report = await run_scan(url)
    if _cache is not None:
        await _cache.set(url, report)
    if ingest_source:
        _spawn(_ingest_scan_bg(url, report, ingest_source))
    return report


async def _ingest_scan_bg(url: str, report: ScanReport, source: str) -> None:
    try:
        await ingest_scan(get_target_store(), url, report, source)
        print(f"[ingest] {url} registrado no banco (source={source})", flush=True)
    except Exception as exc:  # noqa: BLE001 - ingestão é best-effort, não quebra o scan
        print(f"[ingest] falha ao registrar {url} ({exc!r})", flush=True)


async def _safe_scan(url: str, ingest_source: Optional[str] = None):
    try:
        return await get_or_scan(url, ingest_source)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"URL inválida: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Falha na varredura: {exc!r}") from exc


async def _safe_pdf(fn, report, url: str) -> bytes:
    try:
        return await fn(report, url)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Falha ao gerar PDF: {exc!r}") from exc
