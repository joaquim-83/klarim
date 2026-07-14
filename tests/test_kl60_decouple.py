"""Testes do KL-60 — desacoplar scan do e-mail + contato → inbox. Offline.

Cobre: o discovery enfileira TODO site acessível (com e sem e-mail); site inacessível
vira `descartado` (sem enqueue); `POST /contact` grava no inbox com `source='contact_form'`
mesmo se o e-mail falhar; filtro `?source=` no inbox admin; e o script de backlog.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

import api.main as m
from api import auth_users
import discovery.worker as dw


# --------------------------------------------------------------------------- #
# 1-3. Discovery worker: _process_domain enfileira com/sem e-mail (KL-60)
# --------------------------------------------------------------------------- #
class _WStore:
    def __init__(self):
        self.registered = []

    async def register_target(self, url, domain, platform, sector, tier, email,
                              source="ct_log", status="discovered", confidence=0.0,
                              classification_source="auto"):
        self.registered.append({"url": url, "email": email, "status": status})
        return len(self.registered)  # id fictício


def _make_worker(monkeypatch, html, email):
    worker = object.__new__(dw.DiscoveryWorker)  # sem __init__ (evita CT/redis/DB)
    worker.store = _WStore()
    worker._enqueued = []

    async def _fake_fetch(url):
        return html

    async def _fake_enqueue(tid, url):
        worker._enqueued.append((tid, url))

    async def _fake_extract(_html, _url):
        return email

    worker._fetch_html = _fake_fetch
    worker._enqueue = _fake_enqueue
    monkeypatch.setattr(dw, "extract_email", _fake_extract)
    monkeypatch.setattr(dw, "detect_platform", lambda url, html: "wordpress")
    monkeypatch.setattr(dw, "classify_sector", lambda html, url: ("hotel", "standard", 0.9))
    return worker


def _stats():
    return {"no_contact": 0, "registered": 0, "enqueued": 0, "unreachable": 0}


def test_discovery_no_email_still_enqueues(monkeypatch):
    w = _make_worker(monkeypatch, html="<html>ok</html>", email=None)
    stats = _stats()
    asyncio.run(w._process_domain("semmail.com.br", stats))
    assert stats["enqueued"] == 1 and len(w._enqueued) == 1   # ENFILEIRADO mesmo sem e-mail
    assert stats["no_contact"] == 1
    assert w.store.registered[0]["status"] == "sem_contato"
    assert w.store.registered[0]["email"] is None


def test_discovery_with_email_enqueues_and_saves_email(monkeypatch):
    w = _make_worker(monkeypatch, html="<html>ok</html>", email="dono@hotel.com.br")
    stats = _stats()
    asyncio.run(w._process_domain("hotel.com.br", stats))
    assert stats["enqueued"] == 1 and len(w._enqueued) == 1
    assert stats["registered"] == 1
    assert w.store.registered[0]["status"] == "discovered"
    assert w.store.registered[0]["email"] == "dono@hotel.com.br"


def test_discovery_unreachable_is_descartado_no_enqueue(monkeypatch):
    w = _make_worker(monkeypatch, html=None, email=None)  # html None = inacessível
    stats = _stats()
    asyncio.run(w._process_domain("fora.com.br", stats))
    assert stats["unreachable"] == 1
    assert stats["enqueued"] == 0 and len(w._enqueued) == 0
    assert w.store.registered[0]["status"] == "descartado"


# --------------------------------------------------------------------------- #
# 8-9. Contato → inbox + filtro por source (TestClient + FakeStore)
# --------------------------------------------------------------------------- #
class _IStore:
    def __init__(self):
        self.inbox = []
        self.list_calls = []

    async def insert_inbox_message(self, msg):
        self.inbox.append(msg)
        return True

    async def list_inbox_messages(self, box="all", limit=25, offset=0, source=None, search=None):
        self.list_calls.append({"box": box, "source": source, "search": search})
        return [m2 for m2 in self.inbox
                if source is None or (m2.get("source") or "webhook") == source]

    async def inbox_unread_count(self):
        return len(self.inbox)


@pytest.fixture
def istore(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "k" * 64)
    monkeypatch.setattr(auth_users, "_secret", lambda: "k" * 64)
    s = _IStore()
    monkeypatch.setattr(m, "get_target_store", lambda: s)
    monkeypatch.setattr("discovery.store.get_target_store", lambda: s)
    # e-mail desligado → o contato NÃO depende do Resend (KL-60)
    monkeypatch.setattr(m, "_email_enabled", lambda: False)
    m._contact_attempts.clear()
    return s


@pytest.fixture
def client(istore):
    return TestClient(m.app, raise_server_exceptions=False)


def _admin_headers(monkeypatch):
    monkeypatch.setenv("ADMIN_USER", "op")
    monkeypatch.setenv("ADMIN_PASSWORD", "pw")
    return {"Authorization": f"Bearer {m._create_token('op')}"}


def test_contact_writes_to_inbox(client, istore):
    r = client.post("/contact", json={"name": "João", "email": "joao@x.com.br",
                                       "message": "Olá\nsegunda linha"})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert len(istore.inbox) == 1
    msg = istore.inbox[0]
    assert msg["source"] == "contact_form"
    assert msg["from_address"] == "joao@x.com.br" and msg["from_name"] == "João"
    assert msg["message_id"].startswith("contact-")
    assert "segunda linha" in msg["body_html"] and "<br>" in msg["body_html"]


def test_contact_saves_even_when_email_fails(client, istore, monkeypatch):
    # e-mail habilitado mas o envio explode → a mensagem ainda fica no inbox
    monkeypatch.setattr(m, "_email_enabled", lambda: True)

    class _Boom:
        async def send_contact(self, *a, **k):
            raise RuntimeError("resend loop")

    monkeypatch.setattr(m, "_mailer", lambda: _Boom())
    r = client.post("/contact", json={"email": "a@b.com.br", "message": "oi"})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert len(istore.inbox) == 1 and istore.inbox[0]["source"] == "contact_form"


def test_contact_bad_email_422(client, istore):
    assert client.post("/contact", json={"email": "nope", "message": "oi"}).status_code == 422
    assert len(istore.inbox) == 0


def test_inbox_source_filter_forwarded(client, istore, monkeypatch):
    istore.inbox = [
        {"id": 1, "source": "webhook", "subject": "email"},
        {"id": 2, "source": "contact_form", "subject": "form"},
    ]
    h = _admin_headers(monkeypatch)
    body = client.get("/admin/inbox?source=contact_form", headers=h).json()
    assert body["source"] == "contact_form"
    assert len(body["messages"]) == 1 and body["messages"][0]["id"] == 2
    # webhook exclui o contato
    body2 = client.get("/admin/inbox?source=webhook", headers=h).json()
    assert [x["id"] for x in body2["messages"]] == [1]
    # source inválido → None (todas)
    body3 = client.get("/admin/inbox?source=xxx", headers=h).json()
    assert body3["source"] is None and len(body3["messages"]) == 2


def test_inbox_requires_admin(client):
    assert client.get("/admin/inbox?source=contact_form").status_code == 401


# --------------------------------------------------------------------------- #
# 10. Script de backlog: enfileira sem_contato sem scan (fake store + redis)
# --------------------------------------------------------------------------- #
def test_enqueue_unscanned_script(monkeypatch):
    import scripts.enqueue_unscanned as eq

    class _EStore:
        async def ensure_schema(self):
            pass

        async def count_unscanned_targets(self, status="sem_contato"):
            return 3

        async def list_unscanned_targets(self, limit=500, status="sem_contato"):
            return [{"id": 1, "url": "https://a.com.br"},
                    {"id": 2, "url": "https://b.com.br"}]

    class _FakeRedis:
        def __init__(self):
            self.pushed = []

        async def rpush(self, key, val):
            self.pushed.append((key, val))

        async def aclose(self):
            pass

    fake_redis = _FakeRedis()
    monkeypatch.setattr(eq, "get_target_store", lambda: _EStore())

    async def _mk():
        return fake_redis

    monkeypatch.setattr(eq, "_make_redis", _mk)
    asyncio.run(eq.run(limit=500, status="sem_contato", dry_run=False))
    assert len(fake_redis.pushed) == 2
    import json
    payload = json.loads(fake_redis.pushed[0][1])
    assert payload["target_id"] == 1 and payload["source"] == "discovery"
    assert payload["url"] == "https://a.com.br"


def test_enqueue_unscanned_dry_run(monkeypatch):
    import scripts.enqueue_unscanned as eq

    class _EStore:
        async def ensure_schema(self):
            pass

        async def count_unscanned_targets(self, status="sem_contato"):
            return 1

        async def list_unscanned_targets(self, limit=500, status="sem_contato"):
            return [{"id": 9, "url": "https://c.com.br"}]

    called = {"mk": 0}

    async def _mk():
        called["mk"] += 1
        return None

    monkeypatch.setattr(eq, "get_target_store", lambda: _EStore())
    monkeypatch.setattr(eq, "_make_redis", _mk)
    asyncio.run(eq.run(limit=500, status="sem_contato", dry_run=True))
    assert called["mk"] == 0   # dry-run nunca conecta no Redis
