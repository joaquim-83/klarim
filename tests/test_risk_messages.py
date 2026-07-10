"""Testes das mensagens de risco dinâmicas (KL-20) — offline."""

from __future__ import annotations

from reporter.risk_messages import (
    RISK_MESSAGES, get_risk_messages, get_risk_summary,
)


def _fail(check_id, severity):
    return {"check_id": check_id, "status": "FAIL", "severity": severity}


def test_all_checks_mapped():
    # O conjunto de checks cresce (KL-22 levou a 29); asserção no contrato, não no número.
    from scanner import ALL_CHECKS

    registered = {cid for cid, _ in ALL_CHECKS}
    assert len(RISK_MESSAGES) == len(registered)
    assert registered == set(RISK_MESSAGES)  # cada check registrado tem mensagem de risco
    for entry in RISK_MESSAGES.values():
        assert entry.get("headline") and entry.get("risk") and entry.get("icon")


def test_get_risk_messages_orders_by_severity_and_limits():
    results = [
        _fail("check_05_csp", "ALTA"),
        _fail("check_01_https", "CRITICA"),
        _fail("check_12_metatags", "BAIXA"),
        _fail("check_06_xfo", "MEDIA"),
        _fail("check_13_sri", "ALTA"),
        {"check_id": "check_07_xcto", "status": "PASS", "severity": "MEDIA"},  # PASS ignora
    ]
    risks = get_risk_messages(results)
    assert len(risks) == 4  # limite
    assert risks[0]["check_id"] == "check_01_https"  # crítica primeiro
    assert all("headline" in r and "risk" in r and "icon" in r for r in risks)
    # PASS não entra
    assert "check_07_xcto" not in {r["check_id"] for r in risks}


def test_get_risk_messages_empty_without_fails():
    assert get_risk_messages([]) == []
    assert get_risk_messages([{"check_id": "check_01_https", "status": "PASS", "severity": "CRITICA"}]) == []


def test_get_risk_summary_categories():
    # vazamento (https) + golpes (csp) + supply (sri)
    risks = get_risk_messages([_fail("check_01_https", "CRITICA"),
                               _fail("check_05_csp", "ALTA"),
                               _fail("check_13_sri", "ALTA")])
    s = get_risk_summary(risks)
    assert "vazamento de dados" in s and "golpes" in s and "código malicioso" in s

    # 1 categoria só
    one = get_risk_summary(get_risk_messages([_fail("check_11_dirlist", "ALTA")]))
    assert one.startswith("Seu site apresenta risco de") and "invasão" in one

    # sem categoria mapeável (só xcto) -> proteções básicas
    basic = get_risk_summary(get_risk_messages([_fail("check_07_xcto", "MEDIA")]))
    assert basic == "Seu site não tem proteções básicas contra ataques comuns."

    # vazio
    assert get_risk_summary([]) == ""


def test_consistency_across_surfaces():
    """Mesmo scan → mesmos riscos (o que garante consistência PDF/e-mail/frontend)."""
    results = [_fail("check_01_https", "CRITICA"), _fail("check_05_csp", "ALTA")]
    a = get_risk_messages(results)
    b = get_risk_messages({"results": results})  # via dict (to_dict)
    assert [r["check_id"] for r in a] == [r["check_id"] for r in b]
    assert get_risk_summary(a) == get_risk_summary(b)
