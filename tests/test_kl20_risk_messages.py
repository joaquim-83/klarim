"""KL-20 — mensagens de risco dinâmicas por falha e setor.

Testa a dimensão setorial (build_risk_summary), a linha de benchmark
(build_benchmark_line) e a renderização do e-mail de alerta (sector risks + CTA duplo).
"""

from __future__ import annotations

from reporter import risk_messages as rm
from reporter.risk_messages import build_risk_summary, build_benchmark_line, RISK_MESSAGES
from notifier.email_client import build_alert_text


def _fail(cid, sev="ALTA"):
    return {"check_id": cid, "status": "FAIL", "severity": sev}


# --------------------------------------------------------------------------- #
# Cobertura: os 48 checks têm mensagem-base
# --------------------------------------------------------------------------- #
def test_all_48_checks_have_message():
    for i in range(1, 49):
        prefix = f"check_{i:02d}_"
        assert any(k.startswith(prefix) for k in RISK_MESSAGES), f"falta {prefix}"


# --------------------------------------------------------------------------- #
# build_risk_summary
# --------------------------------------------------------------------------- #
def test_top_n_and_remaining():
    fails = [_fail("check_01_https", "CRITICA"), _fail("check_05_csp", "ALTA"),
             _fail("check_21_spf", "MEDIA"), _fail("check_35_referrer_policy", "BAIXA"),
             _fail("check_45_html_comments", "BAIXA")]
    r = build_risk_summary(fails, "hotel", limit=3)
    assert len(r["risks"]) == 3
    assert r["remaining_count"] == 2          # 5 FAILs, 3 mostrados
    assert r["plural"] == "hotéis" and r["audience"] == "hóspedes"
    # ordenado por severidade: CRITICA primeiro
    assert r["risks"][0]["check_id"] == "check_01_https"


def test_no_fails_empty():
    r = build_risk_summary([_fail("x", "ALTA")], "hotel")  # check inválido → ignorado
    assert r["risks"] == [] and r["remaining_count"] == 0
    r2 = build_risk_summary([], "hotel")
    assert r2["risks"] == []


def test_unknown_sector_uses_default():
    r = build_risk_summary([_fail("check_01_https")], "setor_inexistente")
    assert r["plural"] == "sites" and r["audience"] == "clientes"  # DEFAULT_RISK


def test_sector_override_message():
    # check_01 + ecommerce → mensagem de pagamento (variação setorial)
    r = build_risk_summary([_fail("check_01_https")], "ecommerce")
    assert "pagamento" in r["risks"][0]["message"].lower()
    # check_01 + setor sem override → mensagem-base
    base = RISK_MESSAGES["check_01_https"]["risk"]
    r2 = build_risk_summary([_fail("check_01_https")], "grafica")
    assert r2["risks"][0]["message"] == base


def test_macro_fallback():
    # 'clinica' → macro 'saude': contexto de paciente + override de check_01 por macro
    r = build_risk_summary([_fail("check_01_https")], "odontologia")  # macro saude
    assert r["audience"] == "pacientes"
    assert "saúde" in r["risks"][0]["message"].lower() or "pacientes" in r["risks"][0]["message"].lower()


# --------------------------------------------------------------------------- #
# build_benchmark_line
# --------------------------------------------------------------------------- #
def test_benchmark_below():
    line = build_benchmark_line(50, "hotel", {"avg_score": 68, "count": 40})
    assert "abaixo da média" in line and "68" in line


def test_benchmark_above():
    line = build_benchmark_line(80, "hotel", {"avg_score": 68, "count": 40})
    assert "acima da média" in line and "40 hotéis" in line  # "Com base em 40 hotéis."


def test_benchmark_score_100():
    assert "nota máxima" in build_benchmark_line(100, "hotel", {"avg_score": 68, "count": 40})


def test_benchmark_none():
    assert build_benchmark_line(72, "hotel", None) == "Score: 72/100"


# --------------------------------------------------------------------------- #
# e-mail de alerta (KL-20): riscos setorizados + CTA duplo
# --------------------------------------------------------------------------- #
def test_alert_email_has_sector_risks_and_dual_cta():
    rs = build_risk_summary([_fail("check_01_https", "CRITICA"),
                             _fail("check_25_form_security", "ALTA")], "ecommerce", limit=3)
    bl = build_benchmark_line(55, "ecommerce", {"avg_score": 70, "count": 30})
    body = build_alert_text("loja.com.br", 55, "https://klarim.net/unsub",
                            risk_summary=rs, benchmark_line=bl, sector_slug="ecommerce")
    assert "pagamento" in body.lower()               # mensagem setorial de negócio
    assert "abaixo da média" in body                 # benchmark
    assert "/site/loja.com.br" in body               # CTA perfil
    assert "/setor/ecommerce" in body                # 2º CTA (KL-20)
    assert "setor de lojas" in body                  # plural setorial (gênero-neutro)
    assert "HSTS" not in body                        # sem jargão técnico


def test_alert_email_score100_positive_no_risks():
    body = build_alert_text("perfeito.com.br", 100, None, is_score100=True)
    assert "Parabéns" in body and "100/100" in body
    assert "⚠" not in body and "/setor/" not in body


def test_alert_email_generic_fallback_without_risk_summary():
    # sem risk_summary → corpo genérico retrocompatível
    body = build_alert_text("x.com.br", 60, None)
    assert "60/100" in body and "/site/x.com.br" in body
