"""KL-56 — gestão de landing + paginação/filtro de scans + inbox — offline.

TestClient + FakeStore (mesmo padrão de test_target_edit.py). Sem rede, sem DB.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

import api.main as m
from mcp_server.tools import targets as targets_tools


class FakeStore:
    def __init__(self):
        self.calls = []
        self.inbox = {}          # id -> msg
        self.inbox_seen = set()  # message_ids já inseridos (dedup)
        self.scan_kwargs = None
        self.profile_visible = True  # controla o get_site_profile

    # --- perfil / landing ---------------------------------------------------
    async def update_site_profile_fields(self, target_id, fields):
        self.calls.append(("update_profile", target_id, dict(fields)))
        if target_id == 999:
            return None
        tags = fields.get("tags")
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]
        return {"target_id": target_id, "edited_by_admin": True,
                "description": fields.get("description"),
                "business_type": fields.get("business_type"),
                "company_name": fields.get("company_name"),
                "tags": tags}

    async def set_profile_visibility(self, target_id, visible):
        self.calls.append(("visibility", target_id, visible))
        if target_id == 999:
            return None
        return {"target_id": target_id, "public_visible": bool(visible)}

    # --- public profile -----------------------------------------------------
    async def get_target_by_domain(self, domain):
        return {"id": 1, "url": f"https://{domain}", "domain": domain,
                "status": "scanned", "last_scan_score": 88, "sector": "hotel",
                "platform": "wordpress", "last_scan_at": None}

    async def get_site_profile(self, target_id):
        return {"description": "Hotel boutique", "public_visible": self.profile_visible,
                "tags": ["hotel"], "business_type": "Hotel"}

    async def get_target_classifications(self, target_id):
        return []

    async def sector_avg_score(self, sector):
        return {"avg": 70.0, "count": 10}

    async def global_avg_score(self):
        return {"avg": 65.0, "count": 100}

    async def list_scans(self, target_id=None, score_min=None, score_max=None,
                         source=None, limit=50, distinct_url=False, offset=0,
                         from_date=None, to_date=None):
        self.scan_kwargs = {"offset": offset, "from_date": from_date,
                            "to_date": to_date, "limit": limit, "target_id": target_id}
        if target_id is not None:  # chamada do public_profile (semáforo)
            return [{"id": 1, "semaphore": "verde"}]
        base = offset + 1  # offset 0 -> id 1; offset 25 -> id 26 (páginas distintas)
        return [{"id": base, "url": f"https://s{base}.com.br", "score": 90,
                 "semaphore": "verde", "pass_count": 1, "fail_count": 0,
                 "inconclusive_count": 0, "source": "public", "scanned_at": None}]

    async def list_public_profile_domains(self, limit=50000):
        return [{"domain": "a.com.br", "last_scan_at": None}]

    # --- inbox --------------------------------------------------------------
    async def insert_inbox_message(self, msg):
        mid = msg.get("message_id")
        if mid in self.inbox_seen:
            return False  # dedup (UNIQUE)
        self.inbox_seen.add(mid)
        new_id = len(self.inbox) + 1
        self.inbox[new_id] = {"id": new_id, "is_read": False, "is_starred": False,
                              "is_archived": False, **msg}
        return True

    async def list_inbox_messages(self, box="all", limit=25, offset=0, source=None, search=None):
        # espelha o store real: all/unread/starred escondem arquivadas.
        rows = list(self.inbox.values())
        if box == "unread":
            rows = [r for r in rows if not r["is_read"] and not r["is_archived"]]
        elif box == "starred":
            rows = [r for r in rows if r["is_starred"] and not r["is_archived"]]
        elif box == "archived":
            rows = [r for r in rows if r["is_archived"]]
        else:  # all -> caixa de entrada (não-arquivadas)
            rows = [r for r in rows if not r["is_archived"]]
        if source in ("webhook", "contact_form"):  # KL-60
            rows = [r for r in rows if (r.get("source") or "webhook") == source]
        if search:  # fix MCP: busca por texto
            q = search.lower()
            rows = [r for r in rows if q in (r.get("subject") or "").lower()
                    or q in (r.get("from_address") or "").lower()]
        return rows[offset:offset + limit]

    async def get_inbox_message(self, msg_id):
        return self.inbox.get(msg_id)

    async def set_inbox_read(self, msg_id, read=True):
        r = self.inbox.get(msg_id)
        if not r:
            return None
        r["is_read"] = read
        return r

    async def toggle_inbox_star(self, msg_id):
        r = self.inbox.get(msg_id)
        if not r:
            return None
        r["is_starred"] = not r["is_starred"]
        return r

    async def set_inbox_archived(self, msg_id, archived=True):
        r = self.inbox.get(msg_id)
        if not r:
            return None
        r["is_archived"] = archived
        return r

    async def inbox_unread_count(self):
        return sum(1 for r in self.inbox.values()
                   if not r["is_read"] and not r["is_archived"])


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("ADMIN_USER", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "s3nha-forte")
    monkeypatch.setenv("JWT_SECRET", "x" * 64)
    monkeypatch.setenv("HOSTINGER_WEBHOOK_TOKEN", "tok-abc-123")
    m._login_attempts.clear()  # rate limit de login é global — zera por teste
    store = FakeStore()
    monkeypatch.setattr(m, "get_target_store", lambda: store)
    c = TestClient(m.app, raise_server_exceptions=False)
    c._store = store
    return c


def _auth(client):
    # loga uma vez por cliente (evita estourar o rate limit de 5 logins/min)
    if not getattr(client, "_tok", None):
        client._tok = client.post(
            "/auth/login",
            json={"username": "admin", "password": "s3nha-forte"}).json()["token"]
    return {"Authorization": f"Bearer {client._tok}"}


# ============================ 1. Gestão de landing ========================== #

def test_01_put_profile_updates_description_and_tags(client):
    r = client.put("/targets/5/profile",
                   json={"description": "Nova desc", "tags": "hotel, spa, café"},
                   headers=_auth(client))
    assert r.status_code == 200
    body = r.json()
    assert body["edited_by_admin"] is True
    assert body["description"] == "Nova desc"
    assert body["tags"] == ["hotel", "spa", "café"]
    assert client._store.calls[-1][0] == "update_profile"


def test_put_profile_protected():
    c = TestClient(m.app, raise_server_exceptions=False)
    assert c.put("/targets/5/profile", json={"description": "x"}).status_code == 401


def test_02_visibility_off_hides_public_profile(client):
    # desliga a landing
    r = client.patch("/targets/1/profile/visibility", json={"visible": False},
                     headers=_auth(client))
    assert r.status_code == 200 and r.json()["public_visible"] is False
    # o get_site_profile passa a devolver public_visible=False
    client._store.profile_visible = False
    pub = client.get("/public/profile/exemplo.com.br")
    assert pub.status_code == 200 and pub.json()["status"] == "not_found"


def test_03_visibility_on_shows_public_profile(client):
    client._store.profile_visible = True
    pub = client.get("/public/profile/exemplo.com.br")
    assert pub.status_code == 200 and pub.json()["status"] == "ok"
    assert pub.json()["profile"]["description"] == "Hotel boutique"


def test_04_sitemap_sql_excludes_hidden(monkeypatch):
    # Store-level: a query do sitemap tem o guard public_visible.
    from discovery.store import TargetStore

    class Cur:
        def __init__(self):
            self.sql = None
            self.description = [("domain",), ("last_scan_at",)]
        def execute(self, sql, params=None):
            self.sql = sql
        def fetchall(self):
            return []

    store = TargetStore()
    cur = Cur()
    monkeypatch.setattr(store, "_run", lambda fn: fn(cur))
    asyncio.run(store.list_public_profile_domains())
    assert "public_visible" in cur.sql


def test_05_mcp_toggle_visibility(client):
    # a tool MCP chama api.main.api_profile_visibility (get_target_store já é fake)
    res = asyncio.run(targets_tools.toggle_profile_visibility(7, False))
    assert res["public_visible"] is False
    assert ("visibility", 7, False) in client._store.calls


def test_mcp_update_site_profile(client):
    res = asyncio.run(targets_tools.update_site_profile(7, description="via mcp",
                                                        tags=["a", "b"]))
    assert res["edited_by_admin"] is True and res["description"] == "via mcp"


# ============================ 2. Scans: paginação + data =================== #

def test_06_scans_offset_pagination_returns_new_rows(client):
    p1 = client.get("/scans?limit=25&offset=0", headers=_auth(client)).json()
    p2 = client.get("/scans?limit=25&offset=25", headers=_auth(client)).json()
    assert p1["scans"][0]["id"] == 1
    assert p2["scans"][0]["id"] == 26           # página 2 != página 1
    assert p1["scans"][0]["id"] != p2["scans"][0]["id"]


def test_07_scans_date_filter_forwarded(client):
    client.get("/scans?from_date=2026-07-12&to_date=2026-07-13", headers=_auth(client))
    assert client._store.scan_kwargs["from_date"] == "2026-07-12"
    assert client._store.scan_kwargs["to_date"] == "2026-07-13"


def test_08_scans_offset_forwarded_to_store(client):
    client.get("/scans?limit=25&offset=50", headers=_auth(client))
    assert client._store.scan_kwargs["offset"] == 50


# ============================ 3. Inbox (webhook + admin) =================== #

_AGENTMAIL = {
    "type": "event", "event_type": "message.received", "event_id": "evt_1",
    "message": {
        "message_id": "<abc@agentmail.to>", "from": "João <joao@hotel.com.br>",
        "to": ["scan@klarim.net"], "subject": "Dúvida sobre o Klarim",
        "preview": "Gostaria de saber…", "text": "Gostaria de saber mais.",
        "html": "<p>Gostaria de saber mais.</p>", "timestamp": "2026-07-13T14:30:00Z",
    },
}


def test_parse_inbox_payload_agentmail_shape():
    msg = m.parse_inbox_payload(_AGENTMAIL)
    assert msg["message_id"] == "<abc@agentmail.to>"
    assert msg["from_address"] == "joao@hotel.com.br" and msg["from_name"] == "João"
    assert msg["subject"] == "Dúvida sobre o Klarim"
    assert msg["received_at"] is not None


def test_parse_inbox_payload_flat_shape():
    msg = m.parse_inbox_payload({"from": "a@b.com.br", "subject": "Oi", "text": "corpo"})
    assert msg["from_address"] == "a@b.com.br" and msg["subject"] == "Oi"


def test_parse_inbox_payload_ignores_non_message_event():
    assert m.parse_inbox_payload({"event_type": "message.delivered", "send": {}}) is None


def test_parse_inbox_payload_unwraps_data_wrapper():
    # KL-58: alguns webhooks embrulham a mensagem em data/payload/body/email.
    msg = m.parse_inbox_payload({"data": {"from": "x@y.com.br", "subject": "Wrap", "text": "c"}})
    assert msg and msg["from_address"] == "x@y.com.br" and msg["subject"] == "Wrap"


def test_parse_inbox_payload_accepts_list():
    # KL-58: webhook que manda uma lista de eventos → usa o primeiro.
    msg = m.parse_inbox_payload([{"from": "a@b.com.br", "subject": "Lista", "text": "c"}])
    assert msg and msg["subject"] == "Lista"


def test_09_webhook_valid_token_stores(client):
    r = client.post("/email/webhook", json=_AGENTMAIL,
                    headers={"Authorization": "Bearer tok-abc-123"})
    assert r.status_code == 200 and r.json()["stored"] is True
    assert len(client._store.inbox) == 1


def test_09b_webhook_valid_token_via_query(client):
    r = client.post("/email/webhook?token=tok-abc-123", json=_AGENTMAIL)
    assert r.status_code == 200 and r.json()["stored"] is True


def test_10_webhook_invalid_token_401(client):
    r = client.post("/email/webhook", json=_AGENTMAIL,
                    headers={"Authorization": "Bearer WRONG"})
    assert r.status_code == 401
    assert len(client._store.inbox) == 0


def test_10b_webhook_public_not_admin_protected():
    # sem HOSTINGER_WEBHOOK_TOKEN configurado → fail-closed (401), NÃO 401 de JWT
    assert m._is_protected("/email/webhook") is False
    assert m._is_protected("/email/test") is True


def test_11_webhook_duplicate_message_id_ignored(client):
    h = {"Authorization": "Bearer tok-abc-123"}
    r1 = client.post("/email/webhook", json=_AGENTMAIL, headers=h)
    r2 = client.post("/email/webhook", json=_AGENTMAIL, headers=h)
    assert r1.json()["stored"] is True
    assert r2.json()["stored"] is False           # dedup por message_id
    assert len(client._store.inbox) == 1


def _seed(client, n=3):
    h = {"Authorization": "Bearer tok-abc-123"}
    for i in range(n):
        payload = {"message": {"message_id": f"<m{i}@x>", "from": f"u{i}@x.com.br",
                               "subject": f"Assunto {i}", "text": "corpo",
                               "timestamp": "2026-07-13T10:00:00Z"}}
        client.post("/email/webhook", json=payload, headers=h)


def test_12_inbox_list(client):
    _seed(client, 3)
    r = client.get("/admin/inbox", headers=_auth(client))
    assert r.status_code == 200
    assert r.json()["count"] == 3 and r.json()["box"] == "all"


def test_12b_inbox_list_protected():
    c = TestClient(m.app, raise_server_exceptions=False)
    assert c.get("/admin/inbox").status_code == 401


def test_13_inbox_mark_read(client):
    _seed(client, 1)
    r = client.post("/admin/inbox/1/read", headers=_auth(client))
    assert r.status_code == 200 and r.json()["is_read"] is True


def test_13b_inbox_get_marks_read(client):
    _seed(client, 1)
    r = client.get("/admin/inbox/1", headers=_auth(client))
    assert r.status_code == 200 and r.json()["is_read"] is True


def test_14_inbox_unread_count(client):
    _seed(client, 3)
    assert client.get("/admin/inbox/unread-count",
                      headers=_auth(client)).json()["unread"] == 3
    client.post("/admin/inbox/1/read", headers=_auth(client))
    assert client.get("/admin/inbox/unread-count",
                      headers=_auth(client)).json()["unread"] == 2


def test_15_inbox_star_and_archive(client):
    _seed(client, 1)
    star = client.post("/admin/inbox/1/star", headers=_auth(client))
    assert star.json()["is_starred"] is True
    arch = client.post("/admin/inbox/1/archive", headers=_auth(client))
    assert arch.json()["is_archived"] is True
    # arquivada some da caixa "all" mas aparece em "archived"
    assert client.get("/admin/inbox", headers=_auth(client)).json()["count"] == 0
    assert client.get("/admin/inbox?box=archived", headers=_auth(client)).json()["count"] == 1
