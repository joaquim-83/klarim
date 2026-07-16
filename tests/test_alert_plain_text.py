"""KL-44 — alertas e notificação de perfil em TEXTO PURO (plain text).

O template HTML (alert.html / alert_score100.html / profile_view.html) foi mantido
como referência, mas os e-mails PROATIVOS (cold) saem em plain text (campo `text`,
sem `html`) — menos cara de e-mail marketing, cai menos no spam. O CTA aponta para o
perfil público `/site/{domain}` com UTM. Offline (mock do Resend)."""

from __future__ import annotations

import asyncio

from notifier.email_client import (
    KlarimMailer, alert_subject, build_alert_text, build_profile_view_text,
)


UNSUB = "https://klarim.net/api/unsubscribe?email=d%40e.com&token=abc"


# --- builders puros --------------------------------------------------------- #

def test_alert_text_normal():
    t = build_alert_text("movenegocios.com.br", 72, UNSUB)
    assert t.startswith("Olá,")
    assert "O site movenegocios.com.br foi verificado" in t
    assert "nota 72/100" in t
    assert ("https://klarim.net/site/movenegocios.com.br"
            "?utm_source=klarim&utm_medium=email&utm_campaign=alerta") in t
    assert "48 pontos" in t
    assert "klarimscan.com" in t
    assert UNSUB in t                        # unsubscribe presente
    assert "R$" not in t and "<" not in t    # sem preço, sem tag HTML


def test_alert_text_score100_differs_from_normal():
    normal = build_alert_text("x.com.br", 80, UNSUB, is_score100=False)
    top = build_alert_text("x.com.br", 100, UNSUB, is_score100=True)
    assert top != normal
    assert "Parabéns!" in top and "nota 100/100" in top
    assert "utm_campaign=alerta_score100" in top
    assert "menos de 2%" in top
    assert "R$" not in top and "<" not in top


def test_alert_subject_normal_and_score100():
    assert alert_subject("x.com.br") == "Alguém verificou a segurança do site x.com.br"
    assert (alert_subject("x.com.br", is_score100=True)
            == "Parabéns! O site x.com.br alcançou nota máxima em segurança")


def test_unsub_line_omitted_when_absent():
    # Sem link de descadastro, a linha some (não renderiza 'None').
    assert "Não quer receber" not in build_alert_text("x.com.br", 50, None)
    assert "Não quer receber mais avisos?" in build_alert_text("x.com.br", 50, UNSUB)


def test_profile_view_text():
    t = build_profile_view_text("hotelparaiso.com.br", 65, UNSUB)
    assert "Alguém consultou o perfil de segurança do site hotelparaiso.com.br" in t
    assert "A nota atual é 65/100" in t
    assert "utm_campaign=profile_view" in t
    assert "100% passiva" in t
    assert UNSUB in t
    assert "<" not in t


# --- integração: o payload chega ao Resend como `text` (sem html) ----------- #

def _capture_send(monkeypatch):
    import resend
    captured: dict = {}

    class FakeEmails:
        @staticmethod
        def send(params):
            captured.update(params)
            return {"id": "em_1"}

    monkeypatch.setattr(resend, "Emails", FakeEmails)
    return captured


def test_send_alert_is_plain_text(monkeypatch):
    monkeypatch.setenv("UNSUBSCRIBE_SECRET", "s" * 32)
    captured = _capture_send(monkeypatch)
    m = KlarimMailer("re_fake")
    asyncio.run(m.send_alert("d@e.com", "https://www.movenegocios.com.br", 72,
                             "amarelo", 2, {}))
    assert "html" not in captured and "text" in captured
    assert captured["subject"] == "Alguém verificou a segurança do site movenegocios.com.br"
    assert "/site/movenegocios.com.br" in captured["text"]
    assert "utm_campaign=alerta" in captured["text"]
    assert "unsubscribe" in captured["text"]   # UNSUBSCRIBE_SECRET set → link no corpo


def test_send_alert_score100_is_plain_text(monkeypatch):
    captured = _capture_send(monkeypatch)
    m = KlarimMailer("re_fake")
    asyncio.run(m.send_alert("d@e.com", "https://empresa.com.br", 100, "verde", 0, {}))
    assert "html" not in captured and "text" in captured
    assert captured["subject"] == "Parabéns! O site empresa.com.br alcançou nota máxima em segurança"
    assert "nota 100/100" in captured["text"]
    assert "utm_campaign=alerta_score100" in captured["text"]


def test_send_profile_view_is_plain_text(monkeypatch):
    monkeypatch.setenv("UNSUBSCRIBE_SECRET", "s" * 32)
    captured = _capture_send(monkeypatch)
    m = KlarimMailer("re_fake")
    asyncio.run(m.send_profile_view("d@e.com", "hotelparaiso.com.br", 65, "amarelo",
                                    "https://klarim.net/site/hotelparaiso.com.br"))
    assert "html" not in captured and "text" in captured
    assert captured["subject"] == "Alguém consultou a segurança do site hotelparaiso.com.br"
    assert "/site/hotelparaiso.com.br" in captured["text"]
    assert "utm_campaign=profile_view" in captured["text"]
