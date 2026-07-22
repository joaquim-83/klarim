"""KL-86 — helpers puros do dashboard. Offline.

⚠️ O ENDPOINT `/account/dashboard-summary` foi reescrito para o Dashboard v2 (KL-90) —
os testes do endpoint agora vivem em `tests/test_kl90_dashboard_summary.py`. Este
arquivo cobre só os helpers puros de `api/main.py` que continuam existindo:
`_dashboard_categories`, `_ssl_expiry_days`, `_score_trend`, `_vigilia_summary`,
`_new_user_checklist`, `_build_checklist`.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import api.main as m


# --------------------------------------------------------------------------- #
# Helpers puros
# --------------------------------------------------------------------------- #

def _checks(fail_ids=(), ssl_days=None):
    """Monta uma lista de checks (checks_json) com alguns FAIL e evidência de SSL opcional."""
    metas = m.CHECK_META
    out = []
    for meta in metas:
        cid = meta["check_id"]
        st = "FAIL" if cid in fail_ids else "PASS"
        ev = ""
        if ssl_days is not None and cid == "check_42_cert_chain":
            ev = f"Certificado válido até 2026-10-05 ({ssl_days} dias). Cadeia completa."
        out.append({"check_id": cid, "name": meta["name"], "status": st,
                    "severity": "ALTA" if st == "FAIL" else "BAIXA", "evidence": ev})
    return out


def test_dashboard_categories_shape():
    cats = m._dashboard_categories(_checks(fail_ids=("check_01_https",)))
    assert len(cats) == 6
    ids = {c["id"] for c in cats}
    assert {"transport", "headers", "supply_chain", "dns_email", "content", "osint"} <= ids
    tls = next(c for c in cats if c["id"] == "transport")
    assert tls["total"] >= 1 and 0 <= tls["passed"] <= tls["total"]
    assert tls["status"] in ("ok", "warning", "critical")


def test_ssl_expiry_days_parses_evidence():
    assert m._ssl_expiry_days(_checks(ssl_days=10)) == 10
    assert m._ssl_expiry_days(_checks()) is None  # sem evidência de dias


def test_score_trend():
    assert m._score_trend({"score": 80}, {"score": 70}) == ("up", 10)
    assert m._score_trend({"score": 60}, {"score": 70}) == ("down", -10)
    assert m._score_trend({"score": 71}, {"score": 70}) == ("stable", 1)
    assert m._score_trend({"score": 80}, None) == ("stable", 0)


def test_vigilia_summary():
    v = m._vigilia_summary([
        {"enabled": True, "last_status": "ok", "alert_count": 0, "site_domain": "x.com.br"},
        {"enabled": True, "last_status": "error", "alert_count": 2, "site_domain": "x.com.br"},
        {"enabled": False, "last_status": "alert", "alert_count": 1, "site_domain": "outro.com.br"},
    ], "x.com.br")
    assert v["active"] == 2 and v["ok"] == 1 and v["error"] == 1 and v["alerts"] == 2


def test_new_user_checklist():
    items = m._new_user_checklist({"email_confirmed": False})
    ids = {i["id"] for i in items}
    assert "add_site" in ids and "confirm_email" in ids


def test_checklist_email_and_dropped_and_ssl():
    user = {"email_confirmed": False}
    target = {"id": 7}
    latest, prev = {"score": 60}, {"score": 70}
    checks = _checks(ssl_days=5)
    cl = m._build_checklist(user, target, latest, prev, {"company_name": "X"},
                            {"error": 0}, checks, top_risk=None)
    ids = {i["id"] for i in cl}
    assert "confirm_email" in ids
    assert "score_dropped" in ids               # 60 < 70-2
    assert "ssl_expiry" in ids                  # 5 <= 30
    ssl = next(i for i in cl if i["id"] == "ssl_expiry")
    assert ssl["priority"] == 1                 # <=7 dias → urgente
    assert cl == sorted(cl, key=lambda x: x["priority"])


def test_checklist_all_good():
    user = {"email_confirmed": True}
    cl = m._build_checklist(user, {"id": 1}, {"score": 95}, {"score": 95},
                            {"company_name": "X"}, {"error": 0}, _checks(), top_risk=None)
    assert cl[0]["id"] == "all_good" and cl[0]["completed"] is True
