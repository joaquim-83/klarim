"""KL-138 — hardening: (1) root `/` sem mapa de endpoints, (3) redirect curto `/a/{target_id}`
(302 p/ /site/{domain}, sem open redirect, rate limit, IP mascarado). O bloqueio de paths de
exploit (Fix 2) é do nginx (validado por `nginx -t` no CI). Offline (store fake, TestClient)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import api.main as m


class _TargetStore:
    """Store fake: get_target_domain devolve o domínio p/ ids conhecidos; registra cliques."""
    def __init__(self):
        self.clicks = []

    async def get_target_domain(self, target_id):
        return {"domain": "exemplo.com.br"} if target_id == 42 else None

    async def log_email_click(self, target_id, ip_masked=None):
        self.clicks.append((target_id, ip_masked))


@pytest.fixture
def store():
    return _TargetStore()


@pytest.fixture
def client(monkeypatch, store):
    monkeypatch.setattr(m, "get_target_store", lambda: store)
    # rate limit cai no fallback in-memory (sem Redis nos testes) — zera o bucket entre testes
    m._email_click_attempts.clear()
    return TestClient(m.app, raise_server_exceptions=False, follow_redirects=False)


# =========================================================================== #
# Fix 1 — root sem listagem de endpoints
# =========================================================================== #

def test_root_minimal(client):
    r = client.get("/")
    assert r.status_code == 200
    body = r.json()
    assert body == {"name": "Klarim API", "status": "ok"}


def test_root_hides_surface(client):
    body = client.get("/").json()
    for leaked in ("endpoints", "scanner_version", "payments_enabled", "email_enabled", "dev_mode"):
        assert leaked not in body


# =========================================================================== #
# Fix 3 — redirect curto /a/{target_id}
# =========================================================================== #

def test_redirect_302_to_site(client, store):
    r = client.get("/a/42")
    assert r.status_code == 302
    assert r.headers["location"] == "/site/exemplo.com.br"   # destino FIXO (não aceita parâmetro)


def test_redirect_logs_click_masked_ip(client, store):
    # IP real via CF-Connecting-IP (o que o Cloudflare sempre envia) → mascarado /24 no log (LGPD).
    client.get("/a/42", headers={"CF-Connecting-IP": "203.0.113.55"})
    assert len(store.clicks) == 1
    tid, ip = store.clicks[0]
    assert tid == 42
    assert ip == "203.0.113.x"   # último octeto oculto (/24)


def test_redirect_unknown_target_404(client):
    r = client.get("/a/999999")
    assert r.status_code == 404


def test_redirect_non_integer_422(client):
    r = client.get("/a/abc")
    assert r.status_code == 422   # FastAPI valida {target_id: int}


def test_redirect_rate_limited(client, store):
    # 30/min por IP: as 30 primeiras passam (302/404), a 31ª → 429.
    codes = [client.get("/a/42").status_code for _ in range(30)]
    assert all(c == 302 for c in codes)
    assert client.get("/a/42").status_code == 429


def test_redirect_only_to_site_no_open_redirect(client, store):
    # Um destino arbitrário via query NÃO é honrado — o Location é sempre /site/{domain}.
    r = client.get("/a/42?url=https://evil.com&next=//evil.com")
    assert r.status_code == 302
    assert r.headers["location"] == "/site/exemplo.com.br"
    assert "evil.com" not in r.headers["location"]
