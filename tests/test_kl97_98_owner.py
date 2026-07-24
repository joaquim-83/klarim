"""KL-97 + KL-98 — gestão do dono no dashboard: monitoramento (vigílias), notificações,
perfil público, selo. Ownership/nível, sanitização, plan gating, preservação da edição do
dono contra a IA. Offline (FakeStore). O SQL é validado na VM.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import api.main as m
from api import auth_users
from scanner.ai_enrichment import merge_ai_into_profile

_DEFAULT_PREFS = {"bulletin_frequency": None, "bulletin_hour": None,
                  "notify_vigilia": True, "notify_bulletin": True, "notify_news": False}


class FakeStore:
    def __init__(self):
        self.users, self.links, self.targets = {}, {}, {}
        self.vigilias, self.profiles, self.prefs = {}, {}, {}

    async def get_user_by_id(self, uid):
        return self.users.get(int(uid))

    async def get_user_site(self, uid, tid):
        return self.links.get((int(uid), int(tid)))

    async def get_target(self, tid):
        return self.targets.get(int(tid))

    async def get_target_by_domain(self, d):
        for t in self.targets.values():
            if t["domain"] == (d or "").lower().strip():
                return t
        return None

    async def get_latest_scan_full(self, tid):
        return {"score": 78, "semaphore": "amarelo", "checks_json": {}, "scanned_at": None}

    async def list_site_vigilias(self, uid, domain):
        out = []
        for (u, d, tipo), v in self.vigilias.items():
            if u == int(uid) and d == domain:
                out.append({"tipo": tipo, "enabled": v["enabled"],
                            "last_status": v.get("last_status", "ok"),
                            "last_check_at": None, "next_check_at": None,
                            "last_data": {"threshold": v["threshold"]} if v.get("threshold") else {}})
        return out

    async def set_vigilia_enabled(self, uid, domain, tipo, enabled, threshold=None, next_check_at=None):
        v = self.vigilias.setdefault((int(uid), domain, tipo), {})
        v["enabled"] = enabled
        if threshold is not None:
            v["threshold"] = threshold

    async def get_notification_prefs(self, uid):
        return dict(self.prefs.get(int(uid), _DEFAULT_PREFS))

    async def update_notification_prefs(self, uid, fields):
        p = self.prefs.setdefault(int(uid), dict(_DEFAULT_PREFS))
        p.update(fields)
        return dict(p)

    async def get_site_profile(self, tid):
        return self.profiles.get(int(tid))

    async def update_site_profile_fields(self, tid, fields, actor="admin"):
        p = self.profiles.setdefault(int(tid), {"target_id": tid})
        touched = []
        for k, v in fields.items():
            if k == "clear_fields":
                for c in v:
                    p[c] = None
                    touched.append(c)
                continue
            p[k] = v
            touched.append(k)
        if actor == "owner":
            p["edited_by_owner"] = True
            p["owner_edited_fields"] = sorted(set((p.get("owner_edited_fields") or []) + touched))
        else:
            p["edited_by_admin"] = True
        return dict(p)

    async def set_seal_config(self, tid, enabled, style=None):
        p = self.profiles.setdefault(int(tid), {"target_id": tid})
        p["seal_enabled"] = bool(enabled)
        if style:
            p["seal_style"] = style
        return dict(p)

    async def set_profile_visibility(self, tid, visible):
        p = self.profiles.setdefault(int(tid), {"target_id": tid})
        p["public_visible"] = bool(visible)
        return dict(p)


@pytest.fixture
def store():
    s = FakeStore()
    s.users[10] = {"id": 10, "email": "dono@site.com.br", "account_level": 3,
                   "is_active": True, "plan": "pro"}
    s.users[20] = {"id": 20, "email": "monitor@x.com.br", "account_level": 2,
                   "is_active": True, "plan": "pro"}
    s.targets[1] = {"id": 1, "domain": "site.com.br", "url": "https://site.com.br",
                    "last_scan_score": 78, "owner_verified": True}
    s.targets[2] = {"id": 2, "domain": "outro.com.br", "url": "https://outro.com.br",
                    "last_scan_score": 50, "owner_verified": False}
    s.links[(10, 1)] = {"id": 1, "user_id": 10, "target_id": 1, "is_owner": True}
    s.links[(20, 2)] = {"id": 2, "user_id": 20, "target_id": 2, "is_owner": True}  # nível 2
    s.profiles[1] = {"target_id": 1, "company_name": "Antiga", "seal_enabled": True,
                     "seal_style": "badge", "public_visible": True}
    return s


@pytest.fixture
def client(store, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "x" * 64)
    monkeypatch.setattr(m, "get_target_store", lambda: store)
    monkeypatch.setattr("discovery.store.get_target_store", lambda: store)  # auth_users usa este

    async def fake_allowed(uid):   # plano Pro: core + uptime (sem changes/phishing)
        return ["ssl", "domain", "score", "email", "reputation", "uptime"]
    monkeypatch.setattr(m, "_vigilia_allowed_types", fake_allowed)
    return TestClient(m.app, raise_server_exceptions=False)


def _hdr(store, uid):
    return {"Authorization": f"Bearer {auth_users.create_user_token(store.users[uid])}"}


# --------------------------- KL-97: monitoramento ------------------------- #

def test_monitoring_get_lists_vigilias_with_configurability(client, store):
    r = client.get("/account/sites/1/monitoring", headers=_hdr(store, 10))
    assert r.status_code == 200
    d = r.json()
    assert d["site"]["domain"] == "site.com.br" and d["site"]["semaphore"] == "amarelo"
    by = {v["tipo"]: v for v in d["vigilias"]}
    assert by["ssl"]["configurable"] is True                       # plano Pro habilita
    assert by["changes"]["configurable"] is False                  # só Agency
    assert by["changes"]["requires_plan"] == "agency"


def test_monitoring_put_toggles_and_threshold(client, store):
    r = client.put("/account/sites/1/monitoring", headers=_hdr(store, 10),
                   json={"vigilias": {"ssl": {"enabled": True},
                                      "score": {"enabled": True, "threshold": 10}}})
    assert r.status_code == 200
    assert store.vigilias[(10, "site.com.br", "ssl")]["enabled"] is True
    assert store.vigilias[(10, "site.com.br", "score")]["threshold"] == 10


def test_monitoring_put_plan_gated_403(client, store):
    # 'changes' é Agency; o usuário Pro não pode habilitar → 403 requires_plan
    r = client.put("/account/sites/1/monitoring", headers=_hdr(store, 10),
                   json={"vigilias": {"changes": {"enabled": True}}})
    assert r.status_code == 403 and r.json()["detail"]["requires_plan"] == "agency"


def test_monitoring_not_owner_404(client, store):
    # user 20 não tem vínculo com o target 1 → 404 (nunca vaza)
    r = client.get("/account/sites/1/monitoring", headers=_hdr(store, 20))
    assert r.status_code == 404


def test_monitoring_requires_auth(client):
    assert client.get("/account/sites/1/monitoring").status_code == 401


# --------------------------- KL-97: notificações -------------------------- #

def test_notification_prefs_defaults(client, store):
    r = client.get("/account/notification-preferences", headers=_hdr(store, 10))
    assert r.status_code == 200
    assert r.json() == {"bulletin_frequency": None, "bulletin_hour": None,
                        "notify_vigilia": True, "notify_bulletin": True, "notify_news": False}


def test_notification_prefs_update(client, store):
    r = client.put("/account/notification-preferences", headers=_hdr(store, 10),
                   json={"bulletin_frequency": "monthly", "notify_news": True})
    assert r.status_code == 200
    d = r.json()
    assert d["bulletin_frequency"] == "monthly" and d["notify_news"] is True


def test_notification_prefs_invalid_frequency_422(client, store):
    r = client.put("/account/notification-preferences", headers=_hdr(store, 10),
                   json={"bulletin_frequency": "hourly"})
    assert r.status_code == 422


# --------------------------- KL-98: perfil -------------------------------- #

def test_profile_update_requires_level3(client, store):
    # user 20 é nível 2 → 403 insufficient_level (o gate de nível vem antes da posse)
    r = client.put("/account/sites/2/profile", headers=_hdr(store, 20),
                   json={"company_name": "X"})
    assert r.status_code == 403 and r.json()["detail"]["error"] == "insufficient_level"


def test_profile_update_owner_sanitizes_and_tracks_fields(client, store):
    r = client.put("/account/sites/1/profile", headers=_hdr(store, 10), json={
        "company_name": "  Minha <b>Empresa</b> Ltda  ",
        "description": "<script>alert(1)</script>Boa descrição",
        "tags": ["loja", "roupas", "moda"]})
    assert r.status_code == 200
    p = store.profiles[1]
    assert p["company_name"] == "Minha Empresa Ltda"           # HTML removido, trim
    assert "script" not in p["description"] and "Boa descrição" in p["description"]
    assert p["tags"] == ["loja", "roupas", "moda"]
    assert p["edited_by_owner"] is True
    assert set(p["owner_edited_fields"]) >= {"company_name", "description", "tags"}


def test_profile_update_invalid_cnpj_422(client, store):
    r = client.put("/account/sites/1/profile", headers=_hdr(store, 10),
                   json={"cnpj": "123"})
    assert r.status_code == 422


def test_profile_update_invalid_phone_422(client, store):
    r = client.put("/account/sites/1/profile", headers=_hdr(store, 10),
                   json={"phone": "abc"})
    assert r.status_code == 422


def test_visibility_toggle(client, store):
    r = client.put("/account/sites/1/visibility", headers=_hdr(store, 10),
                   json={"public_visible": False})
    assert r.status_code == 200 and r.json()["public_visible"] is False
    assert store.profiles[1]["public_visible"] is False


# --------------------------- KL-98: selo ---------------------------------- #

def test_seal_get_returns_variants(client, store):
    r = client.get("/account/sites/1/seal", headers=_hdr(store, 10))
    assert r.status_code == 200
    d = r.json()
    assert d["enabled"] is True and d["style"] == "badge" and d["verified"] is True
    assert set(d["variants"]) == {"badge", "footer", "floating"}
    assert 'data-domain="site.com.br"' in d["variants"]["badge"]["embed_code"]
    assert 'data-style="floating"' in d["variants"]["floating"]["embed_code"]


def test_seal_put_configures(client, store):
    r = client.put("/account/sites/1/seal", headers=_hdr(store, 10),
                   json={"enabled": False, "style": "footer"})
    assert r.status_code == 200
    assert store.profiles[1]["seal_enabled"] is False
    assert store.profiles[1]["seal_style"] == "footer"


def test_seal_public_endpoint_reflects_enabled(client, store):
    store.profiles[1]["seal_enabled"] = False
    j = client.get("/seal/site.com.br").json()
    assert j["found"] is True and j["enabled"] is False   # widget esconde


# --------------------------- IA preservation ------------------------------ #

def test_merge_ai_skips_owner_edited_fields():
    # o dono editou company_name e depois o limpou; a IA NÃO pode repreencher.
    profile = {"company_name": None, "description": None,
               "owner_edited_fields": ["company_name"]}
    ai = {"company_name": "Nome da IA", "description": "Desc da IA"}
    changed = merge_ai_into_profile(profile, ai)
    assert profile["company_name"] is None                # protegido
    assert profile["description"] == "Desc da IA"         # não protegido → preenche
    assert "company_name" not in changed and "description" in changed


def test_merge_ai_skips_owner_edited_tags():
    profile = {"tags": [], "owner_edited_fields": ["tags"]}
    ai = {"tags": ["a", "b"]}
    merge_ai_into_profile(profile, ai)
    assert profile["tags"] == []                          # tags do dono preservadas
