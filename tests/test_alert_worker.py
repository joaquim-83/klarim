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
    """KL-91 — o worker envia individualmente via send_cold_alert (rotação). `fail`
    simula erro de infra; `bad` são e-mails que o Resend rejeita (422)."""
    def __init__(self, fail=False, bad=None):
        self.sent = []            # {to, from, variant, subject}
        self.fail = fail
        self.bad = set(bad or [])  # e-mails que o Resend rejeita (422)

    async def send_cold_alert(self, *, to_email, from_address, subject, text,
                              template_variant=None, target_id=None, domain=None, **kw):
        if self.fail:
            raise RuntimeError("boom")  # infra/inesperado (não KlarimMailerError)
        if to_email in self.bad:
            from notifier import KlarimMailerError
            raise KlarimMailerError("Falha no envio Resend (422): Invalid 'to' field")
        self.sent.append({"to": to_email, "from": from_address, "variant": template_variant,
                          "subject": subject, "text": text, "target_id": target_id})
        return {"email_id": f"single_{len(self.sent)}"}


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

    async def count_alerts_sent_today_by_domain(self):  # KL-91
        return dict(getattr(self, "_sent_by_domain", {}))

    async def email_health(self):
        return dict(self._health)

    async def email_health_by_domain(self):  # KL-91
        return dict(getattr(self, "_health_by_domain", {}))

    async def sector_benchmark(self, sector, min_count=10):  # KL-91 (variante 2)
        return getattr(self, "_benchmark", None)

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

    async def update_target_alert_score(self, target_id, score):  # KL-85
        self.alert_scores = getattr(self, "alert_scores", {})
        self.alert_scores[target_id] = score

    async def domain_has_bounce(self, domain):  # KL-85
        return False

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
    w.validate_mx = False  # sem DNS nos testes de fluxo
    w.alert_score_threshold = -100000  # KL-85: não filtrar nos testes de fluxo
    w.max_bounce_rate = 8.0
    w.bounce_min_sample = 20
    # KL-91: limite por remetente alto (não binda), cooldown 0 (sem espera), breaker off.
    w.sender_daily_limit = 100000
    w.send_interval_min = 0
    w.send_interval_max = 0
    w.sender_max_bounce_rate = 100.0
    w._mailer = lambda: FakeMailer()  # noqa: E731
    return w


def test_run_cycle_sends_all_individually():
    store = FakeStore(eligible=[_target(1), _target(2)])
    stats = asyncio.run(_worker(store).run_cycle())
    assert stats["eligible"] == 2 and stats["sent"] == 2
    assert store.alerted == [1, 2]
    assert all(l["status"] == "sent" for l in store.logged)


def test_run_cycle_rotates_between_senders(monkeypatch):
    # KL-91: 4 alvos → round-robin alterna os 2 remetentes cold (2 e 2).
    monkeypatch.delenv("ALERT_SENDER_EMAILS", raising=False)
    store = FakeStore(eligible=[_target(i) for i in range(1, 5)])
    sent = []
    w = _worker(store)
    fm = FakeMailer()
    w._mailer = lambda: fm  # noqa: E731
    stats = asyncio.run(w.run_cycle())
    froms = [e["from"] for e in fm.sent]
    assert stats["sent"] == 4
    assert sum("alertas.klarim.net" in f for f in froms) == 2
    assert sum("aviso.klarim.net" in f for f in froms) == 2


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


def test_run_cycle_sends_up_to_cycle_cap():
    # cycle_cap = batch_size(50) * batches_per_cycle(4) = 200; 120 elegíveis < 200 → todos
    store = FakeStore(eligible=[_target(i) for i in range(1, 121)])
    stats = asyncio.run(_worker(store).run_cycle())
    assert stats["sent"] == 120
    assert len(store.alerted) == 120


def test_run_cycle_respects_sender_daily_limit():
    # 2 remetentes × limite 3 = 6 envios máx no dia; 10 elegíveis → só 6 saem.
    store = FakeStore(eligible=[_target(i) for i in range(1, 11)])
    w = _worker(store)
    w.sender_daily_limit = 3
    stats = asyncio.run(w.run_cycle())
    assert stats["sent"] == 6 and len(store.alerted) == 6


def test_run_cycle_skips_when_senders_exhausted():
    # ambos os remetentes já bateram o limite hoje → ciclo pulado.
    store = FakeStore(eligible=[_target(1)])
    store._sent_by_domain = {"alertas.klarim.net": 3, "aviso.klarim.net": 3}
    w = _worker(store)
    w.sender_daily_limit = 3
    stats = asyncio.run(w.run_cycle())
    assert stats.get("sender_limit_reached") is True and stats["sent"] == 0
    assert store.alerted == []


def test_run_cycle_pauses_high_bounce_sender():
    # alertas.klarim.net com 12% de bounce (amostra 100) → pausado; aviso continua.
    store = FakeStore(eligible=[_target(i) for i in range(1, 5)])
    store._health_by_domain = {
        "alertas.klarim.net": {"total": 100, "bounced": 12, "complained": 0},
        "aviso.klarim.net": {"total": 100, "bounced": 1, "complained": 0}}
    w = _worker(store)
    w.sender_max_bounce_rate = 5.0
    fm = FakeMailer()
    w._mailer = lambda: fm  # noqa: E731
    stats = asyncio.run(w.run_cycle())
    assert stats["senders_paused"] == ["alertas.klarim.net"]
    assert all("aviso.klarim.net" in e["from"] for e in fm.sent)  # só o saudável envia


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


def test_run_cycle_infra_error_aborts_and_logs_failed():
    store = FakeStore(eligible=[_target(1), _target(2)])
    w = _worker(store)
    w._mailer = lambda: FakeMailer(fail=True)  # RuntimeError (infra) já no 1º envio
    stats = asyncio.run(w.run_cycle())
    assert stats["sent"] == 0 and stats["errors"] == 1
    assert store.alerted == []  # falha não marca alerted
    assert store.logged and all(l["status"] == "failed" for l in store.logged)


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


def test_run_cycle_sends_best_leads_first(monkeypatch):
    # Fix livelock 2026-07-23: busca TODOS os candidatos, filtra pelo threshold e envia os de
    # MAIOR score primeiro. Antes, os de baixa qualidade da frente entupiam e mandava 0.
    import discovery.alert_worker as aw
    scores = {1: 5, 2: 25, 3: 50, 4: 10, 5: 40}   # threshold 20 mantém 2, 3, 5
    monkeypatch.setattr(aw, "calculate_alert_score",
                        lambda t, e, b: {"score": scores[t["id"]], "signals": []})
    store = FakeStore(eligible=[_target(i) for i in range(1, 6)])
    w = _worker(store)
    w.alert_score_threshold = 20
    fm = FakeMailer()
    w._mailer = lambda: fm  # noqa: E731
    stats = asyncio.run(w.run_cycle())
    assert stats["sent"] == 3 and stats["skipped_low_quality"] == 2
    # ordenados por score DESC: t3(50) → t5(40) → t2(25)
    assert [e["target_id"] for e in fm.sent] == [3, 5, 2]


def test_apply_scoring_logs_skip_with_reason(monkeypatch, capsys):
    # Fix 2026-07-23: log PERMANENTE do porquê do skip (score + sinais + e-mail mascarado).
    import discovery.alert_worker as aw
    monkeypatch.setattr(aw, "calculate_alert_score", lambda t, e, b: {
        "score": 5, "signals": [{"signal": "corporate_email", "points": 10},
                                 {"signal": "role_based_prefix", "points": -15}]})
    w = _worker(FakeStore())
    w.alert_score_threshold = 20
    kept, skipped, _ = asyncio.run(
        w._apply_alert_scoring([_target(1, email="contato@x.com.br")]))
    out = capsys.readouterr().out
    assert skipped == 1 and kept == []
    assert "skip lead t=1" in out and "role_based_prefix=-15" in out
    assert "c***o@x.com.br" in out          # e-mail mascarado (privacidade)


def test_run_cycle_isolates_bad_email():
    # 2 bons + 1 ruim (422): os 2 bons enviam, o ruim é descartado sem abortar o ciclo
    store = FakeStore(eligible=[_target(1, email="ok1@x.com.br"),
                                _target(2, email="ok2@x.com.br"),
                                _target(3, email="bad@x.com.br")])
    w = _worker(store)
    w._mailer = lambda: FakeMailer(bad={"bad@x.com.br"})  # noqa: E731
    stats = asyncio.run(w.run_cycle())
    assert stats["sent"] == 2 and stats["failed"] == 1
    assert sorted(store.alerted) == [1, 2]
    assert (3, "descartado") in store.discarded
