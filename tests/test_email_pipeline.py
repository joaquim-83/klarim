"""KL-26 — pipeline de e-mail completo (interações entre KL-91/102/108/110).

Cobre as junções que os testes por-feature não cruzam: circuit breaker hard/soft →
rotação, verificação → decisão de envio, webhook de bounce → blocklist → worker pula.
Sem rede real (Resend/Reoon/DNS mockados ou funções puras).
"""
from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

import api.main as m
from notifier import cold_alert
from notifier import email_verifier as ev
from notifier.email_client import (
    generate_unsubscribe_token, verify_unsubscribe_token, build_cold_unsubscribe_headers,
)
from discovery.alert_worker import AlertWorker

_SECRET = "s" * 40


# --------------------------------------------------------------------------- #
# Circuit breaker hard/soft (KL-108)
# --------------------------------------------------------------------------- #

def test_breaker_pauses_on_hard_over_threshold():
    s = cold_alert.load_senders({})
    by = {"alertas.klarim.net": {"total": 100, "hard_bounced": 6, "soft_bounced": 10},
          "aviso.klarim.net": {"total": 100, "hard_bounced": 1, "soft_bounced": 0}}
    paused = cold_alert.flag_high_bounce(s, by, max_rate=5.0, min_sample=20)
    assert paused == [("alertas.klarim.net", 6.0)]  # hard 6% > 5% → pausa (soft irrelevante)


def test_breaker_ignores_high_soft():
    s = cold_alert.load_senders({})
    by = {"alertas.klarim.net": {"total": 100, "hard_bounced": 3, "soft_bounced": 15},
          "aviso.klarim.net": {"total": 100, "hard_bounced": 0, "soft_bounced": 0}}
    paused = cold_alert.flag_high_bounce(s, by, max_rate=5.0, min_sample=20)
    assert paused == []  # hard 3% < 5% → NÃO pausa apesar do soft 15%


def test_breaker_reads_hard_not_combined():
    # Combinado seria 4+8=12% (>5%); hard sozinho é 4% (<5%) → não pausa.
    s = cold_alert.load_senders({})
    by = {"alertas.klarim.net": {"total": 100, "hard_bounced": 4, "soft_bounced": 8}}
    assert cold_alert.flag_high_bounce(s, by, max_rate=5.0, min_sample=20) == []


# --------------------------------------------------------------------------- #
# Verificação → decisão de envio (KL-110)
# --------------------------------------------------------------------------- #

def test_send_decision_safe_high_score():
    assert ev.is_safe_to_send(ev.VerifyResult("safe", "x"), 60) is True


def test_send_decision_catch_all_low_score_skips():
    # KL-122: gate caiu p/ 20 → um score abaixo do gate (15) ainda pula.
    assert ev.is_safe_to_send(ev.VerifyResult("catch_all", "x"), 15) is False


def test_send_decision_catch_all_high_score_sends():
    assert ev.is_safe_to_send(ev.VerifyResult("catch_all", "x"), 60) is True


def test_send_decision_invalid_never_sends():
    assert ev.is_safe_to_send(ev.VerifyResult("invalid", "x"), 99) is False


def test_send_decision_unknown_is_fail_open_gated():
    # Reoon fora → 'unknown'; envia só se lead de qualidade (KL-122 gate=20).
    assert ev.is_safe_to_send(ev.VerifyResult("unknown", "api_unavailable"), 15) is False
    assert ev.is_safe_to_send(ev.VerifyResult("unknown", "api_unavailable"), 60) is True


def test_verify_api_timeout_fails_open(monkeypatch):
    async def _boom(*a, **k):
        raise TimeoutError("reoon down")
    monkeypatch.setattr(ev, "verify_reoon", _boom)
    r = asyncio.run(ev.verify_api("x@y.com", api_key="k"))
    assert r.status == "unknown" and r.source == "fallback"


# --------------------------------------------------------------------------- #
# List-Unsubscribe (KL-102) — só cold, token válido
# --------------------------------------------------------------------------- #

def test_cold_headers_have_oneclick_and_token():
    h = build_cold_unsubscribe_headers("dono@x.com.br", "x.com.br", "alertas.klarim.net",
                                       secret=_SECRET)
    assert "mailto:scan@klarim.net" in h["List-Unsubscribe"]
    assert "https://klarim.net/remover?token=" in h["List-Unsubscribe"]
    assert h["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"


def test_cold_headers_fallback_mailto_when_no_secret():
    h = build_cold_unsubscribe_headers("dono@x.com.br", "x.com.br", "alertas.klarim.net",
                                       secret="")
    assert h["List-Unsubscribe"].startswith("<mailto:")
    assert "List-Unsubscribe-Post" not in h  # sem one-click sem secret


def test_unsubscribe_token_roundtrip_normalizes_case():
    t1 = generate_unsubscribe_token("Dono@X.com.BR", "X.com.br", _SECRET, "perfil.klarim.net")
    t2 = generate_unsubscribe_token("dono@x.com.br", "x.com.br", _SECRET, "perfil.klarim.net")
    assert t1 == t2  # determinístico + normaliza
    info = verify_unsubscribe_token(t1, _SECRET)
    assert info["email"] == "dono@x.com.br" and info["domain"] == "x.com.br"
    assert info["sender"] == "perfil.klarim.net"


def test_unsubscribe_token_rejects_tamper():
    t = generate_unsubscribe_token("a@b.com", "b.com", _SECRET, "alertas.klarim.net")
    assert verify_unsubscribe_token(t, "outro-secret") is None
    assert verify_unsubscribe_token(t + "x", _SECRET) is None


# --------------------------------------------------------------------------- #
# Rotação round-robin (KL-91)
# --------------------------------------------------------------------------- #

def test_rotation_alternates_between_two_active():
    s = cold_alert.load_senders({})
    counts = {"alertas.klarim.net": 0, "aviso.klarim.net": 0}
    first = cold_alert.pick_sender(s, counts, 100)
    counts[first.from_domain] += 1
    second = cold_alert.pick_sender(s, counts, 100)
    assert first.from_domain != second.from_domain  # alterna pelo de menor volume


def test_rotation_uses_only_active_when_one_paused():
    s = cold_alert.load_senders({})
    s[0].status = "paused"
    got = cold_alert.pick_sender(s, {"alertas.klarim.net": 0, "aviso.klarim.net": 5}, 100)
    assert got.from_domain == "aviso.klarim.net"  # o ativo, mesmo com mais volume


def test_rotation_none_when_all_at_daily_limit():
    s = cold_alert.load_senders({})
    counts = {"alertas.klarim.net": 100, "aviso.klarim.net": 100}
    assert cold_alert.pick_sender(s, counts, 100) is None  # ninguém disponível


# --------------------------------------------------------------------------- #
# Webhook de bounce → blocklist → o worker PULA o alvo (integração)
# --------------------------------------------------------------------------- #

class _BounceStore:
    def __init__(self):
        self.discarded, self.blocked, self.marked, self.status_updates = [], [], [], []
        self._blocklist = set()

    async def discard_target_by_email(self, email, reason="bounced"):
        self.discarded.append((email, reason)); return 1

    async def block_email(self, email, reason="bounced"):
        self.blocked.append((email, reason)); self._blocklist.add(email.lower())

    async def mark_unsubscribed(self, email):
        return 1

    async def mark_alert_status_by_email_id(self, email_id, status):
        self.marked.append((email_id, status)); return 1

    async def mark_email_status_by_email_id(self, email_id, status):
        self.marked.append((email_id, status)); return 1

    async def is_email_blocked(self, email):
        return (email or "").lower() in self._blocklist

    async def update_status(self, target_id, status):
        self.status_updates.append((target_id, status))


def _client(monkeypatch, store):
    monkeypatch.setattr(m, "get_target_store", lambda: store)
    monkeypatch.delenv("RESEND_WEBHOOK_SECRET", raising=False)
    return TestClient(m.app, raise_server_exceptions=False)


def test_permanent_bounce_discards_and_blocklists(monkeypatch):
    store = _BounceStore()
    c = _client(monkeypatch, store)
    r = c.post("/webhooks/resend", json={"type": "email.bounced", "data": {
        "email_id": "e1", "to": ["ruim@dominio.com.br"],
        "bounce": {"type": "permanent", "message": "550 no such user"}}})
    assert r.status_code == 200
    assert ("ruim@dominio.com.br", "bounced") in store.blocked
    assert store.discarded and ("e1", "bounced") in store.marked


def test_transient_bounce_kept(monkeypatch):
    store = _BounceStore()
    c = _client(monkeypatch, store)
    r = c.post("/webhooks/resend", json={"type": "email.bounced", "data": {
        "email_id": "e2", "to": ["cheio@dominio.com.br"],
        "bounce": {"type": "Transient", "message": "mailbox full"}}})
    assert r.status_code == 200
    assert store.discarded == [] and store.blocked == []  # não descarta
    assert ("e2", "soft_bounced") in store.marked


def test_unknown_email_id_no_crash(monkeypatch):
    store = _BounceStore()
    c = _client(monkeypatch, store)
    r = c.post("/webhooks/resend", json={"type": "email.bounced", "data": {
        "email_id": "nao-existe", "to": ["x@y.com"],
        "bounce": {"type": "permanent", "message": "x"}}})
    assert r.status_code == 200  # rows==0 loga, não crasha


def test_blocklisted_email_skipped_by_validate_batch(monkeypatch):
    # Integração: um e-mail que bounceou (blocklist) é filtrado antes do envio.
    store = _BounceStore()
    asyncio.run(store.block_email("ruim@dominio.com.br", "bounced"))
    w = AlertWorker.__new__(AlertWorker)
    w.store = store
    w.validate_mx = False
    targets = [{"id": 1, "contact_email": "ruim@dominio.com.br", "url": "https://a.com"},
               {"id": 2, "contact_email": "bom@dominio.com.br", "url": "https://b.com"}]
    kept = asyncio.run(w._validate_batch(targets))
    kept_ids = {t["id"] for t in kept}
    assert kept_ids == {2}  # o blocklistado saiu
    assert (1, "descartado") in store.status_updates
