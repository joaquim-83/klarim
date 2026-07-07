"""Testes do módulo notifier (e-mail) — offline, com Resend mockado."""

from __future__ import annotations

import asyncio
import base64

from notifier import KlarimMailer, semaphore_from_score, site_name
from notifier.email_client import _env


def test_semaphore_from_score():
    assert semaphore_from_score(86) == "verde"
    assert semaphore_from_score(60) == "amarelo"
    assert semaphore_from_score(30) == "vermelho"


def test_site_name():
    assert site_name("https://www.verdegreen.com.br/x") == "verdegreen.com.br"
    assert site_name("https://klarim.net") == "klarim.net"


def test_templates_render():
    ctx = dict(
        score=86, semaphore="verde", semaphore_label="VERDE", semaphore_emoji="🟢",
        score_color="#00D26A", site_name="x.com", target_url="https://x.com",
        referral_link="https://klarim.net/parceiros",
    )
    alert = _env.get_template("alert.html").render(
        fail_count=2, sev={"critica": 0, "alta": 2, "media": 0, "baixa": 0},
        result_link="https://klarim.net/result?url=x", lgpd="LGPD…", **ctx,
    )
    assert "Ver detalhes" in alert and "VERDE" in alert
    report = _env.get_template("report_delivery.html").render(**ctx)
    assert "Executivo" in report and "Técnico" in report


def test_send_test_mocked(monkeypatch):
    import resend

    captured = {}

    class FakeEmails:
        @staticmethod
        def send(params):
            captured.update(params)
            return {"id": "email_abc"}

    monkeypatch.setattr(resend, "Emails", FakeEmails)
    m = KlarimMailer("re_fake", "Klarim <onboarding@resend.dev>")
    res = asyncio.run(m.send_test("dest@example.com"))
    assert res["email_id"] == "email_abc"
    assert captured["to"] == ["dest@example.com"]
    assert "Teste" in captured["subject"]
    assert captured["from"] == "Klarim <onboarding@resend.dev>"


def test_send_alert_mocked(monkeypatch):
    import resend

    captured = {}

    class FakeEmails:
        @staticmethod
        def send(params):
            captured.update(params)
            return {"id": "email_alert"}

    monkeypatch.setattr(resend, "Emails", FakeEmails)
    m = KlarimMailer("re_fake")
    res = asyncio.run(m.send_alert(
        "d@e.com", "https://www.verdegreen.com.br", 86, "verde", 2,
        {"critica": 0, "alta": 2, "media": 0, "baixa": 0}))
    assert res["email_id"] == "email_alert"
    assert "verdegreen.com.br" in captured["subject"]
    assert "attachments" not in captured  # alerta não tem anexo


def test_send_report_attachments_mocked(monkeypatch):
    import resend

    captured = {}

    class FakeEmails:
        @staticmethod
        def send(params):
            captured.update(params)
            return {"id": "email_report"}

    monkeypatch.setattr(resend, "Emails", FakeEmails)
    m = KlarimMailer("re_fake")
    res = asyncio.run(m.send_report("d@e.com", "https://x.com", 86, b"PDF-EXEC", b"PDF-TECH"))
    assert res["email_id"] == "email_report"
    atts = captured["attachments"]
    assert len(atts) == 2
    assert base64.b64decode(atts[0]["content"]) == b"PDF-EXEC"
    assert base64.b64decode(atts[1]["content"]) == b"PDF-TECH"
    assert atts[0]["filename"].endswith(".pdf")
