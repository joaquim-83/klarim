"""Template freemium do alert worker — alert.html / alert_score100.html.

Fix: o alert worker estava pausado porque o template usava linguagem do modelo antigo
(R$19/relatório). Estes testes renderizam os templates via Jinja e validam a linguagem
freemium: sem preço/pagamento/relatório pago, CTA de conta (/cadastrar) e disclaimer.
Offline (não envia nada; só renderiza o HTML).
"""

from __future__ import annotations

from notifier.email_client import _env

# Contexto igual ao que `_alert_params` passa ao template (link de unsubscribe sem '&'
# para não colidir com o autoescape do Jinja no assert de substring).
_CTX = dict(
    score=65, semaphore="amarelo", semaphore_label="AMARELO", semaphore_emoji="🟡",
    score_color="#F2C744", referral_link="https://klarim.net/parceiros",
    site_name="exemplo.com.br", target_url="https://exemplo.com.br",
    fail_count=4, result_link="https://klarim.net/result?url=x",
    unsubscribe_link="https://klarim.net/unsub/xyz123",
)

# Linguagem do modelo antigo que NÃO pode aparecer no e-mail freemium.
_FORBIDDEN = ["R$", "pagar", "comprar", "relatório completo", "desbloquear", "preço"]


def _render(name, **over):
    return _env.get_template(name).render(**{**_CTX, **over})


def _s100(**over):
    return _render("alert_score100.html", score=100, semaphore="verde",
                   semaphore_label="VERDE", semaphore_emoji="🟢", score_color="#00D26A",
                   fail_count=0, **over)


# --- alert.html ------------------------------------------------------------ #

def test_alert_no_payment_language():
    low = _render("alert.html").lower()
    for bad in _FORBIDDEN:
        assert bad.lower() not in low, f"linguagem proibida em alert.html: {bad}"


def test_alert_cta_points_to_cadastrar():
    html = _render("alert.html")
    assert "/cadastrar" in html
    assert "criar conta e monitorar" in html.lower()


def test_alert_mentions_free():
    assert "gratuita" in _render("alert.html").lower()


def test_alert_has_disclaimer_and_unsubscribe():
    html = _render("alert.html")
    assert "avalia a segurança do" in html.lower()
    assert "unsub/xyz123" in html  # link de unsubscribe preservado


def test_alert_preserves_variables():
    html = _render("alert.html", site_name="minhaloja.com.br", fail_count=7)
    assert "minhaloja.com.br" in html
    assert "<strong>7</strong>" in html  # fail_count renderizado
    assert "65" in html                   # score renderizado (do _CTX)
    assert "{{" not in html and "{%" not in html  # nenhuma variável não resolvida


# --- alert_score100.html --------------------------------------------------- #

def test_score100_no_payment_language():
    low = _s100().lower()
    for bad in _FORBIDDEN:
        assert bad.lower() not in low, f"linguagem proibida em alert_score100.html: {bad}"
    # também sem a linguagem antiga de "análise completa/29 verificações/15 verificações"
    assert "29 verificações" not in low and "15 verificações" not in low


def test_score100_cta_points_to_cadastrar():
    html = _s100()
    assert "/cadastrar" in html and "criar conta e monitorar" in html.lower()


def test_score100_celebratory_and_disclaimer():
    html = _s100()
    low = html.lower()
    assert "parabéns" in low and "nota máxima" in low
    assert "avalia a segurança do" in low       # disclaimer
    assert "unsub/xyz123" in html               # unsubscribe preservado
    # tom celebratório: sem menção a problemas/pontos de atenção
    assert "pontos de atenção" not in low and "problema" not in low


def test_score100_preserves_variables():
    html = _s100(site_name="topsite.com.br")
    assert "topsite.com.br" in html and "100" in html
    assert "{{" not in html and "{%" not in html
