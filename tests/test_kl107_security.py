"""KL-107 — auditoria de segurança: (1) ownership check no verify/check; (2) aviso ao dono
verificado quando um terceiro adiciona o site dele ao monitoramento. Offline (FakeStore).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import api.main as m
from api import auth_users


class FakeMailer:
    def __init__(self):
        self.sent = []

    async def send_owner_site_added(self, to_email, domain, added_by_email):
        self.sent.append({"to": to_email, "domain": domain, "by": added_by_email})
        return {"id": "re_1"}


class Store:
    def __init__(self):
        self.users, self.links, self.targets = {}, {}, {}
        self.owner, self.pending, self.events = {}, {}, []

    async def get_user_by_id(self, uid):
        return self.users.get(int(uid))

    async def get_user_site(self, uid, tid):
        return self.links.get((int(uid), int(tid)))

    async def get_target(self, tid):
        return self.targets.get(int(tid))

    async def get_site_owner(self, tid):
        return self.owner.get(int(tid))

    async def get_pending_domain_verification(self, uid, tid):
        return self.pending.get((int(uid), int(tid)))

    async def log_event(self, *a, **k):
        self.events.append((a, k))
        return 1

    # add_site
    async def count_user_sites(self, uid):
        return 0

    async def site_has_owner(self, tid, exclude_user_id=None):
        return bool(self.owner.get(int(tid)))

    async def link_user_site(self, uid, tid, is_owner=False):
        self.links[(int(uid), int(tid))] = {"is_owner": is_owner}
        return True

    async def set_lead_monitoring(self, email):
        pass

    async def mark_site_verified(self, uid, tid, method):
        pass


# --------------------------------------------------------------------------- #
# Achado 2 — _notify_owner_site_added (unit, direto)
# --------------------------------------------------------------------------- #

def _wire(monkeypatch, store, mailer=None, email=True):
    monkeypatch.setattr(m, "get_target_store", lambda: store)
    monkeypatch.setattr("discovery.store.get_target_store", lambda: store)
    monkeypatch.setattr(m, "_email_enabled", lambda: email)
    if mailer is not None:
        monkeypatch.setattr(m, "_mailer", lambda: mailer)
    m._owner_notify_hits.clear()


@pytest.mark.asyncio
async def test_notify_owner_sends_to_verified_owner(monkeypatch):
    store = Store()
    store.owner[1] = {"id": 99, "email": "dono@site.com.br"}
    mailer = FakeMailer()
    _wire(monkeypatch, store, mailer)
    await m._notify_owner_site_added(1, "terceiro@x.com.br", "site.com.br")
    assert len(mailer.sent) == 1
    assert mailer.sent[0]["to"] == "dono@site.com.br"
    assert mailer.sent[0]["by"] == "terceiro@x.com.br"
    assert mailer.sent[0]["domain"] == "site.com.br"
    # evento KL-57 registrado
    assert any(a[0] == "owner_notification_sent" for a, _k in store.events)


@pytest.mark.asyncio
async def test_notify_owner_dedup_same_day(monkeypatch):
    store = Store()
    store.owner[1] = {"id": 99, "email": "dono@site.com.br"}
    mailer = FakeMailer()
    _wire(monkeypatch, store, mailer)
    await m._notify_owner_site_added(1, "a@x.com", "site.com.br")
    await m._notify_owner_site_added(1, "b@y.com", "site.com.br")   # 2ª no mesmo dia
    assert len(mailer.sent) == 1   # dedup 24h


@pytest.mark.asyncio
async def test_notify_owner_no_owner_skips(monkeypatch):
    store = Store()   # sem dono verificado
    mailer = FakeMailer()
    _wire(monkeypatch, store, mailer)
    await m._notify_owner_site_added(1, "x@x.com", "site.com.br")
    assert mailer.sent == []


@pytest.mark.asyncio
async def test_notify_owner_self_skips(monkeypatch):
    store = Store()
    store.owner[1] = {"id": 99, "email": "dono@site.com.br"}
    mailer = FakeMailer()
    _wire(monkeypatch, store, mailer)
    await m._notify_owner_site_added(1, "DONO@site.com.br", "site.com.br")  # mesmo e-mail, outra caixa
    assert mailer.sent == []


@pytest.mark.asyncio
async def test_notify_owner_email_failure_is_swallowed(monkeypatch):
    store = Store()
    store.owner[1] = {"id": 99, "email": "dono@site.com.br"}

    class BadMailer:
        async def send_owner_site_added(self, *a, **k):
            raise RuntimeError("resend down")
    _wire(monkeypatch, store, BadMailer())
    await m._notify_owner_site_added(1, "x@x.com", "site.com.br")   # NÃO pode levantar


# --------------------------------------------------------------------------- #
# HTTP — Achado 1 (verify/check) + Achado 2 (endpoint)
# --------------------------------------------------------------------------- #

@pytest.fixture
def store():
    s = Store()
    s.users[10] = {"id": 10, "email": "eu@site.com.br", "account_level": 2, "is_active": True, "plan": "pro"}
    s.users[20] = {"id": 20, "email": "terceiro@x.com.br", "account_level": 2, "is_active": True, "plan": "pro"}
    s.targets[1] = {"id": 1, "domain": "site.com.br", "url": "https://site.com.br"}
    s.links[(10, 1)] = {"id": 1, "user_id": 10, "target_id": 1, "is_owner": True}
    return s


@pytest.fixture
def client(store, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "x" * 64)
    monkeypatch.setattr(m, "get_target_store", lambda: store)
    monkeypatch.setattr("discovery.store.get_target_store", lambda: store)
    monkeypatch.setattr(m, "_spawn", lambda coro: coro.close())   # fire-and-forget não roda
    m._verify_check_hits.clear()
    return TestClient(m.app, raise_server_exceptions=False)


def _hdr(store, uid):
    return {"Authorization": f"Bearer {auth_users.create_user_token(store.users[uid])}"}


def test_verify_check_other_user_404(client, store):
    # KL-107 achado 1: site de OUTRO usuário → 404 (antes vazava 200 no_pending)
    r = client.post("/account/sites/1/verify/check", headers=_hdr(store, 20))
    assert r.status_code == 404


def test_verify_check_own_site_no_pending(client, store):
    # site próprio, sem verificação pendente → 200 no_pending (comportamento normal)
    r = client.post("/account/sites/1/verify/check", headers=_hdr(store, 10))
    assert r.status_code == 200 and r.json()["status"] == "no_pending"


def test_verify_start_other_user_404(client, store):
    # double-check do card: verify/start já bloqueia site de terceiro
    r = client.post("/account/sites/1/verify/start", json={"method": "dns_txt"}, headers=_hdr(store, 20))
    assert r.status_code == 404


def test_add_site_third_party_returns_200_not_owner(client, store, monkeypatch):
    # KL-107 achado 2: terceiro adiciona site com dono verificado → 200 is_owner=false (não bloqueia)
    store.owner[1] = {"id": 10, "email": "eu@site.com.br"}
    monkeypatch.setattr(m.domain_guard, "is_blocked_domain", lambda d: (False, None))
    monkeypatch.setattr(m, "_resolve_or_create_target", _fixed_tid)
    monkeypatch.setattr(m, "_effective_plan_limits", _plan_limits)
    monkeypatch.setattr(m, "_ownership_method", _no_ownership)
    monkeypatch.setattr(m, "_create_site_vigilias", _noop2)
    r = client.post("/account/sites", json={"url": "https://site.com.br"}, headers=_hdr(store, 20))
    assert r.status_code == 200
    assert r.json()["is_owner"] is False


async def _fixed_tid(url, source=None):
    return 1


async def _plan_limits(user):
    return {"max_sites": 5, "plan_name": "Pro"}


async def _no_ownership(email, tid):
    return None


async def _noop2(*a, **k):
    return None
