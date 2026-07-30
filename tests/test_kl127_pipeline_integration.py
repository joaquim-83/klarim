"""KL-127/128 — teste de integração do pipeline de verificação de e-mail (solução definitiva).

Garante que um lote realista de elegíveis **nunca zera** os envios e que a regra ÚNICA
(`is_safe_to_send`) governa a decisão. **KL-128 (definitivo):** `unknown` NUNCA envia (o gate
`> ALERT_UNSAFE_SCORE_GATE` vale só p/ catch_all/inbox_full); safe/valid/role sempre enviam.
Offline: os alvos entram JÁ verificados e frescos (source=power, `email_verified_at`=agora) para
exercitar a decisão sem tocar a API; `verify_max=0` força o caminho `rest` (gate por cache).
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

    async def trusted_recipient_domains(self, domains, hours=48):
        return set()


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
    monkeypatch.setenv("ALERT_TRUST_DOMAIN_DOWNGRADE", "false")   # trust↓ testado à parte (KL-129)


# --------------------------- mix realista de 200 -------------------------- #

def test_mixed_batch_sends_nonzero():
    """200 elegíveis com mix realista → envia os não-unknown válidos, NUNCA os unknown (KL-128)."""
    targets = []
    tid = 0

    def add(n, status, score, source="power"):
        nonlocal tid
        for _ in range(n):
            tid += 1
            targets.append(_t(tid, status, score, source))

    add(40, "safe", 50)                    # → enviam
    add(30, "role", 50)                    # → enviam
    add(50, "unknown", 35, source="bulk")  # KL-128: unknown NUNCA envia
    add(20, "unknown", 35, source="power") # KL-128: unknown NUNCA envia
    add(15, "unknown", 10)                 # KL-128: unknown NUNCA envia
    add(10, "catch_all", 35)               # score>20 → enviam via gate
    add(5, "disabled", 90)                 # nunca (block)
    add(5, "invalid", 90)                  # nunca (block)
    add(5, "spamtrap", 90)                 # nunca (block)
    add(10, "safe", 0, source=None)        # verificado safe → enviam
    add(10, "inbox_full", 35)              # score>20 → enviam via gate

    kept, stats = _run(_mk_worker(_MiniStore()), targets)
    # 40 safe + 30 role + 10 catch + 10 safe + 10 inbox = 100 (nenhum unknown; 15 block)
    assert len(kept) == 100
    assert len(kept) > 0                       # NUNCA zera
    assert stats["from_cache"] == 100          # sendable (já verificados aprovados)
    # KL-129: os já-verificados barrados (85 unknown + 15 block-status) não tocam a API.
    assert stats["blocked_known"] == 100
    assert stats["verified"] == 0              # nenhuma chamada Reoon (todos frescos)
    assert all(k["email_verify_status"] != "unknown" for k in kept)   # nenhum unknown enviado


def test_all_unknown_sends_zero():
    # KL-128: unknown NUNCA envia — nem com score alto (o KL-127 mandava 200; era o bug).
    targets = [_t(i, "unknown", 25, source="bulk") for i in range(1, 201)]
    kept, stats = _run(_mk_worker(_MiniStore()), targets)
    assert kept == []
    assert stats["blocked_known"] == 200 and stats["verified"] == 0   # 0 API (não gira em falso)


def test_all_unknown_low_score_sends_zero():
    targets = [_t(i, "unknown", 15, source="bulk") for i in range(1, 201)]
    kept, stats = _run(_mk_worker(_MiniStore()), targets)
    assert kept == []
    assert stats["blocked_known"] == 200


def test_100_safe_100_disabled_sends_exactly_100():
    targets = ([_t(i, "safe", 40) for i in range(1, 101)]
               + [_t(i, "disabled", 90) for i in range(101, 201)])
    kept, _ = _run(_mk_worker(_MiniStore()), targets)
    assert len(kept) == 100
    assert all(k["email_verify_status"] == "safe" for k in kept)


# --------------------------- regra do gate (`>` 20) ----------------------- #

def test_gate_boundary_is_strict_greater_than():
    # KL-128: unknown NUNCA envia; o gate `>` 20 vale só p/ catch_all/inbox_full.
    assert ev.is_safe_to_send(ev.VerifyResult("unknown", "x"), 21) is False
    assert ev.is_safe_to_send(ev.VerifyResult("unknown", "x"), 100) is False
    assert ev.is_safe_to_send(ev.VerifyResult("catch_all", "x"), 21) is True
    assert ev.is_safe_to_send(ev.VerifyResult("catch_all", "x"), 20) is False
    assert ev.is_safe_to_send(ev.VerifyResult("safe", "x"), 0) is True
    assert ev.is_safe_to_send(ev.VerifyResult("disabled", "x"), 100) is False
    assert ev.is_safe_to_send(ev.VerifyResult("inbox_full", "x"), 25) is True


# --------------- _verify_and_filter: decisão por estado de verificação -------- #

@pytest.mark.parametrize("verified,status,score,should_send", [
    (False, "safe", 90, False),     # não verificado → não envia (deferido; cap=0 → sem API)
    (True, "", 90, False),          # verificado mas sem status → não fresco → deferido → não envia
    (True, "safe", 40, True),       # verificado safe → envia
    (True, "unknown", 90, False),   # verificado unknown → NÃO envia (KL-128)
    (True, "catch_all", 25, True),  # catch_all + score>20 → envia (gate)
    (True, "catch_all", 15, False), # catch_all + score<=20 → não envia (gate)
])
def test_verify_and_filter_decision_by_state(verified, status, score, should_send):
    # verify_max=0 → nenhum toque na API. Os frescos-verificados são decididos na partição pelo
    # status cacheado (KL-129); os não-verificados/sem-status ficam deferidos (não enviam).
    t = {"id": 1, "contact_email": "x@dominio.com.br", "_alert_score": score,
         "email_verified": verified, "email_verify_status": status}
    if verified:
        t["email_verified_at"] = datetime.now(timezone.utc)   # fresco → cai na partição
    kept, _ = _run(_mk_worker(_MiniStore(), verify_max=0), [t])
    assert (len(kept) == 1) is should_send


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
    """Garante ZERO código morto (`if False`) nos módulos do pipeline (regra KL-127/128)."""
    import inspect
    import notifier.email_verifier as evmod
    from discovery import alert_worker
    for mod in (evmod, alert_worker):
        src = inspect.getsource(mod)
        assert "if False" not in src, f"código morto 'if False' em {mod.__name__}"
        assert "DESABILITADO" not in src, f"patch comentado em {mod.__name__}"
