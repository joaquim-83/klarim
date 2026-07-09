"""Testes do módulo notifier (e-mail) — offline, com Resend mockado."""

from __future__ import annotations

import asyncio
import base64

from notifier import KlarimMailer, semaphore_from_score, site_name, batch_idempotency_key
from notifier.email_client import _env, RESEND_BATCH_URL


def test_semaphore_from_score():
    assert semaphore_from_score(92) == "verde"
    assert semaphore_from_score(86) == "amarelo"  # calibração KL-12 (verde >= 90)
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


# --- batch sending (KL-23) ------------------------------------------------- #

def test_batch_idempotency_key_deterministic_and_order_independent():
    a = [{"to_email": "b@x.com"}, {"to_email": "a@x.com"}]
    b = [{"to_email": "a@x.com"}, {"to_email": "b@x.com"}]  # ordem trocada
    assert batch_idempotency_key(a) == batch_idempotency_key(b)  # mesma chave
    assert batch_idempotency_key(a).startswith("batch_")
    assert len(batch_idempotency_key(a)) == len("batch_") + 32
    # e-mails diferentes -> chave diferente
    assert batch_idempotency_key(a) != batch_idempotency_key([{"to_email": "c@x.com"}])


def _alert(i):
    return {"to_email": f"d{i}@e.com", "target_url": f"https://site{i}.com.br",
            "score": 60, "semaphore": "amarelo", "fail_count": 2,
            "severity_counts": {"critica": 0, "alta": 2, "media": 0, "baixa": 0},
            "risk_messages": [], "unsubscribe_link": None, "target_id": i}


def test_send_alert_batch_counts_and_ids(monkeypatch):
    captured = {}

    async def fake_raw(self, payloads, key):
        captured["payloads"] = payloads
        captured["key"] = key
        return {"data": [{"id": f"em_{i}"} for i in range(len(payloads))]}

    monkeypatch.setattr(KlarimMailer, "_send_batch_raw", fake_raw)
    m = KlarimMailer("re_fake")
    res = asyncio.run(m.send_alert_batch([_alert(1), _alert(2), _alert(3)]))
    assert res["sent"] == 3 and res["failed"] == 0
    assert res["ids"] == ["em_0", "em_1", "em_2"]
    # renderizou 1 payload por alerta, com subject e html
    assert len(captured["payloads"]) == 3
    assert "site1.com.br" in captured["payloads"][0]["subject"]
    assert "Ver detalhes" in captured["payloads"][0]["html"]
    assert captured["key"].startswith("batch_")


def test_send_alert_batch_empty():
    m = KlarimMailer("re_fake")
    assert asyncio.run(m.send_alert_batch([])) == {"sent": 0, "failed": 0, "ids": []}


def test_send_alert_batch_caps_at_100(monkeypatch):
    seen = {}

    async def fake_raw(self, payloads, key):
        seen["n"] = len(payloads)
        return {"data": [{"id": "x"} for _ in payloads]}

    monkeypatch.setattr(KlarimMailer, "_send_batch_raw", fake_raw)
    m = KlarimMailer("re_fake")
    res = asyncio.run(m.send_alert_batch([_alert(i) for i in range(150)]))
    assert seen["n"] == 100 and res["sent"] == 100  # trunca no máx da API


def test_send_evolution_batch_counts(monkeypatch):
    captured = {}

    async def fake_raw(self, payloads, key):
        captured["payloads"] = payloads
        return {"data": [{"id": "evo_1"}, {"id": "evo_2"}]}

    monkeypatch.setattr(KlarimMailer, "_send_batch_raw", fake_raw)
    m = KlarimMailer("re_fake")
    evos = [
        {"to_email": "a@e.com", "target_url": "https://a.com.br", "old_score": 80,
         "new_score": 92, "evolution": "improved", "semaphore": "verde", "fail_count": 1,
         "severity_counts": {"critica": 0, "alta": 1, "media": 0, "baixa": 0},
         "price_display": "R$ 29,00", "rescan_id": 5, "target_id": 3},
        {"to_email": "b@e.com", "target_url": "https://b.com.br", "old_score": 90,
         "new_score": 60, "evolution": "worsened", "semaphore": "amarelo", "fail_count": 3,
         "severity_counts": {"critica": 0, "alta": 3, "media": 0, "baixa": 0},
         "price_display": "R$ 29,00", "rescan_id": 6, "target_id": 4},
    ]
    res = asyncio.run(m.send_evolution_batch(evos))
    assert res["sent"] == 2 and res["ids"] == ["evo_1", "evo_2"]
    assert "melhorou" in captured["payloads"][0]["subject"]
    assert "caiu de 90 para 60" in captured["payloads"][1]["subject"]


def test_send_batch_raw_sends_idempotency_header(monkeypatch):
    """Valida endpoint + header Idempotency-Key via httpx falso."""
    import httpx

    seen = {}

    class FakeResp:
        status_code = 200

        def json(self):
            return {"data": [{"id": "ok"}]}

        text = ""

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, headers=None, timeout=None):
            seen["url"] = url
            seen["headers"] = headers
            seen["json"] = json
            return FakeResp()

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: FakeClient())
    m = KlarimMailer("re_key_123")
    body = asyncio.run(m._send_batch_raw([{"to": ["a@e.com"]}], "batch_abc"))
    assert body == {"data": [{"id": "ok"}]}
    assert seen["url"] == RESEND_BATCH_URL
    assert seen["headers"]["Idempotency-Key"] == "batch_abc"
    assert seen["headers"]["Authorization"] == "Bearer re_key_123"


def test_send_batch_raw_raises_on_4xx(monkeypatch):
    import httpx
    from notifier import KlarimMailerError

    class FakeResp:
        status_code = 422

        def json(self):
            return {"message": "invalid"}

        text = "invalid"

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            return FakeResp()

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: FakeClient())
    m = KlarimMailer("re_fake")
    try:
        asyncio.run(m._send_batch_raw([{"x": 1}], "batch_x"))
        assert False, "esperava KlarimMailerError"
    except KlarimMailerError as exc:
        assert "422" in str(exc)
