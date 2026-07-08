"""Testes do Re-scan Worker (KL-13) — offline, com scan/store/mailer falsos."""

from __future__ import annotations

import asyncio

from notifier import KlarimMailer
import discovery.rescan_worker as rw
from discovery.rescan_worker import (
    RescanWorker,
    rescan_target,
    classify_evolution,
    price_display_for_tier,
)

# Sem pausa entre reenvios nos testes.
rw.EVOLUTION_PAUSE_SECONDS = 0


# --- classify_evolution ---------------------------------------------------- #

def test_classify_evolution():
    assert classify_evolution(None, 80) == "first_rescan"
    assert classify_evolution(80, 92) == "improved"
    assert classify_evolution(92, 60) == "worsened"
    assert classify_evolution(80, 80) == "unchanged"
    assert classify_evolution(80, None) == "unchanged"


def test_price_display_for_tier():
    assert price_display_for_tier("standard") == "R$ 29,00"
    assert price_display_for_tier("enterprise") == "R$ 49,00"
    assert price_display_for_tier(None) == "R$ 29,00"
    assert price_display_for_tier("desconhecido") == "R$ 29,00"


# --- templates de evolução (Resend mockado) -------------------------------- #

def _send_evolution(monkeypatch, evolution, old, new, semaphore, fail_count):
    import resend

    captured = {}

    class FakeEmails:
        @staticmethod
        def send(params):
            captured.update(params)
            return {"id": "em_evo"}

    monkeypatch.setattr(resend, "Emails", FakeEmails)
    m = KlarimMailer("re_fake")
    res = asyncio.run(m.send_evolution(
        "d@e.com", "https://www.hotelx.com.br", old, new, evolution, semaphore,
        fail_count, {"critica": 0, "alta": fail_count, "media": 0, "baixa": 0},
        "R$ 29,00", unsubscribe_link="https://klarim.net/api/unsubscribe?email=d%40e.com&token=x"))
    return res, captured


def test_evolution_improved_email(monkeypatch):
    res, cap = _send_evolution(monkeypatch, "improved", 80, 92, "verde", 1)
    assert res["email_id"] == "em_evo"
    assert "melhorou" in cap["subject"] and "de 80 para 92" in cap["subject"]
    assert "Parabéns" in cap["html"] and "descadastrar" in cap["html"]


def test_evolution_worsened_email(monkeypatch):
    res, cap = _send_evolution(monkeypatch, "worsened", 92, 60, "amarelo", 3)
    assert "caiu de 92 para 60" in cap["subject"]
    assert "Novos problemas" in cap["html"] and "LGPD" in cap["html"]


def test_evolution_unchanged_email(monkeypatch):
    res, cap = _send_evolution(monkeypatch, "unchanged", 80, 80, "amarelo", 2)
    assert "permanece em 80/100" in cap["subject"]
    assert "permanece em" in cap["html"]


# --- fakes ----------------------------------------------------------------- #

class FakeScore:
    def __init__(self, score, semaphore, passed, failed, inconclusive):
        self.score, self.semaphore = score, semaphore
        self.passed, self.failed, self.inconclusive = passed, failed, inconclusive


class FakeReport:
    def __init__(self, score_obj, results):
        self.score = score_obj
        self._results = results

    def to_dict(self):
        return {"results": self._results}


def _fake_run_scan(new_score, failed=1):
    results = [{"status": "FAIL", "severity": "ALTA"}] * failed
    sem = "verde" if new_score >= 90 and failed == 0 else ("amarelo" if new_score >= 50 else "vermelho")

    async def _run(url):
        return FakeReport(FakeScore(new_score, sem, 14 - failed, failed, 1), results)

    return _run


class FakeMailer:
    def __init__(self):
        self.sent = []

    async def send_evolution(self, to_email, target_url, old_score, new_score, evolution,
                             semaphore, fail_count, severity_counts, price_display,
                             unsubscribe_link=None, risk_messages=None):
        self.sent.append({"to": to_email, "evolution": evolution,
                          "old": old_score, "new": new_score, "risks": risk_messages})
        return {"email_id": f"em_{len(self.sent)}"}


class FakeStore:
    def __init__(self, eligible=None, pending=None, hour=0, day=0):
        self._eligible = eligible or []
        self._pending = pending or []
        self._hour, self._day = hour, day
        self.saved = []
        self.rescans = []
        self.contacted = []
        self.updated_emails = []

    async def save_scan(self, *a, **kw):
        self.saved.append((a, kw))
        return 999

    async def update_scan_result(self, target_id, scan_id, score):
        pass

    async def log_rescan(self, target_id, old_score, new_score, evolution,
                         old_sem, new_sem, email_id=None):
        self.rescans.append({"target_id": target_id, "evolution": evolution,
                            "email_id": email_id})
        return len(self.rescans)

    async def mark_target_contacted(self, target_id):
        self.contacted.append(target_id)

    async def update_rescan_email(self, rescan_id, email_id):
        self.updated_emails.append((rescan_id, email_id))

    async def count_proactive_emails_last_hours(self, hours):
        return self._day if hours >= 24 else self._hour

    async def get_targets_for_rescan(self, days=30, limit=50):
        return list(self._eligible)

    async def get_pending_evolution_emails(self, days=7, limit=50):
        return list(self._pending)


def _target(tid=1, email="c@x.com.br", old_score=80, tier="standard"):
    return {"id": tid, "url": f"https://site{tid}.com.br", "contact_email": email,
            "last_scan_score": old_score, "old_semaphore": "amarelo", "price_tier": tier}


# --- rescan_target --------------------------------------------------------- #

def test_rescan_target_improved_sends_and_logs(monkeypatch):
    monkeypatch.setattr(rw, "run_scan", _fake_run_scan(92, failed=1))
    store, mailer = FakeStore(), FakeMailer()
    res = asyncio.run(rescan_target(store, mailer, None, _target(old_score=80), send_email=True))
    assert res["evolution"] == "improved" and res["sent"] is True
    assert store.rescans[0]["evolution"] == "improved"
    assert store.rescans[0]["email_id"] == "em_1"
    assert store.contacted == [1]  # gate do Alert Worker
    assert mailer.sent[0]["evolution"] == "improved"


def test_rescan_target_no_email_when_disabled(monkeypatch):
    monkeypatch.setattr(rw, "run_scan", _fake_run_scan(70))
    store, mailer = FakeStore(), FakeMailer()
    res = asyncio.run(rescan_target(store, mailer, None, _target(old_score=80), send_email=False))
    assert res["evolution"] == "worsened" and res["sent"] is False
    assert store.rescans[0]["email_id"] is None
    assert store.contacted == []  # sem e-mail -> não marca contato
    assert mailer.sent == []


# --- run_cycle ------------------------------------------------------------- #

def _worker(store):
    w = RescanWorker()
    w.store = store
    w.max_hour, w.max_day = 10, 50
    w.pause_s = 0
    w._mailer = lambda: FakeMailer()  # noqa: E731
    return w


def test_run_cycle_rescans_and_emails(monkeypatch):
    monkeypatch.setattr(rw, "run_scan", _fake_run_scan(92))
    store = FakeStore(eligible=[_target(1), _target(2)])
    stats = asyncio.run(_worker(store).run_cycle())
    assert stats["eligible"] == 2 and stats["rescanned"] == 2 and stats["emailed"] == 2
    assert stats["deferred"] == 0


def test_run_cycle_defers_email_when_throttled(monkeypatch):
    monkeypatch.setattr(rw, "run_scan", _fake_run_scan(92))
    # já no teto por hora: reescaneia mas adia o e-mail
    store = FakeStore(eligible=[_target(1)], hour=10)
    stats = asyncio.run(_worker(store).run_cycle())
    assert stats["rescanned"] == 1 and stats["emailed"] == 0 and stats["deferred"] == 1
    assert store.rescans[0]["email_id"] is None


def test_run_cycle_flushes_pending(monkeypatch):
    monkeypatch.setattr(rw, "run_scan", _fake_run_scan(92))
    pending = [{"rescan_id": 5, "target_id": 3, "old_score": 80, "new_score": 70,
                "evolution": "worsened", "new_semaphore": "amarelo",
                "url": "https://site3.com.br", "contact_email": "c@x.com.br",
                "price_tier": "standard", "fail_count": 2,
                "checks_json": {"results": [{"status": "FAIL", "severity": "ALTA"}]}}]
    store = FakeStore(eligible=[], pending=pending)
    w = _worker(store)
    stats = asyncio.run(w.run_cycle())
    assert stats["pending_resent"] == 1
    assert store.updated_emails and store.updated_emails[0][0] == 5
    assert store.contacted == [3]
