"""Testes do Alert Worker (KL-12 + KL-23 batch) — offline, com store/mailer falsos."""

from __future__ import annotations

import asyncio
import hmac

from notifier import unsubscribe_token, build_unsubscribe_link
from discovery.alert_worker import (
    AlertWorker,
    build_alert_payload,
    send_alert_for_target,
    severity_counts_from_checks,
)


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
    def __init__(self, fail=False):
        self.batches = []
        self.singles = []
        self.fail = fail

    async def send_alert_batch(self, alerts):
        if self.fail:
            raise RuntimeError("boom")
        self.batches.append(list(alerts))
        ids = [f"em_{i}" for i in range(len(alerts))]
        return {"sent": len(alerts), "failed": 0, "ids": ids}

    async def send_alert(self, to_email, target_url, score, semaphore, fail_count,
                         severity_counts, unsubscribe_link=None, risk_messages=None,
                         target_id=None):
        self.singles.append({"to": to_email, "target_id": target_id})
        return {"email_id": f"single_{len(self.singles)}"}


class FakeStore:
    def __init__(self, eligible=None, sent_month=0):
        self._eligible = eligible or []
        self._sent_month = sent_month
        self.alerted = []
        self.logged = []

    async def get_scan(self, scan_id):
        return {"score": 86, "semaphore": "amarelo", "fail_count": 2,
                "checks_json": {"results": [{"status": "FAIL", "severity": "ALTA"}]}}

    async def get_eligible_targets_for_alert(self, limit=50):
        return list(self._eligible)[:limit]

    async def count_eligible_targets_for_alert(self):
        return len(self._eligible)

    async def count_proactive_emails_this_month(self):
        return self._sent_month

    async def mark_target_alerted(self, target_id):
        self.alerted.append(target_id)

    async def log_alert(self, target_id, contact_email, score, semaphore, fail_count,
                        email_id, status="sent"):
        self.logged.append({"target_id": target_id, "status": status, "email_id": email_id})
        return len(self.logged)


def _target(tid, email="c@x.com.br"):
    # inclui os campos do JOIN de get_eligible_targets_for_alert (sem get_scan extra)
    return {"id": tid, "url": f"https://site{tid}.com.br", "contact_email": email,
            "last_scan_id": 100 + tid, "last_scan_score": 86,
            "scan_semaphore": "amarelo", "scan_fail_count": 2,
            "scan_checks": {"results": [{"status": "FAIL", "severity": "ALTA"}]}}


# --- build_alert_payload --------------------------------------------------- #

def test_build_alert_payload_from_join_fields():
    payload = asyncio.run(build_alert_payload(FakeStore(), _target(7)))
    assert payload["to_email"] == "c@x.com.br"
    assert payload["target_id"] == 7 and payload["score"] == 86
    assert payload["severity_counts"]["alta"] == 1


def test_build_alert_payload_falls_back_to_get_scan():
    # alvo sem os campos do JOIN -> usa get_scan
    t = {"id": 1, "url": "https://x.com.br", "contact_email": "c@x.com.br",
         "last_scan_id": 101}
    payload = asyncio.run(build_alert_payload(FakeStore(), t))
    assert payload["score"] == 86 and payload["fail_count"] == 2


def test_build_alert_payload_requires_email():
    try:
        asyncio.run(build_alert_payload(FakeStore(), {"id": 1, "url": "x", "contact_email": None}))
        assert False, "esperava ValueError"
    except ValueError:
        pass


# --- send_alert_for_target (envio único, manual) --------------------------- #

def test_send_alert_for_target_marks_and_logs():
    store, mailer = FakeStore(), FakeMailer()
    email_id = asyncio.run(send_alert_for_target(store, mailer, _target(1)))
    assert email_id == "single_1"
    assert store.alerted == [1]
    assert store.logged[0]["status"] == "sent"


# --- run_cycle (batch) ----------------------------------------------------- #

def _worker(store):
    w = AlertWorker()
    w.store = store
    w.batch_size, w.batches_per_cycle, w.batch_pause = 50, 4, 0
    w.monthly_limit = 45000
    w._mailer = lambda: FakeMailer()  # noqa: E731
    return w


def test_run_cycle_sends_in_one_batch():
    store = FakeStore(eligible=[_target(1), _target(2)])
    stats = asyncio.run(_worker(store).run_cycle())
    assert stats["eligible"] == 2 and stats["sent"] == 2 and stats["batches"] == 1
    assert store.alerted == [1, 2]
    assert all(l["status"] == "sent" for l in store.logged)


def test_run_cycle_splits_into_batches():
    # 120 elegíveis, batch 50 -> 3 batches (50 + 50 + 20)
    store = FakeStore(eligible=[_target(i) for i in range(1, 121)])
    stats = asyncio.run(_worker(store).run_cycle())
    assert stats["sent"] == 120 and stats["batches"] == 3
    assert len(store.alerted) == 120


def test_run_cycle_monthly_limit_skips_when_full():
    store = FakeStore(eligible=[_target(1)], sent_month=45000)
    stats = asyncio.run(_worker(store).run_cycle())
    assert stats["sent"] == 0 and store.alerted == []


def test_run_cycle_monthly_limit_caps_fetch():
    # cota mensal deixa só 10 -> envia exatamente 10 e para
    store = FakeStore(eligible=[_target(i) for i in range(1, 51)], sent_month=44990)
    w = _worker(store)
    stats = asyncio.run(w.run_cycle())
    assert stats["sent"] == 10 and len(store.alerted) == 10


def test_run_cycle_batch_failure_logs_failed_not_alerted():
    store = FakeStore(eligible=[_target(1), _target(2)])
    w = _worker(store)
    w._mailer = lambda: FakeMailer(fail=True)  # noqa: E731
    stats = asyncio.run(w.run_cycle())
    assert stats["sent"] == 0 and stats["errors"] == 1
    assert store.alerted == []  # falha não marca alerted
    assert all(l["status"] == "failed" for l in store.logged)


def test_run_cycle_skips_without_mailer():
    store = FakeStore(eligible=[_target(1)])
    w = _worker(store)
    w._mailer = lambda: None  # noqa: E731
    stats = asyncio.run(w.run_cycle())
    assert stats["sent"] == 0 and store.alerted == []
