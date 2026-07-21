"""KL-93 — hardening de segurança dos endpoints públicos sensíveis. Testa cada proteção
nova: /payment/create (e-mail obrigatório, domínio+scan, rate limit), /notify/profile-view,
/monitoring/offer, /monitoring/sites (auth), /report/{executive,technical} (rate limit), e o
delete de cobrança (cleanup). Offline (stores fakes, sem AbacatePay/rede)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import api.main as m
from payments.store import MemoryStore
from payments import Charge


NOW = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)


_UNSET = object()


class _TargetStore:
    """Store de targets fake: por padrão o domínio EXISTE e tem scan; `target=None` força
    'não existe' (o sentinel distingue 'não passado' de 'explicitamente None')."""
    def __init__(self, target=_UNSET):
        self._target = ({"id": 9, "last_scan_at": NOW, "last_scan_score": 88}
                        if target is _UNSET else target)

    async def get_target_by_url(self, url):
        return self._target

    async def get_target_by_domain(self, domain):
        return self._target


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "k" * 64)
    monkeypatch.setenv("ADMIN_USER", "op")
    # payments habilitado (senão /payment/create dá 503 antes das validações que interessam)
    monkeypatch.setenv("ABACATEPAY_API_KEY", "test_key")
    monkeypatch.setattr(m, "get_target_store", lambda: _TargetStore())
    monkeypatch.setattr(m, "get_store", lambda: MemoryStore())
    return TestClient(m.app, raise_server_exceptions=False)


def _admin():
    return {"Authorization": f"Bearer {m._create_token('op')}"}


# =========================================================================== #
# P0 — /payment/create
# =========================================================================== #

def test_payment_create_requires_email(client):
    r = client.post("/payment/create", json={"url": "https://klarim.net"})
    assert r.status_code == 422                                   # sem e-mail


def test_payment_create_bad_email(client):
    r = client.post("/payment/create", json={"url": "https://klarim.net", "buyer_email": "notanemail"})
    assert r.status_code == 422


def test_payment_create_unknown_domain_404(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "k" * 64)
    monkeypatch.setenv("ABACATEPAY_API_KEY", "test_key")
    # domínio NÃO existe na base → 404
    monkeypatch.setattr(m, "get_target_store", lambda: _TargetStore(target=None))
    monkeypatch.setattr(m, "get_store", lambda: MemoryStore())
    c = TestClient(m.app, raise_server_exceptions=False)
    r = c.post("/payment/create", json={"url": "https://naoexiste123.com", "buyer_email": "x@x.com"})
    assert r.status_code == 404


def test_payment_create_domain_without_scan_404(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "k" * 64)
    monkeypatch.setenv("ABACATEPAY_API_KEY", "test_key")
    # alvo existe mas SEM scan (last_scan_at/score None) → 404
    monkeypatch.setattr(m, "get_target_store",
                        lambda: _TargetStore(target={"id": 1, "last_scan_at": None, "last_scan_score": None}))
    monkeypatch.setattr(m, "get_store", lambda: MemoryStore())
    c = TestClient(m.app, raise_server_exceptions=False)
    r = c.post("/payment/create", json={"url": "https://novo.com.br", "buyer_email": "x@x.com"})
    assert r.status_code == 404


def test_payment_create_rate_limit(client, monkeypatch):
    # modo demo p/ o caminho 200 (sem AbacatePay): DEMO_EMAIL == buyer_email → cobrança PAID
    monkeypatch.setenv("DEMO_EMAIL", "demo@klarim.net")
    body = {"url": "https://klarim.net", "buyer_email": "demo@klarim.net"}
    codes = [client.post("/payment/create", json=body).status_code for _ in range(4)]
    assert codes[:3] == [200, 200, 200]                          # 3/hora permitidos
    assert codes[3] == 429                                        # 4ª bloqueada


def test_payment_create_valid_returns_200(client, monkeypatch):
    monkeypatch.setenv("DEMO_EMAIL", "demo@klarim.net")
    r = client.post("/payment/create", json={"url": "https://klarim.net", "buyer_email": "demo@klarim.net"})
    assert r.status_code == 200
    assert r.json()["paid"] is True                              # demo → PAID


# =========================================================================== #
# P1 — /notify/profile-view (rate limit 1/h por IP+domínio)
# =========================================================================== #

def test_notify_profile_view_rate_limit(client, monkeypatch):
    monkeypatch.setattr(m, "_spawn", lambda coro: coro.close())  # não dispara e-mail real
    b = {"domain": "hotel.com.br"}
    r1 = client.post("/notify/profile-view", json=b)
    r2 = client.post("/notify/profile-view", json=b)
    assert r1.status_code == 200
    assert r2.status_code == 429                                 # 2ª no mesmo domínio → bloqueia


def test_notify_profile_view_different_domains_ok(client, monkeypatch):
    monkeypatch.setattr(m, "_spawn", lambda coro: coro.close())
    assert client.post("/notify/profile-view", json={"domain": "a.com.br"}).status_code == 200
    assert client.post("/notify/profile-view", json={"domain": "b.com.br"}).status_code == 200


# =========================================================================== #
# P1 — /monitoring/sites (auth admin)
# =========================================================================== #

def test_monitoring_sites_401_without_admin(client):
    assert client.get("/monitoring/sites").status_code == 401


def test_monitoring_sites_200_with_admin(client, monkeypatch):
    monkeypatch.setattr(m, "get_target_store",
                        lambda: type("S", (), {"get_active_monitored_sites": staticmethod(
                            lambda: _empty())})())
    r = client.get("/monitoring/sites", headers=_admin())
    assert r.status_code == 200


async def _empty():
    return []


# =========================================================================== #
# P1 — /monitoring/offer (rate limit 3/h + domínio existe)
# =========================================================================== #

def test_monitoring_offer_unknown_domain_404(client, monkeypatch):
    monkeypatch.setattr(m, "get_target_store", lambda: _TargetStore(target=None))
    r = client.post("/monitoring/offer", json={"url": "naoexiste.com.br", "email": "a@b.com.br"})
    assert r.status_code == 404


def test_monitoring_offer_rate_limit(client, monkeypatch):
    # domínio inexistente devolve 404 rápido, mas o rate limit é checado ANTES → 4ª vira 429
    monkeypatch.setattr(m, "get_target_store", lambda: _TargetStore(target=None))
    codes = [client.post("/monitoring/offer",
                         json={"url": "x.com.br", "email": "a@b.com.br"}).status_code for _ in range(4)]
    assert codes[:3] == [404, 404, 404]                          # 3/hora chegam ao 404
    assert codes[3] == 429                                        # 4ª barrada pelo rate limit


# =========================================================================== #
# P1 — /report/{executive,technical} (rate limit 5/h)
# =========================================================================== #

class _FakeReport:
    started_at = NOW
    score = None


@pytest.fixture
def report_client(client, monkeypatch):
    async def _scan(url, full=True, **kw):
        return _FakeReport()

    async def _pdf(fn, report, url, sector):
        return b"%PDF-1.4 fake"

    async def _sector(url):
        return None

    monkeypatch.setattr(m, "_safe_scan", _scan)
    monkeypatch.setattr(m, "_safe_pdf", _pdf)
    monkeypatch.setattr(m, "_sector_for_url", _sector)
    return client


def test_report_executive_rate_limit(report_client):
    codes = [report_client.get("/report/executive?url=https://klarim.net").status_code
             for _ in range(6)]
    assert codes[:5] == [200] * 5                                # 5/hora permitidos
    assert codes[5] == 429                                       # 6ª bloqueada


def test_report_technical_shares_bucket(report_client):
    # executive + technical compartilham o mesmo teto (report_dl por IP): 5 no total → 6ª 429.
    urls = ["/report/executive?url=https://klarim.net", "/report/technical?url=https://klarim.net"]
    codes = [report_client.get(urls[i % 2]).status_code for i in range(6)]
    assert codes[5] == 429


# =========================================================================== #
# Cleanup — delete de cobrança (idempotente)
# =========================================================================== #

@pytest.mark.asyncio
async def test_store_delete_charge_idempotent():
    store = MemoryStore()
    await store.save(Charge("pix_char_test", "https://x.com", 1900))
    assert await store.delete("pix_char_test") == 1              # removeu
    assert await store.delete("pix_char_test") == 0              # idempotente
    assert await store.get("pix_char_test") is None
