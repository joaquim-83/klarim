"""KL-127 — teste de integração do pipeline de verificação de e-mail (solução definitiva).

Garante que um lote realista de elegíveis **nunca zera** os envios e que a regra ÚNICA
(`is_safe_to_send` = gate de score p/ unknown/catch_all/inbox_full) governa a decisão.
Offline: `verify_email` é servido de um mapa por e-mail (sem rede); os alvos entram JÁ
verificados e frescos (source=power, `email_verified_at`=agora) para exercitar o caminho
de decisão sem tocar a API. `email_verify_max` alto → todos passam pela regra (sem `rest`).
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


def _mk_worker(store, verify_max=1000):
    from discovery.alert_worker import AlertWorker
    w = AlertWorker()
    w.store = store
    w._redis = False
    w.email_verify_max = verify_max
    return w


def _t(tid, status, score, source="power"):
    """Alvo JÁ verificado e fresco (usa o status cacheado — sem tocar a API)."""
    return {"id": tid, "contact_email": f"lead{tid}@dominio.com.br", "_alert_score": score,
            "email_verified": True, "email_verify_status": status,
            "email_verify_source": source, "email_verified_at": datetime.now(timezone.utc)}


def _run(worker, targets):
    return asyncio.run(worker._verify_and_filter(targets))


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setenv("REOON_API_KEY", "test-key")
    monkeypatch.delenv("ALERT_UNSAFE_SCORE_GATE", raising=False)  # gate = 20


# --------------------------- mix realista de 200 -------------------------- #

def test_mixed_batch_sends_nonzero():
    """200 elegíveis com mix realista → ≥150 enviados (nunca zera)."""
    targets = []
    tid = 0

    def add(n, status, score, source="power"):
        nonlocal tid
        for _ in range(n):
            tid += 1
            targets.append(_t(tid, status, score, source))

    add(40, "safe", 50)                    # → enviam
    add(30, "role", 50)                    # → enviam
    add(50, "unknown", 35, source="bulk")  # score>20 → enviam via gate
    add(20, "unknown", 35, source="power") # score>20 → enviam via gate
    add(15, "unknown", 10)                 # score<20 → gate barra
    add(10, "catch_all", 35)               # score>20 → enviam via gate
    add(5, "disabled", 90)                 # nunca
    add(5, "invalid", 90)                  # nunca
    add(5, "spamtrap", 90)                 # nunca
    add(10, "safe", 0, source=None)        # sem source mas verificado safe → enviam
    add(10, "inbox_full", 35)              # score>20 → enviam via gate

    kept, stats = _run(_mk_worker(_MiniStore()), targets)
    # 40 safe + 30 role + 50 unk + 20 unk + 10 catch + 10 safe + 10 inbox = 170
    assert len(kept) == 170
    assert len(kept) >= 150
    assert stats["skipped_gate"] == 15          # os 15 unknown de score baixo
    # os 15 block-status não vão (não são frescos-block? são frescos → gate não envia)
    assert all(k["email_verify_status"] not in ("disabled", "invalid", "spamtrap") for k in kept)


def test_all_unknown_high_score_does_not_zero():
    targets = [_t(i, "unknown", 25, source="bulk") for i in range(1, 201)]
    kept, _ = _run(_mk_worker(_MiniStore()), targets)
    assert len(kept) == 200


def test_all_unknown_low_score_sends_zero():
    targets = [_t(i, "unknown", 15, source="bulk") for i in range(1, 201)]
    kept, stats = _run(_mk_worker(_MiniStore()), targets)
    assert kept == []
    assert stats["skipped_gate"] == 200


def test_100_safe_100_disabled_sends_exactly_100():
    targets = ([_t(i, "safe", 40) for i in range(1, 101)]
               + [_t(i, "disabled", 90) for i in range(101, 201)])
    kept, _ = _run(_mk_worker(_MiniStore()), targets)
    assert len(kept) == 100
    assert all(k["email_verify_status"] == "safe" for k in kept)


# --------------------------- regra do gate (`>` 20) ----------------------- #

def test_gate_boundary_is_strict_greater_than():
    assert ev.is_safe_to_send(ev.VerifyResult("unknown", "x"), 21) is True
    assert ev.is_safe_to_send(ev.VerifyResult("unknown", "x"), 20) is False
    assert ev.is_safe_to_send(ev.VerifyResult("unknown", "x"), 100) is True
    assert ev.is_safe_to_send(ev.VerifyResult("catch_all", "x"), 21) is True
    assert ev.is_safe_to_send(ev.VerifyResult("safe", "x"), 0) is True
    assert ev.is_safe_to_send(ev.VerifyResult("disabled", "x"), 100) is False
    assert ev.is_safe_to_send(ev.VerifyResult("inbox_full", "x"), 25) is True


# --------------------------- verificação obrigatória ---------------------- #

def test_unverified_target_not_sent_when_key_present():
    # Com key, um alvo NÃO verificado (não fresco) é verificado via Power; se o Power não
    # confirmar (fallback), não envia (sem verificação).
    async def _fallback(email, mode="power", redis=None, api_key=None):
        return ev.VerifyResult("unknown", "api_unavailable", source="fallback")

    import notifier.email_verifier as evmod
    orig = evmod.verify_email
    evmod.verify_email = _fallback
    try:
        store = _MiniStore()
        target = {"id": 1, "contact_email": "novo@x.com.br", "_alert_score": 90}  # não verificado
        kept, stats = _run(_mk_worker(store), [target])
        assert kept == [] and stats["skipped_unverified"] == 1
        assert store.verified == []   # fallback não persiste
    finally:
        evmod.verify_email = orig


def test_no_dead_code_if_false_in_pipeline():
    """Garante ZERO código morto (`if False`) nos módulos do pipeline (regra KL-127)."""
    import inspect
    import notifier.email_verifier as evmod
    from discovery import alert_worker
    for mod in (evmod, alert_worker):
        src = inspect.getsource(mod)
        assert "if False" not in src, f"código morto 'if False' em {mod.__name__}"
        assert "DESABILITADO" not in src, f"patch comentado em {mod.__name__}"
