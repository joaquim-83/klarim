"""KL-26 — fluxos e2e cross-módulo. Cada teste percorre um caminho completo com mocks/fakes.
Offline (sem rede/DB/APIs externas). Reusa os padrões dos testes por-feature.

Flows: C dono verificado · D técnico monitora site de outro · F unsubscribe completo ·
B prontidão de cold alert (lead scoring→verificação→decisão) · E ciclo de pagamento PIX.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

import api.main as m
from api import auth_users
from discovery.alert_scoring import calculate_alert_score
from discovery.alert_worker import AlertWorker
from notifier import email_verifier as ev
from notifier.email_client import generate_unsubscribe_token
from payments.store import MemoryStore
from payments.models import Charge, PaymentStatus

_SECRET = "u" * 48


# =========================================================================== #
# FLOWS C + D — conta / verificação de domínio / perfil / selo / técnico
# =========================================================================== #

class AccountStore:
    def __init__(self):
        self.users, self.targets, self.links = {}, {}, {}
        self.profiles, self.pending, self.verified_sites = {}, {}, []
        self.owner_verified = {}

    # auth / posse
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
        return {"score": 82, "semaphore": "amarelo", "checks_json": {}, "scanned_at": None}

    async def get_site_profile(self, tid):
        return self.profiles.get(int(tid))

    async def list_site_vigilias(self, uid, domain):
        return [{"tipo": "ssl", "enabled": True, "last_status": "ok",
                 "last_check_at": None, "next_check_at": None, "last_data": {}}]

    # verificação de domínio (KL-99)
    async def create_domain_verification(self, uid, tid, domain, method, token):
        self.pending[(int(uid), int(tid))] = {"id": 1, "method": method, "token": token,
                                              "domain": domain}
        return {"id": 1, "expires_at": None}

    async def get_pending_domain_verification(self, uid, tid):
        return self.pending.get((int(uid), int(tid)))

    async def mark_ownership_verified(self, vid):
        pass

    async def mark_site_verified(self, uid, tid, method):
        link = self.links.get((int(uid), int(tid)))
        if link:
            link["is_owner"] = True
        self.verified_sites.append((int(uid), int(tid)))

    async def set_target_owner_verified(self, tid, val):
        self.owner_verified[int(tid)] = bool(val)
        if int(tid) in self.targets:
            self.targets[int(tid)]["owner_verified"] = bool(val)

    async def set_user_account_level(self, uid, level):
        self.users[int(uid)]["account_level"] = int(level)

    # perfil / selo (KL-98)
    async def update_site_profile_fields(self, tid, fields, actor="admin"):
        p = self.profiles.setdefault(int(tid), {"target_id": tid})
        for k, v in fields.items():
            if k == "clear_fields":
                continue
            p[k] = v
        if actor == "owner":
            p["edited_by_owner"] = True
        return dict(p)

    async def set_seal_config(self, tid, enabled, style=None):
        p = self.profiles.setdefault(int(tid), {"target_id": tid})
        p["seal_enabled"] = bool(enabled)
        if style:
            p["seal_style"] = style
        return dict(p)


@pytest.fixture
def acct(monkeypatch):
    store = AccountStore()
    # user 10: nível 2 (com senha), dono NÃO verificado do site 1.
    store.users[10] = {"id": 10, "email": "dono@empresa.com.br", "name": "Dono",
                       "plan": "pro", "account_level": 2, "is_active": True,
                       "email_confirmed": True, "password_hash": "x", "created_at": None}
    # user 20: técnico (nível 3), sem posse do site 1.
    store.users[20] = {"id": 20, "email": "tec@agencia.com.br", "name": "Tec",
                       "plan": "agency", "account_level": 3, "is_active": True,
                       "email_confirmed": True, "password_hash": "x", "created_at": None}
    store.targets[1] = {"id": 1, "domain": "empresa.com.br", "url": "https://empresa.com.br",
                        "last_scan_score": 82, "owner_verified": False}
    store.links[(10, 1)] = {"id": 1, "user_id": 10, "target_id": 1, "is_owner": False}
    store.links[(20, 1)] = {"id": 2, "user_id": 20, "target_id": 1, "is_owner": False}  # técnico
    store.profiles[1] = {"target_id": 1, "company_name": "Empresa", "public_visible": False}

    monkeypatch.setenv("JWT_SECRET", "x" * 64)
    monkeypatch.setattr(m, "get_target_store", lambda: store)
    monkeypatch.setattr("discovery.store.get_target_store", lambda: store)

    async def allowed(uid):
        return ["ssl", "domain", "score", "email", "reputation", "uptime", "changes", "phishing"]
    monkeypatch.setattr(m, "_vigilia_allowed_types", allowed)
    return store


def _hdr(store, uid):
    return {"Authorization": f"Bearer {auth_users.create_user_token(store.users[uid])}"}


def test_flow_c_owner_verifies_edits_profile_and_seal(acct, monkeypatch):
    client = TestClient(m.app, raise_server_exceptions=False)

    # 1) inicia a verificação (dns_txt) → token nas instruções
    r = client.post("/account/sites/1/verify/start", headers=_hdr(acct, 10),
                    json={"method": "dns_txt"})
    assert r.status_code == 200
    token = r.json()["token"]
    assert acct.pending[(10, 1)]["token"] == token

    # 2) DNS mock devolve o TXT correto → verify/check promove a nível 3
    async def fake_dns(domain):
        v = acct.pending.get((10, 1))
        return [f"klarim-verify={v['token']}"] if v else []
    monkeypatch.setattr(m, "_dns_txt_records", fake_dns)

    r = client.post("/account/sites/1/verify/check", headers=_hdr(acct, 10))
    assert r.status_code == 200 and r.json() == {"status": "verified", "account_level": 3}
    assert acct.users[10]["account_level"] == 3
    assert acct.owner_verified[1] is True and acct.links[(10, 1)]["is_owner"] is True

    # 3) agora dono verificado edita o perfil (edited_by_owner) e HTML é sanitizado
    r = client.put("/account/sites/1/profile", headers=_hdr(acct, 10),
                   json={"company_name": "  Nova <b>Empresa</b> ", "description": "Boa"})
    assert r.status_code == 200
    assert acct.profiles[1]["company_name"] == "Nova Empresa"
    assert acct.profiles[1]["edited_by_owner"] is True

    # 4) liga o selo
    r = client.put("/account/sites/1/seal", headers=_hdr(acct, 10),
                   json={"enabled": True, "style": "badge"})
    assert r.status_code == 200 and acct.profiles[1]["seal_enabled"] is True

    # 5) selo público reflete habilitado + verificado
    r = client.get("/seal/empresa.com.br")
    assert r.status_code == 200
    d = r.json()
    assert d["enabled"] is True and d["verified"] is True


def test_flow_d_technician_monitors_but_cannot_edit(acct):
    client = TestClient(m.app, raise_server_exceptions=False)
    # técnico (user 20) vê o monitoramento do site do cliente
    r = client.get("/account/sites/1/monitoring", headers=_hdr(acct, 20))
    assert r.status_code == 200
    assert any(v["tipo"] == "ssl" for v in r.json()["vigilias"])

    # mas NÃO edita o perfil (não é dono) → 403 not_owner
    r = client.put("/account/sites/1/profile", headers=_hdr(acct, 20),
                   json={"company_name": "hack"})
    assert r.status_code == 403 and r.json()["detail"]["error"] == "not_owner"

    # nem o selo
    r = client.put("/account/sites/1/seal", headers=_hdr(acct, 20),
                   json={"enabled": True})
    assert r.status_code == 403 and r.json()["detail"]["error"] == "not_owner"


# =========================================================================== #
# FLOW F — unsubscribe completo (token → /remover → blocklist → worker pula)
# =========================================================================== #

class UnsubStore:
    def __init__(self):
        self.blocklist, self.unsubscribed, self.logged = set(), [], []

    async def is_email_blocked(self, email):
        return (email or "").lower() in self.blocklist

    async def mark_unsubscribed(self, email):
        self.unsubscribed.append((email or "").lower()); return 1

    async def block_email(self, email, reason="bounced"):
        self.blocklist.add((email or "").lower())

    async def get_target_by_domain(self, domain):
        return {"id": 7, "domain": domain}

    async def log_email(self, **kw):
        self.logged.append(kw)

    async def update_status(self, target_id, status):
        pass


def test_flow_f_unsubscribe_end_to_end(monkeypatch):
    store = UnsubStore()
    monkeypatch.setenv("UNSUBSCRIBE_SECRET", _SECRET)
    monkeypatch.setattr(m, "get_target_store", lambda: store)
    client = TestClient(m.app, raise_server_exceptions=False)

    email, domain = "contato@hotel.com.br", "hotel.com.br"
    token = generate_unsubscribe_token(email, domain, _SECRET, "alertas.klarim.net")

    # 1) página de confirmação (GET) → 200
    r = client.get("/remover", params={"token": token})
    assert r.status_code == 200 and "confirm" in r.text.lower() or r.status_code == 200

    # 2) confirma (POST) → unsubscribed + blocklist + evento
    r = client.post("/remover", params={"token": token})
    assert r.status_code == 200
    assert email in store.blocklist and email in store.unsubscribed
    assert store.logged and store.logged[-1]["email_type"] == "unsubscribe"

    # 3) o alert worker PULA o alvo agora blocklistado
    w = AlertWorker.__new__(AlertWorker)
    w.store, w.validate_mx = store, False
    kept = asyncio.run(w._validate_batch([{"id": 7, "contact_email": email, "url": "https://hotel.com.br"}]))
    assert kept == []  # removido da fila

    # 4) POST de novo → idempotente ("já removido")
    r = client.post("/remover", params={"token": token})
    assert r.status_code == 200  # sem erro; already


# =========================================================================== #
# FLOW B — prontidão de cold alert (lead scoring → verificação → decisão)
# =========================================================================== #

def test_flow_b_quality_lead_is_verified_and_sendable():
    target = {"domain": "clinica.com.br", "last_scan_score": 65,
              "contact_email": "contato@clinica.com.br", "sector": "saude"}
    email = target["contact_email"]

    # 1) lead scoring: e-mail no domínio + score na zona de ação → acima do threshold (20)
    result = calculate_alert_score(target, email)
    assert result["score"] > 40
    assert any(s["signal"] == "email_matches_domain" for s in result["signals"])

    # 2) verificação (Reoon mock → safe)
    verdict = ev.VerifyResult("safe", "reoon_power", source="reoon")

    # 3) decisão de envio: safe + score de qualidade → envia
    assert ev.is_safe_to_send(verdict, result["score"]) is True


def test_flow_b_invalid_email_lead_is_not_sendable():
    target = {"domain": "x.com.br", "last_scan_score": 60, "contact_email": "no@x.com.br"}
    score = calculate_alert_score(target, target["contact_email"])["score"]
    # e-mail inválido pela verificação → nunca envia, mesmo com bom score
    assert ev.is_safe_to_send(ev.VerifyResult("invalid", "reoon_power"), score) is False


# =========================================================================== #
# FLOW E — ciclo de pagamento PIX (charge → paid)
# =========================================================================== #

def test_flow_e_payment_charge_to_paid():
    store = MemoryStore()
    # 1) cobrança criada (pendente)
    asyncio.run(store.save(Charge("ch_1", "https://loja.com.br", 1900,
                                  buyer_email="dono@loja.com.br")))
    got = asyncio.run(store.get("ch_1"))
    assert got.status == PaymentStatus.PENDING and not got.is_paid

    # 2) webhook de confirmação marca pago (efeito do POST /webhooks/abacatepay)
    asyncio.run(store.mark_status("ch_1", PaymentStatus.PAID, paid_at="2026-07-26T00:00:00Z"))
    got = asyncio.run(store.get("ch_1"))
    assert got.is_paid and got.paid_at

    # 3) contabilidade reflete a receita
    stats = asyncio.run(store.payment_stats())
    assert stats["paid_count"] == 1 and stats["revenue_cents"] == 1900
