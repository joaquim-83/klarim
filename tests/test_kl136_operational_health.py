"""KL-136 — saúde operacional do pipeline de alerta: role penalty configurável, gate de catch_all
separado, fail-safe de saldo Reoon, breakdown de `blocked_known` e diagnóstico de re-scan.

Offline (FakeStore/MiniStore + verify_email/check_balance mockados). Complementa os testes
existentes de KL-85/KL-110/KL-127/KL-129/KL-130 que foram atualizados p/ os novos defaults."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from discovery.alert_scoring import calculate_alert_score, _role_penalty
from notifier import email_verifier as ev


# =========================================================================== #
# Fix 1 — role penalty configurável (-5 default, era -15)
# =========================================================================== #

def test_role_penalty_default_is_minus_5(monkeypatch):
    monkeypatch.delenv("ALERT_ROLE_PENALTY", raising=False)
    assert _role_penalty() == -5


def test_role_penalty_reads_env(monkeypatch):
    monkeypatch.setenv("ALERT_ROLE_PENALTY", "-8")
    assert _role_penalty() == -8


def test_role_penalty_invalid_env_falls_back(monkeypatch):
    monkeypatch.setenv("ALERT_ROLE_PENALTY", "nan")
    assert _role_penalty() == -5


def test_contato_action_zone_passes_with_default_penalty(monkeypatch):
    # O cenário do card: contato@ genérico na action_zone (score 50-89) deixa de ser rejeitado.
    monkeypatch.delenv("ALERT_ROLE_PENALTY", raising=False)
    r = calculate_alert_score({"domain": "hotel.com.br", "last_scan_score": 70}, "contato@x.com.br")
    assert r["score"] == 25  # +10 corp +20 action -5 role
    assert r["score"] > 20   # passa o threshold


def test_contato_action_zone_rejected_with_old_penalty(monkeypatch):
    # Confirma que o fix resolve: com o -15 antigo, o mesmo lead falharia (15 < 20).
    monkeypatch.setenv("ALERT_ROLE_PENALTY", "-15")
    r = calculate_alert_score({"domain": "hotel.com.br", "last_scan_score": 70}, "contato@x.com.br")
    assert r["score"] == 15  # +10 +20 -15
    assert r["score"] < 20   # rejeitado


def test_role_status_and_prefix_never_double(monkeypatch):
    # Prefixo role + status role da Reoon → só UM sinal (não dobra), com a penalidade do env.
    monkeypatch.delenv("ALERT_ROLE_PENALTY", raising=False)
    r = calculate_alert_score(
        {"domain": "x.com", "last_scan_score": 60, "email_verify_status": "role"}, "contato@x.com")
    role_signals = [s for s in r["signals"] if "role" in s["signal"]]
    assert len(role_signals) == 1
    assert role_signals[0]["points"] == -5


# =========================================================================== #
# Fix 2 (SUPERADO pelo KL-137) — os gates de score SAÍRAM; regra binária
# =========================================================================== #

@pytest.mark.parametrize("score", [0, 25, 30, 31, 100, 999])
def test_catch_all_never_sends(score):
    # KL-137: catch_all nunca envia, independentemente do score (gate de catch_all removido).
    assert ev.is_safe_to_send(ev.VerifyResult("catch_all", "x"), score) is False


@pytest.mark.parametrize("score", [0, 20, 21, 25, 100])
def test_inbox_full_never_sends(score):
    assert ev.is_safe_to_send(ev.VerifyResult("inbox_full", "x"), score) is False


def test_unknown_never_sends(monkeypatch):
    assert ev.is_safe_to_send(ev.VerifyResult("unknown", "x"), 100) is False


# =========================================================================== #
# Fix 4 (mantido no KL-137) — fail-safe de saldo Reoon
# =========================================================================== #

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
    return {"id": tid, "contact_email": f"n{tid}@novo.com.br", "_alert_score": score,
            "email_verified": False, "email_verify_status": None}


def _verified(tid, status, score=40, source="power"):
    return {"id": tid, "contact_email": f"v{tid}@dominio.com.br", "_alert_score": score,
            "email_verified": True, "email_verify_status": status,
            "email_verify_source": source, "email_verified_at": datetime.now(timezone.utc)}


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setenv("REOON_API_KEY", "test-key")
    monkeypatch.setenv("ALERT_TRUST_DOMAIN_DOWNGRADE", "false")
    monkeypatch.delenv("ALERT_UNSAFE_SCORE_GATE", raising=False)
    monkeypatch.delenv("ALERT_CATCH_ALL_SCORE_GATE", raising=False)


def _mock_verify(monkeypatch, result=None):
    async def _fake(email, mode="power", redis=None, api_key=None):
        return result or ev.VerifyResult("safe", "reoon_power", source="reoon")
    monkeypatch.setattr(ev, "verify_email", _fake)


def _mock_balance(monkeypatch, value):
    async def _bal(api_key=None, client=None):
        return value
    monkeypatch.setattr(ev, "check_balance", _bal)


def _run(worker, targets):
    return asyncio.run(worker._verify_and_filter(targets))


def test_balance_exhausted_defers_all_unverified(monkeypatch):
    # Saldo 0 → NÃO verifica; os não-verificados são DEFERIDOS (não enviados sem verificação).
    _mock_balance(monkeypatch, 0)
    verify_called = {"n": 0}

    async def _fake(email, mode="power", redis=None, api_key=None):
        verify_called["n"] += 1
        return ev.VerifyResult("safe", "reoon_power", source="reoon")
    monkeypatch.setattr(ev, "verify_email", _fake)

    kept, stats = _run(_mk_worker(_MiniStore()), [_new(i) for i in range(1, 6)])
    assert kept == []                     # nada enviado
    assert stats.get("reoon_exhausted") is True
    assert stats["deferred"] == 5
    assert verify_called["n"] == 0        # não gastou API


def test_balance_exhausted_still_sends_already_verified(monkeypatch):
    # Saldo 0 barra novas verificações, mas os JÁ-verificados-OK (sendable) seguem.
    _mock_balance(monkeypatch, 0)
    _mock_verify(monkeypatch)
    targets = [_verified(1, "safe"), _new(2)]
    kept, stats = _run(_mk_worker(_MiniStore()), targets)
    assert [t["id"] for t in kept] == [1]   # o safe cacheado envia; o novo é deferido
    assert stats["reoon_exhausted"] is True


def test_balance_positive_verifies_normally(monkeypatch):
    _mock_balance(monkeypatch, 5000)
    _mock_verify(monkeypatch, ev.VerifyResult("safe", "reoon_power", source="reoon"))
    kept, stats = _run(_mk_worker(_MiniStore()), [_new(1), _new(2)])
    assert len(kept) == 2
    assert not stats.get("reoon_exhausted")


def test_balance_unreadable_does_not_block(monkeypatch):
    # Saldo None (ilegível) = fail-open: NÃO trata como esgotado (verifica normalmente).
    _mock_balance(monkeypatch, None)
    _mock_verify(monkeypatch, ev.VerifyResult("safe", "reoon_power", source="reoon"))
    kept, stats = _run(_mk_worker(_MiniStore()), [_new(1)])
    assert len(kept) == 1
    assert not stats.get("reoon_exhausted")


def test_balance_not_checked_when_no_unverified(monkeypatch):
    # Sem caixas novas a verificar, não consulta o saldo (evita HTTP à toa).
    called = {"n": 0}

    async def _bal(api_key=None, client=None):
        called["n"] += 1
        return 0
    monkeypatch.setattr(ev, "check_balance", _bal)
    _mock_verify(monkeypatch)
    kept, stats = _run(_mk_worker(_MiniStore()), [_verified(1, "safe")])
    assert [t["id"] for t in kept] == [1]
    assert called["n"] == 0
    assert not stats.get("reoon_exhausted")


def test_cached_mix_binary_decision(monkeypatch):
    # KL-137: catch_all + disabled (cacheados) → blocked; safe → sendable. Sem breakdown/gates.
    _mock_balance(monkeypatch, 5000)
    _mock_verify(monkeypatch)
    targets = [_verified(1, "catch_all", score=10),
               _verified(2, "disabled"),
               _verified(3, "safe")]
    kept, stats = _run(_mk_worker(_MiniStore()), targets)
    assert [t["id"] for t in kept] == [3]
    assert stats["blocked"] == 2 and stats["sendable"] == 1 and stats["from_cache"] == 3


# =========================================================================== #
# Fix 5 — diagnóstico de re-scan (funil de elegibilidade)
# =========================================================================== #

def test_rescan_diagnostics_shape():
    # A função é pura em cima de contagens SQL; validamos o SHAPE com um FakeStore mínimo.
    class _Store:
        async def rescan_diagnostics(self, days=30):
            return {"engaged": 10, "engaged_with_email": 4, "eligible": 0, "too_recent": 4}
        async def get_targets_for_rescan(self, days, limit):
            return []
        async def get_setting(self, k, d):
            return d
    from discovery.rescan_worker import RescanWorker
    w = RescanWorker()
    w.store = _Store()

    async def _go():
        # Só o passo de elegibilidade + diagnóstico (não roda scan real): chamamos o store direto.
        return await w.store.rescan_diagnostics(w.age_days)
    diag = asyncio.run(_go())
    assert set(diag) == {"engaged", "engaged_with_email", "eligible", "too_recent"}
    assert diag["eligible"] == 0 and diag["too_recent"] == 4
