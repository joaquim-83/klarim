"""Testes do Re-scan Worker (KL-13 + KL-23 batch) — offline, com fakes."""

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


# KL-27: assunto neutro e único (sem preço, sem "melhorou"/"caiu"), corpo sem preço.
_EVO_SUBJECT = "hotelx.com.br — atualização da avaliação de segurança"


def test_evolution_improved_email(monkeypatch):
    res, cap = _send_evolution(monkeypatch, "improved", 80, 92, "verde", 1)
    assert res["email_id"] == "em_evo"
    assert cap["subject"] == _EVO_SUBJECT
    assert "Parabéns" in cap["html"] and "descadastrar" in cap["html"]
    assert "R$" not in cap["html"]


def test_evolution_worsened_email(monkeypatch):
    res, cap = _send_evolution(monkeypatch, "worsened", 92, 60, "amarelo", 3)
    assert cap["subject"] == _EVO_SUBJECT
    assert "Novos problemas" in cap["html"] and "R$" not in cap["html"]


def test_evolution_unchanged_email(monkeypatch):
    res, cap = _send_evolution(monkeypatch, "unchanged", 80, 80, "amarelo", 2)
    assert cap["subject"] == _EVO_SUBJECT
    assert "permanece em" in cap["html"] and "R$" not in cap["html"]


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

    async def _run(url, full=True):
        return FakeReport(FakeScore(new_score, sem, 14 - failed, failed, 1), results)

    return _run


class FakeMailer:
    def __init__(self):
        self.batches = []
        self.singles = []

    async def send_evolution_batch(self, evolutions):
        self.batches.append(list(evolutions))
        ids = [f"evo_{i}" for i in range(len(evolutions))]
        return {"sent": len(evolutions), "failed": 0, "ids": ids}

    async def send_evolution(self, to_email, target_url, old_score, new_score, evolution,
                             semaphore, fail_count, severity_counts, price_display,
                             unsubscribe_link=None, risk_messages=None, target_id=None):
        self.singles.append({"to": to_email, "evolution": evolution, "old": old_score,
                             "new": new_score, "target_id": target_id})
        return {"email_id": f"single_{len(self.singles)}"}


class FakeStore:
    """Simula o ciclo real: log_rescan(email_id=None) cria uma pendência que o
    get_pending_evolution_emails devolve (como o JOIN do Postgres)."""

    def __init__(self, eligible=None, pending=None, sent_month=0):
        self._eligible = eligible or []
        self._by_id = {t["id"]: t for t in self._eligible}
        self._pending = list(pending or [])
        self._sent_month = sent_month
        self.saved = []
        self.rescans = []
        self.contacted = []
        self.updated_emails = []
        self._next_rescan_id = 1000

    async def save_scan(self, *a, **kw):
        self.saved.append((a, kw))
        return 999

    async def update_scan_result(self, target_id, scan_id, score):
        pass

    async def log_rescan(self, target_id, old_score, new_score, evolution,
                         old_sem, new_sem, email_id=None):
        rid = self._next_rescan_id
        self._next_rescan_id += 1
        self.rescans.append({"rescan_id": rid, "target_id": target_id,
                             "evolution": evolution, "email_id": email_id})
        if email_id is None:
            t = self._by_id.get(target_id, {})
            self._pending.append({
                "rescan_id": rid, "target_id": target_id,
                "old_score": old_score, "new_score": new_score,
                "evolution": evolution, "new_semaphore": new_sem,
                "url": t.get("url", f"https://site{target_id}.com.br"),
                "contact_email": t.get("contact_email", "c@x.com.br"),
                "price_tier": t.get("price_tier", "standard"), "fail_count": 1,
                "checks_json": {"results": [{"status": "FAIL", "severity": "ALTA"}]},
            })
        return rid

    async def mark_target_contacted(self, target_id):
        self.contacted.append(target_id)

    async def update_rescan_email(self, rescan_id, email_id):
        self.updated_emails.append((rescan_id, email_id))
        for p in self._pending:
            if p["rescan_id"] == rescan_id:
                p["_sent"] = True

    async def count_proactive_emails_this_month(self):
        return self._sent_month

    async def get_targets_for_rescan(self, days=30, limit=50):
        return list(self._eligible)

    async def get_pending_evolution_emails(self, days=7, limit=50):
        return [p for p in self._pending if not p.get("_sent")][:limit]


def _target(tid=1, email="c@x.com.br", old_score=80, tier="standard"):
    return {"id": tid, "url": f"https://site{tid}.com.br", "contact_email": email,
            "last_scan_score": old_score, "old_semaphore": "amarelo", "price_tier": tier}


# --- rescan_target (envio único, usado pela API) --------------------------- #

def test_rescan_target_improved_sends_and_logs(monkeypatch):
    monkeypatch.setattr(rw, "run_scan", _fake_run_scan(92, failed=1))
    store, mailer = FakeStore(), FakeMailer()
    res = asyncio.run(rescan_target(store, mailer, None, _target(old_score=80), send_email=True))
    assert res["evolution"] == "improved" and res["sent"] is True
    assert store.rescans[0]["evolution"] == "improved"
    assert store.rescans[0]["email_id"] == "single_1"
    assert store.contacted == [1]  # gate do Alert Worker
    assert mailer.singles[0]["evolution"] == "improved"


def test_rescan_target_no_email_when_disabled(monkeypatch):
    monkeypatch.setattr(rw, "run_scan", _fake_run_scan(70))
    store, mailer = FakeStore(), FakeMailer()
    res = asyncio.run(rescan_target(store, mailer, None, _target(old_score=80), send_email=False))
    assert res["evolution"] == "worsened" and res["sent"] is False
    assert store.rescans[0]["email_id"] is None
    assert store.contacted == []  # sem e-mail -> não marca contato
    assert mailer.singles == []


# --- run_cycle (batch) ----------------------------------------------------- #

def _worker(store):
    w = RescanWorker()
    w.store = store
    w.pause_s = 0
    w.batch_pause = 0
    w.email_batch_size = 50
    w.monthly_limit = 45000
    w._mailer = lambda: FakeMailer()  # noqa: E731
    return w


def test_run_cycle_rescans_then_batches_emails(monkeypatch):
    monkeypatch.setattr(rw, "run_scan", _fake_run_scan(92))
    store = FakeStore(eligible=[_target(1), _target(2)])
    stats = asyncio.run(_worker(store).run_cycle())
    assert stats["eligible"] == 2 and stats["rescanned"] == 2 and stats["emailed"] == 2
    # os 2 e-mails de evolução saíram, foram vinculados e marcaram contato
    assert len(store.updated_emails) == 2
    assert sorted(store.contacted) == [1, 2]


def test_run_cycle_defers_emails_when_monthly_full(monkeypatch):
    monkeypatch.setattr(rw, "run_scan", _fake_run_scan(92))
    store = FakeStore(eligible=[_target(1)], sent_month=45000)
    stats = asyncio.run(_worker(store).run_cycle())
    assert stats["rescanned"] == 1 and stats["emailed"] == 0
    assert store.updated_emails == []  # e-mail adiado
    assert store.rescans[0]["email_id"] is None  # continua pendente


def test_run_cycle_flushes_prior_pending(monkeypatch):
    monkeypatch.setattr(rw, "run_scan", _fake_run_scan(92))
    pending = [{"rescan_id": 5, "target_id": 3, "old_score": 80, "new_score": 70,
                "evolution": "worsened", "new_semaphore": "amarelo",
                "url": "https://site3.com.br", "contact_email": "c@x.com.br",
                "price_tier": "standard", "fail_count": 2,
                "checks_json": {"results": [{"status": "FAIL", "severity": "ALTA"}]}}]
    store = FakeStore(eligible=[], pending=pending)
    stats = asyncio.run(_worker(store).run_cycle())
    assert stats["emailed"] == 1
    assert store.updated_emails and store.updated_emails[0][0] == 5
    assert store.contacted == [3]
