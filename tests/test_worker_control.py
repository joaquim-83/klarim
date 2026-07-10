"""Testes do controle centralizado de workers (KL-32). Offline."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from discovery import worker_control as wc


@pytest.fixture
def ctrl_file(tmp_path, monkeypatch):
    p = tmp_path / "wc.json"
    monkeypatch.setenv("WORKER_CONTROL_FILE", str(p))
    return p


# --- módulo ---------------------------------------------------------------- #

def test_default_all_enabled(ctrl_file):
    # arquivo ausente → fail-open (todos habilitados)
    assert all(wc.is_enabled(w) for w in wc.WORKERS)


def test_pause_resume(ctrl_file):
    wc.pause("alert")
    assert wc.is_enabled("alert") is False and wc.is_enabled("discovery") is True
    d = wc.load()
    assert d["alert"]["paused_by"] == "mcp" and d["alert"]["paused_at"]
    wc.resume("alert")
    assert wc.is_enabled("alert") is True
    assert wc.load()["alert"]["paused_at"] is None and wc.load()["alert"]["paused_by"] is None


def test_pause_resume_all(ctrl_file):
    wc.pause("all")
    assert not any(wc.is_enabled(w) for w in wc.WORKERS)
    wc.resume("all")
    assert all(wc.is_enabled(w) for w in wc.WORKERS)


def test_persistence_survives_reload(ctrl_file):
    wc.pause("scan")
    # "restart": um novo load lê o arquivo do disco
    assert wc.is_enabled("scan") is False
    assert json.loads(ctrl_file.read_text())["scan"]["enabled"] is False


def test_invalid_worker_raises(ctrl_file):
    with pytest.raises(ValueError):
        wc.pause("nope")


def test_corrupt_file_fail_open(ctrl_file):
    ctrl_file.write_text("{ isto não é json")
    assert wc.is_enabled("alert") is True  # fail-open, não trava o worker


def test_missing_key_fail_open(ctrl_file):
    ctrl_file.write_text(json.dumps({"alert": {"enabled": False}}))  # só o alert
    assert wc.is_enabled("alert") is False       # respeita o que está no arquivo
    assert wc.is_enabled("discovery") is True     # ausente → default habilitado


def test_set_config(ctrl_file):
    wc.set_config("alert", max_per_hour=100, batch_size=25)
    assert wc.worker_config("alert") == {"max_per_hour": 100, "batch_size": 25}
    # chave inválida para o worker é ignorada
    wc.set_config("scan", max_per_hour=10, bogus=1)
    assert wc.worker_config("scan") == {"max_per_hour": 10}
    # config não interfere no enabled
    assert wc.is_enabled("alert") is True


# --- endpoints admin ------------------------------------------------------- #

def test_admin_workers_require_jwt(ctrl_file):
    import api.main as m
    c = TestClient(m.app, raise_server_exceptions=False)
    assert c.post("/admin/workers/pause", json={"worker": "alert"}).status_code == 401
    assert c.post("/admin/workers/resume", json={"worker": "alert"}).status_code == 401
    assert c.get("/admin/workers/control").status_code == 401


def test_admin_workers_pause_resume(ctrl_file, monkeypatch):
    import api.main as m
    monkeypatch.setenv("JWT_SECRET", "x" * 40)
    monkeypatch.setenv("ADMIN_USER", "op")
    monkeypatch.setenv("ADMIN_PASSWORD", "pw")
    tok = m._create_token("op")
    h = {"Authorization": f"Bearer {tok}"}
    c = TestClient(m.app, raise_server_exceptions=False)

    r = c.post("/admin/workers/pause", json={"worker": "alert"}, headers=h)
    assert r.status_code == 200 and r.json()["control"]["alert"]["enabled"] is False
    assert r.json()["control"]["alert"]["paused_by"] == "painel"

    ctl = c.get("/admin/workers/control", headers=h).json()
    assert ctl["control"]["alert"]["enabled"] is False

    r2 = c.post("/admin/workers/resume", json={"worker": "alert"}, headers=h)
    assert r2.json()["control"]["alert"]["enabled"] is True

    assert c.post("/admin/workers/pause", json={"worker": "nope"}, headers=h).status_code == 422
