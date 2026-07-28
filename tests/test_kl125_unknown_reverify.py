"""KL-125 — reverificação Power dos `unknown` + source + bloqueio definitivo.

64% dos bounces vinham de e-mails `unknown` da Bulk API (menos precisa p/ servidores BR).
Estes testes cobrem as Regras do `_verify_and_filter`: unknown de fonte não-power é
reverificado via Power; unknown de source=power é pulado sem gastar crédito; unknown NUNCA
é enviado; e o `source` é gravado. Offline (`_MiniStore` + `verify_email` mockado).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from notifier import email_verifier as ev


class _MiniStore:
    def __init__(self):
        self.blocked, self.discarded, self.verified = [], [], []

    async def block_email(self, email, reason="bounced"):
        self.blocked.append((email, reason))

    async def update_status(self, target_id, status):
        self.discarded.append((target_id, status))

    async def update_target_email_verification(self, tid, status, is_role_based,
                                               verified=True, source=None):
        self.verified.append((tid, status, source))


def _mk_worker(store):
    from discovery.alert_worker import AlertWorker
    w = AlertWorker()
    w.store = store
    w._redis = False  # sem redis nos testes
    return w


def _t(tid, email, score=40, status=None, source=None, verified=False, verified_at=None):
    return {"id": tid, "contact_email": email, "_alert_score": score,
            "email_verify_status": status, "email_verify_source": source,
            "email_verified": verified, "email_verified_at": verified_at}


def _mock_verify(monkeypatch, table):
    calls = []

    async def _fake(email, mode="power", redis=None, api_key=None):
        calls.append(email)
        r = table[email] if isinstance(table, dict) else table
        return r

    monkeypatch.setattr(ev, "verify_email", _fake)
    return calls


# --------------------------- Regra 1: reverificar ------------------------- #

def test_reverify_unknown_bulk_to_safe(monkeypatch):
    monkeypatch.setenv("REOON_API_KEY", "k")
    calls = _mock_verify(monkeypatch, ev.VerifyResult("safe", "reoon_power", source="reoon"))
    store = _MiniStore()
    kept, stats = asyncio.run(_mk_worker(store)._verify_and_filter(
        [_t(1, "a@x.com", status="unknown", source="bulk")]))
    assert calls == ["a@x.com"]                       # reverificou via Power
    assert {t["id"] for t in kept} == {1}
    assert stats["reverified"] == 1 and stats["reverified_safe"] == 1
    assert (1, "safe", "power") in store.verified     # gravou source=power


def test_reverify_unknown_to_disabled_blocks(monkeypatch):
    monkeypatch.setenv("REOON_API_KEY", "k")
    _mock_verify(monkeypatch, ev.VerifyResult("disabled", "reoon_power", source="reoon"))
    store = _MiniStore()
    kept, stats = asyncio.run(_mk_worker(store)._verify_and_filter(
        [_t(1, "a@x.com", status="unknown", source="bulk")]))
    assert kept == []
    assert stats["blocked"] == 1 and stats["reverified_blocked"] == 1
    assert ("a@x.com", "power_verify_disabled") in store.blocked
    assert (1, "descartado") in store.discarded


def test_reverify_still_unknown_blocks_send_not_blocklist(monkeypatch):
    monkeypatch.setenv("REOON_API_KEY", "k")
    _mock_verify(monkeypatch, ev.VerifyResult("unknown", "reoon_power", source="reoon"))
    store = _MiniStore()
    kept, stats = asyncio.run(_mk_worker(store)._verify_and_filter(
        [_t(1, "a@x.com", status="unknown", source="bulk")]))
    assert kept == []                                 # unknown 2× → não envia
    assert stats["reverified_unknown"] == 1
    assert store.blocked == []                        # NÃO blocklist (pode ser transitório)
    assert (1, "unknown", "power") in store.verified  # source=power → próximo ciclo pula


def test_reverify_fallback_not_persisted(monkeypatch):
    monkeypatch.setenv("REOON_API_KEY", "k")
    _mock_verify(monkeypatch, ev.VerifyResult("unknown", "api_unavailable", source="fallback"))
    store = _MiniStore()
    kept, stats = asyncio.run(_mk_worker(store)._verify_and_filter(
        [_t(1, "a@x.com", status="unknown", source="bulk")]))
    assert kept == [] and stats["reverify_infra"] == 1
    assert store.verified == []                        # Reoon fora → NÃO condena o alvo


# --------------------------- Regra 2: unknown/power ----------------------- #

def test_unknown_power_skips_without_api(monkeypatch):
    monkeypatch.setenv("REOON_API_KEY", "k")

    async def _boom(*a, **k):
        raise AssertionError("não deveria chamar a API para unknown já verificado via Power")

    monkeypatch.setattr(ev, "verify_email", _boom)
    store = _MiniStore()
    kept, stats = asyncio.run(_mk_worker(store)._verify_and_filter(
        [_t(1, "a@x.com", status="unknown", source="power")]))
    assert kept == [] and stats["skipped_unknown_power"] == 1


# --------------------------- Regra 3: demais status ----------------------- #

def test_safe_bulk_fresh_sends_without_api(monkeypatch):
    monkeypatch.setenv("REOON_API_KEY", "k")

    async def _boom(*a, **k):
        raise AssertionError("alvo fresco não-unknown não deveria tocar a API")

    monkeypatch.setattr(ev, "verify_email", _boom)
    store = _MiniStore()
    t = _t(1, "a@x.com", status="safe", source="bulk", verified=True,
           verified_at=datetime.now(timezone.utc))
    kept, stats = asyncio.run(_mk_worker(store)._verify_and_filter([t]))
    assert {x["id"] for x in kept} == {1} and stats["from_cache"] == 1


# --------------------------- teto (rest) + no-key ------------------------- #

def test_rest_beyond_cap_drops_unknown(monkeypatch):
    monkeypatch.setenv("REOON_API_KEY", "k")
    _mock_verify(monkeypatch, ev.VerifyResult("safe", "reoon_power", source="reoon"))
    w = _mk_worker(_MiniStore())
    w.email_verify_max = 1                             # cap=1 → id 2 fica no "rest"
    targets = [_t(1, "a@x.com"), _t(2, "b@x.com", status="unknown", source="bulk")]
    kept, stats = asyncio.run(w._verify_and_filter(targets))
    assert {t["id"] for t in kept} == {1}             # id 2 (unknown, além do teto) pulado
    assert stats["rest_unknown_skipped"] == 1


def test_no_key_still_drops_known_unknown(monkeypatch):
    monkeypatch.delenv("REOON_API_KEY", raising=False)
    store = _MiniStore()
    targets = [_t(1, "a@x.com", status="unknown", source="bulk"), _t(2, "b@x.com")]
    kept, stats = asyncio.run(_mk_worker(store)._verify_and_filter(targets))
    assert {t["id"] for t in kept} == {2}             # unknown não é enviado nem sem API
    assert stats["rest_unknown_skipped"] == 1
    assert store.verified == []                        # sem key → nada de API
