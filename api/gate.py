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

import asyncio
import base64
import hashlib
import logging
import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from api import auth_users
from api import gate_rate_limiter as gate_rl   # KL-153 — rate limiting de 3 camadas + abuso
from api.validators import validate_cpf        # KL-153 — validação de CPF (KYC)
from discovery.store import get_target_store
from security_gate.config import GateConfig, load_config
from security_gate.engine import _DEFAULT_ORDER as _ENGINE_ORDER, run_all
from security_gate.models import SEVERITY_RANK, Severity, Status
from security_gate import vendor as _vendor

router = APIRouter()
logger = logging.getLogger("gate")

# KL-153 — mensagem única de conta suspensa (todos os endpoints Gate devolvem 403 com este corpo).
_SUSPENDED_MSG = ("Conta suspensa por atividade incomum. "
                  "Entre em contato com suporte@klarim.net.")
_SUPPORT_EMAIL = "suporte@klarim.net"   # KL-156 — fallback quando o pagamento não está configurado
_GATE_UPGRADE_ATTEMPTS: dict = {}


def _kyc_complete(cpf, address, phone, email_confirmed) -> bool:
    """KL-156 — `kyc_completed` = CPF válido + endereço (≥10 chars) + telefone + **e-mail
    CONFIRMADO**. O `email_confirmed` é a ÚNICA verificação de identidade REAL (código no signup);
    `phone_verified` é placeholder p/ SMS futuro e NÃO gateia mais. Defesa-em-profundidade: o
    endpoint já devolve 403 sem e-mail confirmado, mas a condição também o exige."""
    return bool(cpf and address and len(str(address)) >= 10 and phone and email_confirmed)

# Lista canônica dos checks do Gate (para o enforcement `["all"]`).
ALL_CHECK_NAMES: List[str] = list(_ENGINE_ORDER)

# KL-158 — removido o `_TRIAL_DAYS` (trial Pro automático): todo dev começa no Free; Pro exige pagamento.
_KEY_PREFIX = "KLM_"
_KEY_GRACE_MIN = 60   # KL-151 P4 — a key antiga vale +1h após a regeneração (CI em andamento)
_RPM_BY_SLUG = {"free": 10, "pro": 30, "team": 60, "enterprise": 120}   # req/min por key

# Buckets de fallback in-memory do rate limit (reusa `api.main._redis_allow`).
_gate_register_hits: dict = {}
_gate_invite_hits: dict = {}
_gate_verify_hits: dict = {}

# KL-152 P3 — fallback in-memory dos PDFs de relatório quando o Redis está fora (dev/testes).
_VENDOR_REPORTS: dict = {}
_VENDOR_REPORT_MAX = 50


# --------------------------------------------------------------------------- #
# Audit log (KL-151 P4) — compliance Enterprise. NUNCA guarda o VALOR da key.
# --------------------------------------------------------------------------- #

def _client_meta(request: Optional[Request]) -> Tuple[Optional[str], Optional[str]]:
    if request is None:
        return None, None
    try:
        ip = (request.headers.get("cf-connecting-ip")
              or (request.client.host if request.client else None))
    except Exception:  # noqa: BLE001
        ip = None
    ua = (request.headers.get("user-agent") or "")[:500] or None
    return ip, ua


async def log_gate_audit(account_id: int, action: str, request: Optional[Request] = None,
                         key_id: Optional[int] = None, domain: Optional[str] = None,
                         detail: Optional[dict] = None, cpf: Optional[str] = None,
                         url_scanned: Optional[str] = None, score: Optional[int] = None,
                         passed: Optional[bool] = None) -> None:
    """Registra uma ação no `gate_audit_log`. Fail-safe (nunca derruba a ação). O `detail` NUNCA
    contém o valor de uma API key — só o prefixo. KL-153: nos scans, grava `cpf`/`url_scanned`/
    `domain`/`score`/`passed` (compliance — rastreio por CPF)."""
    ip, ua = _client_meta(request)
    try:
        await get_target_store().insert_gate_audit(
            account_id=account_id, action=action, key_id=key_id, target_domain=domain,
            detail=detail or {}, ip_address=ip, user_agent=ua, cpf=cpf, url_scanned=url_scanned,
            domain=domain, score=score, passed=passed)
    except Exception as exc:  # noqa: BLE001 - audit best-effort
        print(f"[gate] audit falhou ({action}): {exc!r}", flush=True)


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
    revogada (fora do grace). Atualiza `last_used_at` e aplica o rate limit por MINUTO por key."""
    key = (request.headers.get("X-API-Key") or "").strip()
    if not key or not key.startswith(_KEY_PREFIX):
        raise HTTPException(status_code=401, detail="API key inválida.")
    store = get_target_store()
    record = await store.get_gate_api_key_by_hash(_hash_key(key))
    if not record:
        raise HTTPException(status_code=401, detail="API key inválida ou revogada.")
    if not record.get("is_active"):
        # KL-151 P4: aceita uma key revogada DENTRO do grace period (rotação sem quebrar CI).
        ge = _to_utc(record.get("grace_expires_at"))
        if not ge or ge < _now():
            raise HTTPException(status_code=401, detail="API key inválida ou revogada.")
    await store.touch_gate_api_key(record["id"])
    plan = await get_effective_gate_plan(record["account_id"])
    await _enforce_rpm(record["id"], plan)
    return {"account_id": record["account_id"], "key_id": record["id"], "plan": plan}


async def _enforce_rpm(key_id: int, plan: Optional[dict]) -> None:
    """Rate limit por MINUTO por key (Redis: `gate_rpm:{key}:{minuto}`). Fail-open sem Redis."""
    redis = _scan_redis()
    if redis is None:
        return
    max_rpm = _RPM_BY_SLUG.get((plan or {}).get("slug"), 10)
    try:
        rkey = f"gate_rpm:{int(key_id)}:{int(time.time()) // 60}"
        n = await redis.incr(rkey)
        if n == 1:
            await redis.expire(rkey, 60)
        if n > max_rpm:
            raise HTTPException(status_code=429,
                                detail=f"Rate limit: {max_rpm} requisições/minuto. Aguarde.")
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001 - Redis instável → não bloqueia
        pass


async def _resolve_gate_account(request: Request) -> dict:
    """Auth do PORTAL/CLI: aceita API key (header `X-API-Key`) OU a sessão de usuário (JWT cookie
    do dashboard). Retorna `{account_id, key_id, plan}`. 401 se nenhum dos dois."""
    if (request.headers.get("X-API-Key") or "").strip():
        return await authenticate_api_key(request)
    user = await auth_users.require_user(request)   # 401 se sem sessão
    plan = await get_effective_gate_plan(user["id"])
    return {"account_id": user["id"], "key_id": None, "plan": plan}


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


def _redact_third_party(results: List[dict]) -> None:
    """KL-151 P4 — scan de terceiro (Enterprise `scan_third_party`): NÃO vaza path/valor de
    credencial nem caminho de recurso exposto; só a CATEGORIA + severidade do risco. Mutação
    in-place dos dicts já serializados (não afeta score/counts, que vêm do report cru)."""
    for r in results:
        if r.get("category") == "credentials":
            r["detail"] = "Credential finding (redigido — scan de terceiro)"
            r["path"] = "[redacted]"
        elif r.get("category") == "exposure" and r.get("status") == "fail":
            r["detail"] = f"Recurso exposto detectado ({r.get('severity')})"
            r["path"] = "[redacted]"


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
# KL-153 — KYC, filtragem do resultado do scan por nível de KYC, scan avulso
# --------------------------------------------------------------------------- #

_KYC_MESSAGE = ("Complete seu cadastro (CPF + endereço + telefone) para ver os detalhes de cada "
                "verificação, recomendações e o histórico de scans.")


def _access_level(kyc_completed: bool) -> str:
    return "complete" if kyc_completed else "basic"


def _aggregate_categories(results: List[dict]) -> List[dict]:
    """Agrega os checks por categoria em `{name, status, checks_total, checks_passed,
    checks_failed}` (sem vazar os checks individuais — é o que o nível 'basic' vê)."""
    order: List[str] = []
    cats: dict = {}
    for r in results:
        c = r.get("category") or "outros"
        if c not in cats:
            cats[c] = {"name": c, "checks_total": 0, "checks_passed": 0, "checks_failed": 0}
            order.append(c)
        d = cats[c]
        d["checks_total"] += 1
        if r.get("status") == "pass":
            d["checks_passed"] += 1
        elif r.get("status") == "fail":
            d["checks_failed"] += 1
    for c in order:
        cats[c]["status"] = "fail" if cats[c]["checks_failed"] > 0 else "pass"
    return [cats[c] for c in order]


def _ci_snippet(url: str, fail_on: str) -> str:
    """Snippet de GitHub Actions pré-preenchido (só no nível 'complete')."""
    return (
        "# .github/workflows/security-gate.yml\n"
        "name: Security Gate\n"
        "on: [push]\n"
        "jobs:\n"
        "  security-gate:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - run: pip install httpx\n"
        f"      - run: python klarim_gate_cli.py scan {url} --fail-on {fail_on}\n"
        "        env:\n"
        "          KLARIM_API_KEY: ${{ secrets.KLARIM_API_KEY }}\n"
    )


async def _scan_history(store, account_id: int, domain: str, exclude_run_id: int,
                        limit: int = 10) -> List[dict]:
    """Runs anteriores do MESMO domínio pela mesma conta (só no nível 'complete')."""
    try:
        runs = await store.list_gate_runs(account_id=account_id, limit=50)
    except Exception:  # noqa: BLE001 - histórico é best-effort
        return []
    out = []
    for r in runs or []:
        if r.get("id") == exclude_run_id:
            continue
        if _extract_domain(r.get("url") or "") != domain:
            continue
        out.append({"id": r.get("id"), "score": r.get("score"), "passed": r.get("passed"),
                    "created_at": _iso(r.get("created_at"))})
        if len(out) >= limit:
            break
    return out


def _build_scan_response(*, run_id, scan_url, report, results, passed, fail_on, allowed, blocked,
                         plan_name, kyc_completed, history, ci_snippet) -> dict:
    """Monta a resposta do scan FILTRADA por KYC. 'basic' (sem KYC): score + categorias com
    contagens (sem checks individuais/paths/recomendações/histórico). 'complete' (com KYC): tudo."""
    base = {
        "run_id": run_id, "url": scan_url, "score": report.score, "passed": passed,
        "threshold": fail_on, "fail_on": fail_on, "duration_ms": report.duration_ms,
        "critical": report.critical_count, "high": report.high_count, "medium": report.medium_count,
        "checks_run": allowed, "checks_blocked": blocked, "plan": plan_name,
        "categories": _aggregate_categories(results),
        "access_level": _access_level(kyc_completed),
        "dashboard_url": f"https://klarim.net/dashboard/gate/runs/{run_id}",
    }
    if kyc_completed:
        base["results"] = results
        base["history"] = history
        base["ci_snippet"] = ci_snippet
    else:
        base["kyc_required_for_details"] = True
        base["kyc_message"] = _KYC_MESSAGE
    return base


async def _resolve_scan_project(store, account_id: int, plan: dict, acct: dict,
                                project_id: Optional[int], domain: str):
    """Resolve o projeto do scan e devolve `(project|None, third_party)`.

    - `project_id` explícito → valida posse + verificação (comportamento clássico).
    - senão, casa por domínio um projeto EXISTENTE (backward compat com o CI atual).
    - senão → **scan avulso** (KL-153): sem projeto; exige `email_confirmed`.
    """
    def _verify_gate(project):
        if not project.get("verified") and not plan.get("scan_third_party"):
            raise HTTPException(status_code=403,
                                detail="Domínio não verificado. Verifique em "
                                       "/api/gate/projects/{id}/verify/start.")
        return (not project.get("verified")) and bool(plan.get("scan_third_party"))

    if project_id is not None:
        project = await store.get_gate_project_by_id(int(project_id))
        if not project or project.get("account_id") != account_id:
            raise HTTPException(status_code=404, detail="Projeto não encontrado.")
        return project, _verify_gate(project)

    project = await store.get_gate_project_by_domain(account_id, domain)
    if project:
        return project, _verify_gate(project)

    # Scan avulso: sem projeto, sem verificação de domínio. Exige e-mail confirmado.
    if not acct.get("email_confirmed"):
        raise HTTPException(status_code=403,
                            detail="Confirme seu e-mail antes de escanear.")
    return None, False


async def provision_gate_developer(store, account_id: int) -> str:
    """KL-153/KL-158 — promove a conta a `developer`, concede o plano **Free (SEM trial)** e cria a
    API key. Devolve a key COMPLETA (exibida UMA VEZ). Usado pelo signup `source=security-gate`.
    KL-158: removido o trial Pro automático — Pro exige pagamento; todo dev começa no Free."""
    await store.set_account_type(account_id, "developer")
    free = await store.get_gate_plan_by_slug("free")
    await store.set_account_gate_plan(account_id, (free or {}).get("id"), None, None)   # Free, sem trial
    full_key, prefix, key_hash = generate_api_key()
    await store.create_gate_api_key(account_id, prefix, key_hash, name="default")
    await log_gate_audit(account_id, "key_created", detail={"key_prefix": prefix})
    await log_gate_audit(account_id, "gate_activated", detail={"source": "security-gate"})
    return full_key


async def _create_gate_pix_charge(amount_cents: int, description: str) -> dict:
    """Cria a cobrança PIX na AbacatePay (seam isolado — os testes monkeypatcham isto)."""
    import api.main as _m
    client = _m.AbacatePayClient(_m._api_key())
    return await client.create_pix_charge(amount_cents, description)


# --------------------------------------------------------------------------- #
# 1. Registro dev (público) — cria conta developer + API key + projeto + trial Pro
# --------------------------------------------------------------------------- #

class GateRegisterBody(BaseModel):
    email: str
    password: str
    full_name: Optional[str] = None
    company: Optional[str] = None
    project_url: str


async def _account_exists_response(store, account_id: Optional[int]) -> JSONResponse:
    """409 estruturado quando o e-mail já tem conta na Klarim (a landing/registro mostra a
    mensagem certa + link de login em vez de um erro genérico). Se o Gate JÁ está ativo, orienta
    a só logar; senão, a logar e ativar no dashboard (`activate_after_login`)."""
    fields = (await store.get_account_gate_fields(account_id) or {}) if account_id else {}
    if _gate_active(fields.get("account_type")):
        return JSONResponse(status_code=409, content={
            "error": "account_exists", "login_url": "/entrar",
            "message": "Esta conta já tem o Security Gate ativo. Faça login para acessar."})
    return JSONResponse(status_code=409, content={
        "error": "account_exists", "login_url": "/entrar", "activate_after_login": True,
        "message": "Você já tem conta na Klarim. Faça login e ative o Security Gate no dashboard."})


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
    existing = await store.get_user_by_email(email)
    if existing:
        return await _account_exists_response(store, existing["id"])
    pw_hash = auth_users.hash_password(body.password)
    user = await store.create_user(email, pw_hash, name=(body.full_name or None),
                                    email_confirmed=False, source="signup")
    if user is None:   # corrida: outra request criou a conta neste meio-tempo
        again = await store.get_user_by_email(email)
        return await _account_exists_response(store, again["id"] if again else None)
    account_id = user["id"]

    await store.set_account_type(account_id, "developer")
    await store.set_account_dev_profile(account_id, full_name=body.full_name,
                                        company_name_dev=body.company)
    # KL-158: plano Free SEM trial (Pro exige pagamento).
    free = await store.get_gate_plan_by_slug("free")
    await store.set_account_gate_plan(account_id, (free or {}).get("id"), None, None)
    # API key (exibida UMA VEZ).
    full_key, prefix, key_hash = generate_api_key()
    await store.create_gate_api_key(account_id, prefix, key_hash, name="default")
    # 1º projeto.
    project = await store.create_gate_project(
        account_id, name=(body.company or domain), url=body.project_url.strip(), domain=domain)

    await log_gate_audit(account_id, "key_created", request, detail={"key_prefix": prefix})
    if project:
        await log_gate_audit(account_id, "project_created", request, domain=domain,
                             detail={"project_id": project["id"], "domain": domain})

    resp = JSONResponse({"account_id": account_id, "api_key": full_key,
                         "project_id": (project or {}).get("id")})
    _m._set_session_cookie(resp, auth_users.create_user_token(user))
    return resp


# --------------------------------------------------------------------------- #
# 2. Regenerar API key (JWT de usuário — dashboard)
# --------------------------------------------------------------------------- #

@router.post("/account/gate/regenerate-key")
async def gate_regenerate_key(request: Request) -> dict:
    """Revoga as keys ativas COM grace period de 1h (a antiga vale até lá — o CI em andamento não
    quebra) e emite uma nova (exibida UMA VEZ)."""
    user = await auth_users.require_user(request)
    store = get_target_store()
    old_prefixes = await store.revoke_gate_api_keys_with_grace(user["id"], grace_minutes=_KEY_GRACE_MIN)
    full_key, prefix, key_hash = generate_api_key()
    await store.create_gate_api_key(user["id"], prefix, key_hash, name="default")
    await log_gate_audit(user["id"], "key_regenerated", request,
                         detail={"old_prefix": old_prefixes[0] if old_prefixes else None,
                                 "new_prefix": prefix})
    return {"api_key": full_key, "prefix": prefix, "grace_period_minutes": _KEY_GRACE_MIN}


@router.get("/account/gate/keys")
async def gate_list_keys(request: Request) -> dict:
    """Lista as keys da conta (prefixo/estado/uso — NUNCA o valor)."""
    user = await auth_users.require_user(request)
    keys = await get_target_store().list_gate_api_keys(user["id"])
    return {"keys": keys}


# --------------------------------------------------------------------------- #
# 2b. Ativação do Gate numa conta EXISTENTE (owner/técnico logado) — sem novo registro
# --------------------------------------------------------------------------- #

def _gate_active(account_type: Optional[str]) -> bool:
    """Uma conta tem o Gate ativo quando é `developer` ou `both`."""
    return (account_type or "owner") in ("developer", "both")


@router.get("/account/gate/status")
async def gate_status(request: Request) -> dict:
    """Estado do Gate para a conta LOGADA — a landing `/security-gate` (CTA ativar × abrir) e o
    dashboard (KL-153: wizard × KYC × upgrade) usam para decidir o que renderizar. 401 se não há
    sessão (a landing trata como "não logado")."""
    user = await auth_users.require_user(request)
    store = get_target_store()
    fields = await store.get_account_gate_fields(user["id"]) or {}
    account_type = fields.get("account_type") or "owner"
    active = _gate_active(account_type)
    plan = await get_effective_gate_plan(user["id"]) if active else None
    keys = await store.list_gate_api_keys(user["id"])
    active_key = next((k for k in keys if k.get("is_active")), None)
    projects = await store.list_gate_projects(user["id"]) if active else []
    kyc = bool(fields.get("kyc_completed"))
    slug = (plan or {}).get("slug") or "free"
    used, limit = await gate_rl.user_hour_usage(_scan_redis(), user["id"], slug)
    prefix = (active_key or {}).get("key_prefix")
    return {
        "logged_in": True, "gate_active": active, "account_type": account_type,
        "is_developer": active,
        "kyc_completed": kyc,
        "has_api_key": bool(active_key), "has_key": bool(active_key),
        "api_key_prefix": prefix, "key_prefix": prefix,
        "has_projects": bool(projects), "projects_count": len(projects),
        # `plan` mantém o NOME (backward compat com a landing); `plan_slug` traz o slug.
        "plan": (plan or {}).get("name"), "plan_slug": slug if active else None,
        "scans_used_hour": used, "scans_limit_hour": limit,
        "access_level": _access_level(kyc),
        "suspended": bool(fields.get("suspended")),
        "dashboard_url": "/dashboard/gate"}


class KYCBody(BaseModel):
    cpf: str
    address: Optional[str] = None
    phone: Optional[str] = None


@router.post("/account/kyc")
async def account_kyc(body: KYCBody, request: Request) -> dict:
    """KL-153/KL-156 — KYC progressivo. Exige sessão + e-mail confirmado. `kyc_completed` vira TRUE
    só quando CPF válido + endereço (≥10 chars) + telefone + **e-mail confirmado** (KL-156 — o
    e-mail é a verificação de identidade REAL; `phone_verified` é placeholder de SMS e não gateia)."""
    user = await auth_users.require_user(request)
    store = get_target_store()
    fields = await store.get_account_gate_fields(user["id"]) or {}
    if not fields.get("email_confirmed"):
        raise HTTPException(status_code=403,
                            detail="Confirme seu email antes de completar o cadastro.")
    try:
        cpf = validate_cpf(body.cpf)
    except ValueError:
        raise HTTPException(status_code=422,
                            detail="CPF inválido. Verifique os dígitos e tente novamente.")
    if await store.is_cpf_taken(cpf, exclude_account_id=user["id"]):
        raise HTTPException(status_code=409, detail="Este CPF já está vinculado a outra conta.")

    address = (body.address or "").strip()
    phone = (body.phone or "").strip()
    # KL-156: exige e-mail confirmado (defesa-em-profundidade — o endpoint já 403 acima).
    kyc_completed = _kyc_complete(cpf, address, phone, fields.get("email_confirmed"))
    phone_verified = bool(phone)
    await store.update_user_kyc(user["id"], cpf=cpf, address=(address or None),
                               phone=(phone or None), phone_verified=phone_verified,
                               kyc_completed=kyc_completed, kyc_completed_at=_now())
    await log_gate_audit(user["id"], "kyc_completed" if kyc_completed else "kyc_updated", request,
                         detail={"kyc_completed": kyc_completed}, cpf=cpf)
    updated = await store.get_account_gate_fields(user["id"]) or {}
    return {"kyc_completed": kyc_completed,
            "kyc_completed_at": _iso(updated.get("kyc_completed_at")),
            "access_level": _access_level(kyc_completed)}


class GateUpgradeBody(BaseModel):
    plan: str


@router.post("/account/gate/upgrade")
async def gate_upgrade(body: GateUpgradeBody, request: Request) -> dict:
    """KL-153 — upgrade de plano do Gate via PIX (AbacatePay). Gera uma cobrança avulsa do mês (a
    integração não tem assinatura recorrente — recorrência é escopo futuro); o webhook de pagamento
    confirmado ativa o `gate_plan_id`. Nível ≥ 2 (pagamento exige senha)."""
    import api.main as _m
    user = await auth_users.require_user(request)
    _m._require_level(user, 2)
    slug = (body.plan or "").lower().strip()
    if slug not in ("pro", "team"):
        raise HTTPException(status_code=400, detail="Plano inválido para upgrade.")
    ok, retry = await _m._redis_allow("gate_upgrade", _m._client_ip(request), 10, 3600,
                                      _GATE_UPGRADE_ATTEMPTS)
    if not ok:
        raise HTTPException(status_code=429, detail="Muitas tentativas. Aguarde um pouco.",
                            headers={"Retry-After": str(retry)})
    store = get_target_store()
    target = await store.get_gate_plan_by_slug(slug)
    if not target or int(target.get("price_brl") or 0) <= 0:
        raise HTTPException(status_code=400, detail="Plano indisponível para compra.")
    current = await get_effective_gate_plan(user["id"]) or {}
    if (current.get("slug") or "free") == slug:
        raise HTTPException(status_code=409, detail=f"Você já está no plano {target.get('name')}.")
    amount = int(target["price_brl"])
    price_display = f"R$ {amount // 100}/mês"
    # KL-156: sem pagamento configurado → fallback claro (nunca loading silencioso). Responde 200
    # com `fallback` + e-mail de suporte para o front mostrar a mensagem acionável.
    if not _m._payments_enabled():
        return {"fallback": True, "plan": slug, "price_display": price_display,
                "contact_email": _SUPPORT_EMAIL,
                "message": f"Para assinar o plano {target.get('name')}, entre em contato pelo "
                           f"e-mail {_SUPPORT_EMAIL}."}
    try:
        data = await _create_gate_pix_charge(
            amount, f"Klarim Security Gate {target.get('name')} — assinatura mensal")
    except Exception as exc:  # noqa: BLE001 - falha na AbacatePay
        raise HTTPException(status_code=502, detail=f"Falha ao criar cobrança: {exc}") from exc
    charge_id = data.get("id")
    if not charge_id:
        raise HTTPException(status_code=502, detail="AbacatePay não retornou o id da cobrança.")
    # Reusa a tabela de pagamentos de assinatura; o prefixo `gate:` roteia a ativação no webhook.
    await store.create_subscription_payment(
        user["id"], f"gate:{slug}", amount, charge_id, data.get("brCode"),
        data.get("brCodeBase64"), expires_at=data.get("expiresAt"))
    await log_gate_audit(user["id"], "upgrade_requested", request,
                         detail={"plan": slug, "charge_id": charge_id})
    # KL-156: o front mostra o PIX (QR `br_code_base64` + copia-e-cola `br_code`) e faz polling em
    # /account/upgrade/status?charge_id= — NÃO abre `checkout_url` (que só reabria o dashboard).
    return {"checkout_url": f"/dashboard/gate?upgrade={charge_id}", "plan": slug,
            "price_display": price_display, "charge_id": charge_id,
            "br_code": data.get("brCode"), "br_code_base64": data.get("brCodeBase64"),
            "expires_at": data.get("expiresAt")}


@router.post("/account/gate/activate")
async def gate_activate(request: Request) -> dict:
    """Ativa o Security Gate numa conta EXISTENTE (owner ou técnico já logado) — sem passar pelo
    registro. Idempotente: se já está ativo, devolve o estado atual (não regera a key). Caso
    contrário: promove o `account_type` (owner→both, senão developer), gera a API key (exibida
    UMA VEZ, só se ainda não houver uma ativa) e concede o trial Pro de 14 dias (plano base Free)
    caso a conta ainda não tenha plano/trial. Nível ≥ 1."""
    import api.main as _m
    user = await auth_users.require_user(request)
    _m._require_level(user, 1)
    store = get_target_store()
    fields = await store.get_account_gate_fields(user["id"]) or {}
    account_type = fields.get("account_type") or "owner"

    # Já ativo → estado atual (não regera key nem reinicia trial).
    if _gate_active(account_type):
        keys = await store.list_gate_api_keys(user["id"])
        active = next((k for k in keys if k.get("is_active")), None)
        plan = await get_effective_gate_plan(user["id"]) or {}
        return {"status": "already_active", "plan": plan.get("name"),
                "key_prefix": (active or {}).get("key_prefix"), "has_key": bool(active),
                "dashboard_url": "/dashboard/gate"}

    # 1. Promove o tipo da conta.
    new_type = "both" if account_type == "owner" else "developer"
    await store.set_account_type(user["id"], new_type)

    # 2. API key — só se ainda não há uma ativa (exibida UMA VEZ).
    keys = await store.list_gate_api_keys(user["id"])
    active_key = next((k for k in keys if k.get("is_active")), None)
    api_key_display = None
    key_prefix = (active_key or {}).get("key_prefix")
    if not active_key:
        full_key, prefix, key_hash = generate_api_key()
        await store.create_gate_api_key(user["id"], prefix, key_hash, name="default")
        api_key_display, key_prefix = full_key, prefix
        await log_gate_audit(user["id"], "key_created", request, detail={"key_prefix": prefix})

    # 3. KL-158: plano Free SEM trial — só p/ conta nova (sem plano/trial). Pro exige pagamento.
    # Contas com trial LEGADO (KL-151/153) NÃO são alteradas retroativamente (o if as pula).
    trial_ends = _to_utc(fields.get("gate_trial_ends_at"))
    if not fields.get("gate_plan_id") and not trial_ends:
        free = await store.get_gate_plan_by_slug("free")
        await store.set_account_gate_plan(user["id"], (free or {}).get("id"), None, None)

    # 4. Audit.
    await log_gate_audit(user["id"], "gate_activated", request,
                         detail={"previous_type": account_type, "new_type": new_type})

    plan = await get_effective_gate_plan(user["id"]) or {}
    return {"status": "activated", "api_key": api_key_display, "plan": plan.get("name"),
            "key_prefix": key_prefix, "has_key": True, "trial_ends_at": _iso(trial_ends),
            "dashboard_url": "/dashboard/gate"}


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
    ctx = await _resolve_gate_account(request)
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
    ctx = await _resolve_gate_account(request)
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
    await log_gate_audit(ctx["account_id"], "project_verified", request, key_id=ctx.get("key_id"),
                         domain=project["domain"],
                         detail={"project_id": project_id, "method": challenge["method"]})
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
    await log_gate_audit(user["id"], "invite_sent", request, domain=domain,
                         detail={"domain": domain, "dev_email": dev_email})
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
    await log_gate_audit(user["id"], "invite_accepted", request, domain=domain,
                         detail={"domain": domain, "invite_id": invite["id"]})
    return {"status": "accepted", "domain": domain, "project_id": (project or {}).get("id")}


@router.delete("/account/gate/invite/{invite_id}")
async def gate_invite_revoke(invite_id: int, request: Request) -> dict:
    """O dono revoga o convite e REMOVE o projeto correspondente do dev (perde o acesso ao scan) +
    avisa o dev por e-mail (transacional). Audit: `invite_revoked`."""
    import api.main as _m
    user = await auth_users.require_user(request)
    store = get_target_store()
    revoked = await store.revoke_gate_invite(invite_id, user["id"])
    if not revoked:
        raise HTTPException(status_code=404, detail="Convite não encontrado.")
    dev = await store.get_user_by_email(revoked["dev_email"])
    if dev:
        await store.delete_gate_project_by_domain(dev["id"], revoked["domain"])
    _m._spawn(_send_gate_revoked_email(revoked["dev_email"], revoked["domain"]))
    await log_gate_audit(user["id"], "invite_revoked", request, domain=revoked["domain"],
                         detail={"domain": revoked["domain"], "dev_email": revoked["dev_email"]})
    return {"status": "revoked"}


async def _send_gate_revoked_email(dev_email: str, domain: str) -> None:
    """Fire-and-forget: avisa o dev que o acesso ao domínio foi revogado. Silencioso se e-mail off."""
    import api.main as _m
    try:
        mailer = _m._mailer()
        if mailer is None:
            return
        await mailer.send_gate_access_revoked(dev_email, domain)
    except Exception as exc:  # noqa: BLE001
        print(f"[gate] falha ao avisar revogação p/ {dev_email}: {exc!r}", flush=True)


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
    ctx = await _resolve_gate_account(request)
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
    ctx = await _resolve_gate_account(request)
    await enforce_domain_limit(ctx["account_id"], ctx["plan"] or {})
    domain = _extract_domain(body.url)
    if not domain:
        raise HTTPException(status_code=400, detail="URL inválida.")
    store = get_target_store()
    project = await store.create_gate_project(
        ctx["account_id"], name=(body.name or domain), url=body.url.strip(), domain=domain)
    if project is None:
        raise HTTPException(status_code=409, detail="Você já tem um projeto para este domínio.")
    await log_gate_audit(ctx["account_id"], "project_created", request, key_id=ctx.get("key_id"),
                         domain=domain, detail={"project_id": project["id"], "domain": domain})
    return {"project": project,
            "next_step": "Verifique o domínio via /api/gate/projects/{id}/verify/start"}


# --------------------------------------------------------------------------- #
# 6. Scan (API key) — a engine roda no SERVIDOR; o client só envia URL + key
# --------------------------------------------------------------------------- #

class GateScanBody(BaseModel):
    url: str
    project_id: Optional[int] = None   # KL-153: explícito → valida o projeto; ausente → domínio/avulso
    fail_on: Optional[str] = None
    timeout: Optional[int] = None
    metadata: Optional[dict] = None


@router.post("/gate/scan")
async def gate_scan(body: GateScanBody, request: Request):
    """Roda a engine do Gate contra `url` no SERVIDOR e devolve o resultado (síncrono, <60s).

    KL-153: aceita **scan avulso** (sem projeto/verificação — exige e-mail confirmado) além do scan
    de projeto verificado. Antes do scan: conta suspensa → 403; rate limiting de 3 camadas (IP →
    conta → domínio → intervalo) → 429; detecção de abuso (>20 domínios/24h → suspende). O resultado
    é **filtrado por KYC**: sem KYC → resumido (score + categorias); com KYC → completo."""
    import api.main as _m
    ctx = await _resolve_gate_account(request)
    account_id = ctx["account_id"]
    plan = ctx["plan"] or {}
    store = get_target_store()
    acct = await store.get_account_gate_fields(account_id) or {}

    # (0) Conta suspensa → 403 em TODOS os endpoints Gate.
    if acct.get("suspended"):
        return JSONResponse(status_code=403, content={"detail": _SUSPENDED_MSG, "suspended": True})

    domain = _extract_domain(body.url)
    if not domain:
        raise HTTPException(status_code=400, detail="URL inválida.")

    # (1) Resolve projeto (explícito / por domínio / avulso). Levanta os 403/404 apropriados.
    project, third_party = await _resolve_scan_project(store, account_id, plan, acct,
                                                       body.project_id, domain)

    # (2) Rate limiting de 3 camadas + intervalo (fail-fast). Sem Redis → fail-open.
    redis = _scan_redis()
    ip = _m._client_ip(request)
    limited = await gate_rl.enforce(redis, ip, account_id, plan, domain)
    if limited:
        await log_gate_audit(account_id, "scan_blocked", request, key_id=ctx.get("key_id"),
                             domain=domain, detail={"reason": limited["limit_type"]})
        return JSONResponse(status_code=429, content=limited,
                            headers={"Retry-After": str(limited["retry_after_seconds"])})

    # (3) Detecção de abuso: >20 domínios distintos/24h → suspende a conta.
    if await gate_rl.is_abuse(redis, account_id, domain):
        await store.set_user_suspended(account_id, True)
        domains = await gate_rl.get_distinct_domains(redis, account_id)
        await log_gate_audit(account_id, "abuse_detected", request, key_id=ctx.get("key_id"),
                             domain=domain, detail={"distinct_domains": domains[:50]})
        logger.warning("[gate] conta %s suspensa por abuso: %d domínios distintos em 24h",
                       account_id, len(domains))
        return JSONResponse(status_code=403, content={"detail": _SUSPENDED_MSG, "suspended": True})

    # (4) Teto diário do plano (existente).
    try:
        await enforce_scan_limit(account_id, plan)
    except HTTPException as exc:
        if exc.status_code == 429:
            await log_gate_audit(account_id, "scan_blocked", request, key_id=ctx.get("key_id"),
                                 domain=domain, detail={"reason": "daily_limit",
                                                        "limit": plan.get("scans_per_day")})
        raise

    # (5) Engine.
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
    if third_party:
        _redact_third_party(results)   # KL-151 P4: scan de terceiro não vaza path/credencial
    passed = _passed_for(report, fail_on)

    run_id = await store.create_gate_run(
        project_id=(project or {}).get("id"), account_id=account_id, url=scan_url,
        score=report.score, passed=passed, fail_on=fail_on, duration_ms=report.duration_ms,
        results=results, checks_run=allowed, checks_blocked=blocked, metadata=(body.metadata or {}))

    # (6) Audit obrigatório: cpf + url + domínio + score + passed (compliance — rastreio por CPF).
    await log_gate_audit(account_id, "scan", request, key_id=ctx.get("key_id"), domain=domain,
                         detail={"url": scan_url, "score": report.score, "passed": passed,
                                 "duration_ms": report.duration_ms, "plan": plan.get("name"),
                                 "third_party": third_party, "standalone": project is None},
                         cpf=acct.get("cpf"), url_scanned=scan_url, score=report.score, passed=passed)

    # (7) Resultado filtrado por KYC.
    kyc = bool(acct.get("kyc_completed"))
    history = await _scan_history(store, account_id, domain, run_id) if kyc else []
    snippet = _ci_snippet(scan_url, fail_on) if kyc else None
    return _build_scan_response(run_id=run_id, scan_url=scan_url, report=report, results=results,
                                passed=passed, fail_on=fail_on, allowed=allowed, blocked=blocked,
                                plan_name=plan.get("name"), kyc_completed=kyc, history=history,
                                ci_snippet=snippet)


@router.get("/gate/runs")
async def gate_list_runs(request: Request, project_id: Optional[int] = Query(default=None),
                         limit: int = Query(default=20, ge=1, le=200)) -> dict:
    """Runs da conta (sumário, sem `results`). Filtro opcional por `project_id`."""
    ctx = await _resolve_gate_account(request)
    runs = await get_target_store().list_gate_runs(
        account_id=ctx["account_id"], project_id=project_id, limit=limit)
    return {"runs": runs}


@router.get("/gate/runs/{run_id}")
async def gate_get_run(run_id: int, request: Request) -> dict:
    """Detalhe de UM run da conta (com `results`). 404 se não é da conta."""
    ctx = await _resolve_gate_account(request)
    run = await get_target_store().get_gate_run(run_id, account_id=ctx["account_id"])
    if not run:
        raise HTTPException(status_code=404, detail="Run não encontrado.")
    return {"run": run}


# --------------------------------------------------------------------------- #
# 6b. Fornecedores (Enterprise) — due diligence, comparativo, PDF, monitoramento (KL-152 P3)
# --------------------------------------------------------------------------- #

def _require_enterprise(plan: Optional[dict]) -> None:
    """403 se o plano não tem `scan_third_party` (capacidade Enterprise). Todos os endpoints de
    fornecedor exigem isto."""
    if not (plan or {}).get("scan_third_party"):
        raise HTTPException(status_code=403,
                            detail="Avaliação de fornecedores disponível apenas no plano Enterprise.")


class VendorBody(BaseModel):
    name: str
    url: str
    approval_threshold: Optional[int] = None
    critical_threshold: Optional[int] = None
    notify_vendor: Optional[bool] = None
    monitor_enabled: Optional[bool] = None
    monitor_interval_days: Optional[int] = None
    notes: Optional[str] = None


class VendorUpdateBody(BaseModel):
    name: Optional[str] = None
    approval_threshold: Optional[int] = None
    critical_threshold: Optional[int] = None
    notify_vendor: Optional[bool] = None
    monitor_enabled: Optional[bool] = None
    monitor_interval_days: Optional[int] = None
    notes: Optional[str] = None


class VendorReportBody(BaseModel):
    vendor_ids: List[int]
    title: Optional[str] = None


async def run_vendor_scan(account_id: int, vendor: dict, request: Optional[Request] = None) -> dict:
    """Escaneia o site do fornecedor (engine no servidor), REDIGE o resultado (terceiro), calcula o
    status vs thresholds, persiste o scan + atualiza o vendor e (opt-in) notifica o fornecedor.
    Reusado pelo endpoint e pelo worker de monitoramento."""
    store = get_target_store()
    plan = await get_effective_gate_plan(account_id) or {}
    allowed = get_allowed_checks(plan)
    scan_url = (vendor.get("url") or "").strip()
    if "://" not in scan_url:
        scan_url = "https://" + scan_url
    config = GateConfig(target=scan_url, fail_on="critical", checks=allowed, timeout=60)
    report = await run_all(url=scan_url, timeout=60, checks=allowed, config=config)
    payload = _vendor.build_vendor_scan_payload(
        report, int(vendor.get("approval_threshold") or 80), int(vendor.get("critical_threshold") or 0))
    scan_id = await store.create_gate_vendor_scan(
        vendor["id"], account_id, payload["score"], payload["passed"], payload["critical"],
        payload["high"], payload["medium"], payload["status"], payload["duration_ms"],
        payload["results"], payload["summary"])
    next_at = None
    if vendor.get("monitor_enabled"):
        next_at = _now() + timedelta(days=int(vendor.get("monitor_interval_days") or 30))
    await store.apply_gate_vendor_scan(vendor["id"], account_id, scan_id, payload["score"],
                                       payload["status"], next_at)
    await log_gate_audit(account_id, "vendor_scan", request, domain=vendor.get("domain"),
                         detail={"vendor_id": vendor["id"], "score": payload["score"],
                                 "status": payload["status"], "third_party": True})
    if vendor.get("notify_vendor"):
        import api.main as _m
        _m._spawn(_notify_vendor(account_id, vendor, payload["score"], scan_id))
    return {**payload, "vendor_id": vendor["id"], "scan_id": scan_id}


async def _notify_vendor(account_id: int, vendor: dict, score, scan_id: int) -> None:
    """Fire-and-forget: e-mail opt-in ao dono do site avaliado. Dedup 1/scan (Redis). Silencioso se
    o domínio não tem contato na base ou o e-mail está desligado."""
    import api.main as _m
    try:
        redis = _scan_redis()
        if redis is not None:  # dedup: 1 notificação por scan
            try:
                ok = await redis.set(f"gate:vendor_notify:{vendor['id']}:{scan_id}", "1",
                                     ex=86400, nx=True)
                if not ok:
                    return
            except Exception:  # noqa: BLE001 - Redis instável → segue (best-effort)
                pass
        store = get_target_store()
        to_email = await store.get_contact_email_for_domain(vendor.get("domain") or "")
        if not to_email:
            print(f"[gate] vendor notify: sem contato p/ {vendor.get('domain')}", flush=True)
            return
        prof = await store.get_enterprise_profile(account_id) or {}
        mailer = _m._mailer()
        if mailer is None:
            return
        await mailer.send_vendor_assessment(to_email, prof.get("name") or "Uma empresa",
                                            vendor.get("url"), vendor.get("domain"), int(score or 0))
    except Exception as exc:  # noqa: BLE001 - nunca derruba o scan
        print(f"[gate] vendor notify falhou: {exc!r}", flush=True)


@router.post("/gate/vendors")
async def gate_create_vendor(body: VendorBody, request: Request) -> dict:
    """Cria um fornecedor + roda o 1º scan (Enterprise). Retorna vendor + resultado do scan."""
    ctx = await _resolve_gate_account(request)
    _require_enterprise(ctx["plan"])
    domain = _extract_domain(body.url)
    if not domain:
        raise HTTPException(status_code=400, detail="URL inválida.")
    monitor = bool(body.monitor_enabled)
    interval = int(body.monitor_interval_days or 30)
    vendor = await get_target_store().create_gate_vendor(
        ctx["account_id"], name=body.name, url=body.url.strip(), domain=domain,
        approval_threshold=int(body.approval_threshold if body.approval_threshold is not None else 80),
        critical_threshold=int(body.critical_threshold if body.critical_threshold is not None else 0),
        notify_vendor=bool(body.notify_vendor), monitor_enabled=monitor,
        monitor_interval_days=interval,
        next_monitor_at=(_now() + timedelta(days=interval)) if monitor else None)
    await log_gate_audit(ctx["account_id"], "vendor_created", request, key_id=ctx.get("key_id"),
                         domain=domain, detail={"vendor_id": vendor["id"]})
    scan = await run_vendor_scan(ctx["account_id"], vendor, request)
    return {"vendor_id": vendor["id"], "vendor": {**vendor, "status": scan["status"],
            "last_scan_score": scan["score"]}, "scan": scan}


@router.get("/gate/vendors")
async def gate_list_vendors(request: Request) -> dict:
    """Lista os fornecedores da conta (Enterprise) com o último score/status."""
    ctx = await _resolve_gate_account(request)
    _require_enterprise(ctx["plan"])
    vendors = await get_target_store().list_gate_vendors(ctx["account_id"])
    return {"vendors": vendors}


@router.get("/gate/vendors/{vendor_id}")
async def gate_get_vendor(vendor_id: int, request: Request) -> dict:
    """Detalhe do fornecedor + histórico de scans (results já redigidos)."""
    ctx = await _resolve_gate_account(request)
    _require_enterprise(ctx["plan"])
    store = get_target_store()
    vendor = await store.get_gate_vendor(vendor_id, ctx["account_id"])
    if not vendor:
        raise HTTPException(status_code=404, detail="Fornecedor não encontrado.")
    scans = await store.list_gate_vendor_scans(vendor_id, ctx["account_id"], limit=20)
    return {"vendor": vendor, "scans": scans}


@router.put("/gate/vendors/{vendor_id}")
async def gate_update_vendor(vendor_id: int, body: VendorUpdateBody, request: Request) -> dict:
    """Edita thresholds/notify/monitor/notas do fornecedor."""
    ctx = await _resolve_gate_account(request)
    _require_enterprise(ctx["plan"])
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    vendor = await get_target_store().update_gate_vendor(vendor_id, ctx["account_id"], **fields)
    if not vendor:
        raise HTTPException(status_code=404, detail="Fornecedor não encontrado.")
    return {"vendor": vendor}


@router.delete("/gate/vendors/{vendor_id}")
async def gate_delete_vendor(vendor_id: int, request: Request) -> dict:
    """Remove o fornecedor (e o histórico via CASCADE)."""
    ctx = await _resolve_gate_account(request)
    _require_enterprise(ctx["plan"])
    ok = await get_target_store().delete_gate_vendor(vendor_id, ctx["account_id"])
    if not ok:
        raise HTTPException(status_code=404, detail="Fornecedor não encontrado.")
    await log_gate_audit(ctx["account_id"], "vendor_deleted", request, detail={"vendor_id": vendor_id})
    return {"status": "deleted"}


@router.post("/gate/vendors/{vendor_id}/scan")
async def gate_scan_vendor(vendor_id: int, request: Request) -> dict:
    """Re-escaneia o fornecedor agora (atualiza last_scan_* e status)."""
    ctx = await _resolve_gate_account(request)
    _require_enterprise(ctx["plan"])
    vendor = await get_target_store().get_gate_vendor(vendor_id, ctx["account_id"])
    if not vendor:
        raise HTTPException(status_code=404, detail="Fornecedor não encontrado.")
    scan = await run_vendor_scan(ctx["account_id"], vendor, request)
    return {"scan": scan}


async def _store_report_pdf(account_id: int, pdf: bytes) -> str:
    """Guarda o PDF por 1h (Redis; fallback in-memory) e devolve o report_id. Armazena em BASE64 —
    o cliente Redis do app usa `decode_responses=True`, então bytes crus de PDF não fariam round-trip."""
    report_id = secrets.token_urlsafe(16)
    key = f"gate:vreport:{account_id}:{report_id}"
    b64 = base64.b64encode(pdf).decode("ascii")
    redis = _scan_redis()
    stored = False
    if redis is not None:
        try:
            await redis.set(key, b64, ex=3600)
            stored = True
        except Exception:  # noqa: BLE001
            stored = False
    if not stored:
        if len(_VENDOR_REPORTS) >= _VENDOR_REPORT_MAX:
            _VENDOR_REPORTS.pop(next(iter(_VENDOR_REPORTS)))
        _VENDOR_REPORTS[key] = b64
    return report_id


@router.post("/gate/vendors/report")
async def gate_vendor_report(body: VendorReportBody, request: Request) -> dict:
    """Gera o PDF comparativo dos fornecedores escolhidos. Retorna um link temporário (1h)."""
    from reporter.gate_report import build_vendor_context, generate_vendor_report_pdf
    ctx = await _resolve_gate_account(request)
    _require_enterprise(ctx["plan"])
    store = get_target_store()
    ids = list(dict.fromkeys(body.vendor_ids or []))[:50]
    if not ids:
        raise HTTPException(status_code=422, detail="Informe ao menos um fornecedor.")
    vendors_data = []
    for vid in ids:
        v = await store.get_gate_vendor(vid, ctx["account_id"])
        if not v:
            continue
        scans = await store.list_gate_vendor_scans(vid, ctx["account_id"], limit=1)
        last = scans[0] if scans else {}
        vendors_data.append({
            "name": v.get("name"), "domain": v.get("domain"), "score": v.get("last_scan_score"),
            "status": v.get("status"), "critical": last.get("critical", 0),
            "high": last.get("high", 0), "medium": last.get("medium", 0),
            "summary": last.get("summary"), "categories": _vendor.vendor_categories(last.get("results") or []),
        })
    if not vendors_data:
        raise HTTPException(status_code=404, detail="Nenhum fornecedor válido.")
    prof = await store.get_enterprise_profile(ctx["account_id"]) or {}
    context = build_vendor_context(
        vendors_data, title=(body.title or "Avaliação de Fornecedores"),
        enterprise_name=prof.get("name"), cnpj=prof.get("company_cnpj"),
        generated_at=_now().strftime("%d/%m/%Y %H:%M UTC"))
    pdf = await generate_vendor_report_pdf(context)
    report_id = await _store_report_pdf(ctx["account_id"], pdf)
    await log_gate_audit(ctx["account_id"], "vendor_report", request,
                         detail={"vendor_ids": ids, "report_id": report_id})
    return {"report_id": report_id,
            "download_url": f"https://klarim.net/api/gate/vendors/report/{report_id}"}


def _pdf_response(pdf: bytes, filename: str) -> Response:
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{filename}"'})


@router.get("/gate/vendors/report/{report_id}")
async def gate_get_vendor_report(report_id: str, request: Request) -> Response:
    """Baixa um relatório comparativo gerado (link temporário de 1h)."""
    ctx = await _resolve_gate_account(request)
    _require_enterprise(ctx["plan"])
    key = f"gate:vreport:{ctx['account_id']}:{report_id}"
    b64 = None
    redis = _scan_redis()
    if redis is not None:
        try:
            b64 = await redis.get(key)
        except Exception:  # noqa: BLE001
            b64 = None
    if b64 is None:
        b64 = _VENDOR_REPORTS.get(key)
    if b64 is None:
        raise HTTPException(status_code=404, detail="Relatório expirado ou inexistente.")
    try:
        pdf = base64.b64decode(b64)
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=404, detail="Relatório inválido.")
    return _pdf_response(pdf, "klarim-fornecedores.pdf")


@router.get("/gate/runs/{run_id}/pdf")
async def gate_run_pdf(run_id: int, request: Request) -> Response:
    """Exporta um run (projeto próprio) como PDF compartilhável."""
    from reporter.gate_report import build_vendor_context, generate_vendor_report_pdf
    ctx = await _resolve_gate_account(request)
    store = get_target_store()
    run = await store.get_gate_run(run_id, account_id=ctx["account_id"])
    if not run:
        raise HTTPException(status_code=404, detail="Run não encontrado.")
    results = run.get("results") or []
    crit = sum(1 for r in results if r.get("status") == "fail" and r.get("severity") == "critical")
    high = sum(1 for r in results if r.get("status") == "fail" and r.get("severity") == "high")
    med = sum(1 for r in results if r.get("status") == "fail" and r.get("severity") == "medium")
    domain = _extract_domain(run.get("url") or "")
    vendor_data = [{
        "name": domain, "domain": domain, "score": run.get("score"),
        "status": "approved" if run.get("passed") else "rejected",
        "critical": crit, "high": high, "medium": med,
        "summary": _vendor.vendor_summary(results), "categories": _vendor.vendor_categories(results),
    }]
    prof = await store.get_enterprise_profile(ctx["account_id"]) or {}
    context = build_vendor_context(
        vendor_data, title=f"Relatório de scan — {domain}", enterprise_name=prof.get("name"),
        cnpj=prof.get("company_cnpj"), generated_at=_now().strftime("%d/%m/%Y %H:%M UTC"))
    pdf = await generate_vendor_report_pdf(context)
    return _pdf_response(pdf, f"klarim-{domain}.pdf")


# --------------------------------------------------------------------------- #
# 7. Planos públicos (landing) + info da key (portal) — KL-151 P3
# --------------------------------------------------------------------------- #

def _public_plan(p: dict) -> dict:
    """Campos do plano seguros p/ a landing (sem ids internos irrelevantes)."""
    return {"slug": p.get("slug"), "name": p.get("name"), "price_brl": p.get("price_brl"),
            "scans_per_day": p.get("scans_per_day"), "max_domains": p.get("max_domains"),
            "history_days": p.get("history_days"), "scan_third_party": p.get("scan_third_party"),
            "checks_allowed": get_allowed_checks(p),
            "checks_count": len(get_allowed_checks(p)),
            "notifications": p.get("notifications")}


@router.get("/gate/plans")
async def gate_public_plans() -> dict:
    """Planos ATIVOS (público, sem auth) — a landing renderiza a tabela a partir daqui, então uma
    edição no admin reflete SEM deploy."""
    plans = await get_target_store().list_gate_plans(active_only=True)
    return {"plans": [_public_plan(p) for p in plans]}


def _mask_key_prefix(prefix: str) -> str:
    """`KLM_ab...` mascarado para exibição (o valor completo nunca é recuperável)."""
    p = prefix or ""
    return f"{p}…" if p else "—"


@router.get("/account/gate/key-info")
async def gate_key_info(request: Request) -> dict:
    """Metadados da API key ativa da conta (prefixo/criação/último uso) — NUNCA o valor."""
    user = await auth_users.require_user(request)
    keys = await get_target_store().list_gate_api_keys(user["id"])
    active = next((k for k in keys if k.get("is_active")), None)
    if not active:
        return {"has_key": False}
    return {"has_key": True, "prefix": active.get("key_prefix"),
            "masked": _mask_key_prefix(active.get("key_prefix")),
            "name": active.get("name"),
            "created_at": _iso(active.get("created_at")),
            "last_used_at": _iso(active.get("last_used_at"))}


def _iso(v):
    return v.isoformat() if hasattr(v, "isoformat") else v


# --------------------------------------------------------------------------- #
# 8. Admin de planos (JWT admin via prefixo /admin) — KL-151 P3
# --------------------------------------------------------------------------- #

class GatePlanBody(BaseModel):
    name: Optional[str] = None
    slug: Optional[str] = None
    price_brl: Optional[int] = None
    scans_per_day: Optional[int] = None
    max_domains: Optional[int] = None
    history_days: Optional[int] = None
    trial_days: Optional[int] = None
    checks_allowed: Optional[list] = None
    scan_third_party: Optional[bool] = None
    notifications: Optional[list] = None
    active: Optional[bool] = None


@router.get("/admin/gate/plans")
async def admin_gate_plans(request: Request) -> dict:
    """Todos os planos (inclusive inativos), com a lista de checks disponíveis para o editor."""
    plans = await get_target_store().list_gate_plans(active_only=False)
    return {"plans": plans, "all_checks": list(ALL_CHECK_NAMES)}


@router.post("/admin/gate/plans")
async def admin_gate_create_plan(body: GatePlanBody, request: Request) -> dict:
    """Cria um plano (slug único)."""
    if not (body.name and body.slug):
        raise HTTPException(status_code=422, detail="Nome e slug são obrigatórios.")
    fields = {k: v for k, v in body.model_dump().items() if v is not None and k not in ("name", "slug")}
    plan = await get_target_store().create_gate_plan(body.name, body.slug, **fields)
    if plan is None:
        raise HTTPException(status_code=409, detail="Já existe um plano com este slug.")
    return {"plan": plan}


@router.put("/admin/gate/plans/{plan_id}")
async def admin_gate_update_plan(plan_id: int, body: GatePlanBody, request: Request) -> dict:
    """Edita um plano (o slug é imutável). Reflete no próximo scan (plano efetivo lido a cada auth)."""
    fields = {k: v for k, v in body.model_dump().items() if v is not None and k != "slug"}
    plan = await get_target_store().update_gate_plan(plan_id, **fields)
    if plan is None:
        raise HTTPException(status_code=404, detail="Plano não encontrado.")
    return {"plan": plan}


@router.get("/admin/gate/accounts")
async def admin_gate_accounts(request: Request) -> dict:
    """Contas dev + uso (plano, scans hoje, projetos, prefixo da key, fim do trial)."""
    accounts = await get_target_store().list_gate_dev_accounts()
    for a in accounts:
        a["gate_trial_ends_at"] = _iso(a.get("gate_trial_ends_at"))
    return {"accounts": accounts}


# ------------------------------------------------------------------------- #
# KL-160 — varredura de segurança da PLATAFORMA (self-scan pelo painel admin)
# ------------------------------------------------------------------------- #
# Roda o Security Gate contra o próprio klarim.net e salva em `platform_security_scans`. Assíncrono
# (não bloqueia a UI) — o POST dispara e devolve na hora; o painel faz polling do /status. Admin-only
# (o middleware `_admin_auth_mw` já exige o Bearer admin em qualquer rota /admin*). Rate limit 5min.

_SECSCAN_TARGET = "https://klarim.net"
_SECSCAN_COOLDOWN = 300                       # 1 varredura manual a cada 5 min
_SECSCAN_RUNNING_KEY = "admin:secscan:running"
_SECSCAN_COOLDOWN_KEY = "admin:secscan:cooldown"
_secscan_running_mem = {"v": False}           # fallback quando não há Redis (dev)


async def _run_platform_security_scan(store, redis) -> None:
    """Roda o Gate completo (todos os checks) contra o klarim.net e persiste o resultado. Alerta o
    operador se o score cair < 80 ou surgir um finding CRÍTICO. Fail-safe: erro vira um run com
    `error` (não trava). Sempre limpa o flag de 'running' no fim."""
    url = _SECSCAN_TARGET
    try:
        # KL-160 fix — usa a MESMA config do CLI (`security-gate.yml`): inclui `/api/scan/` na checagem
        # de rate limit (zona restrita 2r/s que dispara o 429) e a allowlist de exposição. Sem isto o
        # admin usava os defaults (`["/","/api/"]`, generosos) → rate_limit FAIL → 90 (CLI dava 100).
        # Caminho absoluto (raiz do repo, relativo a api/gate.py) — não depende do CWD do container.
        yml = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "security-gate.yml")
        config = load_config(yml)
        config.target = url
        config.fail_on = "critical"
        config.timeout = 60
        checks = config.checks or list(_ENGINE_ORDER)   # fail-safe: YAML sem checks → roda todos
        report = await run_all(url=url, timeout=config.timeout, checks=checks, config=config)
        results = [_serialize_result(r) for r in report.results]
        low = sum(1 for r in report.results
                  if r.status == Status.FAIL and r.severity == Severity.LOW)
        await store.create_platform_security_scan(
            url=url, score=report.score, passed=report.passed, critical=report.critical_count,
            high=report.high_count, medium=report.medium_count, low=low,
            duration_ms=report.duration_ms, results=results, error=report.error,
            triggered_by="admin")
        if (report.score is not None and report.score < 80) or report.critical_count > 0:
            await _alert_platform_scan(url, report)
    except Exception as exc:  # noqa: BLE001 - nunca deixa o flag preso; grava o erro
        logger.warning("[secscan] varredura falhou: %r", exc)
        try:
            await store.create_platform_security_scan(
                url=url, score=None, passed=None, critical=0, high=0, medium=0, low=0,
                duration_ms=None, results=None, error=str(exc), triggered_by="admin")
        except Exception:  # noqa: BLE001
            pass
    finally:
        _secscan_running_mem["v"] = False
        if redis is not None:
            try:
                await redis.delete(_SECSCAN_RUNNING_KEY)
            except Exception:  # noqa: BLE001
                pass


async def _alert_platform_scan(url: str, report) -> None:
    """Best-effort — avisa o operador (score baixo/critical). Reusa o mailer transacional."""
    try:
        import api.main as _m
        if not _m._email_enabled():
            return
        to = os.environ.get("LGPD_ADMIN_EMAIL", "klarimscan@gmail.com")
        subj = f"[Klarim] ⚠️ Varredura de segurança: {report.score}/100 "
        subj += "— CRÍTICO" if report.critical_count else "— score baixo"
        lines = "\n".join(f"- [{r.severity.value}] {r.check}: {r.detail}"
                          for r in report.results if r.status == Status.FAIL)
        text = (f"A varredura de segurança de {url} retornou {report.score}/100.\n\n"
                f"Críticos: {report.critical_count} · Altos: {report.high_count} · "
                f"Médios: {report.medium_count}\n\nFindings:\n{lines or '—'}\n\n"
                "Veja o detalhe no painel: /painel/sistema")
        await _m._mailer()._send({
            "from": _m._mailer().from_address, "to": [to],
            "subject": subj, "text": text,
        }, email_type="admin_alert", source="secscan", skip_blocklist=True)
    except Exception as exc:  # noqa: BLE001 - alerta é opcional
        logger.info("[secscan] alerta não enviado: %r", exc)


@router.post("/admin/security-scan")
async def admin_security_scan(request: Request) -> JSONResponse:
    """Dispara a varredura do Gate contra o klarim.net (assíncrona). 429 se dentro do cooldown de
    5 min; devolve `running` se já há uma em curso."""
    store = get_target_store()
    redis = _scan_redis()
    if redis is not None:
        try:
            if await redis.get(_SECSCAN_RUNNING_KEY):
                return JSONResponse({"status": "running"})
            ttl = await redis.ttl(_SECSCAN_COOLDOWN_KEY)
            if ttl and ttl > 0:
                return JSONResponse({"status": "cooldown", "retry_after": ttl}, status_code=429,
                                    headers={"Retry-After": str(ttl)})
            await redis.set(_SECSCAN_COOLDOWN_KEY, "1", ex=_SECSCAN_COOLDOWN)
            await redis.set(_SECSCAN_RUNNING_KEY, "1", ex=180)
        except Exception:  # noqa: BLE001 - Redis fora → segue com o guard in-memory
            pass
    if _secscan_running_mem["v"]:
        return JSONResponse({"status": "running"})
    _secscan_running_mem["v"] = True
    asyncio.create_task(_run_platform_security_scan(store, redis))
    return JSONResponse({"status": "started", "url": _SECSCAN_TARGET})


@router.get("/admin/security-scan/status")
async def admin_security_scan_status(request: Request) -> dict:
    """Estado da varredura (running) + último resultado + histórico (sumário)."""
    store = get_target_store()
    redis = _scan_redis()
    running = _secscan_running_mem["v"]
    if redis is not None:
        try:
            running = bool(await redis.get(_SECSCAN_RUNNING_KEY))
        except Exception:  # noqa: BLE001
            pass
    history = await store.list_platform_security_scans(limit=20)
    for h in history:
        h["created_at"] = _iso(h.get("created_at"))
    return {"running": running, "target": _SECSCAN_TARGET,
            "last": history[0] if history else None, "history": history}


@router.get("/admin/security-scan/{scan_id}")
async def admin_security_scan_detail(scan_id: int, request: Request) -> dict:
    """Uma varredura COMPLETA (com os checks detalhados) — para o detalhe expandível no painel."""
    scan = await get_target_store().get_platform_security_scan(scan_id)
    if not scan:
        raise HTTPException(status_code=404, detail="Varredura não encontrada.")
    scan["created_at"] = _iso(scan.get("created_at"))
    return {"scan": scan}


class AssignPlanBody(BaseModel):
    plan_id: int


@router.post("/admin/gate/accounts/{account_id}/plan")
async def admin_gate_assign_plan(account_id: int, body: AssignPlanBody, request: Request) -> dict:
    """Atribui um plano a uma conta dev (efeito imediato — sem trial). Zera o trial (o plano
    atribuído passa a valer diretamente). Audit: `plan_changed`."""
    store = get_target_store()
    plan = await store.get_gate_plan(body.plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Plano não encontrado.")
    old = await get_effective_gate_plan(account_id)
    await store.set_account_gate_plan(account_id, body.plan_id, trial_started_at=None,
                                      trial_ends_at=None)
    await log_gate_audit(account_id, "plan_changed", request,
                         detail={"old_plan": (old or {}).get("slug"), "new_plan": plan["slug"],
                                 "by_admin": True})
    return {"status": "assigned", "account_id": account_id, "plan": plan["slug"]}


class EnterpriseBody(BaseModel):
    cnpj: Optional[str] = None
    contract_url: Optional[str] = None
    notes: Optional[str] = None


@router.post("/admin/gate/accounts/{account_id}/enterprise")
async def admin_gate_enterprise(account_id: int, body: EnterpriseBody, request: Request) -> dict:
    """Grava CNPJ/contrato/notas de uma conta Enterprise (JWT admin). Audit: `enterprise_updated`."""
    await get_target_store().set_enterprise_fields(
        account_id, cnpj=body.cnpj, contract_url=body.contract_url, notes=body.notes)
    await log_gate_audit(account_id, "enterprise_updated", request,
                         detail={"cnpj_set": bool(body.cnpj), "contract_set": bool(body.contract_url)})
    return {"status": "saved", "account_id": account_id}


# --------------------------------------------------------------------------- #
# 9. Audit log — leitura (admin: todas as contas · dev: a própria) — KL-151 P4
# --------------------------------------------------------------------------- #

def _audit_ser(row: dict) -> dict:
    out = dict(row)
    out["created_at"] = _iso(out.get("created_at"))
    return out


@router.get("/admin/gate/audit")
async def admin_gate_audit(request: Request, account_id: Optional[int] = Query(default=None),
                           action: Optional[str] = Query(default=None),
                           limit: int = Query(default=50, ge=1, le=500)) -> dict:
    """Audit log de QUALQUER conta (JWT admin). Filtros opcionais por `account_id`/`action`."""
    rows = await get_target_store().list_gate_audit(account_id=account_id, action=action, limit=limit)
    return {"audit": [_audit_ser(r) for r in rows]}


@router.get("/account/gate/audit")
async def account_gate_audit(request: Request, action: Optional[str] = Query(default=None),
                             limit: int = Query(default=20, ge=1, le=200)) -> dict:
    """Audit log da PRÓPRIA conta (JWT/API key). Ownership enforcado (nunca vê o de outros)."""
    ctx = await _resolve_gate_account(request)
    rows = await get_target_store().list_gate_audit(account_id=ctx["account_id"], action=action,
                                                    limit=limit)
    return {"audit": [_audit_ser(r) for r in rows]}
