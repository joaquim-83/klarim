"""KL-150 P2 (item 2) — propagação da verificação do scanner (KL-99) para o Gate.
GET /gate/projects chama `propagate_scanner_verification` ANTES de listar → um projeto cujo domínio
foi verificado pelo dono no scanner aparece como "Verificado". Offline (FakeStore + X-API-Key)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import api.main as m
from api import gate as g

SECRET = "k" * 64
_FREE = {"id": 1, "name": "Free", "slug": "free", "scans_per_day": 5, "max_domains": 1,
         "checks_allowed": ["headers", "ssl", "exposure", "https_redirect"], "scan_third_party": False}


class FakeStore:
    def __init__(self):
        # domínio verificado no scanner (owner) mas o projeto Gate nasce NÃO verificado
        self.projects = [{"id": 5, "account_id": 10, "name": "Acme", "url": "https://acme.com.br",
                          "domain": "acme.com.br", "verified": False, "verification_method": None,
                          "config": {}, "invited_by": None}]
        self.owner_verified_domains = {"acme.com.br"}
        self.propagate_calls = 0
        self.hash_to_key = {}

    # --- auth --- #
    async def get_gate_api_key_by_hash(self, key_hash):
        return self.hash_to_key.get(key_hash)

    async def touch_gate_api_key(self, key_id):
        pass

    async def get_account_gate_fields(self, account_id):
        return {"id": account_id, "email": "d@acme.com", "account_type": "developer",
                "gate_plan_id": _FREE["id"], "gate_trial_started_at": None, "gate_trial_ends_at": None,
                "email_confirmed": True, "kyc_completed": True, "suspended": False}

    async def get_gate_plan(self, plan_id):
        return dict(_FREE) if plan_id == _FREE["id"] else None

    async def get_gate_plan_by_slug(self, slug):
        return dict(_FREE) if slug == "free" else None

    # --- item 2 --- #
    async def propagate_scanner_verification(self, account_id):
        self.propagate_calls += 1
        n = 0
        for p in self.projects:
            if p["account_id"] == account_id and not p["verified"] and p["domain"] in self.owner_verified_domains:
                p["verified"] = True
                p["verification_method"] = "scanner"
                n += 1
        return n

    async def list_gate_projects(self, account_id):
        return [dict(p) for p in self.projects if p["account_id"] == account_id]


def _register_key(store, full_key, account_id=10):
    store.hash_to_key[g._hash_key(full_key)] = {"id": 1, "account_id": account_id,
                                                 "key_prefix": full_key[:8], "name": "d",
                                                 "is_active": True, "last_used_at": None}


@pytest.fixture
def store(monkeypatch):
    s = FakeStore()
    monkeypatch.setenv("JWT_SECRET", SECRET)
    monkeypatch.setattr(m, "get_target_store", lambda: s)
    monkeypatch.setattr(g, "get_target_store", lambda: s)
    monkeypatch.setattr(m, "_cache", None)
    return s


@pytest.fixture
def client(store):
    return TestClient(m.app, raise_server_exceptions=False)


def test_list_propagates_scanner_verification(client, store):
    key = "KLM_propagate1"
    _register_key(store, key)
    r = client.get("/gate/projects", headers={"X-API-Key": key})
    assert r.status_code == 200
    proj = r.json()["projects"][0]
    assert proj["verified"] is True                      # propagado do scanner
    assert proj["verification_method"] == "scanner"
    assert store.propagate_calls == 1                    # chamado ANTES de listar


def test_list_does_not_propagate_unowned_domain(client, store):
    store.owner_verified_domains = set()   # domínio NÃO verificado no scanner
    key = "KLM_propagate2"
    _register_key(store, key)
    proj = client.get("/gate/projects", headers={"X-API-Key": key}).json()["projects"][0]
    assert proj["verified"] is False                     # nada a propagar
    assert store.propagate_calls == 1
