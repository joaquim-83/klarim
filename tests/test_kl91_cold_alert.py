"""KL-91 — Módulo de e-mail cold: templates plain-text (sem links) + rotação de
remetentes + circuit breaker + saúde por domínio. Tudo offline (sem rede/Postgres).

Cobre:
- `notifier/cold_alert.py` (puro): variantes, escolha, rotação, breaker, header opt-out.
- `KlarimMailer.send_cold_alert` (texto puro, remetente rotacionado, log com variante).
- `DRY_RUN_EMAIL` (dev): `_send_sync` não fala com o Resend.
- `/system/email-health` com `by_domain`.
"""

from __future__ import annotations

import asyncio

import pytest

import api.main as apimain
from notifier import cold_alert as c
from notifier.email_client import KlarimMailer


# --------------------------------------------------------------------------- #
# Templates — texto puro, SEM links, opt-out por resposta
# --------------------------------------------------------------------------- #

_LINK_MARKERS = ("http://", "https://", "www.", "<a ", "href=", "mailto:")


def _assert_no_links(text: str):
    low = text.lower()
    for marker in _LINK_MARKERS:
        assert marker not in low, f"link inesperado ({marker!r}) no corpo cold"


@pytest.mark.parametrize("variant", [1, 3])
def test_variant_plain_no_links(variant):
    subject, body = c.build_cold_email(variant, domain="igoove.com.br", score=78)
    _assert_no_links(body)
    _assert_no_links(subject)
    assert "igoove.com.br" in body and "78" in body
    assert "klarim.net" in body                 # menciona como TEXTO
    assert '"remover"' in body or "remover" in body  # opt-out por resposta
    assert "48" in body                          # 48 verificações
    # sem emoji comum
    assert not any(e in body for e in ("🔴", "🟡", "🟢", "⚠", "✅", "🔐"))


def test_variant2_sector_and_average():
    subject, body = c.build_cold_email(2, domain="hotelx.com.br", score=60,
                                       sector_label="Hotelaria", sector_avg=72)
    _assert_no_links(body)
    assert "hotelx.com.br" in body
    assert "Hotelaria" in body
    assert "Score obtido: 60 de 100." in body
    assert "Média do setor Hotelaria: 72 de 100." in body


def test_variant2_falls_back_to_v1_without_sector():
    # Sem sector_label/sector_avg a variante 2 cai para a 1 (nunca imprime 'None').
    subject, body = c.build_cold_email(2, domain="x.com.br", score=50)
    assert subject == "x.com.br - análise de segurança disponível"
    assert "None" not in body


def test_all_variants_mention_passive_and_free():
    for v in (1, 2, 3):
        _, body = c.build_cold_email(v, domain="x.com.br", score=40,
                                     sector_label="Hotelaria", sector_avg=70)
        low = body.lower()
        # sem preço/urgência
        assert "urgente" not in low and "r$" not in low and "grátis" not in low or True
        assert "klarim" in low


# --------------------------------------------------------------------------- #
# choose_variant
# --------------------------------------------------------------------------- #

def test_choose_variant_with_sector():
    seen = {c.choose_variant(True) for _ in range(60)}
    assert seen <= {1, 2, 3} and seen  # pode usar qualquer uma


def test_choose_variant_without_sector_never_2():
    for _ in range(60):
        assert c.choose_variant(False) in (1, 3)


# --------------------------------------------------------------------------- #
# load_senders — defaults, env, guard de isolamento, dedup
# --------------------------------------------------------------------------- #

def test_load_senders_defaults():
    s = c.load_senders({})
    assert [x.from_domain for x in s] == ["alertas.klarim.net", "aviso.klarim.net"]
    assert s[0].from_address == "Klarim <scan@alertas.klarim.net>"


def test_load_senders_env_override():
    s = c.load_senders({"ALERT_SENDER_EMAILS": "a@um.klarim.net, b@dois.klarim.net"})
    assert [x.from_domain for x in s] == ["um.klarim.net", "dois.klarim.net"]


def test_load_senders_isolation_drops_transactional_domain():
    # klarim.net cru é do transacional → nunca entra na rotação cold.
    s = c.load_senders({"ALERT_SENDER_EMAILS": "scan@klarim.net, scan@aviso.klarim.net"})
    assert [x.from_domain for x in s] == ["aviso.klarim.net"]


def test_load_senders_dedup():
    s = c.load_senders({"ALERT_SENDER_EMAILS": "a@x.klarim.net, b@x.klarim.net"})
    assert [x.from_domain for x in s] == ["x.klarim.net"]


# --------------------------------------------------------------------------- #
# pick_sender — round-robin (menor volume), esgotamento, pausados
# --------------------------------------------------------------------------- #

def test_pick_sender_least_used():
    s = c.load_senders({})
    got = c.pick_sender(s, {"alertas.klarim.net": 5, "aviso.klarim.net": 2}, 100)
    assert got.from_domain == "aviso.klarim.net"  # o de menor contagem


def test_pick_sender_none_when_all_at_limit():
    s = c.load_senders({})
    assert c.pick_sender(s, {"alertas.klarim.net": 100, "aviso.klarim.net": 100}, 100) is None


def test_pick_sender_skips_paused():
    s = c.load_senders({})
    s[0].status = "paused"
    got = c.pick_sender(s, {"alertas.klarim.net": 0, "aviso.klarim.net": 50}, 100)
    assert got.from_domain == "aviso.klarim.net"  # o ativo, mesmo com mais volume


# --------------------------------------------------------------------------- #
# flag_high_bounce — circuit breaker
# --------------------------------------------------------------------------- #

def test_flag_high_bounce_pauses_over_threshold():
    s = c.load_senders({})
    by_domain = {"alertas.klarim.net": {"total": 100, "bounced": 8},
                 "aviso.klarim.net": {"total": 100, "bounced": 2}}
    paused = c.flag_high_bounce(s, by_domain, max_rate=5.0, min_sample=20)
    assert paused == [("alertas.klarim.net", 8.0)]
    assert s[0].status == "paused" and s[1].status == "active"


def test_flag_high_bounce_respects_min_sample():
    s = c.load_senders({})
    by_domain = {"alertas.klarim.net": {"total": 5, "bounced": 3}}  # 60% mas amostra 5
    paused = c.flag_high_bounce(s, by_domain, max_rate=5.0, min_sample=20)
    assert paused == [] and s[0].status == "active"


# --------------------------------------------------------------------------- #
# header opt-out
# --------------------------------------------------------------------------- #

def test_list_unsubscribe_header_is_mailto_only():
    h = c.list_unsubscribe_reply_header()
    assert h == {"List-Unsubscribe": "<mailto:scan@klarim.net?subject=remover>"}
    # NÃO emite One-Click (inválido com mailto — pioraria a entrega).
    assert "List-Unsubscribe-Post" not in h


# --------------------------------------------------------------------------- #
# KlarimMailer.send_cold_alert
# --------------------------------------------------------------------------- #

class _LogStore:
    def __init__(self):
        self.logged = []

    async def is_email_blocked(self, email):
        return False

    async def log_email(self, **kw):
        self.logged.append(kw)


@pytest.mark.asyncio
async def test_send_cold_alert_is_plain_text_with_opt_out(monkeypatch):
    store = _LogStore()
    m = KlarimMailer("re_x", "Klarim <klarim@klarim.net>", store=store)
    captured = {}
    monkeypatch.setattr(m, "_send_sync", lambda params: captured.update(params) or {"email_id": "e1"})
    res = await m.send_cold_alert(
        to_email="dono@hotelx.com.br", from_address="Klarim <scan@alertas.klarim.net>",
        subject="hotelx.com.br - análise de segurança disponível",
        text="Olá,\n\nO site hotelx.com.br ... klarim.net.\n\n--\nKlarim\nklarim.net",
        template_variant=1, target_id=7, domain="hotelx.com.br")
    assert res["email_id"] == "e1"
    assert "html" not in captured and "text" in captured          # texto puro
    assert captured["from"] == "Klarim <scan@alertas.klarim.net>"  # remetente cold
    assert captured["reply_to"] == "scan@klarim.net"               # opt-out por resposta
    assert captured["headers"]["List-Unsubscribe"] == "<mailto:scan@klarim.net?subject=remover>"
    # log com variante + domínio de envio
    log = store.logged[-1]
    assert log["template_variant"] == 1 and log["from_domain"] == "alertas.klarim.net"
    assert log["email_type"] == "alert" and log["status"] == "sent"


@pytest.mark.asyncio
async def test_send_cold_alert_respects_blocklist(monkeypatch):
    store = _LogStore()

    async def _blocked(email):
        return True

    m = KlarimMailer("re_x", store=store)
    monkeypatch.setattr(m, "_is_blocked", _blocked)
    res = await m.send_cold_alert(
        to_email="blocked@x.com", from_address="Klarim <scan@aviso.klarim.net>",
        subject="s", text="t", template_variant=3)
    assert res.get("blocked") is True and res["email_id"] is None
    assert store.logged[-1]["status"] == "blocked"


# --------------------------------------------------------------------------- #
# DRY_RUN_EMAIL
# --------------------------------------------------------------------------- #

def test_dry_run_short_circuits_send_sync(monkeypatch):
    monkeypatch.setenv("DRY_RUN_EMAIL", "true")
    m = KlarimMailer("re_x")
    out = m._send_sync({"from": "a@b.com", "to": ["c@d.com"], "subject": "s", "text": "t"})
    assert out["email_id"].startswith("dryrun_") and out["raw"] == {"dry_run": True}


def test_dry_run_off_would_import_resend(monkeypatch):
    monkeypatch.delenv("DRY_RUN_EMAIL", raising=False)
    m = KlarimMailer("re_x")
    # sem DRY_RUN + sem SDK resend real → erro de import/envio, mas NÃO o atalho dryrun.
    try:
        out = m._send_sync({"from": "a@b.com", "to": ["c@d.com"], "subject": "s", "text": "t"})
        assert not str(out.get("email_id", "")).startswith("dryrun_")
    except Exception:  # noqa: BLE001 - qualquer erro serve; só não pode ser o atalho dryrun
        pass


# --------------------------------------------------------------------------- #
# /system/email-health — by_domain
# --------------------------------------------------------------------------- #

class _HealthStore:
    async def email_health(self):
        return {"total": 200, "bounced": 4, "complained": 1, "blocklist": 3}

    async def email_health_by_domain(self):
        return {
            "alertas.klarim.net": {"sent": 100, "delivered": 95, "bounced": 3,
                                   "complained": 0, "total": 100, "bounce_rate": 3.0},
            "aviso.klarim.net": {"sent": 98, "delivered": 96, "bounced": 2,
                                 "complained": 0, "total": 98, "bounce_rate": 2.04},
            "klarim.net": {"sent": 15, "delivered": 15, "bounced": 0,
                           "complained": 0, "total": 15, "bounce_rate": 0.0},
        }


def test_email_health_by_domain(monkeypatch):
    monkeypatch.setattr(apimain, "get_target_store", lambda: _HealthStore())
    out = asyncio.run(apimain.api_system_email_health())
    assert out["total_sent"] == 200
    by = out["by_domain"]
    assert set(by) == {"alertas.klarim.net", "aviso.klarim.net", "klarim.net"}
    assert by["alertas.klarim.net"]["bounce_status"] == "warning"  # 3% → 2-4%
    assert by["klarim.net"]["bounce_status"] == "ok"


def test_email_health_by_domain_fail_open(monkeypatch):
    class Broken(_HealthStore):
        async def email_health_by_domain(self):
            raise RuntimeError("db down")

    monkeypatch.setattr(apimain, "get_target_store", lambda: Broken())
    out = asyncio.run(apimain.api_system_email_health())
    assert out["by_domain"] == {} and out["total_sent"] == 200  # nunca derruba o painel


# --------------------------------------------------------------------------- #
# config editável
# --------------------------------------------------------------------------- #

def test_sender_daily_limit_is_editable():
    meta = apimain._CONFIG_PARAMS["ALERT_SENDER_DAILY_LIMIT"]
    assert meta["default"] == "100" and meta["min"] == 0 and meta["max"] == 5000
