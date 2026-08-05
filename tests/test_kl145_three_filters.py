"""KL-145 — decisão de envio DESACOPLADA do Reoon: 3 filtros (sintaxe + MX + blocklist).

O Reoon classificava ~97% dos servidores BR como `unknown` e o pipeline binário do KL-137
travava o volume em 2-8 envios/dia. Agora `is_safe_to_send` e `_verify_and_filter` decidem o
envio com 3 filtros LOCAIS: sintaxe válida, domínio com MX e não-blocklistado. Tudo que passa
nos 3 → ENVIA (status de verificação NÃO decide mais). Offline: DNS mockado, store falso.
"""
from __future__ import annotations

import asyncio

import pytest

from notifier import email_verifier as ev


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #

class _FakeStore:
    """Store mínimo: só a blocklist (o que `_verify_and_filter` filtro 3 usa)."""
    def __init__(self, blocked=None):
        self._blocked = {(e or "").lower() for e in (blocked or [])}

    async def block_email(self, email, reason="bounced"):
        self._blocked.add((email or "").lower())

    async def is_email_blocked(self, email):
        return (email or "").lower() in self._blocked


def _mk_worker(store, validate_mx=False):
    from discovery.alert_worker import AlertWorker
    w = AlertWorker()
    w.store = store
    w._redis = False          # sem redis nos testes → MX usa _resolve_mx_sync mockado
    w.validate_mx = validate_mx
    return w


@pytest.fixture(autouse=True)
def _mx_ok(monkeypatch):
    # Por padrão todo domínio tem MX (fail-open). Testes específicos sobrescrevem.
    monkeypatch.setattr(ev, "_resolve_mx_sync", lambda d: "ok")


# --------------------------------------------------------------------------- #
# is_safe_to_send — os 3 filtros
# --------------------------------------------------------------------------- #

def test_valid_email_with_mx_and_not_blocked_sends():
    ok = asyncio.run(ev.is_safe_to_send("contato@empresa.com.br", store=_FakeStore()))
    assert ok is True


def test_invalid_syntax_blocked():
    assert asyncio.run(ev.is_safe_to_send("invalido", store=_FakeStore())) is False


def test_no_mx_blocked(monkeypatch):
    monkeypatch.setattr(ev, "_resolve_mx_sync", lambda d: "no_mx")
    assert asyncio.run(ev.is_safe_to_send("email@dominio-sem-mx.xyz", store=_FakeStore())) is False


def test_blocklisted_blocked():
    store = _FakeStore(blocked=["bounced@dominio.com.br"])
    assert asyncio.run(ev.is_safe_to_send("bounced@dominio.com.br", store=store)) is False


def test_mx_unknown_is_fail_open(monkeypatch):
    # DNS incerto (timeout) → não rejeita (o filtro 2 é fail-open; só 'no_mx' rejeita).
    monkeypatch.setattr(ev, "_resolve_mx_sync", lambda d: "unknown")
    assert asyncio.run(ev.is_safe_to_send("x@dominio.com.br", store=_FakeStore())) is True


def test_no_store_does_not_block():
    # Sem store (fail-open no filtro 3): não bloqueia por falta de infra.
    assert asyncio.run(ev.is_safe_to_send("x@dominio.com.br")) is True


# --------------------------------------------------------------------------- #
# O status de verificação Reoon NÃO decide mais o envio
# --------------------------------------------------------------------------- #

def test_signature_has_no_lead_score_or_status():
    # KL-145: a assinatura é (email, redis, store) — sem VerifyResult, sem lead_score.
    import inspect
    params = list(inspect.signature(ev.is_safe_to_send).parameters)
    assert params == ["email", "redis", "store"]
    assert not hasattr(ev, "SENDABLE_STATUSES")   # constante do KL-137 removida


# --------------------------------------------------------------------------- #
# _verify_and_filter — os 3 filtros + stats por-filtro
# --------------------------------------------------------------------------- #

def _t(tid, email, **extra):
    d = {"id": tid, "contact_email": email, "_alert_score": 40}
    d.update(extra)
    return d


def test_verify_and_filter_partitions_by_three_filters(monkeypatch):
    # 1 ok, 1 sintaxe ruim, 1 sem MX, 1 blocklistado → só o ok passa.
    def _mx(d):
        return "no_mx" if d == "semmx.com.br" else "ok"
    monkeypatch.setattr(ev, "_resolve_mx_sync", _mx)
    store = _FakeStore(blocked=["bloq@x.com.br"])
    w = _mk_worker(store, validate_mx=True)
    targets = [_t(1, "bom@x.com.br"), _t(2, "sem-arroba"),
               _t(3, "alguem@semmx.com.br"), _t(4, "bloq@x.com.br")]
    kept, stats = asyncio.run(w._verify_and_filter(targets))
    assert {t["id"] for t in kept} == {1}
    assert stats["eligible"] == 4
    assert stats["valid_syntax"] == 3 and stats["blocked_syntax"] == 1
    assert stats["has_mx"] == 2 and stats["blocked_mx"] == 1
    assert stats["not_blocklisted"] == 1 and stats["blocked_blocklist"] == 1


def test_verify_and_filter_sends_regardless_of_verify_status():
    # KL-145: unknown/catch_all/email_verified=false/sem verificação → TODOS enviam (status ignorado).
    store = _FakeStore()
    w = _mk_worker(store)
    targets = [
        _t(1, "a@x.com.br"),                                                  # sem verificação
        _t(2, "b@x.com.br", email_verified=False, email_verify_status=None),  # não verificado
        _t(3, "c@x.com.br", email_verified=True, email_verify_status="unknown"),
        _t(4, "d@x.com.br", email_verified=True, email_verify_status="catch_all"),
    ]
    kept, stats = asyncio.run(w._verify_and_filter(targets))
    assert {t["id"] for t in kept} == {1, 2, 3, 4}
    assert stats["not_blocklisted"] == 4


def test_verify_and_filter_no_reoon_call(monkeypatch):
    # Garante que o fluxo de envio NÃO toca a API Reoon (verify_email nunca é chamado).
    async def _boom(*a, **k):
        raise AssertionError("KL-145: _verify_and_filter não pode chamar o Reoon")
    monkeypatch.setattr(ev, "verify_email", _boom)
    monkeypatch.setenv("REOON_API_KEY", "test-key")   # mesmo com key, não chama
    store = _FakeStore()
    w = _mk_worker(store)
    kept, _ = asyncio.run(w._verify_and_filter([_t(1, "a@x.com.br")]))
    assert {t["id"] for t in kept} == {1}


def test_verify_and_filter_volume_200(monkeypatch):
    # 200 elegíveis, todos válidos e não-blocklistados → 200 sendable (era 2-8 com o Reoon).
    store = _FakeStore()
    w = _mk_worker(store, validate_mx=True)
    targets = [_t(i, f"lead{i}@dominio{i}.com.br") for i in range(1, 201)]
    kept, stats = asyncio.run(w._verify_and_filter(targets))
    assert len(kept) == 200 and stats["not_blocklisted"] == 200


def test_verify_and_filter_empty_email_is_error():
    store = _FakeStore()
    w = _mk_worker(store)
    kept, stats = asyncio.run(w._verify_and_filter([_t(1, "")]))
    assert kept == [] and stats["errors"] == 1


# --------------------------------------------------------------------------- #
# Blocklist aprendente: bounce → blocklist → próximo envio bloqueado
# --------------------------------------------------------------------------- #

def test_bounce_added_to_blocklist_blocks_next_send():
    store = _FakeStore()
    email = "vaibouncar@dominio.com.br"
    assert asyncio.run(ev.is_safe_to_send(email, store=store)) is True   # antes do bounce
    asyncio.run(store.block_email(email, "bounced"))                     # webhook de bounce
    assert asyncio.run(ev.is_safe_to_send(email, store=store)) is False  # depois: bloqueado


# --------------------------------------------------------------------------- #
# Sem código morto no pipeline (regra herdada do KL-127/128)
# --------------------------------------------------------------------------- #

def test_no_dead_code_in_pipeline():
    import inspect
    from discovery import alert_worker
    for mod in (ev, alert_worker):
        src = inspect.getsource(mod)
        assert "if False" not in src
        assert "DESABILITADO" not in src
