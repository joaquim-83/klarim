"""Testes do módulo de pagamento (offline — sem AbacatePay real)."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json

import pytest
from fastapi import HTTPException

from payments import (
    AbacatePayClient,
    verify_webhook_signature,
    Charge,
    PaymentStatus,
    PRICING,
    amount_display,
)
from payments.store import MemoryStore
import payments.store as store_mod
import api.main as apimain


# --- modelos / helpers ----------------------------------------------------- #

def test_pricing_and_display():
    assert PRICING["standard"] == 2900
    assert amount_display(2900) == "R$ 29,00"
    assert amount_display(1900) == "R$ 19,00"
    assert amount_display(4905) == "R$ 49,05"


def test_charge_public_dict_data_uri():
    c = Charge("char_1", "https://x.com", 2900, br_code="BR", br_code_base64="AAAA")
    pub = c.to_public_dict()
    assert pub["charge_id"] == "char_1"
    assert pub["amount_display"] == "R$ 29,00"
    assert pub["qr_code_base64"].startswith("data:image/png;base64,AAAA")
    assert pub["paid"] is False


# --- assinatura de webhook (HMAC-SHA256) ----------------------------------- #

def test_verify_webhook_signature():
    secret, body = "s3cr3t", b'{"event":"transparent.completed"}'
    good = base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()
    good_hex = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert verify_webhook_signature(secret, body, good) is True
    assert verify_webhook_signature(secret, body, good_hex) is True
    assert verify_webhook_signature(secret, body, "wrong") is False
    assert verify_webhook_signature("", body, good) is False


# --- MemoryStore ----------------------------------------------------------- #

def test_memory_store_roundtrip():
    store = MemoryStore()
    asyncio.run(store.save(Charge("c1", "https://x.com", 2900)))
    got = asyncio.run(store.get("c1"))
    assert got and got.status == PaymentStatus.PENDING and not got.is_paid
    asyncio.run(store.mark_status("c1", PaymentStatus.PAID, paid_at="2026-07-06T00:00:00Z"))
    got = asyncio.run(store.get("c1"))
    assert got.is_paid and got.paid_at


def test_memory_store_list_and_stats():
    store = MemoryStore()
    asyncio.run(store.save(Charge("p1", "https://a.com", 2900, status=PaymentStatus.PAID)))
    asyncio.run(store.save(Charge("p2", "https://b.com", 1900, status=PaymentStatus.PENDING)))
    asyncio.run(store.save(Charge("p3", "https://c.com", 4900, status=PaymentStatus.PAID)))

    all_charges = asyncio.run(store.list_charges())
    assert len(all_charges) == 3
    paid = asyncio.run(store.list_charges(status=PaymentStatus.PAID))
    assert {c.charge_id for c in paid} == {"p1", "p3"}

    stats = asyncio.run(store.payment_stats())
    assert stats["total"] == 3
    assert stats["paid_count"] == 2
    assert stats["revenue_cents"] == 2900 + 4900
    assert stats["revenue_display"] == "R$ 78,00"
    assert stats["by_status"][PaymentStatus.PENDING] == 1


def test_memory_store_email_status():
    store = MemoryStore()
    asyncio.run(store.save(Charge("c2", "https://x.com", 2900,
                                  buyer_email="a@b.com", email_status="pending")))
    assert asyncio.run(store.get("c2")).email_status == "pending"
    asyncio.run(store.set_email_status("c2", "sending"))
    assert asyncio.run(store.get("c2")).email_status == "sending"
    asyncio.run(store.set_email_status("c2", "sent"))
    got = asyncio.run(store.get("c2"))
    assert got.email_status == "sent" and got.buyer_email == "a@b.com"


# --- AbacatePayClient (parsing, sem rede) ---------------------------------- #

def test_client_create_and_check_parsing(monkeypatch):
    c = AbacatePayClient("key")

    async def fake_request(method, path, **kw):
        if path.endswith("/create"):
            return {"data": {"id": "char_9", "brCode": "000201...", "brCodeBase64": "PNG"},
                    "success": True, "error": None}
        return {"data": {"id": "char_9", "status": "PAID"}, "success": True, "error": None}

    monkeypatch.setattr(c, "_request", fake_request)
    created = asyncio.run(c.create_pix_charge(2900, "desc"))
    assert created["id"] == "char_9" and created["brCodeBase64"] == "PNG"
    checked = asyncio.run(c.check_payment("char_9"))
    assert checked["status"] == "PAID"


def test_extract_charge_id_variants():
    assert apimain._extract_charge_id({"id": "a"}) == "a"
    assert apimain._extract_charge_id({"chargeId": "b"}) == "b"
    assert apimain._extract_charge_id({"transparent": {"id": "c"}}) == "c"
    assert apimain._extract_charge_id({}) is None


# --- gating de pagamento nos relatórios ------------------------------------ #

@pytest.fixture(autouse=True)
def _memory_store(monkeypatch):
    # Força store em memória e limpa o singleton entre testes.
    monkeypatch.delenv("DATABASE_URL", raising=False)
    store_mod._store = MemoryStore()
    yield
    store_mod._store = None


def test_require_paid_dev_mode(monkeypatch):
    monkeypatch.setenv("KLARIM_DEV_MODE", "true")
    monkeypatch.setenv("ABACATEPAY_API_KEY", "key")
    asyncio.run(apimain._require_paid(None))  # não levanta


def test_require_paid_free_when_no_key(monkeypatch):
    monkeypatch.setenv("KLARIM_DEV_MODE", "false")
    monkeypatch.delenv("ABACATEPAY_API_KEY", raising=False)  # sem chave => livre
    asyncio.run(apimain._require_paid(None))  # não levanta


def test_require_paid_402_without_charge(monkeypatch):
    monkeypatch.setenv("KLARIM_DEV_MODE", "false")
    monkeypatch.setenv("ABACATEPAY_API_KEY", "key")
    with pytest.raises(HTTPException) as ei:
        asyncio.run(apimain._require_paid(None))
    assert ei.value.status_code == 402


def test_require_paid_ok_when_paid(monkeypatch):
    monkeypatch.setenv("KLARIM_DEV_MODE", "false")
    monkeypatch.setenv("ABACATEPAY_API_KEY", "key")
    asyncio.run(store_mod._store.save(
        Charge("char_paid", "https://x.com", 2900, status=PaymentStatus.PAID)))
    asyncio.run(apimain._require_paid("char_paid"))  # não levanta


def test_mask_email():
    from payments import mask_email
    assert mask_email("hotel@example.com") == "h***l@example.com"
    assert mask_email("a@x.com") == "a***@x.com"
    assert mask_email("bad") == "***"


def test_recovery_token_store():
    from datetime import datetime, timedelta, timezone
    store = store_mod._store
    asyncio.run(store.save(Charge("cR", "https://x.com", 2900,
                                  status=PaymentStatus.PAID, buyer_email="a@b.com")))
    asyncio.run(store.save(Charge("cU", "https://y.com", 2900,
                                  status=PaymentStatus.PENDING, buyer_email="a@b.com")))
    paid = asyncio.run(store.list_paid_charges_by_email("a@b.com"))
    assert len(paid) == 1 and paid[0].charge_id == "cR"

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    asyncio.run(store.create_recovery_token("tok1", "a@b.com", (now + timedelta(hours=24)).isoformat()))
    asyncio.run(store.create_recovery_token("tok2", "a@b.com", (now - timedelta(hours=1)).isoformat()))
    assert asyncio.run(store.get_valid_recovery_token("tok1")).buyer_email == "a@b.com"
    assert asyncio.run(store.get_valid_recovery_token("tok2")) is None  # expirado
    assert asyncio.run(store.get_valid_recovery_token("nope")) is None
    assert asyncio.run(store.count_recent_recovery_requests("a@b.com")) == 2


def test_recovery_validate_and_download_cross_check():
    from datetime import datetime, timedelta, timezone
    store = store_mod._store
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    asyncio.run(store.create_recovery_token("tokV", "hotel@example.com", (now + timedelta(hours=24)).isoformat()))
    asyncio.run(store.save(Charge("cV", "https://x.com", 2900,
                                  status=PaymentStatus.PAID, buyer_email="hotel@example.com")))
    res = asyncio.run(apimain.recovery_validate(token="tokV"))
    assert res["valid"] and res["email"] == "h***l@example.com" and len(res["reports"]) == 1
    assert asyncio.run(apimain.recovery_validate(token="nope"))["valid"] is False

    # download com charge de outro e-mail -> 401
    asyncio.run(store.save(Charge("cOther", "https://y.com", 2900,
                                  status=PaymentStatus.PAID, buyer_email="outro@x.com")))
    with pytest.raises(HTTPException) as ei:
        asyncio.run(apimain.recovery_download(token="tokV", charge_id="cOther", type="executive"))
    assert ei.value.status_code == 401


def test_require_paid_402_when_pending_no_network(monkeypatch):
    # Cobrança pendente + chave inválida: _refresh falha silenciosamente -> 402.
    monkeypatch.setenv("KLARIM_DEV_MODE", "false")
    monkeypatch.delenv("ABACATEPAY_API_KEY", raising=False)  # payments off p/ evitar rede
    monkeypatch.setenv("ABACATEPAY_API_KEY", "key")  # on, mas _refresh tenta rede
    # Sem rede real, check_payment falha -> status permanece PENDING -> 402.
    asyncio.run(store_mod._store.save(Charge("char_pending", "https://x.com", 2900)))
    # Evita chamada de rede: força check_payment a lançar.
    import payments.abacatepay as ab

    async def boom(self, cid):
        raise ab.AbacatePayError("no network in test")

    monkeypatch.setattr(ab.AbacatePayClient, "check_payment", boom)
    with pytest.raises(HTTPException) as ei:
        asyncio.run(apimain._require_paid("char_pending"))
    assert ei.value.status_code == 402
