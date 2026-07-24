"""KL-104 P2 — filtros avançados da página Alvos. Testa o helper PURO `_target_filters`
(cada filtro → cláusula WHERE + params; combinação AND; injeção; valores inválidos). O SQL
é validado contra o Postgres na VM. Offline.
"""

from __future__ import annotations

from discovery.store import TargetStore


def wf(**f):
    return TargetStore._target_filters(f)


def _sql(w):
    return " ".join(w)


def test_score_ranges_and_sem():
    assert "t.last_scan_score BETWEEN 90 AND 100" in _sql(wf(score="90-100")[0])
    assert "t.last_scan_score BETWEEN 0 AND 49" in _sql(wf(score="0-49")[0])
    assert "t.last_scan_score IS NULL" in _sql(wf(score="sem")[0])


def test_semaphore_derived_from_score():
    assert "t.last_scan_score >= 90" in _sql(wf(semaphore="verde")[0])
    assert "t.last_scan_score >= 50 AND t.last_scan_score < 90" in _sql(wf(semaphore="amarelo")[0])
    assert "t.last_scan_score < 50" in _sql(wf(semaphore="vermelho")[0])


def test_score_and_semaphore_combine_with_and():
    w, _ = wf(score="90-100", semaphore="verde")
    assert len(w) == 2  # ambos entram (AND)


def test_lead_score():
    assert "t.alert_quality_score >= 60" in _sql(wf(lead_score="alto")[0])
    assert "t.alert_quality_score IS NULL" in _sql(wf(lead_score="sem")[0])


def test_toggles_three_states():
    assert "contact_email IS NOT NULL" in _sql(wf(has_email=True)[0])
    assert "contact_email IS NULL" in _sql(wf(has_email=False)[0])
    assert wf(has_email=None)[0] == []                       # None → ignora
    assert "EXISTS (SELECT 1 FROM user_sites" in _sql(wf(monitored=True)[0])
    assert _sql(wf(monitored=False)[0]).startswith("NOT EXISTS")
    assert "t.owner_verified = TRUE" in _sql(wf(owner_verified=True)[0])
    assert "EXISTS (SELECT 1 FROM site_profile" in _sql(wf(has_ai_profile=True)[0])


def test_site_type_and_tech_are_parameterized_arrays():
    w, p = wf(site_type="ecommerce,saas")
    assert "t.site_type = ANY(%s)" in _sql(w) and p == [["ecommerce", "saas"]]
    w2, p2 = wf(tech="wordpress,react")
    assert "site_tech_stack st WHERE st.target_id = t.id AND st.name = ANY(%s)" in _sql(w2)
    assert p2 == [["wordpress", "react"]]


def test_last_scan_windows():
    assert "INTERVAL '7 days'" in _sql(wf(last_scan="7d")[0])
    assert "date_trunc('day', NOW())" in _sql(wf(last_scan="hoje")[0])
    assert "t.last_scan_at IS NULL" in _sql(wf(last_scan="nunca")[0])


def test_and_combination_keeps_all():
    w, p = wf(status="scanned", sector="hotel", score="90-100", has_email=True, tech="wordpress")
    assert len(w) == 5
    assert "scanned" in p and "hotel" in p and ["wordpress"] in p   # valores via params


def test_invalid_values_ignored_never_break():
    w, p = wf(score="bogus", semaphore="xyz", last_scan="99y", lead_score="huge")
    assert w == [] and p == []


def test_injection_safe():
    # input malicioso vira PARÂMETRO (LIKE), nunca SQL cru
    w, p = wf(search="'; DROP TABLE targets; --")
    joined = _sql(w)
    assert "DROP TABLE" not in joined.upper() and "%s" in joined   # nunca interpolado no SQL
    assert any("drop table" in str(x).lower() for x in p)          # vai como param (escapado pelo driver)


def test_existing_filters_still_work():
    w, p = wf(status="alerted", platform="wordpress", source="ct_log", low_confidence=True,
              search="hotel")
    assert "t.status = %s" in _sql(w) and "t.classification_confidence < 0.5" in _sql(w)
    assert "alerted" in p and "wordpress" in p and "ct_log" in p
