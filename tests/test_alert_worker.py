"""Testes do Alert Worker (KL-12) — offline, com store e mailer falsos."""

from __future__ import annotations

import asyncio
import hmac

from notifier import unsubscribe_token, build_unsubscribe_link
import discovery.alert_worker as aw
from discovery.alert_worker import (
    AlertWorker,
    send_alert_for_target,
    severity_counts_from_checks,
)

# Sem pausa de 5s entre envios nos testes.
aw.ALERT_PAUSE_SECONDS = 0


# --- unsubscribe token ----------------------------------------------------- #

def test_unsubscribe_token_roundtrip():
    secret = "s3cr3t"
    tok = unsubscribe_token("Contato@Hotelx.com.BR", secret)
    # normaliza (case/trim) -> mesmo token
    assert tok == unsubscribe_token("  contato@hotelx.com.br ", secret)
    assert len(tok) == 32
    assert hmac.compare_digest(tok, unsubscribe_token("contato@hotelx.com.br", secret))


def test_unsubscribe_token_rejects_tamper():
    tok = unsubscribe_token("a@b.com", "sec")
    assert not hmac.compare_digest(tok, unsubscribe_token("a@b.com", "outro"))
    assert not hmac.compare_digest(tok, unsubscribe_token("x@b.com", "sec"))


def test_build_unsubscribe_link():
    link = build_unsubscribe_link("a@b.com", "sec")
    assert link.startswith("https://klarim.net/api/unsubscribe?email=a%40b.com&token=")
    assert unsubscribe_token("a@b.com", "sec") in link


# --- severity counts ------------------------------------------------------- #

def test_severity_counts_from_checks():
    checks = {"results": [
        {"status": "FAIL", "severity": "ALTA"},
        {"status": "FAIL", "severity": "ALTA"},
        {"status": "FAIL", "severity": "CRITICA"},
        {"status": "PASS", "severity": "ALTA"},   # PASS não conta
        {"status": "FAIL", "severity": "MEDIA"},
    ]}
    assert severity_counts_from_checks(checks) == {"critica": 1, "alta": 2, "media": 1, "baixa": 0}
    assert severity_counts_from_checks(None) == {"critica": 0, "alta": 0, "media": 0, "baixa": 0}


# --- fakes ----------------------------------------------------------------- #

class FakeMailer:
    def __init__(self):
        self.sent = []

    async def send_alert(self, to_email, target_url, score, semaphore, fail_count,
                         severity_counts, unsubscribe_link=None, risk_messages=None):
        self.sent.append({"to": to_email, "url": target_url, "unsub": unsubscribe_link,
                          "risks": risk_messages})
        return {"email_id": f"em_{len(self.sent)}"}


class FakeStore:
    def __init__(self, eligible=None, sent_hour=0, sent_day=0):
        self._eligible = eligible or []
        self._sent_hour = sent_hour
        self._sent_day = sent_day
        self.alerted = []
        self.logged = []

    async def get_scan(self, scan_id):
        return {"score": 86, "semaphore": "amarelo", "fail_count": 2,
                "checks_json": {"results": [{"status": "FAIL", "severity": "ALTA"}]}}

    async def get_eligible_targets_for_alert(self, limit=50):
        return list(self._eligible)

    async def count_alerts_last_hours(self, hours):
        return self._sent_day if hours >= 24 else self._sent_hour

    async def count_proactive_emails_last_hours(self, hours):
        # Throttle global compartilhado (alertas + evolução) — KL-13.
        return self._sent_day if hours >= 24 else self._sent_hour

    async def mark_target_alerted(self, target_id):
        self.alerted.append(target_id)

    async def log_alert(self, target_id, contact_email, score, semaphore, fail_count,
                        email_id, status="sent"):
        self.logged.append({"target_id": target_id, "status": status, "email_id": email_id})
        return len(self.logged)


def _target(tid, email="c@x.com.br"):
    return {"id": tid, "url": f"https://site{tid}.com.br",
            "contact_email": email, "last_scan_id": 100 + tid}


# --- send_alert_for_target ------------------------------------------------- #

def test_send_alert_for_target_marks_and_logs():
    store, mailer = FakeStore(), FakeMailer()
    email_id = asyncio.run(send_alert_for_target(store, mailer, _target(1)))
    assert email_id == "em_1"
    assert store.alerted == [1]
    assert store.logged[0]["status"] == "sent"
    assert mailer.sent[0]["to"] == "c@x.com.br"


def test_send_alert_for_target_requires_email():
    try:
        asyncio.run(send_alert_for_target(FakeStore(), FakeMailer(), _target(1, email=None)))
        assert False, "esperava ValueError"
    except ValueError:
        pass


# --- run_cycle ------------------------------------------------------------- #

def _worker(store):
    w = AlertWorker()
    w.store = store
    w.max_hour, w.max_day = 10, 50
    w._mailer = lambda: FakeMailer()  # noqa: E731
    return w


def test_run_cycle_sends_eligible():
    store = FakeStore(eligible=[_target(1), _target(2)])
    stats = asyncio.run(_worker(store).run_cycle())
    assert stats["eligible"] == 2 and stats["sent"] == 2
    assert store.alerted == [1, 2]


def test_run_cycle_respects_global_throttle():
    # já bateu o teto por hora -> não envia nada
    store = FakeStore(eligible=[_target(1)], sent_hour=10)
    stats = asyncio.run(_worker(store).run_cycle())
    assert stats["sent"] == 0 and store.alerted == []


def test_run_cycle_stops_at_per_target_limit():
    # teto por hora = 1: envia 1, os demais viram throttled
    store = FakeStore(eligible=[_target(1), _target(2), _target(3)])
    w = _worker(store)
    w.max_hour = 1
    stats = asyncio.run(w.run_cycle())
    assert stats["sent"] == 1 and stats["throttled"] == 2


def test_run_cycle_skips_without_mailer():
    store = FakeStore(eligible=[_target(1)])
    w = _worker(store)
    w._mailer = lambda: None  # noqa: E731
    stats = asyncio.run(w.run_cycle())
    assert stats["sent"] == 0 and store.alerted == []
