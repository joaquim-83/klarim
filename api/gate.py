"""KL-151 (Prompt 1/4) — Security Gate como PRODUTO para devs externos: backend core.

Transforma a engine do Gate (86 checks) em produto: o dev registra uma conta, autentica por
**API key** (SHA-256, exibida UMA VEZ), tem um **plano** (Free/Pro/Team/Enterprise) que limita
scans/dia, domínios e checks, cria **projetos** (domínios), verifica a propriedade (challenge de
domínio OU convite do dono) e roda no pipeline dele.

Este prompt NÃO implementa: o endpoint REST de scan, a CLI, o frontend nem o admin de planos
(Prompts 2-4). Aqui: contas dev, API keys, planos+enforcement, projetos, verificação, convites.

Auth: `/gate/*` (API key via header `X-API-Key`) vs `/account/gate/*` (JWT de usuário — dashboard).
Segurança: a key NUNCA vive em claro (só o hash); checks/limites são enforcados no SERVIDOR;
domínio só escaneia se verificado (ou convite do dono).
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from api import auth_users
from discovery.store import get_target_store
from security_gate.config import GateConfig
from security_gate.engine import _DEFAULT_ORDER as _ENGINE_ORDER, run_all
from security_gate.models import SEVERITY_RANK, Severity, Status

router = APIRouter()

# Lista canônica dos checks do Gate (para o enforcement `["all"]`).
ALL_CHECK_NAMES: List[str] = list(_ENGINE_ORDER)

_TRIAL_DAYS = 14
_KEY_PREFIX = "KLM_"

# Buckets de fallback in-memory do rate limit (reusa `api.main._redis_allow`).
_gate_register_hits: dict = {}
_gate_invite_hits: dict = {}
_gate_verify_hits: dict = {}


# --------------------------------------------------------------------------- #
# API key
# --------------------------------------------------------------------------- #

def generate_api_key() -> Tuple[str, str, str]:
    """(full_key, prefix, hash). `full_key` = 'KLM_' + 32 hex = 36 chars, exibido UMA VEZ; no
    banco vive só o SHA-256 e o `prefix` (KLM_xxxx, para identificar sem expor)."""
    raw = secrets.token_hex(16)          # 32 chars hex
    full_key = f"{_KEY_PREFIX}{raw}"     # 36 chars
    prefix = full_key[:8]                # KLM_ + 4
    key_hash = hashlib.sha256(full_key.encode()).hexdigest()
    return full_key, prefix, key_hash


def _hash_key(full_key: str) -> str:
    return hashlib.sha256((full_key or "").encode()).hexdigest()


async def authenticate_api_key(request: Request) -> dict:
    """Valida o header `X-API-Key`. Retorna `{account_id, key_id, plan}`. 401 se ausente/inválida/
    revogada. Atualiza `last_used_at`."""
    key = (request.headers.get("X-API-Key") or "").strip()
    if not key or not key.startswith(_KEY_PREFIX):
        raise HTTPException(status_code=401, detail="API key inválida.")
    store = get_target_store()
    record = await store.get_gate_api_key_by_hash(_hash_key(key))
    if not record or not record.get("is_active"):
        raise HTTPException(status_code=401, detail="API key inválida ou revogada.")
    await store.touch_gate_api_key(record["id"])
    plan = await get_effective_gate_plan(record["account_id"])
    return {"account_id": record["account_id"], "key_id": record["id"], "plan": plan}


# --------------------------------------------------------------------------- #
# Planos: plano efetivo (trial > plano), checks permitidos, enforcement
# --------------------------------------------------------------------------- #

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _to_utc(dt) -> Optional[datetime]:
    if dt is None or not hasattr(dt, "tzinfo"):
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


async def get_effective_gate_plan(account_id: int) -> Optional[dict]:
    """Plano EFETIVO da conta: trial Pro ativo tem precedência sobre o plano associado; sem plano
    associado, cai no Free. None só se a conta não existe."""
    store = get_target_store()
    fields = await store.get_account_gate_fields(account_id)
    if not fields:
        return None
    ends = _to_utc(fields.get("gate_trial_ends_at"))
    if ends and ends > _now():
        pro = await store.get_gate_plan_by_slug("pro")
        if pro:
            return pro
    plan = await store.get_gate_plan(fields.get("gate_plan_id"))
    return plan or await store.get_gate_plan_by_slug("free")


def get_allowed_checks(plan: Optional[dict]) -> List[str]:
    """Checks que o plano permite. `["all"]` → todos os checks da engine."""
    allowed = (plan or {}).get("checks_allowed") or []
    if isinstance(allowed, str):   # jsonb pode voltar como string em alguns drivers
        import json
        try:
            allowed = json.loads(allowed)
        except (ValueError, TypeError):
            allowed = []
    if "all" in allowed:
        return list(ALL_CHECK_NAMES)
    return list(allowed)


def _scan_redis():
    """Cliente Redis do app (`api.main._cache.redis`) ou None se indisponível."""
    try:
        import api.main as _m
        return _m._cache.redis if _m._cache is not None else None
    except Exception:  # noqa: BLE001
        return None


async def enforce_scan_limit(account_id: int, plan: dict) -> None:
    """429 se a conta já atingiu o teto de scans/dia do plano (`-1` = ilimitado). Contador ATÔMICO
    no Redis por dia-calendário UTC (`gate_scans:{account}:{YYYY-MM-DD}`, TTL 24h) — INCRementa a
    cada chamada (consome 1 crédito) e bloqueia acima do teto. **Fallback** para a contagem no banco
    (`count_gate_runs_today`) se o Redis estiver fora — nunca desliga o limite por falha de infra."""
    spd = int((plan or {}).get("scans_per_day") or 0)
    if spd == -1:
        return
    msg = f"Limite de {spd} scans/dia atingido. Faça upgrade do plano para mais scans."
    redis = _scan_redis()
    if redis is not None:
        try:
            rkey = f"gate_scans:{int(account_id)}:{_now().date().isoformat()}"
            n = await redis.incr(rkey)
            if n == 1:
                await redis.expire(rkey, 86400)
            if n > spd:
                raise HTTPException(status_code=429, detail=msg)
            return
        except HTTPException:
            raise
        except Exception:  # noqa: BLE001 - Redis instável → cai na contagem do banco
            pass
    today = await get_target_store().count_gate_runs_today(account_id)
    if today >= spd:
        raise HTTPException(status_code=429, detail=msg)


async def enforce_domain_limit(account_id: int, plan: dict) -> None:
    """403 se a conta já atingiu o teto de domínios/projetos do plano (`-1` = ilimitado)."""
    maxd = int((plan or {}).get("max_domains") or 0)
    if maxd == -1:
        return
    n = await get_target_store().count_gate_projects(account_id)
    if n >= maxd:
        raise HTTPException(status_code=403, detail=f"Limite de {maxd} domínios atingido.")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _extract_domain(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return ""
    if "://" not in u:
        u = "https://" + u
    host = (urlparse(u).hostname or "").lower().strip()
    return host[4:] if host.startswith("www.") else host


def _invite_status(invite: dict) -> str:
    """Status EFETIVO do convite (resolve expiração por relógio, não só o campo persistido)."""
    st = (invite or {}).get("status") or "pending"
    if st in ("accepted", "revoked"):
        return st
    exp = _to_utc((invite or {}).get("expires_at"))
    if exp and exp < _now():
        return "expired"
    return st


def _serialize_result(r) -> dict:
    """`security_gate.models.Result` → dict JSON-serializável (o valor bruto nunca sai da engine)."""
    return {"check": r.check, "category": r.category, "path": r.path,
            "status": r.status.value, "severity": r.severity.value,
            "detail": r.detail, "http_status": r.http_status}


def _passed_for(report, fail_on: str) -> bool:
    """`passed` RESPEITANDO o `fail_on` do dev (a engine só olha CRÍTICO). True se não há FAIL de
    severidade ≥ o threshold. `fail_on` inválido → 'critical'."""
    try:
        threshold = SEVERITY_RANK[Severity((fail_on or "critical").strip().lower())]
    except (ValueError, KeyError):
        threshold = SEVERITY_RANK[Severity.CRITICAL]
    return not any(r.status == Status.FAIL and SEVERITY_RANK.get(r.severity, 0) >= threshold
                   for r in report.results)


# --------------------------------------------------------------------------- #
# 1. Registro dev (público) — cria conta developer + API key + projeto + trial Pro
# --------------------------------------------------------------------------- #

class GateRegisterBody(BaseModel):
    email: str
    password: str
    full_name: Optional[str] = None
    company: Optional[str] = None
    project_url: str


@router.post("/gate/register")
async def gate_register(body: GateRegisterBody, request: Request) -> JSONResponse:
    """Cria uma conta `developer` (senha), gera a API key (exibida UMA VEZ), cria o 1º projeto e
    concede o trial Pro de 14 dias (plano base Free). Anti-abuso: descartáveis + rate limit 5/h/IP."""
    import api.main as _m
    email = (body.email or "").lower().strip()
    if _m.is_disposable_email(email):
        raise HTTPException(status_code=400, detail="Use um e-mail permanente para criar sua conta.")
    ok, retry = await _m._redis_allow("gate_register", _m._client_ip(request), 5, 3600,
                                      _gate_register_hits)
    if not ok:
        raise HTTPException(status_code=429, detail="Muitos cadastros. Tente mais tarde.",
                            headers={"Retry-After": str(retry)})
    if not _m._ACCOUNT_EMAIL_RE.match(email):
        raise HTTPException(status_code=400, detail="E-mail inválido.")
    if len((body.password or "")) < _m._PW_MIN:
        raise HTTPException(status_code=400, detail="A senha precisa ter ao menos 8 caracteres.")
    domain = _extract_domain(body.project_url)
    if not domain:
        raise HTTPException(status_code=400, detail="URL do projeto inválida.")

    store = get_target_store()
    if await store.get_user_by_email(email):
        raise HTTPException(status_code=409, detail="Já existe uma conta com este e-mail.")
    pw_hash = auth_users.hash_password(body.password)
    user = await store.create_user(email, pw_hash, name=(body.full_name or None),
                                    email_confirmed=False, source="signup")
    if user is None:
        raise HTTPException(status_code=409, detail="Já existe uma conta com este e-mail.")
    account_id = user["id"]

    await store.set_account_type(account_id, "developer")
    await store.set_account_dev_profile(account_id, full_name=body.full_name,
                                        company_name_dev=body.company)
    # Plano Free + trial Pro 14 dias.
    free = await store.get_gate_plan_by_slug("free")
    now = _now()
    await store.set_account_gate_plan(account_id, (free or {}).get("id"), now,
                                      now + timedelta(days=_TRIAL_DAYS))
    # API key (exibida UMA VEZ).
    full_key, prefix, key_hash = generate_api_key()
    await store.create_gate_api_key(account_id, prefix, key_hash, name="default")
    # 1º projeto.
    project = await store.create_gate_project(
        account_id, name=(body.company or domain), url=body.project_url.strip(), domain=domain)

    resp = JSONResponse({"account_id": account_id, "api_key": full_key,
                         "project_id": (project or {}).get("id")})
    _m._set_session_cookie(resp, auth_users.create_user_token(user))
    return resp


# --------------------------------------------------------------------------- #
# 2. Regenerar API key (JWT de usuário — dashboard)
# --------------------------------------------------------------------------- #

@router.post("/account/gate/regenerate-key")
async def gate_regenerate_key(request: Request) -> dict:
    """Revoga TODAS as keys ativas da conta e emite uma nova (exibida UMA VEZ)."""
    user = await auth_users.require_user(request)
    store = get_target_store()
    await store.revoke_gate_api_keys(user["id"])
    full_key, prefix, key_hash = generate_api_key()
    await store.create_gate_api_key(user["id"], prefix, key_hash, name="default")
    return {"api_key": full_key, "prefix": prefix}


@router.get("/account/gate/keys")
async def gate_list_keys(request: Request) -> dict:
    """Lista as keys da conta (prefixo/estado/uso — NUNCA o valor)."""
    user = await auth_users.require_user(request)
    keys = await get_target_store().list_gate_api_keys(user["id"])
    return {"keys": keys}


# --------------------------------------------------------------------------- #
# 3. Verificação de domínio do projeto (API key) — reusa o KL-99
# --------------------------------------------------------------------------- #

class GateVerifyStartBody(BaseModel):
    method: str


@router.post("/gate/projects/{project_id}/verify/start")
async def gate_verify_start(project_id: int, body: GateVerifyStartBody, request: Request) -> dict:
    """Gera o desafio (meta_tag|dns_txt|html_file) e devolve as instruções. Reusa o mecanismo do
    KL-99 (`_verify_instructions`); o token vive no `config` do projeto (TTL 7 dias)."""
    import api.main as _m
    ctx = await authenticate_api_key(request)
    store = get_target_store()
    project = await store.get_gate_project(project_id, ctx["account_id"])
    if not project:
        raise HTTPException(status_code=404, detail="Projeto não encontrado.")
    method = (body.method or "").strip()
    if method not in _m._DOMAIN_VERIFY_METHODS:
        raise HTTPException(status_code=400, detail="Método de verificação inválido.")
    token = secrets.token_urlsafe(32)
    await store.start_gate_project_verification(project_id, ctx["account_id"], method, token)
    return {"method": method, "domain": project["domain"], "challenge": token,
            "instructions": _m._verify_instructions(method, token, project["domain"])}


@router.post("/gate/projects/{project_id}/verify/check")
async def gate_verify_check(project_id: int, request: Request) -> dict:
    """Confere o desafio no site. Se comprovado: `verified=true`. Rate limit 10/h/IP."""
    import api.main as _m
    ctx = await authenticate_api_key(request)
    ok, retry = await _m._redis_allow("gate_verify", _m._client_ip(request), 10, 3600,
                                      _gate_verify_hits)
    if not ok:
        raise HTTPException(status_code=429, detail="Muitas verificações. Aguarde um pouco.",
                            headers={"Retry-After": str(retry)})
    store = get_target_store()
    project = await store.get_gate_project(project_id, ctx["account_id"])
    if not project:
        raise HTTPException(status_code=404, detail="Projeto não encontrado.")
    challenge = await store.get_gate_verification_challenge(project_id, ctx["account_id"])
    if not challenge:
        return {"status": "no_pending"}
    ok_ctrl = await _m._check_domain_control(challenge["method"], challenge["token"], project["domain"])
    if not ok_ctrl:
        return {"status": "not_found"}
    await store.mark_gate_project_verified(project_id, challenge["method"])
    return {"status": "verified", "verified": True}


# --------------------------------------------------------------------------- #
# 4. Convite dono→dev
# --------------------------------------------------------------------------- #

class GateInviteBody(BaseModel):
    domain: str
    dev_email: str


@router.post("/account/gate/invite")
async def gate_invite(body: GateInviteBody, request: Request) -> dict:
    """O dono VERIFICADO de um domínio convida um dev para escaneá-lo. Requer nível ≥ 3 E posse
    verificada DESTE domínio. Rate limit 10/h/IP. Envia e-mail com o token."""
    import api.main as _m
    user = await auth_users.require_user(request)
    _m._require_level(user, 3)
    ok, retry = await _m._redis_allow("gate_invite", _m._client_ip(request), 10, 3600,
                                      _gate_invite_hits)
    if not ok:
        raise HTTPException(status_code=429, detail="Muitos convites. Aguarde um pouco.",
                            headers={"Retry-After": str(retry)})
    domain = _m._norm_domain(body.domain)
    dev_email = (body.dev_email or "").lower().strip()
    if not domain:
        raise HTTPException(status_code=400, detail="Domínio inválido.")
    if not _m._ACCOUNT_EMAIL_RE.match(dev_email):
        raise HTTPException(status_code=400, detail="E-mail do dev inválido.")
    store = get_target_store()
    if not await store.user_owns_verified_domain(user["id"], domain):
        raise HTTPException(status_code=403, detail="Você não é o dono verificado deste domínio.")
    token = secrets.token_urlsafe(32)
    invite = await store.create_gate_invite(domain, user["id"], dev_email, token)
    accept_url = f"https://klarim.net/gate/invite/{token}"
    owner_name = (user.get("name") or user.get("full_name") or "").strip()
    _m._spawn(_send_gate_invite_email(dev_email, owner_name, domain, accept_url))
    return {"invite_id": invite["id"], "status": "sent"}


async def _send_gate_invite_email(dev_email: str, owner_name: str, domain: str, accept_url: str) -> None:
    """Fire-and-forget: convite ao dev (transacional). Silencioso se o e-mail estiver desligado."""
    import api.main as _m
    try:
        mailer = _m._mailer()
        if mailer is None:
            return
        await mailer.send_gate_invite(dev_email, owner_name, domain, accept_url)
    except Exception as exc:  # noqa: BLE001 - nunca derruba o request
        print(f"[gate] falha ao enviar convite p/ {dev_email}: {exc!r}", flush=True)


@router.get("/gate/invite/{token}")
async def gate_invite_info(token: str) -> dict:
    """Info pública do convite (para a página de aceite renderizar). Não expõe e-mails."""
    invite = await get_target_store().get_gate_invite_by_token(token)
    if not invite:
        raise HTTPException(status_code=404, detail="Convite não encontrado.")
    dev_has_account = bool(await get_target_store().get_user_by_email(invite["dev_email"]))
    return {"domain": invite["domain"], "status": _invite_status(invite),
            "dev_has_account": dev_has_account}


@router.post("/gate/invite/{token}/accept")
async def gate_invite_accept(token: str, request: Request) -> dict:
    """O dev LOGADO aceita o convite: cria/marca o projeto do domínio como verificado por convite
    (`method='invite'`, `invited_by`=dono). O e-mail logado precisa ser o convidado (anti-hijack)."""
    user = await auth_users.require_user(request)
    store = get_target_store()
    invite = await store.get_gate_invite_by_token(token)
    if not invite:
        raise HTTPException(status_code=404, detail="Convite não encontrado.")
    status = _invite_status(invite)
    if status != "pending":
        raise HTTPException(status_code=400, detail=f"Convite {status}.")
    if (user.get("email") or "").lower() != (invite["dev_email"] or "").lower():
        raise HTTPException(status_code=403, detail="Este convite é para outro e-mail.")

    domain = invite["domain"]
    project = await store.create_gate_project(
        user["id"], name=domain, url=f"https://{domain}", domain=domain,
        verified=True, verification_method="invite", invited_by=invite["owner_account_id"])
    if project is None:   # já existe → marca verificado por convite
        existing = await store.get_gate_project_by_domain(user["id"], domain)
        if existing:
            await store.mark_gate_project_verified(existing["id"], "invite",
                                                   invited_by=invite["owner_account_id"])
            project = existing
    await store.mark_gate_invite_accepted(invite["id"])
    # O dev vira 'developer' (ou 'both' se também é dono de site).
    cur_type = (await store.get_account_gate_fields(user["id"]) or {}).get("account_type") or "owner"
    await store.set_account_type(user["id"], "both" if cur_type in ("owner", "both") else "developer")
    return {"status": "accepted", "domain": domain, "project_id": (project or {}).get("id")}


@router.delete("/account/gate/invite/{invite_id}")
async def gate_invite_revoke(invite_id: int, request: Request) -> dict:
    """O dono revoga o convite e REMOVE o projeto correspondente do dev (perde o acesso ao scan)."""
    user = await auth_users.require_user(request)
    store = get_target_store()
    revoked = await store.revoke_gate_invite(invite_id, user["id"])
    if not revoked:
        raise HTTPException(status_code=404, detail="Convite não encontrado.")
    dev = await store.get_user_by_email(revoked["dev_email"])
    if dev:
        await store.delete_gate_project_by_domain(dev["id"], revoked["domain"])
    return {"status": "revoked"}


@router.get("/account/gate/invites")
async def gate_list_invites(request: Request) -> dict:
    """Convites emitidos pelo dono (dashboard)."""
    user = await auth_users.require_user(request)
    invites = await get_target_store().list_gate_invites(user["id"])
    for inv in invites:
        inv["status"] = _invite_status(inv)   # status efetivo (expiração por relógio)
    return {"invites": invites}


# --------------------------------------------------------------------------- #
# 5. Projetos + plano (leitura do dashboard/CLI)
# --------------------------------------------------------------------------- #

@router.get("/gate/projects")
async def gate_list_projects(request: Request) -> dict:
    """Projetos da conta (API key). Inclui o plano efetivo + os checks permitidos."""
    ctx = await authenticate_api_key(request)
    projects = await get_target_store().list_gate_projects(ctx["account_id"])
    plan = ctx["plan"] or {}
    return {"projects": projects, "plan": {"slug": plan.get("slug"), "name": plan.get("name")},
            "allowed_checks": get_allowed_checks(plan)}


class GateProjectBody(BaseModel):
    name: Optional[str] = None
    url: str


@router.post("/gate/projects")
async def gate_create_project(body: GateProjectBody, request: Request) -> dict:
    """Cria um projeto (respeitando o limite de domínios do plano). Nasce NÃO verificado."""
    ctx = await authenticate_api_key(request)
    await enforce_domain_limit(ctx["account_id"], ctx["plan"] or {})
    domain = _extract_domain(body.url)
    if not domain:
        raise HTTPException(status_code=400, detail="URL inválida.")
    store = get_target_store()
    project = await store.create_gate_project(
        ctx["account_id"], name=(body.name or domain), url=body.url.strip(), domain=domain)
    if project is None:
        raise HTTPException(status_code=409, detail="Você já tem um projeto para este domínio.")
    return {"project": project,
            "next_step": "Verifique o domínio via /api/gate/projects/{id}/verify/start"}


# --------------------------------------------------------------------------- #
# 6. Scan (API key) — a engine roda no SERVIDOR; o client só envia URL + key
# --------------------------------------------------------------------------- #

class GateScanBody(BaseModel):
    url: str
    fail_on: Optional[str] = None
    timeout: Optional[int] = None
    metadata: Optional[dict] = None


@router.post("/gate/scan")
async def gate_scan(body: GateScanBody, request: Request) -> dict:
    """Roda a engine do Gate contra `url` no SERVIDOR e devolve o resultado (síncrono, <60s). Só
    escaneia domínio REGISTRADO como projeto e VERIFICADO (exceto planos com `scan_third_party`).
    Os checks rodados são os do plano — os bloqueados voltam em `checks_blocked` (CTA de upgrade)."""
    ctx = await authenticate_api_key(request)
    plan = ctx["plan"] or {}
    domain = _extract_domain(body.url)
    if not domain:
        raise HTTPException(status_code=400, detail="URL inválida.")
    store = get_target_store()
    project = await store.get_gate_project_by_domain(ctx["account_id"], domain)
    if not project:
        raise HTTPException(status_code=403,
                            detail="Domínio não registrado como projeto. Crie-o em /api/gate/projects.")
    if not project.get("verified") and not plan.get("scan_third_party"):
        raise HTTPException(status_code=403,
                            detail="Domínio não verificado. Verifique em "
                                   "/api/gate/projects/{id}/verify/start.")
    # Consome 1 crédito de scan/dia (atômico) ANTES de rodar — invalidez de domínio já barrou acima.
    await enforce_scan_limit(ctx["account_id"], plan)

    allowed = get_allowed_checks(plan)
    blocked = [c for c in ALL_CHECK_NAMES if c not in allowed]
    scan_url = body.url.strip()
    if "://" not in scan_url:
        scan_url = "https://" + scan_url
    fail_on = (body.fail_on or "critical").strip().lower()
    timeout = max(5, min(int(body.timeout or 60), 180))

    config = GateConfig(target=scan_url, fail_on=fail_on, checks=allowed, timeout=timeout)
    report = await run_all(url=scan_url, timeout=timeout, checks=allowed, config=config)
    results = [_serialize_result(r) for r in report.results]
    passed = _passed_for(report, fail_on)

    run_id = await store.create_gate_run(
        project_id=project["id"], account_id=ctx["account_id"], url=scan_url,
        score=report.score, passed=passed, fail_on=fail_on, duration_ms=report.duration_ms,
        results=results, checks_run=allowed, checks_blocked=blocked, metadata=(body.metadata or {}))

    return {"run_id": run_id, "url": scan_url, "score": report.score, "passed": passed,
            "duration_ms": report.duration_ms, "critical": report.critical_count,
            "high": report.high_count, "medium": report.medium_count, "results": results,
            "checks_run": allowed, "checks_blocked": blocked, "plan": plan.get("name"),
            "dashboard_url": f"https://klarim.net/dashboard/gate/runs/{run_id}"}


@router.get("/gate/runs")
async def gate_list_runs(request: Request, project_id: Optional[int] = Query(default=None),
                         limit: int = Query(default=20, ge=1, le=200)) -> dict:
    """Runs da conta (sumário, sem `results`). Filtro opcional por `project_id`."""
    ctx = await authenticate_api_key(request)
    runs = await get_target_store().list_gate_runs(
        account_id=ctx["account_id"], project_id=project_id, limit=limit)
    return {"runs": runs}


@router.get("/gate/runs/{run_id}")
async def gate_get_run(run_id: int, request: Request) -> dict:
    """Detalhe de UM run da conta (com `results`). 404 se não é da conta."""
    ctx = await authenticate_api_key(request)
    run = await get_target_store().get_gate_run(run_id, account_id=ctx["account_id"])
    if not run:
        raise HTTPException(status_code=404, detail="Run não encontrado.")
    return {"run": run}
