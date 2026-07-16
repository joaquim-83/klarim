"""FIX — scan síncrono do admin (POST /targets/{id}/scan?sync=1).

O botão "Escanear" no painel precisa de feedback imediato: com `sync=1` a varredura
roda inline e devolve `score`/`semaphore`; sem `sync`, apenas enfileira. Offline (o
scan real é mockado)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import api.main as m
import scanner
from scanner.runner import ScanReport
from scanner.checks.base import CheckResult, Severity, Status
from scanner.scoring import compute_score


class FakeStore:
    async def get_target(self, target_id):
        if target_id == 999:
            return None
        return {"id": target_id, "url": "https://www.example.com", "domain": "example.com"}


def _report(score_fail=False):
    results = []
    for cid, _fn in scanner.ALL_CHECKS:
        st = Status.FAIL if (score_fail and cid == "check_02_hsts") else Status.PASS
        sv = Severity.ALTA if st == Status.FAIL else Severity.MEDIA
        r = CheckResult(name=cid, status=st, severity=sv, evidence="x")
        r.check_id = cid
        results.append(r)
    return ScanReport(url="https://www.example.com", started_at="t", finished_at="t",
                      duration_s=1.0, results=results, score=compute_score(results))


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("ADMIN_USER", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "s3nha-forte")
    monkeypatch.setenv("JWT_SECRET", "x" * 64)
    store = FakeStore()
    monkeypatch.setattr(m, "get_target_store", lambda: store)
    c = TestClient(m.app, raise_server_exceptions=False)
    return c


def _auth(client):
    token = client.post("/auth/login", json={"username": "admin", "password": "s3nha-forte"}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def test_scan_endpoint_protected():
    assert m._is_protected("/targets/1/scan") is True
    c = TestClient(m.app, raise_server_exceptions=False)
    assert c.post("/targets/1/scan").status_code == 401


def test_sync_scan_returns_score(client, monkeypatch):
    async def fake_get_or_scan(url, full=True, ingest_source=None, scanned_by_email=None):
        assert full is True and ingest_source == "admin"
        return _report()
    monkeypatch.setattr(m, "get_or_scan", fake_get_or_scan)
    r = client.post("/targets/5/scan?sync=1", headers=_auth(client))
    assert r.status_code == 200
    data = r.json()
    assert data["synchronous"] is True
    assert data["score"] == 100 and data["semaphore"] == "verde"


def test_sync_scan_reports_fail(client, monkeypatch):
    async def fake_get_or_scan(url, full=True, ingest_source=None, scanned_by_email=None):
        return _report(score_fail=True)
    monkeypatch.setattr(m, "get_or_scan", fake_get_or_scan)
    r = client.post("/targets/5/scan?sync=1", headers=_auth(client))
    assert r.status_code == 200 and r.json()["fail_count"] >= 1


def test_async_scan_enqueues(client, monkeypatch):
    seen = {}
    async def fake_enqueue(target_id, url, source="manual"):
        seen.update(target_id=target_id, url=url, source=source)
        return True
    monkeypatch.setattr(m, "_enqueue_scan", fake_enqueue)
    r = client.post("/targets/5/scan", headers=_auth(client))  # sem sync → fila
    assert r.status_code == 200 and r.json()["enqueued"] is True
    assert seen["target_id"] == 5 and seen["source"] == "admin"


def test_sync_scan_404(client, monkeypatch):
    r = client.post("/targets/999/scan?sync=1", headers=_auth(client))
    assert r.status_code == 404
