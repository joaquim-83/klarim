"""KL-78 — fixes de UX, segurança e lógica de negócio.

Cobre o SSRF guard e o rate limit do GET /scan (item 8). O selo (item 3) e o bug de
vigília/monitoramento (item 9) são cobertos em test_kl42_social.py e test_accounts.py.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import api.main as m


# --------------------------------------------------------------------------- #
# Item 8 — SSRF guard (unitário, sem rede)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("host", [
    "127.0.0.1", "169.254.169.254",     # loopback + metadata de nuvem (AWS/GCP)
    "10.0.0.5", "192.168.1.1", "172.16.0.1",  # ranges privados
    "0.0.0.0", "::1",                    # unspecified + loopback IPv6
    "localhost", "foo.internal", "bar.local", "db.lan",  # nomes internos
])
def test_ssrf_blocks_internal_hosts(host):
    assert m._scan_host_is_safe(host) is False


def test_ip_is_internal():
    assert m._ip_is_internal("127.0.0.1") is True
    assert m._ip_is_internal("169.254.169.254") is True
    assert m._ip_is_internal("10.1.2.3") is True
    assert m._ip_is_internal("8.8.8.8") is False        # IP público
    assert m._ip_is_internal("not-an-ip") is False       # nome → não é IP literal


def test_ssrf_public_ip_allowed():
    assert m._scan_host_is_safe("8.8.8.8") is True       # IP público literal → ok


# --------------------------------------------------------------------------- #
# Item 8 — GET /scan: SSRF (400) + rate limit (429)
# --------------------------------------------------------------------------- #
@pytest.fixture
def client():
    return TestClient(m.app, raise_server_exceptions=False)


def test_scan_get_blocks_internal_url(client):
    # host interno literal (sem DNS) → 400 antes de qualquer fetch.
    r = client.get("/scan", params={"url": "http://127.0.0.1:6379"})
    assert r.status_code == 400
    r2 = client.get("/scan", params={"url": "http://169.254.169.254/latest/meta-data/"})
    assert r2.status_code == 400


def test_scan_get_rate_limited(client):
    # o rate limit (10/10min) é checado ANTES do scan; usa URL interna (400 rápido, sem rede).
    codes = [client.get("/scan", params={"url": "http://127.0.0.1"}).status_code
             for _ in range(12)]
    assert 429 in codes                    # em algum momento estoura o teto
    assert codes[:10] == [400] * 10        # os 10 primeiros passam o RL e batem no SSRF (400)
