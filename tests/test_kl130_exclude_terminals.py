"""KL-130 — exclui status terminais do pool de elegíveis + aposenta unknown+power.

120+ `unknown`+`power` circulavam no pool a cada ciclo (ordenado por `last_scan_at ASC`),
entupindo o fetch e starvando 3.263 e-mails novos (0 verificações API, 0 envios). O fix:
(1) filtro no `_ALERT_ELIGIBLE_WHERE`; (2) alvo verificado como unknown via Power → `sem_contato`
(sai do pool); (3) script de limpeza retroativa. Offline (verify_email mockado; SQL validado na VM).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from notifier import email_verifier as ev
from discovery.store import TargetStore


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

    async def trusted_recipient_domains(self, domains, hours=48):
        return set()


def _mk_worker(store, verify_max=200):
    from discovery.alert_worker import AlertWorker
    w = AlertWorker()
    w.store = store
    w._redis = False
    w.email_verify_max = verify_max
    return w


def _new(tid, score=40):
    return {"id": tid, "contact_email": f"n{tid}@hotmail.com", "_alert_score": score,
            "email_verified": False, "email_verify_status": None}


def _run(worker, targets):
    return asyncio.run(worker._verify_and_filter(targets))


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("REOON_API_KEY", "test-key")
    monkeypatch.setenv("ALERT_TRUST_DOMAIN_DOWNGRADE", "false")
    monkeypatch.delenv("ALERT_UNSAFE_SCORE_GATE", raising=False)

    async def _bal(api_key=None, client=None):  # KL-136: offline — saldo ilegível (fail-open)
        return None
    monkeypatch.setattr(ev, "check_balance", _bal)


# --------------------- WHERE exclui os terminais -------------------------- #

def test_eligible_where_excludes_unknown_power_and_block_statuses():
    w = TargetStore._ALERT_ELIGIBLE_WHERE
    # NULL-safe (COALESCE): sem isto, `NULL = 'unknown'` vira NULL e o AND NOT(...) excluiria os
    # não-verificados (status NULL) — exatamente os 3.247 que queremos incluir.
    assert "COALESCE(t.email_verify_status, '') = 'unknown'" in w
    assert "COALESCE(t.email_verify_source, '') = 'power'" in w
    assert "AND NOT (COALESCE(t.email_verify_status, '') = 'unknown'" in w
    # block-statuses só excluídos quando NÃO-NULL (IS NULL preserva os não-verificados).
    assert "t.email_verify_status IS NULL OR t.email_verify_status NOT IN" in w
    for s in ("disabled", "invalid", "disposable", "spamtrap"):
        assert f"'{s}'" in w


# --------------------- worker aposenta unknown+power ---------------------- #

def test_new_unknown_power_marked_sem_contato(monkeypatch):
    async def _fake(email, mode="power", redis=None, api_key=None):
        return ev.VerifyResult("unknown", "reoon_power", source="reoon")

    monkeypatch.setattr(ev, "verify_email", _fake)
    store = _MiniStore()
    kept, stats = _run(_mk_worker(store), [_new(1)])
    assert kept == []                              # unknown não envia
    assert stats["verified"] == 1 and stats["retired_unknown"] == 1
    assert (1, "sem_contato") in store.discarded   # saiu do pool
    assert store.blocked == []                     # NÃO blocklist (não é "ruim", só não confirmável)


def test_new_safe_sends_and_is_not_retired(monkeypatch):
    async def _fake(email, mode="power", redis=None, api_key=None):
        return ev.VerifyResult("safe", "reoon_power", source="reoon")

    monkeypatch.setattr(ev, "verify_email", _fake)
    store = _MiniStore()
    kept, stats = _run(_mk_worker(store), [_new(1)])
    assert {t["id"] for t in kept} == {1} and stats["verified"] == 1
    assert stats["retired_unknown"] == 0 and (1, "sem_contato") not in store.discarded


def test_new_disabled_blocklisted_not_retired_unknown(monkeypatch):
    async def _fake(email, mode="power", redis=None, api_key=None):
        return ev.VerifyResult("disabled", "reoon_power", source="reoon")

    monkeypatch.setattr(ev, "verify_email", _fake)
    store = _MiniStore()
    kept, stats = _run(_mk_worker(store), [_new(1)])
    assert kept == [] and stats["blocked"] == 1 and stats["retired_unknown"] == 0
    assert ("n1@hotmail.com", "power_verify_disabled") in store.blocked
    assert (1, "descartado") in store.discarded


# --------------------- método de limpeza retroativa ----------------------- #

class _FakeCur:
    def __init__(self, rowcount=173):
        self.sql = ""
        self.rowcount = rowcount

    def execute(self, sql, params=None):
        self.sql = sql

    def fetchone(self):
        return (self.rowcount,)


class _RetireStore(TargetStore):
    def __init__(self, rowcount=173):
        self.cur = _FakeCur(rowcount)

    def _run(self, fn):
        return fn(self.cur)


def test_retire_unknown_power_targets_sql():
    s = _RetireStore(rowcount=173)
    n = asyncio.run(s.retire_unknown_power_targets())
    assert n == 173
    sql = s.cur.sql
    assert "SET status = 'sem_contato'" in sql
    assert "email_verify_status = 'unknown'" in sql and "email_verify_source = 'power'" in sql
    assert "NOT IN ('sem_contato', 'descartado', 'unsubscribed')" in sql
