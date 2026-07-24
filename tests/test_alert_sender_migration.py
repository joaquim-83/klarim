"""Migração do remetente dos alertas para klarimscan.com (isolamento de reputação).

Cobre: `_proactive_from` (env + fallback), remetente PROATIVO nos alertas/perfil vs
TRANSACIONAL nos códigos/vigília, `_domain_of_from`, `from_domain` no log, o limite
diário de warmup no alert worker, e o parâmetro editável ALERT_DAILY_LIMIT. Offline."""

from __future__ import annotations

import pytest

import api.main as m
import notifier.email_client as ec
from notifier.email_client import KlarimMailer

_NORMAL = "Klarim <seguranca@klarim.net>"


def _mailer():
    return KlarimMailer("re_test", _NORMAL, store=None)


def _capture(mailer):
    cap = {}

    async def fake_send(params, **kw):
        cap["params"] = params
        cap["kw"] = kw
        return {"email_id": "e1"}

    mailer._send = fake_send
    return cap


# --- _domain_of_from -------------------------------------------------------- #

def test_domain_of_from():
    assert ec._domain_of_from("Klarim Scanner <alerta@klarimscan.com>") == "klarimscan.com"
    assert ec._domain_of_from("seguranca@klarim.net") == "klarim.net"
    assert ec._domain_of_from("") == ""
    assert ec._domain_of_from(None) == ""


# --- _proactive_from -------------------------------------------------------- #

def test_proactive_from_uses_env(monkeypatch):
    monkeypatch.setenv("ALERT_FROM_EMAIL", "alerta@klarimscan.com")
    monkeypatch.setenv("ALERT_FROM_NAME", "Klarim Scanner")
    assert _mailer()._proactive_from() == "Klarim Scanner <alerta@klarimscan.com>"


def test_proactive_from_fallback(monkeypatch):
    monkeypatch.delenv("ALERT_FROM_EMAIL", raising=False)
    assert _mailer()._proactive_from() == _NORMAL  # cai para o remetente normal


# --- remetente por tipo ----------------------------------------------------- #

def test_alert_uses_proactive_sender(monkeypatch):
    monkeypatch.setenv("ALERT_FROM_EMAIL", "alerta@klarimscan.com")
    monkeypatch.setenv("ALERT_FROM_NAME", "Klarim Scanner")
    params = _mailer()._alert_params("dono@x.com", "https://x.com.br", 50, "amarelo", 3, {})
    assert params["from"] == "Klarim Scanner <alerta@klarimscan.com>"


def test_alert_fallback_when_unset(monkeypatch):
    monkeypatch.delenv("ALERT_FROM_EMAIL", raising=False)
    params = _mailer()._alert_params("dono@x.com", "https://x.com.br", 50, "amarelo", 3, {})
    assert params["from"] == _NORMAL


@pytest.mark.asyncio
async def test_profile_view_uses_dedicated_perfil_sender(monkeypatch):
    # KL-101: profile_view saiu do _proactive_from (klarim.net) → subdomínio perfil.klarim.net.
    monkeypatch.setenv("ALERT_FROM_EMAIL", "alerta@klarimscan.com")   # ignorado no profile_view
    monkeypatch.delenv("PROFILE_VIEW_FROM_EMAIL", raising=False)
    monkeypatch.delenv("PROFILE_VIEW_FROM_NAME", raising=False)
    mailer = _mailer()
    cap = _capture(mailer)
    await mailer.send_profile_view("dono@x.com", "x.com.br", 70, "amarelo", "https://k/cta")
    assert cap["params"]["from"] == "Klarim <notifica@perfil.klarim.net>"
    assert "http" not in cap["params"]["text"].lower()   # sem links (KL-101)


def test_profile_view_from_env_override(monkeypatch):
    monkeypatch.setenv("PROFILE_VIEW_FROM_EMAIL", "avisos@perfil.klarim.net")
    monkeypatch.setenv("PROFILE_VIEW_FROM_NAME", "Klarim Avisos")
    assert _mailer()._profile_view_from() == "Klarim Avisos <avisos@perfil.klarim.net>"


@pytest.mark.asyncio
async def test_verification_code_stays_transactional(monkeypatch):
    # Mesmo com o domínio de warmup configurado, o transacional sai do domínio normal.
    monkeypatch.setenv("ALERT_FROM_EMAIL", "alerta@klarimscan.com")
    mailer = _mailer()
    cap = _capture(mailer)
    await mailer.send_verification_code("a@b.com", "123456", "x.com.br")
    assert cap["params"]["from"] == _NORMAL


@pytest.mark.asyncio
async def test_signup_verification_stays_transactional(monkeypatch):
    monkeypatch.setenv("ALERT_FROM_EMAIL", "alerta@klarimscan.com")
    mailer = _mailer()
    cap = _capture(mailer)
    await mailer.send_signup_verification_code("a@b.com", "123456")
    assert cap["params"]["from"] == _NORMAL


@pytest.mark.asyncio
async def test_vigilia_stays_transactional(monkeypatch):
    monkeypatch.setenv("ALERT_FROM_EMAIL", "alerta@klarimscan.com")
    mailer = _mailer()
    cap = _capture(mailer)
    await mailer.send_vigilia_alert(to_email="dono@x.com", tipo="ssl", domain="x.com.br",
                                    subject="s", title="t", message="msg", data={"days_left": 7})
    assert cap["params"]["from"] == _NORMAL


# --- from_domain no log ----------------------------------------------------- #

@pytest.mark.asyncio
async def test_from_domain_logged(monkeypatch):
    mailer = _mailer()
    logged = {}

    async def fake_log(**kw):
        logged.update(kw)

    async def fake_blocked(_e):
        return False

    mailer._log_email = fake_log
    mailer._is_blocked = fake_blocked
    monkeypatch.setattr(mailer, "_send_sync", lambda params: {"email_id": "e1"})
    await mailer._send({"from": "Klarim Scanner <alerta@klarimscan.com>", "to": ["a@b.com"],
                        "subject": "s", "html": "<p>x</p>"}, email_type="alert")
    assert logged.get("from_domain") == "klarimscan.com" and logged.get("status") == "sent"


# --- limite diário no alert worker ----------------------------------------- #

async def _noop(*a, **k):
    return None


async def _true(*a, **k):
    return True


def _prep_worker(monkeypatch, sent_today, daily_limit="30", sent_month=100,
                 capture_limit=None):
    from discovery import alert_worker as aw
    w = aw.AlertWorker.__new__(aw.AlertWorker)
    w.batch_size = 50
    w.batches_per_cycle = 4
    w.interval_minutes = 30
    w.monthly_limit = 45000
    # KL-91 — atributos do envio cold (rotação/cooldown/breaker); cooldown 0 nos testes.
    w.sender_daily_limit = 1000
    w.send_interval_min = 0
    w.send_interval_max = 0
    w.sender_max_bounce_rate = 100.0
    w.bounce_min_sample = 20
    w.sender_bounce_min_sample = 100   # fix 24/07: amostra própria do circuit breaker

    class S:
        async def get_setting(self, k, d=None):
            return daily_limit if k == "ALERT_DAILY_LIMIT" else d

        async def email_health_by_domain(self, days=None):  # fix 24/07 (janela 7d)
            return {}

        async def count_proactive_emails_this_month(self):
            return sent_month

        async def count_alerts_sent_today(self):
            return sent_today

        async def get_eligible_targets_for_alert(self, limit):
            if capture_limit is not None:
                capture_limit["limit"] = limit
            return []

        async def count_eligible_targets_for_alert(self):
            return 0

    w.store = S()
    monkeypatch.setattr(w, "_reload_settings", _noop)
    monkeypatch.setattr(w, "_mailer", lambda: object())
    monkeypatch.setattr(w, "_check_bounce_health", _true)
    monkeypatch.setattr(w, "_validate_batch", lambda rows: _empty())
    monkeypatch.setattr(aw.worker_control, "is_enabled", lambda x: True)
    monkeypatch.setattr(aw, "alerts_stopped", lambda: False)
    return w


async def _empty():
    return []


@pytest.mark.asyncio
async def test_daily_limit_skips_cycle(monkeypatch):
    w = _prep_worker(monkeypatch, sent_today=30, daily_limit="30")
    stats = await w.run_cycle()
    assert stats.get("daily_limit_reached") is True and stats["sent"] == 0


@pytest.mark.asyncio
async def test_fetch_decoupled_from_daily_limit(monkeypatch):
    # Fix 2026-07-23: o FETCH não é mais limitado pelo diário (era o que causava o livelock —
    # buscar só `diário_rem` alvos de baixa qualidade da frente e mandar 0). Agora busca
    # ALERT_FETCH_CAP candidatos (200) e o diário limita só os ENVIOS.
    cap = {}
    w = _prep_worker(monkeypatch, sent_today=25, daily_limit="30", capture_limit=cap)
    await w.run_cycle()
    assert cap["limit"] == 200   # fetch_cap, independente do diário_rem=5


# --- config editável -------------------------------------------------------- #

def test_alert_daily_limit_is_editable():
    assert "ALERT_DAILY_LIMIT" in m._CONFIG_PARAMS
    meta = m._CONFIG_PARAMS["ALERT_DAILY_LIMIT"]
    assert meta["min"] == 0 and meta["max"] == 50000 and meta["default"] == "5000"
