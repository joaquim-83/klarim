"""KL-161 — canal de direitos do titular (LGPD/DSAR): POST /lgpd/request.
Offline (store fake, TestClient, mailer mockado). Cobre validação, CPF opcional, rate limit e
o disparo dos 2 e-mails (confirmação ao titular + notificação ao operador)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import api.main as m


class _Store:
    def __init__(self):
        self.requests = []

    async def create_lgpd_request(self, *, req_type, name, email, cpf, description):
        rid = f"uuid-{len(self.requests) + 1}"
        self.requests.append({"id": rid, "type": req_type, "name": name, "email": email,
                              "cpf": cpf, "description": description})
        return rid


class _Mailer:
    def __init__(self):
        self.confirmations = []
        self.admin = []

    async def send_lgpd_confirmation(self, to_email, type_label, protocol, from_address):
        self.confirmations.append({"to": to_email, "label": type_label, "protocol": protocol,
                                   "from": from_address})
        return {"email_id": "conf-1"}

    async def send_lgpd_admin_notification(self, to_admin, from_address, *, type_label, name,
                                           email, cpf, description, protocol):
        self.admin.append({"to": to_admin, "from": from_address, "label": type_label,
                           "name": name, "email": email, "cpf": cpf, "protocol": protocol})
        return {"email_id": "admin-1"}


@pytest.fixture
def store():
    return _Store()


@pytest.fixture
def mailer():
    return _Mailer()


@pytest.fixture
def client(monkeypatch, store, mailer):
    monkeypatch.setattr(m, "get_target_store", lambda: store)
    monkeypatch.setattr(m, "_mailer", lambda: mailer)
    monkeypatch.setattr(m, "_email_enabled", lambda: True)
    m._lgpd_attempts.clear()   # rate limit no fallback in-memory (sem Redis nos testes)
    return TestClient(m.app, raise_server_exceptions=False)


_VALID = {"type": "exclusao", "name": "João Silva", "email": "joao@example.com",
          "description": "Solicito a exclusão dos meus dados pessoais."}


# --- 1. sucesso --- #
def test_valid_request_creates_record(client, store):
    r = client.post("/lgpd/request", json=_VALID)
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == "uuid-1"
    assert "15 dias úteis" in body["message"]
    assert body["confirmation_sent"] is True
    assert len(store.requests) == 1
    assert store.requests[0]["type"] == "exclusao"


# --- 2. sem e-mail → 422 --- #
def test_missing_email_422(client):
    payload = {**_VALID}
    payload.pop("email")
    assert client.post("/lgpd/request", json=payload).status_code == 422


def test_invalid_email_422(client):
    r = client.post("/lgpd/request", json={**_VALID, "email": "não-é-email"})
    assert r.status_code == 422


# --- 3. tipo inválido → 422 --- #
def test_invalid_type_422(client, store):
    r = client.post("/lgpd/request", json={**_VALID, "type": "hackear"})
    assert r.status_code == 422
    assert store.requests == []


def test_short_description_422(client):
    r = client.post("/lgpd/request", json={**_VALID, "description": "curto"})
    assert r.status_code == 422


# --- 4. CPF inválido → aviso (não bloqueio) --- #
def test_invalid_cpf_warns_not_blocks(client, store):
    r = client.post("/lgpd/request", json={**_VALID, "cpf": "111.111.111-11"})
    assert r.status_code == 200
    assert r.json()["cpf_warning"] is True
    assert store.requests[0]["cpf"] is None   # CPF inválido NÃO é gravado


def test_valid_cpf_stored_formatted(client, store):
    r = client.post("/lgpd/request", json={**_VALID, "cpf": "52998224725"})
    assert r.status_code == 200
    assert r.json()["cpf_warning"] is False
    assert store.requests[0]["cpf"] == "529.982.247-25"


# --- 5. rate limit: 4ª do mesmo e-mail → 429 --- #
def test_rate_limit_4th_same_email_429(client):
    for _ in range(3):
        assert client.post("/lgpd/request", json=_VALID).status_code == 200
    r = client.post("/lgpd/request", json=_VALID)
    assert r.status_code == 429
    assert r.headers.get("Retry-After")


def test_rate_limit_is_per_email(client):
    for _ in range(3):
        client.post("/lgpd/request", json=_VALID)
    # outro e-mail não é afetado pela cota do primeiro
    r = client.post("/lgpd/request", json={**_VALID, "email": "maria@example.com"})
    assert r.status_code == 200


# --- 6/7. e-mails disparados --- #
def test_confirmation_email_sent(client, mailer):
    client.post("/lgpd/request", json=_VALID)
    assert len(mailer.confirmations) == 1
    c = mailer.confirmations[0]
    assert c["to"] == "joao@example.com"
    assert c["label"] == "Exclusão"
    assert c["from"] == "privacidade@klarim.net"
    assert c["protocol"] == "uuid-1"


def test_admin_notification_sent(client, mailer):
    client.post("/lgpd/request", json=_VALID)
    assert len(mailer.admin) == 1
    a = mailer.admin[0]
    assert a["to"] == "klarimscan@gmail.com"
    assert a["label"] == "Exclusão"
    assert a["protocol"] == "uuid-1"


def test_no_email_when_disabled(client, monkeypatch, mailer, store):
    monkeypatch.setattr(m, "_email_enabled", lambda: False)
    r = client.post("/lgpd/request", json=_VALID)
    assert r.status_code == 200
    assert r.json()["confirmation_sent"] is False
    assert len(store.requests) == 1        # o registro é criado mesmo sem e-mail
    assert mailer.confirmations == []
