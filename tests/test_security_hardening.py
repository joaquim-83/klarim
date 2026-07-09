"""Testes dos fixes de segurança da auto-auditoria — offline.

Fix 1: docs/openapi desligados em produção.
Fix 2: rate limit no login (5/min por IP → 429).
Fix 3: sanitização anti stored-XSS no /events.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

import api.main as m


def _client():
    return TestClient(m.app, raise_server_exceptions=False)


# --- Fix 1: docs desligados em produção ------------------------------------ #

def test_docs_disabled_in_prod():
    # Sem KLARIM_DEV_MODE=true, o app é criado sem docs/redoc/openapi.
    assert m.app.docs_url is None
    assert m.app.redoc_url is None
    assert m.app.openapi_url is None
    c = _client()
    assert c.get("/docs").status_code == 404
    assert c.get("/openapi.json").status_code == 404
    assert c.get("/redoc").status_code == 404


# --- Fix 2: rate limit no login -------------------------------------------- #

def test_login_rate_limit_429_after_5(monkeypatch):
    monkeypatch.setenv("ADMIN_USER", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "s3nha-forte")
    monkeypatch.setenv("JWT_SECRET", "x" * 64)
    c = _client()
    codes = []
    for _ in range(7):
        r = c.post("/auth/login", json={"username": "admin", "password": "errada"})
        codes.append(r.status_code)
    assert codes == [401, 401, 401, 401, 401, 429, 429], codes
    # última resposta traz Retry-After
    last = c.post("/auth/login", json={"username": "admin", "password": "errada"})
    assert last.status_code == 429 and int(last.headers["Retry-After"]) > 0


def test_login_rate_limit_per_ip(monkeypatch):
    monkeypatch.setenv("ADMIN_USER", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "pw")
    monkeypatch.setenv("JWT_SECRET", "x" * 64)
    c = _client()
    # 5 tentativas de um IP esgotam a cota daquele IP
    for _ in range(6):
        c.post("/auth/login", json={"username": "a", "password": "b"},
               headers={"X-Real-IP": "1.1.1.1"})
    blocked = c.post("/auth/login", json={"username": "a", "password": "b"},
                     headers={"X-Real-IP": "1.1.1.1"})
    assert blocked.status_code == 429
    # outro IP continua livre
    other = c.post("/auth/login", json={"username": "a", "password": "b"},
                   headers={"X-Real-IP": "2.2.2.2"})
    assert other.status_code == 401


# --- Fix 3: sanitização do /events ----------------------------------------- #

def test_sanitize_str_strips_tags_and_schemes():
    assert m._sanitize_str("<script>alert(1)</script>") == "alert(1)"
    assert m._sanitize_str("<img src=x onerror=alert(1)>oi") == "oi"
    assert "javascript:" not in m._sanitize_str("javascript:alert(1)")
    assert m._sanitize_str(None) is None
    assert len(m._sanitize_str("a" * 999)) == 500


def test_sanitize_metadata_recursive():
    md = {"<b>k</b>": "<script>x</script>", "nested": {"u": "<i>v</i>"}}
    out = m._sanitize_metadata(md)
    assert out == {"k": "x", "nested": {"u": "v"}}


def test_events_endpoint_sanitizes(monkeypatch):
    captured = {}

    # Fake síncrono: captura o body já sanitizado no momento da chamada; o _spawn
    # vira no-op (o "coro" já é None). Evita depender do event loop nos testes.
    def fake_log(body, target_id):
        captured["page_url"] = body.page_url
        captured["metadata"] = body.metadata

    monkeypatch.setattr(m, "_log_event_bg", fake_log)
    monkeypatch.setattr(m, "_spawn", lambda _x: None)
    c = _client()
    r = c.post("/events", json={
        "event_type": "page_view", "session_id": "s1",
        "page_url": "<script>alert(1)</script>/result",
        "metadata": {"ref": "<img src=x>"},
    })
    assert r.json() == {"ok": True}
    assert "<script>" not in captured["page_url"] and "/result" in captured["page_url"]
    assert captured["metadata"] == {"ref": ""}
