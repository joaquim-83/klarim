"""Testes do KL-61 — Gestão de Leads (scan_leads + scoring PQL + admin API).

Offline: scoring puro + `_recalc_lead_row`/`upsert_scan_lead` com cursor falso +
endpoints via TestClient + FakeStore (padrão de test_manual_classify/test_kl56).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import api.main as m
from api.lead_scoring import (
    calculate_lead_score, classify, score_breakdown, is_corporate_email,
    CLASSIFICATION_THRESHOLDS, SCORING_RULES, DECAY_RULES,
)
from discovery.store import TargetStore

NOW = datetime(2026, 7, 14, 12, 0, 0, tzinfo=timezone.utc)


# ============================ 1. Scoring puro ============================== #

def test_baseline_email_verified_only():
    # sem scan: só o baseline email_verified (+10)
    score, cls = calculate_lead_score({}, now=NOW)
    assert score == 10 and cls == "cold"


def test_scan_completed_adds_points():
    score, cls = calculate_lead_score({"total_scans": 1}, now=NOW)
    # email_verified 10 + scan_completed 15 = 25
    assert score == 25 and cls == "warm"


def test_score_below_70_adds_dor():
    score, _ = calculate_lead_score({"total_scans": 1, "worst_score": 65}, now=NOW)
    # 10 + 15 + score_below_70 10 = 35
    assert score == 35


def test_score_below_50_is_cumulative_with_70():
    score, _ = calculate_lead_score({"total_scans": 1, "worst_score": 40}, now=NOW)
    # 10 + 15 + below_70 10 + below_50 20 = 55
    assert score == 55


def test_account_created_adds_25():
    base, _ = calculate_lead_score({"total_scans": 1}, now=NOW)
    with_acc, cls = calculate_lead_score({"total_scans": 1, "has_account": True}, now=NOW)
    assert with_acc - base == SCORING_RULES["account_created"] == 25
    assert with_acc == 50 and cls == "hot"


def test_monitoring_added_adds_30():
    s, _ = calculate_lead_score(
        {"total_scans": 1, "has_account": True, "has_monitoring": True}, now=NOW)
    # 10 + 15 + 25 + 30 = 80 => pql
    assert s == 80 and classify(s) == "pql"


def test_multiple_scans_needs_two_distinct_urls():
    one = calculate_lead_score({"total_scans": 1, "distinct_urls": 1}, now=NOW)[0]
    two = calculate_lead_score({"total_scans": 2, "distinct_urls": 2}, now=NOW)[0]
    # 2 URLs distintas => +multiple_scans 20 (e NÃO rescan, pois total==distinct)
    assert two - one == 20


def test_rescan_when_total_exceeds_distinct():
    # mesma URL escaneada 2x: total_scans=2, distinct=1 => rescan +15, sem multiple_scans
    s = calculate_lead_score({"total_scans": 2, "distinct_urls": 1}, now=NOW)[0]
    assert s == 10 + 15 + 15  # email + scan + rescan = 40


def test_corporate_email_adds_5():
    s = calculate_lead_score({"total_scans": 1, "is_corporate_email": True}, now=NOW)[0]
    assert s == 30


def test_inactive_14d_decays():
    active = calculate_lead_score({"total_scans": 1}, now=NOW)[0]
    old = calculate_lead_score(
        {"total_scans": 1, "last_activity_at": NOW - timedelta(days=20)}, now=NOW)[0]
    assert old - active == DECAY_RULES["inactive_14d"] == -15


def test_score_never_negative():
    # baseline 10 + decay -15 => -5, clampado em 0
    s, cls = calculate_lead_score(
        {"last_activity_at": NOW - timedelta(days=30)}, now=NOW)
    assert s == 0 and cls == "cold"


def test_classification_thresholds_boundaries():
    assert classify(0) == "cold"
    assert classify(20) == "cold"
    assert classify(21) == "warm"
    assert classify(40) == "warm"
    assert classify(41) == "hot"
    assert classify(60) == "hot"
    assert classify(61) == "pql"
    assert classify(500) == "pql"


def test_classification_thresholds_config_contiguous():
    # as faixas cobrem 0..inf sem buraco (cold/warm/hot/pql)
    ordered = [CLASSIFICATION_THRESHOLDS[k] for k in ("cold", "warm", "hot", "pql")]
    for (lo1, hi1), (lo2, _hi2) in zip(ordered, ordered[1:]):
        assert lo2 == hi1 + 1


def test_classification_always_derived_from_score():
    # a classificação é SEMPRE classify(score) — não há caminho manual
    for data in ({}, {"total_scans": 1}, {"total_scans": 1, "has_account": True},
                 {"total_scans": 3, "distinct_urls": 3, "has_account": True,
                  "has_monitoring": True, "worst_score": 30}):
        score, cls = calculate_lead_score(data, now=NOW)
        assert cls == classify(score)


def test_is_corporate_email():
    assert is_corporate_email("dono@hotelverde.com.br") is True
    assert is_corporate_email("pessoa@gmail.com") is False
    assert is_corporate_email("x@yahoo.com.br") is False
    assert is_corporate_email("sem-arroba") is False
    assert is_corporate_email(None) is False


def test_score_breakdown_flags_applied():
    bd = {b["key"]: b for b in score_breakdown(
        {"total_scans": 1, "worst_score": 65, "has_account": True}, now=NOW)}
    assert bd["email_verified"]["applied"] is True
    assert bd["scan_completed"]["applied"] is True
    assert bd["score_below_70"]["applied"] is True
    assert bd["score_below_50"]["applied"] is False
    assert bd["account_created"]["applied"] is True
    assert bd["monitoring_added"]["applied"] is False
    # a soma dos aplicados bate com calculate_lead_score
    total = sum(b["points"] for b in bd.values() if b["applied"])
    assert total == calculate_lead_score(
        {"total_scans": 1, "worst_score": 65, "has_account": True}, now=NOW)[0]


def test_verified_examples_from_spec():
    assert calculate_lead_score({"total_scans": 1}, now=NOW) == (25, "warm")
    assert calculate_lead_score(
        {"total_scans": 1, "worst_score": 65, "has_account": True}, now=NOW) == (60, "hot")


# ============================ 2. Store (cursor falso) ===================== #

def test_lead_domain_extraction():
    assert TargetStore._lead_domain("https://www.hotel.com.br/contato") == "hotel.com.br"
    assert TargetStore._lead_domain("hotel.com.br") == "hotel.com.br"
    assert TargetStore._lead_domain(None) is None


class _RecalcCur:
    """Cursor falso: responde ao SELECT do recalc e captura o UPDATE."""
    def __init__(self, row):
        self._row = row
        self.update_params = None
        self.new_id = 7

    def execute(self, sql, params=None):
        s = sql.strip().upper()
        if s.startswith("SELECT TOTAL_SCANS"):
            self._pending = self._row
        elif "RETURNING ID" in s:
            self._pending = (self.new_id,)
        elif s.startswith("UPDATE SCAN_LEADS SET LEAD_SCORE"):
            self.update_params = params
            self._pending = None
        else:
            self._pending = None

    def fetchone(self):
        return self._pending


def test_recalc_lead_row_warm():
    # total_scans, urls, worst, has_account, has_monitoring, is_corp, last_activity_at
    cur = _RecalcCur((1, ["https://x.com.br"], 65, False, False, False, None))
    TargetStore._recalc_lead_row(cur, 5)
    score, classification, lead_id = cur.update_params
    assert (score, classification, lead_id) == (35, "warm", 5)


def test_recalc_lead_row_pql():
    cur = _RecalcCur((3, ["a", "b"], 30, True, True, True, None))
    TargetStore._recalc_lead_row(cur, 9)
    score, classification, _ = cur.update_params
    # email10 + scan15 + below70 10 + below50 20 + account25 + monitor30 +
    # multiple(2 urls)20 + rescan(3>2)15 + corp5 = 150 => pql
    assert score == 150 and classification == "pql"


def test_recalc_lead_row_missing_row_noop():
    class Cur:
        def execute(self, sql, params=None):
            self._n = None
        def fetchone(self):
            return None
    cur = Cur()
    TargetStore._recalc_lead_row(cur, 1)  # não deve levantar


def test_upsert_scan_lead_triggers_recalc(monkeypatch):
    cur = _RecalcCur((1, ["https://x.com.br"], 65, False, False, False, None))
    store = TargetStore()
    monkeypatch.setattr(store, "_run", lambda fn: fn(cur))
    asyncio.run(store.upsert_scan_lead("Dono@Hotel.com.br", "https://hotel.com.br", 65))
    # o UPSERT rodou e o recalc atualizou o score
    assert cur.update_params is not None
    assert cur.update_params[1] == "warm"


def test_upsert_scan_lead_ignores_bad_email(monkeypatch):
    called = {"run": False}
    store = TargetStore()
    monkeypatch.setattr(store, "_run", lambda fn: called.__setitem__("run", True))
    asyncio.run(store.upsert_scan_lead("sem-arroba", "https://x.com.br", 50))
    assert called["run"] is False


# ============================ 3. API (FakeStore) ========================== #

class FakeStore:
    def __init__(self):
        self.kw = None
        self.updated = None
        self.recalc = 0

    async def list_leads(self, **kw):
        self.kw = kw
        return {"leads": [{"id": 1, "email": "dono@empresa.com.br",
                           "classification": "hot", "lead_score": 45,
                           "total_scans": 2, "worst_score": 40, "has_account": False}],
                "total": 1, "by_classification": {"cold": 3, "warm": 2, "hot": 1, "pql": 0}}

    async def lead_stats(self):
        return {"total": 6, "by_classification": {"cold": 3, "warm": 2, "hot": 1, "pql": 0},
                "with_account": 2, "with_monitoring": 1, "avg_lead_score": 30,
                "corporate_emails": 4, "multi_scan": 1, "top_sectors": [],
                "today": 1, "last_7_days": 4, "conversion_by_sector": [],
                "pain_sectors": [{"sector": "hotel", "avg_worst_score": 42}],
                "pql_rate": 0.0}

    async def lead_funnel(self):
        return {"email_verified": 6, "scan_completed": 6, "account_created": 2,
                "monitoring_added": 1, "conversion_rate_scan_to_account": 33.3,
                "conversion_rate_account_to_monitoring": 50.0}

    async def get_lead(self, lead_id):
        if lead_id == 999:
            return None
        return {"id": lead_id, "email": "dono@empresa.com.br", "classification": "warm",
                "lead_score": 35, "total_scans": 1, "urls_scanned": ["https://x.com.br"],
                "worst_score": 65, "has_account": False, "has_monitoring": False,
                "is_corporate_email": True, "last_activity_at": None,
                "tags": ["a"], "notes": "nota", "opted_out": False, "scans": []}

    async def update_lead(self, lead_id, tags=None, notes=None, opted_out=None):
        self.updated = {"id": lead_id, "tags": tags, "notes": notes, "opted_out": opted_out}
        return lead_id != 999

    async def recalculate_all_leads(self):
        self.recalc += 1
        return 6


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("ADMIN_USER", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "s3nha-forte")
    monkeypatch.setenv("JWT_SECRET", "x" * 64)
    store = FakeStore()
    monkeypatch.setattr(m, "get_target_store", lambda: store)
    c = TestClient(m.app, raise_server_exceptions=False)
    c._store = store
    return c


def _auth(client):
    tok = client.post("/auth/login",
                      json={"username": "admin", "password": "s3nha-forte"}).json()["token"]
    return {"Authorization": f"Bearer {tok}"}


def test_leads_endpoints_protected():
    assert m._is_protected("/leads") is True
    assert m._is_protected("/leads/5") is True
    c = TestClient(m.app, raise_server_exceptions=False)
    assert c.get("/leads").status_code == 401
    assert c.get("/leads/stats").status_code == 401


def test_list_leads_forwards_filters(client):
    r = client.get("/leads?classification=hot&has_account=false&search=empresa&limit=10",
                   headers=_auth(client))
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1 and body["by_classification"]["cold"] == 3
    assert client._store.kw["classification"] == "hot"
    assert client._store.kw["has_account"] is False
    assert client._store.kw["search"] == "empresa"


def test_list_leads_bad_classification_ignored(client):
    client.get("/leads?classification=banana", headers=_auth(client))
    assert client._store.kw["classification"] is None


def test_lead_stats_endpoint(client):
    r = client.get("/leads/stats", headers=_auth(client))
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 6 and body["pql_rate"] == 0.0
    assert body["pain_sectors"][0]["sector"] == "hotel"


def test_lead_funnel_endpoint(client):
    r = client.get("/leads/funnel", headers=_auth(client))
    assert r.status_code == 200
    assert r.json()["account_created"] == 2


def test_lead_detail_injects_score_breakdown(client):
    r = client.get("/leads/5", headers=_auth(client))
    assert r.status_code == 200
    body = r.json()
    assert "score_breakdown" in body
    keys = {b["key"] for b in body["score_breakdown"]}
    assert "email_verified" in keys and "monitoring_added" in keys


def test_lead_detail_not_found(client):
    assert client.get("/leads/999", headers=_auth(client)).status_code == 404


def test_patch_lead_forwards_manual_fields(client):
    r = client.patch("/leads/5", json={"tags": ["prioridade"], "notes": "ligar",
                                       "opted_out": True}, headers=_auth(client))
    assert r.status_code == 200
    up = client._store.updated
    assert up["tags"] == ["prioridade"] and up["notes"] == "ligar" and up["opted_out"] is True


def test_patch_lead_cannot_set_score_or_classification(client):
    # mesmo enviando lead_score/classification, eles são ignorados (não existem no body)
    r = client.patch("/leads/5", json={"lead_score": 999, "classification": "pql"},
                     headers=_auth(client))
    assert r.status_code == 200
    up = client._store.updated
    assert up["tags"] is None and up["notes"] is None and up["opted_out"] is None
    assert "lead_score" not in up and "classification" not in up


def test_patch_lead_not_found(client):
    r = client.patch("/leads/999", json={"notes": "x"}, headers=_auth(client))
    assert r.status_code == 404


def test_recalculate_endpoint(client):
    r = client.post("/leads/recalculate", headers=_auth(client))
    assert r.status_code == 200
    assert r.json() == {"ok": True, "recalculated": 6}
    assert client._store.recalc == 1


def test_safe_lead_swallows_exception():
    # o helper fire-and-forget nunca propaga erro (não derruba o scan/signup)
    async def boom():
        raise RuntimeError("db down")

    asyncio.run(m._safe_lead(boom()))  # não deve levantar
