"""KL-163 (Prompt 2/2) — endereço ESTRUTURADO no KYC (CEP + ViaCEP no front; JSONB no back).

Cobre: `POST /account/kyc` com endereço objeto (→ address_data JSONB) vs string (→ address TEXT
legado), validação de CEP/UF/campos (422), `_kyc_complete` com os dois formatos, e o helper
`_city_state_from_address` (cidade/UF no PDF). Offline (FakeStore, engine não é chamada aqui)."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import api.main as m
from api import gate as g
from api import auth_users

SECRET = "k" * 64
VALID_CPF = "529.982.247-25"
FULL_ADDR = {"cep": "80010-000", "street": "Rua XV de Novembro", "number": "123",
             "complement": "Sala 4", "neighborhood": "Centro", "city": "Curitiba", "state": "PR"}


def _now():
    return datetime.now(timezone.utc)


_FREE = {"id": 1, "name": "Free", "slug": "free", "scans_per_day": 5, "max_domains": 1,
         "checks_allowed": ["headers"], "scan_third_party": False}


class FakeStore:
    def __init__(self):
        self.users = {
            10: {"id": 10, "email": "dev@acme.com", "is_active": True, "account_type": "developer",
                 "gate_plan_id": 1, "gate_trial_ends_at": None, "email_confirmed": True,
                 "kyc_completed": False, "kyc_completed_at": None, "suspended": False,
                 "cpf": None, "address": None, "address_data": None, "phone": None,
                 "phone_verified": False},
        }
        self.kyc_calls = []

    async def get_user_by_id(self, uid):
        return self.users.get(int(uid))

    async def get_account_gate_fields(self, account_id):
        u = self.users.get(int(account_id))
        if not u:
            return None
        return {k: u.get(k) for k in ("id", "email", "account_type", "gate_plan_id",
                                      "gate_trial_ends_at", "email_confirmed", "kyc_completed",
                                      "kyc_completed_at", "suspended", "cpf", "address",
                                      "address_data", "phone", "phone_verified")}

    async def is_cpf_taken(self, cpf, exclude_account_id=None):
        return False

    async def update_user_kyc(self, account_id, cpf, address, phone, phone_verified,
                              kyc_completed, kyc_completed_at=None, address_data=None):
        self.kyc_calls.append({"cpf": cpf, "address": address, "address_data": address_data,
                               "phone": phone, "kyc_completed": kyc_completed})
        u = self.users.get(int(account_id))
        if u:
            u.update(cpf=cpf, address=address, address_data=address_data, phone=phone,
                     kyc_completed=kyc_completed)
        return True

    async def get_gate_plan(self, pid):
        return dict(_FREE) if pid == 1 else None

    async def get_gate_plan_by_slug(self, slug):
        return dict(_FREE) if slug == "free" else None

    async def insert_gate_audit(self, account_id, action, **kw):
        pass


def _cookie(user):
    return {auth_users.USER_COOKIE: auth_users.create_user_token(user)}


@pytest.fixture
def store(monkeypatch):
    s = FakeStore()
    monkeypatch.setenv("JWT_SECRET", SECRET)
    monkeypatch.setattr(m, "get_target_store", lambda: s)
    monkeypatch.setattr(g, "get_target_store", lambda: s)
    import discovery.store as ds
    monkeypatch.setattr(ds, "get_target_store", lambda: s)
    monkeypatch.setattr(m, "_cache", None)
    monkeypatch.setattr(g, "_scan_redis", lambda: None)
    return s


@pytest.fixture
def client(store):
    return TestClient(m.app, raise_server_exceptions=False)


# --------------------------------------------------------------------------- #
# Endpoint POST /account/kyc
# --------------------------------------------------------------------------- #

def test_kyc_address_object_goes_to_address_data(client, store):
    r = client.post("/account/kyc", cookies=_cookie(store.users[10]),
                    json={"cpf": VALID_CPF, "address": FULL_ADDR, "phone": "+55 41 99999-9999"})
    assert r.status_code == 200
    assert r.json()["kyc_completed"] is True
    call = store.kyc_calls[-1]
    assert call["address_data"] == {**FULL_ADDR, "cep": "80010-000", "state": "PR"}
    assert call["address"] is None   # o TEXT legado NÃO é usado no formato estruturado


def test_kyc_address_string_legacy_goes_to_text(client, store):
    r = client.post("/account/kyc", cookies=_cookie(store.users[10]),
                    json={"cpf": VALID_CPF, "address": "Rua XV de Novembro, 123, Centro, Curitiba/PR",
                          "phone": "11999998888"})
    assert r.status_code == 200
    assert r.json()["kyc_completed"] is True
    call = store.kyc_calls[-1]
    assert call["address"] == "Rua XV de Novembro, 123, Centro, Curitiba/PR"
    assert call["address_data"] is None


def test_kyc_cep_invalid_422(client, store):
    r = client.post("/account/kyc", cookies=_cookie(store.users[10]),
                    json={"cpf": VALID_CPF, "address": {**FULL_ADDR, "cep": "123"}, "phone": "11999"})
    assert r.status_code == 422
    assert "CEP" in r.json()["detail"]


def test_kyc_uf_invalid_422(client, store):
    r = client.post("/account/kyc", cookies=_cookie(store.users[10]),
                    json={"cpf": VALID_CPF, "address": {**FULL_ADDR, "state": "XX"}, "phone": "11999"})
    assert r.status_code == 422
    assert "UF" in r.json()["detail"]


def test_kyc_missing_number_422(client, store):
    addr = {**FULL_ADDR}
    addr.pop("number")
    r = client.post("/account/kyc", cookies=_cookie(store.users[10]),
                    json={"cpf": VALID_CPF, "address": addr, "phone": "11999"})
    assert r.status_code == 422
    assert "number" in r.json()["detail"]


def test_kyc_cep_normalized_from_digits(client, store):
    """CEP sem traço (8 dígitos) é normalizado para 00000-000."""
    r = client.post("/account/kyc", cookies=_cookie(store.users[10]),
                    json={"cpf": VALID_CPF, "address": {**FULL_ADDR, "cep": "80010000"},
                          "phone": "11999"})
    assert r.status_code == 200
    assert store.kyc_calls[-1]["address_data"]["cep"] == "80010-000"


# --------------------------------------------------------------------------- #
# _kyc_complete (puro)
# --------------------------------------------------------------------------- #

def test_kyc_complete_with_address_data():
    assert g._kyc_complete(VALID_CPF, FULL_ADDR, "11999", True) is True


def test_kyc_complete_with_legacy_text():
    assert g._kyc_complete(VALID_CPF, "Rua Exemplo 123 Centro", "11999", True) is True


def test_kyc_complete_empty_address_false():
    assert g._kyc_complete(VALID_CPF, "", "11999", True) is False
    assert g._kyc_complete(VALID_CPF, {}, "11999", True) is False
    assert g._kyc_complete(VALID_CPF, {**FULL_ADDR, "number": ""}, "11999", True) is False
    # sem e-mail confirmado nunca completa
    assert g._kyc_complete(VALID_CPF, FULL_ADDR, "11999", False) is False


def test_validate_and_normalize_address():
    out = g._validate_and_normalize_address({**FULL_ADDR, "cep": "80010000", "state": "pr"})
    assert out["cep"] == "80010-000"
    assert out["state"] == "PR"
    assert out["complement"] == "Sala 4"
    # complemento opcional (ausente → não quebra)
    no_comp = {k: v for k, v in FULL_ADDR.items() if k != "complement"}
    assert "complement" not in g._validate_and_normalize_address(no_comp)


# --------------------------------------------------------------------------- #
# _city_state_from_address (PDF cabeçalho — cidade/UF, nunca rua/número)
# --------------------------------------------------------------------------- #

def test_city_state_from_address():
    assert g._city_state_from_address(FULL_ADDR) == "Curitiba/PR"
    # aceita string JSON (defensivo, caso o driver devolva jsonb como texto)
    import json
    assert g._city_state_from_address(json.dumps(FULL_ADDR)) == "Curitiba/PR"
    assert g._city_state_from_address(None) is None
    assert g._city_state_from_address({}) is None
    assert g._city_state_from_address("não é json") is None
    # nunca vaza rua/número
    cs = g._city_state_from_address(FULL_ADDR)
    assert "Rua XV" not in cs and "123" not in cs


def test_pdf_header_shows_city_state_only():
    """O PDF mostra cidade/UF (contexto), NUNCA rua/número."""
    from reporter.gate_run_report import build_gate_run_context, build_gate_run_report_html
    run = {"url": "https://x.com.br", "score": 90, "passed": True, "fail_on": "critical",
           "created_at": "2026-08-10T12:00:00Z", "results": []}
    ctx = build_gate_run_context(run, cpf_masked="***.***.247-25", plan_name="Free",
                                 generated_at="11/08/2026 10:00", city_state="Curitiba/PR")
    assert ctx["city_state"] == "Curitiba/PR"
    html = build_gate_run_report_html(ctx)
    assert "Curitiba/PR" in html
    assert "Rua XV" not in html and "Sala 4" not in html   # nunca o endereço completo
    # sem city_state → sem a linha
    ctx2 = build_gate_run_context(run, cpf_masked="***.***.247-25", plan_name="Free",
                                  generated_at="x", city_state=None)
    assert ctx2["city_state"] is None
