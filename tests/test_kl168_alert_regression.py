"""KL-168 — fix da regressão do KL-167 no alert worker (parte MX/90d ainda vigente).

⚠️ O eixo "filtro de genéricos" do KL-168 foi SUPERADO pelo KL-169 (genéricos deixaram de ser
filtrados — passam a ser só priorizados; ver `test_kl169_email_priority.py`). Restam aqui os dois
eixos ainda vigentes:
  1. blocked_mx: `email_mx_status` cru p/ logging; NoAnswer vira 'unknown' (fail-open); só
     'no_mx' definitivo (NXDOMAIN/NULL-MX) bloqueia.
  2. Intervalo de 90 dias: e-mail nunca alertado (fora do set de `recently_alerted_emails`) passa.

Offline: DNS mockado, store falso.
"""
from __future__ import annotations

import asyncio

import pytest

from notifier import email_verifier as ev
from discovery.alert_worker import AlertWorker


# --------------------------------------------------------------------------- #
# Fix 2 — email_mx_status: status cru + fail-open
# --------------------------------------------------------------------------- #

def test_email_mx_status_ok(monkeypatch):
    monkeypatch.setattr(ev, "_resolve_mx_sync", lambda d: "ok")
    assert asyncio.run(ev.email_mx_status("x@empresa.com.br")) == "ok"


def test_email_mx_status_no_mx(monkeypatch):
    monkeypatch.setattr(ev, "_resolve_mx_sync", lambda d: "no_mx")
    assert asyncio.run(ev.email_mx_status("x@empresa.com.br")) == "no_mx"


def test_email_mx_status_unknown_is_fail_open(monkeypatch):
    # DNS incerto (timeout/resolver lento) → 'unknown' → NÃO bloqueia (só ordena/loga).
    monkeypatch.setattr(ev, "_resolve_mx_sync", lambda d: "unknown")
    assert asyncio.run(ev.email_mx_status("x@empresa.com.br")) == "unknown"
    assert asyncio.run(ev._email_has_mx("x@empresa.com.br")) is True


def test_email_mx_status_invalid_when_no_domain():
    assert asyncio.run(ev.email_mx_status("sem-arroba")) == "invalid"


def test_noanswer_maps_to_unknown_not_no_mx(monkeypatch):
    # KL-168: NoAnswer (domínio existe, sem MX) → 'unknown' (implicit MX via registro A), não 'no_mx'.
    import dns.resolver

    def _raise_noanswer(*a, **k):
        raise dns.resolver.NoAnswer()

    monkeypatch.setattr(dns.resolver, "resolve", _raise_noanswer)
    assert ev._resolve_mx_sync("existe-sem-mx.com.br") == "unknown"


def test_nxdomain_maps_to_no_mx(monkeypatch):
    import dns.resolver

    def _raise_nxdomain(*a, **k):
        raise dns.resolver.NXDOMAIN()

    monkeypatch.setattr(dns.resolver, "resolve", _raise_nxdomain)
    assert ev._resolve_mx_sync("nao-existe-mesmo.invalid") == "no_mx"


# --------------------------------------------------------------------------- #
# Fix 2 — _verify_and_filter loga e bloqueia só 'no_mx'; 'unknown' passa
# --------------------------------------------------------------------------- #

class _FakeStore:
    def __init__(self, blocked=None, recent=None):
        self._blocked = {(e or "").lower() for e in (blocked or [])}
        self._recent = {(e or "").lower() for e in (recent or [])}

    async def is_email_blocked(self, email):
        return (email or "").lower() in self._blocked

    async def recently_alerted_emails(self, emails, days=90):
        cand = {(e or "").lower() for e in emails}
        return {e for e in self._recent if e in cand}


def _mk_worker(store, validate_mx=True):
    w = AlertWorker()
    w.store = store
    w._redis = False
    w.validate_mx = validate_mx
    w.realert_min_days = 90
    return w


def _t(tid, email):
    return {"id": tid, "contact_email": email, "_alert_score": 40}


def test_verify_and_filter_blocks_only_definitive_no_mx(monkeypatch):
    # dominio 'nomx.com.br' → no_mx (bloqueia); 'lento.com.br' → unknown (fail-open, passa).
    def _mx(d):
        return {"nomx.com.br": "no_mx", "lento.com.br": "unknown"}.get(d, "ok")
    monkeypatch.setattr(ev, "_resolve_mx_sync", _mx)
    store = _FakeStore()
    w = _mk_worker(store)
    targets = [_t(1, "ana@ok.com.br"), _t(2, "bruno@nomx.com.br"), _t(3, "caio@lento.com.br")]
    kept, stats = asyncio.run(w._verify_and_filter(targets))
    assert {t["id"] for t in kept} == {1, 3}      # ok + unknown passam
    assert stats["blocked_mx"] == 1               # só o no_mx definitivo
    assert stats["has_mx"] == 2


# --------------------------------------------------------------------------- #
# Fix 3 — intervalo de 90 dias: nunca-alertado (fora do set) passa
# --------------------------------------------------------------------------- #

def test_never_alerted_email_passes_90d_filter(monkeypatch):
    monkeypatch.setattr(ev, "_resolve_mx_sync", lambda d: "ok")
    # 'ana@' já foi alertada <90d (no set); 'nova@' NUNCA (NULL) → não está no set → passa.
    store = _FakeStore(recent=["ana@a.com.br"])
    w = _mk_worker(store)
    targets = [_t(1, "ana@a.com.br"), _t(2, "nova@b.com.br")]
    kept, stats = asyncio.run(w._verify_and_filter(targets))
    assert {t["id"] for t in kept} == {2}
    assert stats["blocked_recent_email"] == 1


def test_realert_disabled_when_zero_days(monkeypatch):
    monkeypatch.setattr(ev, "_resolve_mx_sync", lambda d: "ok")
    store = _FakeStore(recent=["ana@a.com.br"])
    w = _mk_worker(store)
    w.realert_min_days = 0   # desliga o intervalo por e-mail
    targets = [_t(1, "ana@a.com.br"), _t(2, "nova@b.com.br")]
    kept, stats = asyncio.run(w._verify_and_filter(targets))
    assert {t["id"] for t in kept} == {1, 2}
    assert stats["blocked_recent_email"] == 0
