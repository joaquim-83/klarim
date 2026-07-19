"""KL-44 P6 — pagamento PIX (upgrade/downgrade), webhook e expiração de trial. Offline.

AbacatePay é mockado (nenhuma chamada de rede). `_sync_user_vigilias` é neutralizado nos
testes de pagamento (a sincronização de vigílias é coberta em test_vigilias/test_subscriptions)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import api.main as m
from api import auth_users, plans


# --------------------------------------------------------------------------- #
# FakeStore com estado real de assinatura + pagamentos
# --------------------------------------------------------------------------- #

_PLANS = {
    "free": {"id": "free", "name": "Free", "max_sites": 1},
    "pro": {"id": "pro", "name": "Pro", "max_sites": 5},
    "agency": {"id": "agency", "name": "Agency", "max_sites": 15},
}


class FakeStore:
    def __init__(self):
        self.users = {1: {"id": 1, "email": "u@x.com.br", "name": None, "is_active": True,
                          "plan": "free", "max_sites": 5}}
        self.subs = {}          # account_id -> row
        self.sub_pays = {}       # charge_id -> payment row
        self._pid = 1
        self.history = []

    # users
    async def get_user_by_id(self, uid):
        return self.users.get(int(uid))

    async def get_user_by_email(self, email, with_hash=False):
        for u in self.users.values():
            if u["email"] == email:
                return u
        return None

    # plans / subscriptions
    async def get_plan(self, plan_id):
        return _PLANS.get(plan_id)

    async def get_subscription_row(self, account_id):
        return self.subs.get(account_id)

    async def upsert_subscription(self, account_id, plan_id, status, trial_ends_at=None, **kw):
        row = {"account_id": account_id, "plan_id": plan_id, "status": status,
               "trial_ends_at": trial_ends_at}
        self.subs[account_id] = row
        return row

    async def update_subscription(self, account_id, **fields):
        row = self.subs.get(account_id) or {"account_id": account_id}
        row.update(fields)
        self.subs[account_id] = row
        return row

    async def log_subscription_change(self, *a, **k):
        self.history.append((a, k))

    # subscription_payments
    async def create_subscription_payment(self, user_id, plan, amount, charge_id, br_code,
                                          br_code_base64, expires_at=None):
        row = {"id": self._pid, "user_id": user_id, "plan": plan, "amount": amount,
               "provider_charge_id": charge_id, "br_code": br_code,
               "br_code_base64": br_code_base64, "status": "pending",
               "created_at": datetime.now(timezone.utc), "paid_at": None, "expires_at": expires_at}
        self._pid += 1
        self.sub_pays[charge_id] = row
        return row

    async def get_subscription_payment_by_charge(self, charge_id):
        return self.sub_pays.get(charge_id)

    async def mark_subscription_payment(self, charge_id, status):
        row = self.sub_pays.get(charge_id)
        if not row or row["status"] != "pending":
            return None   # idempotente: só transiciona de pending
        row["status"] = status
        if status == "paid":
            row["paid_at"] = datetime.now(timezone.utc)
        return row

    async def list_user_subscription_payments(self, user_id, limit=20):
        return [p for p in self.sub_pays.values() if p["user_id"] == user_id][:limit]

    # trial
    async def get_expired_trials(self):
        out = []
        for uid, s in self.subs.items():
            te = s.get("trial_ends_at")
            if s.get("status") == "trial" and te and te < datetime.now(timezone.utc):
                out.append({"user_id": uid, "plan_id": s["plan_id"], "trial_ends_at": te,
                            "email": self.users.get(uid, {}).get("email"), "name": None})
        return out

    async def get_trials_expiring_in(self, days):
        # Espelha o SQL real (trial_ends_at::date == today + N): compara por data.
        today = datetime.now(timezone.utc).date()
        out = []
        for uid, s in self.subs.items():
            te = s.get("trial_ends_at")
            if s.get("status") == "trial" and te and (te.date() - today).days == days:
                out.append({"user_id": uid, "plan_id": s["plan_id"], "trial_ends_at": te,
                            "email": self.users.get(uid, {}).get("email"), "name": None})
        return out

    async def disable_user_vigilias_except(self, user_id, keep):
        self.disabled = getattr(self, "disabled", [])
        self.disabled.append((user_id, list(keep)))
        return 0

    async def delete_unconfirmed_inactive_accounts(self, older_than_days=30):  # KL-82 Slice 2
        self.cleanup_called = getattr(self, "cleanup_called", 0) + 1
        return getattr(self, "cleanup_returns", 0)

    async def ensure_schema(self):
        pass

    async def get_setting(self, key, default=None):
        return default


class FakeAbacate:
    """Client AbacatePay mockado — sem rede."""
    def __init__(self, *a, **k):
        pass

    async def create_pix_charge(self, amount, description):
        return {"id": f"ch_{amount}", "brCode": "000201PIX...", "brCodeBase64": "iVBORw0KGgo=",
                "status": "PENDING", "expiresAt": None}

    async def check_payment(self, charge_id):
        return {"status": "PAID"}


@pytest.fixture
def store(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "k" * 64)
    monkeypatch.setenv("ABACATEPAY_API_KEY", "dev_key")   # _payments_enabled = True
    s = FakeStore()
    monkeypatch.setattr(m, "get_target_store", lambda: s)
    monkeypatch.setattr("discovery.store.get_target_store", lambda: s)
    monkeypatch.setattr(auth_users, "_secret", lambda: "k" * 64)
    monkeypatch.setattr(m, "AbacatePayClient", FakeAbacate)
    # a sincronização de vigílias é testada à parte
    async def _noop_sync(uid):
        return None
    monkeypatch.setattr(m, "_sync_user_vigilias", _noop_sync)
    # e-mail desligado nos testes de pagamento
    monkeypatch.setattr(m, "_email_enabled", lambda: False)
    for b in (m._upgrade_attempts,):
        b.clear()
    return s


@pytest.fixture
def client(store):
    return TestClient(m.app, raise_server_exceptions=False)


def _bearer(uid=1, email="u@x.com.br"):
    return {"Authorization": f"Bearer {auth_users.create_user_token({'id': uid, 'email': email})}"}


def _set_sub(store, plan, status, trial_ends_at=None):
    store.subs[1] = {"account_id": 1, "plan_id": plan, "status": status,
                     "trial_ends_at": trial_ends_at}


# --------------------------------------------------------------------------- #
# A) Upgrade — checkout PIX
# --------------------------------------------------------------------------- #

def test_upgrade_creates_pix_charge(client, store):
    _set_sub(store, "free", "free")
    r = client.post("/account/upgrade", json={"plan": "pro"}, headers=_bearer())
    assert r.status_code == 200
    j = r.json()
    assert j["plan"] == "pro" and j["amount"] == 1900 and j["br_code"] and j["charge_id"]
    assert j["charge_id"] in store.sub_pays and store.sub_pays[j["charge_id"]]["status"] == "pending"


def test_upgrade_rejects_same_or_lower(client, store):
    _set_sub(store, "agency", "active")
    assert client.post("/account/upgrade", json={"plan": "pro"}, headers=_bearer()).status_code == 400
    assert client.post("/account/upgrade", json={"plan": "agency"}, headers=_bearer()).status_code == 400


def test_upgrade_invalid_plan(client, store):
    _set_sub(store, "free", "free")
    assert client.post("/account/upgrade", json={"plan": "free"}, headers=_bearer()).status_code == 400
    assert client.post("/account/upgrade", json={"plan": "banana"}, headers=_bearer()).status_code == 400


def test_upgrade_requires_auth(client):
    assert client.post("/account/upgrade", json={"plan": "pro"}).status_code == 401


# --------------------------------------------------------------------------- #
# B) Confirmação de pagamento (idempotente) → ativa o plano
# --------------------------------------------------------------------------- #

def test_confirm_payment_activates_plan(client, store):
    import asyncio
    _set_sub(store, "pro", "trial", trial_ends_at=datetime.now(timezone.utc) + timedelta(days=5))
    asyncio.get_event_loop().run_until_complete(
        store.create_subscription_payment(1, "agency", 4900, "ch_x", "code", "b64"))
    # 1ª confirmação ativa
    activated = asyncio.get_event_loop().run_until_complete(m._confirm_subscription_payment("ch_x"))
    assert activated is True
    assert store.subs[1]["status"] == "active" and store.subs[1]["plan_id"] == "agency"
    assert store.subs[1]["trial_ends_at"] is None   # saiu do trial
    assert store.sub_pays["ch_x"]["status"] == "paid"
    # 2ª confirmação é no-op (idempotente)
    again = asyncio.get_event_loop().run_until_complete(m._confirm_subscription_payment("ch_x"))
    assert again is False


def test_upgrade_status_polls_and_activates(client, store):
    import asyncio
    _set_sub(store, "free", "free")
    asyncio.get_event_loop().run_until_complete(
        store.create_subscription_payment(1, "pro", 1900, "ch_poll", "code", "b64"))
    r = client.get("/account/upgrade/status?charge_id=ch_poll", headers=_bearer())
    assert r.status_code == 200 and r.json()["paid"] is True   # FakeAbacate.check → PAID
    assert store.subs[1]["plan_id"] == "pro" and store.subs[1]["status"] == "active"


def test_webhook_activates_subscription(client, store):
    _set_sub(store, "free", "free")
    import asyncio
    asyncio.get_event_loop().run_until_complete(
        store.create_subscription_payment(1, "pro", 1900, "ch_wh", "code", "b64"))
    # sem webhookSecret configurado (env vazio) → passa direto
    r = client.post("/webhooks/abacatepay",
                    json={"event": "billing.paid", "data": {"id": "ch_wh"}})
    assert r.status_code == 200
    assert store.subs[1]["status"] == "active" and store.subs[1]["plan_id"] == "pro"


def test_webhook_expired_marks_payment(client, store):
    import asyncio
    asyncio.get_event_loop().run_until_complete(
        store.create_subscription_payment(1, "pro", 1900, "ch_exp", "code", "b64"))
    client.post("/webhooks/abacatepay", json={"event": "billing.expired", "data": {"id": "ch_exp"}})
    assert store.sub_pays["ch_exp"]["status"] == "expired"


# --------------------------------------------------------------------------- #
# C) Downgrade
# --------------------------------------------------------------------------- #

def test_downgrade_to_free(client, store):
    _set_sub(store, "agency", "active")
    r = client.post("/account/downgrade", json={"plan": "free"}, headers=_bearer())
    assert r.status_code == 200 and r.json()["downgraded"] is True
    assert store.subs[1]["plan_id"] == "free" and store.subs[1]["status"] == "free"


def test_downgrade_rejects_upgrade_direction(client, store):
    _set_sub(store, "free", "free")
    assert client.post("/account/downgrade", json={"plan": "pro"}, headers=_bearer()).status_code == 400


def test_payments_history(client, store):
    import asyncio
    asyncio.get_event_loop().run_until_complete(
        store.create_subscription_payment(1, "pro", 1900, "ch_h", "code", "b64"))
    j = client.get("/account/payments", headers=_bearer()).json()
    assert len(j["payments"]) == 1 and j["payments"][0]["plan"] == "pro"


# --------------------------------------------------------------------------- #
# D) activate_paid (plans)
# --------------------------------------------------------------------------- #

def test_activate_paid_clears_trial(store):
    import asyncio
    _set_sub(store, "pro", "trial", trial_ends_at=datetime.now(timezone.utc) + timedelta(days=3))
    row = asyncio.get_event_loop().run_until_complete(plans.activate_paid(1, "pro"))
    assert row["status"] == "active" and row["trial_ends_at"] is None


# --------------------------------------------------------------------------- #
# E) Trial worker — expiração + avisos
# --------------------------------------------------------------------------- #

def _run(coro):
    import asyncio
    return asyncio.get_event_loop().run_until_complete(coro)


def test_trial_worker_downgrades_expired(store, monkeypatch):
    from discovery import trial_worker as tw
    _set_sub(store, "pro", "trial", trial_ends_at=datetime.now(timezone.utc) - timedelta(days=1))
    monkeypatch.setattr(tw, "get_target_store", lambda: store)
    monkeypatch.setattr(tw.worker_control, "is_enabled", lambda w: True)
    monkeypatch.setattr(tw._plans, "change_plan", _mk_change_plan(store))
    sent = []
    worker = tw.TrialWorker()
    worker.hour_utc = datetime.now(timezone.utc).hour   # força agir agora
    worker._mailer = lambda: _FakeMailer(sent)
    stats = _run(worker.run_cycle())
    assert stats["expired"] == 1
    assert store.subs[1]["plan_id"] == "free"
    assert ("u@x.com.br", "trial_expired") in sent
    assert (1, []) in getattr(store, "disabled", [])   # vigílias desativadas


def test_trial_worker_warns_7d(store, monkeypatch):
    from discovery import trial_worker as tw
    # data exatamente 7 dias à frente (ao meio-dia, evita cruzar meia-noite pelo horário do teste)
    ends = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0) + timedelta(days=7)
    _set_sub(store, "pro", "trial", trial_ends_at=ends)
    monkeypatch.setattr(tw, "get_target_store", lambda: store)
    monkeypatch.setattr(tw.worker_control, "is_enabled", lambda w: True)
    sent = []
    worker = tw.TrialWorker()
    worker.hour_utc = datetime.now(timezone.utc).hour
    worker._mailer = lambda: _FakeMailer(sent)
    stats = _run(worker.run_cycle())
    assert stats["warned_7d"] == 1 and ("u@x.com.br", "trial_warning_7") in sent


def test_trial_worker_cleans_unconfirmed(store, monkeypatch):
    # KL-82 Slice 2: o ciclo do trial também limpa contas não confirmadas inativas.
    from discovery import trial_worker as tw
    store.cleanup_returns = 2
    monkeypatch.setattr(tw, "get_target_store", lambda: store)
    monkeypatch.setattr(tw.worker_control, "is_enabled", lambda w: True)
    worker = tw.TrialWorker()
    worker.hour_utc = datetime.now(timezone.utc).hour
    worker._mailer = lambda: _FakeMailer([])
    stats = _run(worker.run_cycle())
    assert getattr(store, "cleanup_called", 0) == 1
    assert stats["unconfirmed_cleaned"] == 2 and stats["errors"] == 0


def test_trial_worker_disabled(store, monkeypatch):
    from discovery import trial_worker as tw
    monkeypatch.setattr(tw, "get_target_store", lambda: store)
    monkeypatch.setattr(tw.worker_control, "is_enabled", lambda w: False)
    worker = tw.TrialWorker()
    assert _run(worker.run_cycle()).get("disabled") is True


def _mk_change_plan(store):
    async def _cp(uid, plan, changed_by="system", reason=None):
        store.subs[uid] = {**store.subs.get(uid, {"account_id": uid}), "plan_id": plan,
                           "status": "free" if plan == "free" else "active", "trial_ends_at": None}
        return store.subs[uid]
    return _cp


class _FakeMailer:
    def __init__(self, sent):
        self.sent = sent

    async def send_trial_warning(self, to_email, days, ends_label=""):
        self.sent.append((to_email, f"trial_warning_{days}"))
        return {"email_id": "x"}

    async def send_trial_expired(self, to_email):
        self.sent.append((to_email, "trial_expired"))
        return {"email_id": "x"}
