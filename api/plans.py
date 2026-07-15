"""Planos, assinaturas e trial reverse de 30 dias (KL-44 Guardião Digital, P1).

Lógica de negócio sobre as tabelas `plans`/`subscriptions`/`subscription_history`
(criadas em `discovery/store.py::_SCHEMA`). O acesso ao banco é sempre via `store`
(TargetStore) — este módulo só orquestra + calcula datas de trial/expiração.

⚠️ Não há tabela `accounts`: a "conta" é o `users`. `account_id` == `users.id`.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

TRIAL_DAYS = 30
DEFAULT_TRIAL_PLAN = "pro"


def _store():
    # Resolve o store no momento da chamada — respeita monkeypatch de
    # discovery.store.get_target_store nos testes.
    import discovery.store as ds
    return ds.get_target_store()


def _as_dt(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def trial_days_left(trial_ends_at: Any, now: Optional[datetime] = None) -> Optional[int]:
    """Dias inteiros restantes do trial (>= 0), ou None se não há trial."""
    te = _as_dt(trial_ends_at)
    if te is None:
        return None
    now = now or datetime.now(timezone.utc)
    secs = (te - now).total_seconds()
    return max(0, int(secs // 86400) + (1 if secs > 0 else 0)) if secs > 0 else 0


async def get_plan(plan_id: str) -> Optional[Dict[str, Any]]:
    return await _store().get_plan(plan_id)


async def get_plans() -> List[Dict[str, Any]]:
    return await _store().list_plans(active_only=True)


async def _merge(row: Optional[Dict[str, Any]], plan: Dict[str, Any],
                 account_id: int, status: str) -> Dict[str, Any]:
    """Assinatura + plano num único dict (para a API/dashboard)."""
    row = row or {}
    return {
        "account_id": account_id,
        "plan_id": plan["id"],
        "plan_name": plan["name"],
        "status": status,
        "trial_ends_at": row.get("trial_ends_at"),
        "trial_days_left": trial_days_left(row.get("trial_ends_at")) if status == "trial" else None,
        "started_at": row.get("started_at"),
        "expires_at": row.get("expires_at"),
        "billing_cycle": row.get("billing_cycle") or "monthly",
        "max_sites": plan["max_sites"],
        "plan": plan,
    }


async def _maybe_expire_trial(store, row: Dict[str, Any]) -> Dict[str, Any]:
    """Expiração lazy do trial (na leitura): trial vencido → status=expired, plano=free."""
    if row.get("status") == "trial":
        te = _as_dt(row.get("trial_ends_at"))
        if te is not None and te < datetime.now(timezone.utc):
            await store.update_subscription(row["account_id"], status="expired", plan_id="free")
            await store.log_subscription_change(
                row["account_id"], row.get("plan_id"), "free", "trial", "expired",
                changed_by="system", reason="trial expirado")
            row = {**row, "status": "expired", "plan_id": "free"}
    return row


async def get_subscription(account_id: int) -> Dict[str, Any]:
    """Assinatura da conta com o plano junto. Sem assinatura → free. Aplica a
    expiração lazy do trial (o enforcement passa a usar os limites do free)."""
    store = _store()
    row = await store.get_subscription_row(account_id)
    if row is None:
        plan = await store.get_plan("free")
        return await _merge(None, plan or {"id": "free", "name": "Free", "max_sites": 1},
                            account_id, status="free")
    row = await _maybe_expire_trial(store, row)
    plan = await store.get_plan(row["plan_id"]) or await store.get_plan("free")
    return await _merge(row, plan or {"id": "free", "name": "Free", "max_sites": 1},
                        account_id, status=row["status"])


async def create_subscription(
    account_id: int, plan_id: str = DEFAULT_TRIAL_PLAN, is_trial: bool = True,
    trial_ends_at: Any = None, changed_by: str = "system",
) -> Dict[str, Any]:
    """Cria a assinatura de uma conta. Free → status='free', sem trial. Trial →
    status='trial', trial_ends_at = now + 30d (ou o valor informado)."""
    store = _store()
    if plan_id == "free" or not is_trial:
        status, te = ("free", None) if plan_id == "free" else ("active", None)
    else:
        status = "trial"
        te = trial_ends_at or (datetime.now(timezone.utc) + timedelta(days=TRIAL_DAYS))
    row = await store.upsert_subscription(account_id, plan_id, status, trial_ends_at=te)
    await store.log_subscription_change(
        account_id, None, plan_id, None, status, changed_by=changed_by,
        reason="assinatura criada")
    return row


async def change_plan(account_id: int, new_plan_id: str, changed_by: str = "admin",
                      reason: Optional[str] = None) -> Dict[str, Any]:
    """Muda o plano. Era trial e novo é free → status='free' (sai do trial). Era trial
    e novo é pro/agency → mantém o trial_ends_at. Registra no histórico."""
    store = _store()
    cur = await store.get_subscription_row(account_id)
    old_plan = cur.get("plan_id") if cur else None
    old_status = cur.get("status") if cur else None
    if new_plan_id == "free":
        new_status = "free"
    elif old_status == "trial":
        new_status = "trial"   # mantém o trial ao trocar de plano pago
    else:
        new_status = "active"
    row = await store.update_subscription(account_id, plan_id=new_plan_id, status=new_status)
    if row is None:  # não havia assinatura ainda
        row = await store.upsert_subscription(account_id, new_plan_id, new_status)
    await store.log_subscription_change(
        account_id, old_plan, new_plan_id, old_status, new_status,
        changed_by=changed_by, reason=reason or "mudança de plano")
    return row


async def extend_trial(account_id: int, days: int, changed_by: str = "admin") -> Dict[str, Any]:
    """Estende o trial_ends_at em N dias (a partir do valor atual, ou de agora)."""
    store = _store()
    cur = await store.get_subscription_row(account_id)
    base = _as_dt(cur.get("trial_ends_at")) if cur else None
    base = base if base and base > datetime.now(timezone.utc) else datetime.now(timezone.utc)
    new_te = base + timedelta(days=int(days))
    new_status = "trial"
    row = await store.update_subscription(account_id, trial_ends_at=new_te, status=new_status)
    await store.log_subscription_change(
        account_id, cur.get("plan_id") if cur else None, cur.get("plan_id") if cur else "pro",
        cur.get("status") if cur else None, new_status, changed_by=changed_by,
        reason=f"trial estendido em {days} dias")
    return row


async def set_status(account_id: int, status: str, changed_by: str = "admin",
                     reason: Optional[str] = None) -> Dict[str, Any]:
    """Muda o status da assinatura (admin). Registra no histórico."""
    store = _store()
    cur = await store.get_subscription_row(account_id)
    extra = {}
    if status == "cancelled":
        extra["cancelled_at"] = datetime.now(timezone.utc)
    row = await store.update_subscription(account_id, status=status, **extra)
    await store.log_subscription_change(
        account_id, cur.get("plan_id") if cur else None, cur.get("plan_id") if cur else "free",
        cur.get("status") if cur else None, status, changed_by=changed_by,
        reason=reason or "mudança de status")
    return row


async def get_subscription_stats() -> Dict[str, Any]:
    """Contagens por plano/status, trials ativos, expirando em 7d e taxa de conversão."""
    store = _store()
    groups = await store.subscription_group_counts()
    by_plan: Dict[str, int] = {}
    by_status: Dict[str, int] = {}
    total = 0
    for g in groups:
        n = int(g["n"])
        total += n
        by_plan[g["plan_id"]] = by_plan.get(g["plan_id"], 0) + n
        by_status[g["status"]] = by_status.get(g["status"], 0) + n
    trials_active = by_status.get("trial", 0)
    trials_expiring_7d = await store.count_trials_expiring(7)
    paid = by_status.get("active", 0)
    converted_base = paid + trials_active + by_status.get("expired", 0)
    conversion_rate = round(paid / converted_base * 100, 1) if converted_base else 0.0
    return {
        "total_accounts": total,
        "by_plan": by_plan,
        "by_status": by_status,
        "trials_active": trials_active,
        "trials_expiring_7d": trials_expiring_7d,
        "conversion_rate": conversion_rate,
    }


async def seed_existing_accounts() -> Dict[str, Any]:
    """Backfill idempotente: dá assinatura às contas que ainda não têm (KL-44 P1).
    Criada há < 30 dias → Pro trial (trial_ends_at = created_at + 30d); >= 30 dias → Free."""
    store = _store()
    rows = await store.users_without_subscription()
    now = datetime.now(timezone.utc)
    pro_trial = free = 0
    for u in rows:
        created = _as_dt(u.get("created_at")) or now
        if (now - created).total_seconds() / 86400.0 < TRIAL_DAYS:
            await create_subscription(u["id"], "pro", is_trial=True,
                                      trial_ends_at=created + timedelta(days=TRIAL_DAYS))
            pro_trial += 1
        else:
            await create_subscription(u["id"], "free", is_trial=False)
            free += 1
    return {"total": len(rows), "pro_trial": pro_trial, "free": free}


async def list_subscribers(plan_id: Optional[str] = None, status: Optional[str] = None,
                           search: Optional[str] = None, limit: int = 25,
                           offset: int = 0) -> List[Dict[str, Any]]:
    """Lista de assinantes (conta + plano + status + sites), com dias de trial restantes."""
    rows = await _store().list_subscribers(plan_id=plan_id, status=status, search=search,
                                           limit=limit, offset=offset)
    for r in rows:
        r["trial_days_left"] = (trial_days_left(r.get("trial_ends_at"))
                                if r.get("status") == "trial" else None)
    return rows
