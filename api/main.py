"""Klarim API (FastAPI).

Superfície: semáforo gratuito (`/scan/summary`), relatórios PDF (protegidos por
pagamento) e o fluxo de pagamento PIX via AbacatePay (`/payment/*`, webhook).

Run local:

    uvicorn api.main:app --reload --port 8000
"""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from typing import Optional
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from scanner import run_scan, summarize_fails, Severity, ScanReport
from scanner import __version__ as scanner_version
from scanner.cache import ScanCache
from reporter import generate_executive_pdf, generate_technical_pdf, pdf_filename
from payments import (
    AbacatePayClient,
    AbacatePayError,
    verify_webhook_signature,
    Charge,
    PaymentStatus,
    PRICING,
    DEFAULT_TIER,
    amount_display,
    get_store,
    init_store,
)
from notifier import KlarimMailer, KlarimMailerError


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
    yield


app = FastAPI(
    title="Klarim API",
    version="0.1.0",
    description="O alarme que toca antes do ataque — scanner passivo de segurança web.",
    lifespan=lifespan,
)


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
    report = await _safe_scan(url)
    score = report.score
    sev = score.fails_by_severity if score else {}
    return {
        "url": report.url,
        "score": score.score if score else None,
        "semaphore": score.semaphore if score else None,
        "grade_icon": score.grade_icon if score else None,
        "summary": summarize_fails(report.results),
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
# Helpers
# --------------------------------------------------------------------------- #

def _pdf_response(pdf: bytes, filename: str) -> Response:
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


async def get_or_scan(url: str) -> ScanReport:
    """Retorna o scan do cache (Redis) ou executa um novo scan e cacheia."""
    if _cache is not None:
        cached = await _cache.get(url)
        if cached is not None:
            return cached
    report = await run_scan(url)
    if _cache is not None:
        await _cache.set(url, report)
    return report


async def _safe_scan(url: str):
    try:
        return await get_or_scan(url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"URL inválida: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Falha na varredura: {exc!r}") from exc


async def _safe_pdf(fn, report, url: str) -> bytes:
    try:
        return await fn(report, url)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Falha ao gerar PDF: {exc!r}") from exc
