"""KL-129 — priorização dos NÃO-verificados no subset + filtro dos já-barrados + canário/domínio.

O bug: o cap de verificação era consumido pelos já-verificados do cache (unknown/catch_all
barrados) → 0 vaga p/ os `email_verified=false` → pipeline girava em falso (0 sent, 0 API).
Estes testes garantem que os NOVOS entram no subset (API neste ciclo), os já-barrados NÃO
consomem vaga, e o domínio confiável rebaixa `unknown` → catch_all. Offline (verify_email mockado).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from notifier import email_verifier as ev


class _MiniStore:
    def __init__(self, trusted=None):
        self.blocked, self.discarded, self.verified = [], [], []
        self._trusted = set(trusted or [])

    async def block_email(self, email, reason="bounced"):
        self.blocked.append((email, reason))

    async def update_status(self, target_id, status):
        self.discarded.append((target_id, status))

    async def update_target_email_verification(self, tid, status, is_role_based,
                                               verified=True, source=None):
        self.verified.append((tid, status, source))

    async def trusted_recipient_domains(self, domains, hours=48):
        return {d for d in domains if d in self._trusted}


def _mk_worker(store, verify_max=200):
    from discovery.alert_worker import AlertWorker
    w = AlertWorker()
    w.store = store
    w._redis = False
    w.email_verify_max = verify_max
    return w


def _verified(tid, status, score=40, source="power"):
    """Alvo JÁ verificado e fresco."""
    return {"id": tid, "contact_email": f"v{tid}@dominio.com.br", "_alert_score": score,
            "email_verified": True, "email_verify_status": status,
            "email_verify_source": source, "email_verified_at": datetime.now(timezone.utc)}


def _new(tid, score=40, domain="novo.com.br"):
    """Alvo NÃO verificado (email_verified=false)."""
    return {"id": tid, "contact_email": f"n{tid}@{domain}", "_alert_score": score,
            "email_verified": False, "email_verify_status": None}


def _run(worker, targets):
    return asyncio.run(worker._verify_and_filter(targets))


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setenv("REOON_API_KEY", "test-key")
    monkeypatch.delenv("ALERT_UNSAFE_SCORE_GATE", raising=False)  # gate = 20
    monkeypatch.setenv("ALERT_TRUST_DOMAIN_DOWNGRADE", "false")   # ligado só nos testes de trust

    async def _bal(api_key=None, client=None):  # KL-136: offline — saldo ilegível (fail-open)
        return None
    monkeypatch.setattr(ev, "check_balance", _bal)


def _mock_verify(monkeypatch, by_email):
    calls = []

    async def _fake(email, mode="power", redis=None, api_key=None):
        calls.append(email)
        return by_email.get(email, ev.VerifyResult("safe", "reoon_power", source="reoon"))

    monkeypatch.setattr(ev, "verify_email", _fake)
    return calls


# --------------------------- prioridade dos novos ------------------------- #

def test_mixed_new_and_cached_verifies_new_and_sends_direct(monkeypatch):
    # 50 novos + 100 unknown(cache) + 50 safe(cache). Os 50 novos vão à API; os 50 safe direto;
    # os 100 unknown NÃO consomem vaga.
    calls = _mock_verify(monkeypatch, {})   # todo novo → safe
    targets = ([_new(i) for i in range(1, 51)]
               + [_verified(100 + i, "unknown") for i in range(1, 101)]
               + [_verified(300 + i, "safe") for i in range(1, 51)])
    kept, stats = _run(_mk_worker(_MiniStore()), targets)
    assert len(calls) == 50                     # só os 50 NOVOS tocaram a API
    assert stats["verified"] == 50 and stats["from_cache"] == 150   # 100 unknown + 50 safe frescos
    assert stats["blocked"] == 100              # os 100 unknown (regra binária)
    assert stats["sendable"] == 100             # 50 novos(safe) + 50 safe(cache)
    assert len(kept) == 100


def test_cached_unknown_does_not_consume_subset(monkeypatch):
    # cap=1, 1 novo + 5 unknown cacheados. O único slot vai ao NOVO (não a um unknown).
    calls = _mock_verify(monkeypatch, {})
    targets = [_verified(10 + i, "unknown") for i in range(5)] + [_new(1)]
    kept, stats = _run(_mk_worker(_MiniStore(), verify_max=1), targets)
    assert calls == ["n1@novo.com.br"]          # o novo foi verificado (não um unknown)
    assert stats["deferred"] == 0 and stats["verified"] == 1
    assert {t["id"] for t in kept} == {1}


def test_new_verified_and_sent_same_cycle(monkeypatch):
    _mock_verify(monkeypatch, {"n1@novo.com.br": ev.VerifyResult("safe", "reoon_power", source="reoon")})
    kept, stats = _run(_mk_worker(_MiniStore()), [_new(1)])
    assert {t["id"] for t in kept} == {1} and stats["verified"] == 1


def test_all_unknown_cached_makes_zero_api_calls(monkeypatch):
    # Pipeline NÃO gira em falso: tudo unknown cacheado → subset vazio → 0 chamadas à API.
    calls = _mock_verify(monkeypatch, {})
    targets = [_verified(i, "unknown") for i in range(1, 121)]
    kept, stats = _run(_mk_worker(_MiniStore()), targets)
    assert calls == [] and kept == []
    assert stats["verified"] == 0 and stats["blocked"] == 120   # KL-137: unknown → blocked


def test_deferred_when_more_new_than_cap(monkeypatch):
    calls = _mock_verify(monkeypatch, {})
    targets = [_new(i) for i in range(1, 11)]        # 10 novos
    kept, stats = _run(_mk_worker(_MiniStore(), verify_max=4), targets)
    assert len(calls) == 4 and stats["deferred"] == 6   # 4 verificados, 6 p/ o próximo ciclo


# KL-137 — o trust-downgrade (KL-129: `unknown`→`catch_all` p/ domínios confiáveis) FOI REMOVIDO:
# com a regra binária, catch_all/unknown nunca enviam, então rebaixar não muda nada. Os testes de
# `test_trusted_domain_downgrades_*` foram removidos junto com a feature.


# --------------------------- cap por env ---------------------------------- #

def test_cap_defaults_to_200(monkeypatch):
    monkeypatch.delenv("EMAIL_VERIFY_MAX_PER_CYCLE", raising=False)
    from discovery.alert_worker import AlertWorker
    assert AlertWorker().email_verify_max == 200


def test_cap_reads_env(monkeypatch):
    monkeypatch.setenv("EMAIL_VERIFY_MAX_PER_CYCLE", "350")
    from discovery.alert_worker import AlertWorker
    assert AlertWorker().email_verify_max == 350
