"""Testes do tracking da jornada do lead (KL-21) — offline."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import api.main as m


@pytest.fixture(autouse=True)
def _no_bg(monkeypatch):
    # Evita a task de background (que tocaria no DB) nos testes do endpoint.
    monkeypatch.setattr(m, "_spawn", lambda coro: coro.close())
    m._event_rl.clear()


def _client():
    return TestClient(m.app, raise_server_exceptions=False)


def test_target_id_from_utm():
    assert m._target_id_from_utm("target_42", None) == 42
    assert m._target_id_from_utm(None, 7) == 7
    assert m._target_id_from_utm("target_x", None) is None
    assert m._target_id_from_utm(None, None) is None
    assert m._target_id_from_utm("target_9", 3) == 3  # explícito ganha


def test_event_rate_limit_helper():
    sid = "rate"
    accepted = sum(1 for _ in range(100) if m._event_rate_ok(sid))
    assert accepted == 100
    assert m._event_rate_ok(sid) is False           # 101º bloqueado
    assert m._event_rate_ok("outra-sessao") is True  # outra sessão livre


def test_post_events_public_and_validation():
    c = _client()
    assert c.post("/events", json={"event_type": "page_view", "session_id": "s1",
                                   "page_url": "/"}).json() == {"ok": True}
    # tipo desconhecido -> não grava
    assert c.post("/events", json={"event_type": "nope", "session_id": "s1"}).json()["recorded"] is False
    # sem session_id -> não grava
    assert c.post("/events", json={"event_type": "page_view", "session_id": ""}).json()["recorded"] is False


def test_post_events_rate_limited_via_endpoint():
    c = _client()
    for _ in range(100):
        c.post("/events", json={"event_type": "page_view", "session_id": "burst"})
    r = c.post("/events", json={"event_type": "page_view", "session_id": "burst"})
    assert r.json().get("rate_limited") is True


def test_events_public_analytics_protected():
    assert m._is_protected("/events") is False
    assert m._is_protected("/analytics/funnel") is True
    assert _client().get("/analytics/funnel").status_code == 401
