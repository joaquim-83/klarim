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
    def __init__(self, fail=False, bad=None):
        self.batches = []
        self.singles = []
        self.fail = fail
        self.bad = set(bad or [])  # e-mails que fazem o batch 422

    async def send_alert_batch(self, alerts):
        if self.fail:
            raise RuntimeError("boom")
        if any(a["to_email"] in self.bad for a in alerts):
            from notifier import KlarimMailerError
            raise KlarimMailerError("Falha no batch Resend (422): Invalid 'to' field")
        self.batches.append(list(alerts))
        ids = [f"em_{i}" for i in range(len(alerts))]
        return {"sent": len(alerts), "failed": 0, "ids": ids}

    async def send_alert(self, to_email, target_url, score, semaphore, fail_count,
                         severity_counts, unsubscribe_link=None, risk_messages=None,
                         target_id=None, bonus_token=None):
        self.singles.append({"to": to_email, "target_id": target_id, "bonus_token": bonus_token})
        return {"email_id": f"single_{len(self.singles)}"}


class FakeStore:
    def __init__(self, eligible=None, sent_month=0, blocked=None, health=None):
        self._eligible = eligible or []
        self._sent_month = sent_month
        self._blocked = set(blocked or [])
        self._health = health or {"total": 0, "bounced": 0, "complained": 0, "blocklist": 0}
        self.alerted = []
        self.logged = []
        self.discarded = []          # via update_status('descartado')
        self.discarded_by_email = []  # via discard_target_by_email
        self.email_updates = []       # via update_target_email (self-heal)

    async def get_scan(self, scan_id):
        return {"score": 86, "semaphore": "amarelo", "fail_count": 2,
                "checks_json": {"results": [{"status": "FAIL", "severity": "ALTA"}]}}

    async def get_eligible_targets_for_alert(self, limit=50):
        return list(self._eligible)[:limit]

    async def count_eligible_targets_for_alert(self):
        return len(self._eligible)

    async def count_proactive_emails_this_month(self):
        return self._sent_month

    async def get_setting(self, key, default=None):
        # espelha a resolução do store real (DB > env > default): sem override, o default.
        return default

    async def count_alerts_sent_today(self):
        return getattr(self, "_sent_today", 0)

    async def email_health(self):
        return dict(self._health)

    async def is_email_blocked(self, email):
        return email in self._blocked

    async def update_status(self, target_id, status):
        self.discarded.append((target_id, status))

    async def update_target_email(self, target_id, email):
        self.email_updates.append((target_id, email))
        return {"id": target_id, "contact_email": email}

    async def discard_target_by_email(self, email, reason="bounced"):
        self.discarded_by_email.append((email, reason))
        return 1

    async def mark_target_alerted(self, target_id):
        self.alerted.append(target_id)

    async def log_alert(self, target_id, contact_email, score, semaphore, fail_count,
                        email_id, status="sent"):
        self.logged.append({"target_id": target_id, "status": status, "email_id": email_id})
        return len(self.logged)

    async def grant_full_scan_credit(self, email, url, reason="score100_bonus"):
        self.full_credits = getattr(self, "full_credits", [])
        self.full_credits.append((email, url))


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
    # KL-27: o alerta não carrega mais severidade/risco (e-mail sem detalhes).
    assert "severity_counts" not in payload and "risk_messages" not in payload


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
    w.validate_mx = False  # sem DNS nos testes de fluxo de batch
    w.max_bounce_rate = 8.0
    w.bounce_min_sample = 20
    w._mailer = lambda: FakeMailer()  # noqa: E731
    return w


def test_run_cycle_sends_in_one_batch():
    store = FakeStore(eligible=[_target(1), _target(2)])
    stats = asyncio.run(_worker(store).run_cycle())
    assert stats["eligible"] == 2 and stats["sent"] == 2 and stats["batches"] == 1
    assert store.alerted == [1, 2]
    assert all(l["status"] == "sent" for l in store.logged)


# --- controle centralizado KL-32 ------------------------------------------- #

def test_run_cycle_disabled_by_worker_control(tmp_path, monkeypatch):
    from discovery import worker_control
    monkeypatch.setenv("WORKER_CONTROL_FILE", str(tmp_path / "wc.json"))
    worker_control.pause("alert")
    store = FakeStore(eligible=[_target(1), _target(2)])
    stats = asyncio.run(_worker(store).run_cycle())
    assert stats.get("disabled") is True and stats["sent"] == 0
    assert store.alerted == []  # nada enviado enquanto pausado


def test_run_cycle_alert_throttle_from_control(tmp_path, monkeypatch):
    from discovery import worker_control
    monkeypatch.setenv("WORKER_CONTROL_FILE", str(tmp_path / "wc.json"))
    # max_per_hour=1 → o teto por ciclo cai para 1 (max(1, floor(1*intervalo/60))=1),
    # independente do intervalo do worker. Sem o throttle, enviaria os 10.
    worker_control.set_config("alert", max_per_hour=1)
    store = FakeStore(eligible=[_target(i) for i in range(1, 11)])
    stats = asyncio.run(_worker(store).run_cycle())
    assert stats["sent"] == 1  # throttle capou o ciclo em 1


# --- kill-switch STOP_ALERTS (KL-27) --------------------------------------- #

def test_alerts_stopped_flag(tmp_path, monkeypatch):
    from discovery.alert_worker import alerts_stopped
    monkeypatch.delenv("ALERTS_STOP_FILE", raising=False)
    assert alerts_stopped() is False               # var não configurada → nunca pausa
    flag = tmp_path / "STOP_ALERTS"
    monkeypatch.setenv("ALERTS_STOP_FILE", str(flag))
    assert alerts_stopped() is False               # configurada mas arquivo ausente
    flag.write_text("")
    assert alerts_stopped() is True                # arquivo presente → pausa


def test_run_cycle_paused_by_flag(tmp_path, monkeypatch):
    flag = tmp_path / "STOP_ALERTS"
    flag.write_text("")
    monkeypatch.setenv("ALERTS_STOP_FILE", str(flag))
    store = FakeStore(eligible=[_target(1), _target(2)])
    stats = asyncio.run(_worker(store).run_cycle())
    assert stats.get("paused_by_flag") is True
    assert stats["sent"] == 0 and store.alerted == []


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


# --- bounce handling (KL-24) ----------------------------------------------- #

def test_run_cycle_pauses_when_bounce_rate_critical():
    # 10/100 = 10% > limite 8% -> pausa (não envia)
    store = FakeStore(eligible=[_target(1)],
                      health={"total": 100, "bounced": 10, "complained": 0, "blocklist": 10})
    stats = asyncio.run(_worker(store).run_cycle())
    assert stats.get("paused") is True and stats["sent"] == 0
    assert store.alerted == []


def test_run_cycle_no_pause_below_min_sample():
    # 3/5 = 60% mas amostra < 20 -> não pausa (envia)
    store = FakeStore(eligible=[_target(1)],
                      health={"total": 5, "bounced": 3, "complained": 0, "blocklist": 0})
    stats = asyncio.run(_worker(store).run_cycle())
    assert not stats.get("paused") and stats["sent"] == 1


def test_validate_batch_drops_blocked_and_discards():
    store = FakeStore(blocked={"c@x.com.br"})
    w = _worker(store)
    clean = asyncio.run(w._validate_batch([_target(1, email="c@x.com.br"), _target(2, email="ok@y.com.br")]))
    assert [t["id"] for t in clean] == [2]
    assert store.discarded == [(1, "descartado")]  # bloqueado -> descartado


def test_run_cycle_validates_before_sending():
    store = FakeStore(eligible=[_target(1, email="blocked@x.com.br"), _target(2, email="ok@y.com.br")],
                      blocked={"blocked@x.com.br"})
    stats = asyncio.run(_worker(store).run_cycle())
    assert stats["eligible"] == 2 and stats["invalid"] == 1 and stats["sent"] == 1
    assert store.alerted == [2]  # só o válido recebeu


# --- e-mail sujo / batch resiliente (fix) ---------------------------------- #

def test_validate_batch_cleans_dirty_email():
    # %20contato@... é limpo, consertado no banco e mantido no batch
    store = FakeStore()
    w = _worker(store)
    clean = asyncio.run(w._validate_batch([_target(1, email="%20contato@envioz.com.br")]))
    assert len(clean) == 1
    assert clean[0]["contact_email"] == "contato@envioz.com.br"   # usado no batch
    assert store.email_updates == [(1, "contato@envioz.com.br")]  # self-heal no banco


def test_validate_batch_discards_invalid_email():
    store = FakeStore()
    w = _worker(store)
    clean = asyncio.run(w._validate_batch([_target(1, email="isto nao eh email"),
                                           _target(2, email="ok@y.com.br")]))
    assert [t["id"] for t in clean] == [2]
    assert (1, "descartado") in store.discarded


def test_send_with_split_isolates_bad_email():
    store = FakeStore()
    w = _worker(store)
    mailer = FakeMailer(bad={"bad@x.com.br"})
    alerts = [{"to_email": f"ok{i}@x.com.br", "target_id": i} for i in range(3)]
    alerts.append({"to_email": "bad@x.com.br", "target_id": 99})
    sent_pairs, bad = asyncio.run(w._send_with_split(mailer, alerts))
    assert len(sent_pairs) == 3 and [a["target_id"] for a, _ in sent_pairs] == [0, 1, 2]
    assert [a["target_id"] for a in bad] == [99]


def test_send_with_split_reraises_infra_error():
    store = FakeStore()
    w = _worker(store)
    mailer = FakeMailer(fail=True)  # RuntimeError, não 422
    try:
        asyncio.run(w._send_with_split(mailer, [{"to_email": "a@x.com.br", "target_id": 1}]))
        assert False, "esperava exceção de infra propagada"
    except RuntimeError:
        pass


def test_run_cycle_isolates_bad_email_in_batch():
    # 2 bons + 1 ruim no mesmo batch: os 2 bons enviam, o ruim é descartado
    store = FakeStore(eligible=[_target(1, email="ok1@x.com.br"),
                                _target(2, email="ok2@x.com.br"),
                                _target(3, email="bad@x.com.br")])
    w = _worker(store)
    w._mailer = lambda: FakeMailer(bad={"bad@x.com.br"})  # noqa: E731
    stats = asyncio.run(w.run_cycle())
    assert stats["sent"] == 2 and stats["failed"] == 1
    assert sorted(store.alerted) == [1, 2]
    assert (3, "descartado") in store.discarded
