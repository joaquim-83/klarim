"""KL-75 Prompt 2 — tipo de site (Grupo 7) + subdomínios via CT logs (Grupo 8).

Cobre a classificação pura de site_type (dentro de detect_tech_stack, sem 2ª passagem),
a detecção de sinais, a classificação de subdomínios, o registro no discovery worker
(cache, ignora fora-da-base, ignora www, fail-safe) e os endpoints. Offline.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

import api.main as m
from scanner.tech_detector import detect_tech_stack, classify_site_type
from scanner.main import persist_tech_detection
from discovery.subdomains import (classify_subdomain, DomainCache, register_subdomain,
                                  process_subdomains)
from discovery.ct_poller import subdomain_of, CTLogPoller


# HTML "vivo" (com script) → status ativo, para o site_type não cair em abandonado.
_LIVE = "<script>1</script>"


def _type(html, headers=None, dns=None, ssl=None):
    return detect_tech_stack(headers or {}, _LIVE + html, dns or {}, ssl or {})


# --------------------------------------------------------------------------- #
# Grupo 7 — classify_site_type (8 cenários, um por tipo) + prioridade
# --------------------------------------------------------------------------- #

def test_site_type_saas_login_pricing_apidocs():
    r = _type('<input type="password"><a href="/pricing">Planos</a><a href="/api-docs">API</a>')
    assert r["site_type"] == "saas"


def test_site_type_saas_login_pricing_register():
    r = _type('<input type="password"><a href="/planos">P</a> Comece grátis agora')
    assert r["site_type"] == "saas"


def test_site_type_ecommerce_tech_without_pricing():
    r = _type("", headers={"x-shopify-stage": "prod"})
    assert r["site_type"] == "ecommerce"


def test_site_type_ecommerce_payment_without_pricing():
    r = _type("sdk.mercadopago.com aqui")
    assert r["site_type"] == "ecommerce"


def test_site_type_portal_login_without_pricing():
    r = _type('<input type="password" name="senha">')
    assert r["site_type"] == "portal"


def test_site_type_blog_rss_without_login():
    r = _type('<link type="application/rss+xml" href="/feed">')
    assert r["site_type"] == "blog"


def test_site_type_parked_overrides_login():
    # Parked tem prioridade sobre login residual.
    assert classify_site_type([], "", "parked", {"login_form"}) == "parked"


def test_site_type_abandonado_status():
    assert classify_site_type([], "", "abandonado", set()) == "abandonado"


def test_site_type_institucional_default():
    r = _type("<p>Somos uma empresa tradicional. Fale conosco.</p>")
    assert r["site_type"] == "institucional"


def test_site_type_oauth_counts_as_login():
    # Login social (sem campo de senha) também é sinal de portal.
    r = _type('<script src="https://accounts.google.com/gsi/client"></script>')
    assert r["site_type"] == "portal"


# --- sinais de detecção ---------------------------------------------------- #

def _signals(html):
    return {s["signal"] for s in _type(html)["site_type_signals"]}


def test_signal_login_form():
    assert "login_form" in _signals('<input type="password">')


def test_signal_pricing_link():
    assert "pricing_link" in _signals('<a href="/precos">Preços</a>')


def test_signal_register_button():
    assert "register_button" in _signals("<p>Cadastre-se para começar</p>")


def test_signal_api_docs_link():
    assert "api_docs_link" in _signals('<a href="/documentacao">Docs</a>')


def test_signal_oauth_from_technology():
    # OAuth vem das technologies já detectadas, não de novo regex.
    assert "oauth_button" in _signals('<script src="https://appleid.apple.com/auth"></script>')


def test_site_type_signals_have_weights():
    detail = _type('<input type="password">')["site_type_signals"]
    assert all("weight" in d and d["weight"] in ("strong", "medium") for d in detail)


# --------------------------------------------------------------------------- #
# Grupo 8 — classify_subdomain
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("sub,expected", [
    ("app.hotel.com.br", "app"),
    ("sistema.hotel.com.br", "app"),
    ("api.hotel.com.br", "api"),
    ("admin.hotel.com.br", "admin"),
    ("painel.hotel.com.br", "admin"),
    ("staging.hotel.com.br", "staging"),
    ("dev.hotel.com.br", "staging"),
    ("hml.hotel.com.br", "staging"),
    ("mail.hotel.com.br", "mail"),
    ("loja.hotel.com.br", "shop"),
    ("docs.hotel.com.br", "docs"),
    ("status.hotel.com.br", "status"),
    ("cdn.hotel.com.br", "cdn"),
    ("blog.hotel.com.br", "blog"),
    ("www.hotel.com.br", "www"),
    ("random.hotel.com.br", "outro"),
])
def test_classify_subdomain(sub, expected):
    assert classify_subdomain(sub) == expected


def test_subdomain_of_helper():
    assert subdomain_of("app.hotel.com.br") == "app.hotel.com.br"
    assert subdomain_of("hotel.com.br") is None          # raiz, não subdomínio
    assert subdomain_of("mail.hotel.com.br") == "mail.hotel.com.br"  # infra CONTA aqui
    assert subdomain_of("x.com") is None                 # não .com.br
    assert subdomain_of("*.hotel.com.br") is None        # wildcard vira raiz
    assert subdomain_of("") is None


# --------------------------------------------------------------------------- #
# Grupo 8 — DomainCache + register_subdomain + process_subdomains
# --------------------------------------------------------------------------- #

class _SubStore:
    def __init__(self, root_domains=None, fail=False):
        self._roots = root_domains or [{"domain": "hotel.com.br", "id": 1}]
        self.upserts = []
        self.fail = fail

    async def get_all_root_domains(self):
        return list(self._roots)

    async def upsert_subdomain(self, target_id, subdomain, subdomain_type, cert_issuer=None):
        if self.fail:
            raise RuntimeError("db down")
        self.upserts.append((target_id, subdomain, subdomain_type, cert_issuer))


def test_domain_cache_load_and_lookup():
    store = _SubStore([{"domain": "hotel.com.br", "id": 7}, {"domain": "loja.com.br", "id": 9}])
    cache = DomainCache()
    size = asyncio.run(cache.load(store))
    assert size == 2 and cache.get("hotel.com.br") == 7
    assert cache.get("HOTEL.com.br") == 7        # case-insensitive
    assert cache.get("naoexiste.com.br") is None


def test_domain_cache_reload_keeps_previous_on_error():
    class _Boom:
        def __init__(self): self.n = 0
        async def get_all_root_domains(self):
            self.n += 1
            if self.n == 1:
                return [{"domain": "hotel.com.br", "id": 1}]
            raise RuntimeError("db down")
    store = _Boom()
    cache = DomainCache()
    asyncio.run(cache.load(store))
    asyncio.run(cache.load(store))               # 2ª falha
    assert cache.get("hotel.com.br") == 1         # mantém o anterior


def test_register_subdomain_in_base():
    store = _SubStore()
    cache = DomainCache(); asyncio.run(cache.load(store))
    ok = asyncio.run(register_subdomain(store, cache, "hotel.com.br", "app.hotel.com.br", "R3"))
    assert ok is True and store.upserts[0] == (1, "app.hotel.com.br", "app", "R3")


def test_register_subdomain_not_in_base_ignored():
    store = _SubStore()
    cache = DomainCache(); asyncio.run(cache.load(store))
    ok = asyncio.run(register_subdomain(store, cache, "outro.com.br", "app.outro.com.br", None))
    assert ok is False and store.upserts == []


def test_register_subdomain_www_ignored():
    store = _SubStore()
    cache = DomainCache(); asyncio.run(cache.load(store))
    ok = asyncio.run(register_subdomain(store, cache, "hotel.com.br", "www.hotel.com.br", None))
    assert ok is False and store.upserts == []   # www não conta


def test_register_subdomain_error_swallowed():
    store = _SubStore(fail=True)
    cache = DomainCache(); asyncio.run(cache.load(store))
    # Erro no upsert NÃO propaga (o stream de CT deve continuar).
    ok = asyncio.run(register_subdomain(store, cache, "hotel.com.br", "api.hotel.com.br", None))
    assert ok is False


def test_process_subdomains_batch():
    store = _SubStore([{"domain": "hotel.com.br", "id": 1}])
    cache = DomainCache(); asyncio.run(cache.load(store))
    sub_map = {
        "app.hotel.com.br": "R3",          # na base → registra
        "api.hotel.com.br": "R3",          # na base → registra
        "www.hotel.com.br": None,          # www → ignora
        "app.outro.com.br": "R3",          # fora da base → ignora
    }
    stats = asyncio.run(process_subdomains(store, cache, sub_map))
    assert stats["registered"] == 2 and stats["www"] == 1
    assert stats["skipped_not_in_base"] == 1
    assert {u[1] for u in store.upserts} == {"app.hotel.com.br", "api.hotel.com.br"}


def test_process_subdomains_respects_max_items():
    store = _SubStore([{"domain": "hotel.com.br", "id": 1}])
    cache = DomainCache(); asyncio.run(cache.load(store))
    sub_map = {f"s{i}.hotel.com.br": None for i in range(10)}
    stats = asyncio.run(process_subdomains(store, cache, sub_map, max_items=3))
    assert stats["seen"] == 3


# --------------------------------------------------------------------------- #
# Poller — captura de subdomínios no _ingest (via cert real)
# --------------------------------------------------------------------------- #

def _make_entry(sans):
    import base64
    import datetime as dt
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, sans[0])])
    issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "R3")])
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(issuer)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(dt.datetime(2026, 1, 1)).not_valid_after(dt.datetime(2027, 1, 1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(d) for d in sans]),
                           critical=False).sign(key, hashes.SHA256()))
    der = cert.public_bytes(serialization.Encoding.DER)
    leaf = b"\x00\x00" + b"\x00" * 8 + b"\x00\x00" + len(der).to_bytes(3, "big") + der
    return {"leaf_input": base64.b64encode(leaf).decode(), "extra_data": ""}


def test_poller_captures_subdomains_and_roots():
    p = CTLogPoller()
    p._ingest(_make_entry(["hotel.com.br", "app.hotel.com.br", "mail.hotel.com.br"]))
    roots = set(p.flush_buffer())
    subs = p.flush_subdomains()
    assert roots == {"hotel.com.br"}                     # mail. (infra) fora das raízes
    # app. e mail. entram como subdomínios; issuer capturado.
    assert set(subs) == {"app.hotel.com.br", "mail.hotel.com.br"}
    assert subs["app.hotel.com.br"] == "R3"
    assert p.flush_subdomains() == {}                    # já limpo


# --------------------------------------------------------------------------- #
# Integração — persist_tech_detection grava site_type
# --------------------------------------------------------------------------- #

class _CapStore:
    def __init__(self):
        self.calls = {}

    async def save_tech_stack(self, target_id, scan_id, technologies):
        return len(technologies)

    async def update_target_tech_fields(self, target_id, email_provider, related_domains,
                                        site_type=None):
        self.calls["site_type"] = site_type

    async def fill_empty_company_name(self, target_id, company_name):
        return True

    async def save_site_status(self, target_id, status, http_code=None, response_time_ms=None):
        self.calls["status"] = status


def test_persist_writes_site_type():
    store = _CapStore()
    resp = {"http_status": 200, "headers": {},
            "html": '<script>1</script><input type="password"><a href="/pricing">P</a>'
                    '<a href="/docs">API</a>',
            "dns": {}, "ssl": {}}
    result = asyncio.run(persist_tech_detection(store, 1, 1, resp))
    assert result["site_type"] == "saas"
    assert store.calls["site_type"] == "saas"


def test_persist_site_type_uses_authoritative_status():
    # HTTP 200 mas HTML com parking → parked (o site_type segue o status autoritativo).
    store = _CapStore()
    resp = {"http_status": 200, "headers": {},
            "html": "Este domínio está à venda", "dns": {}, "ssl": {}}
    result = asyncio.run(persist_tech_detection(store, 1, 1, resp))
    assert result["site_type"] == "parked"


# --------------------------------------------------------------------------- #
# Endpoints — site_type e subdomain_count
# --------------------------------------------------------------------------- #

class FakeStore:
    async def get_target_by_domain(self, domain):
        if domain == "naoexiste.com.br":
            return None
        return {"id": 5, "domain": domain, "status": "scanned", "email_provider": None,
                "related_domains": [], "site_type": "saas", "subdomain_count": 3}

    async def get_site_profile(self, target_id):
        return {"public_visible": True}

    async def tech_summary_by_domain(self, target_id):
        return {"has_analytics": True, "has_cdn": False, "has_payment": False,
                "has_chat": False, "has_captcha": False, "has_ecommerce": False,
                "tech_count": 2}

    async def get_tech_stack(self, target_id):
        return []

    async def get_site_status_history(self, target_id, limit=10):
        return [{"status": "ativo", "http_code": 200, "response_time_ms": 90, "detected_at": None}]

    async def get_subdomains(self, target_id, limit=50):
        return [{"subdomain": "app.hotel.com.br", "subdomain_type": "app",
                 "first_seen": None, "last_seen": None, "cert_issuer": "R3"}]


@pytest.fixture
def client(monkeypatch):
    s = FakeStore()
    monkeypatch.setattr(m, "get_target_store", lambda: s)
    monkeypatch.setattr("discovery.store.get_target_store", lambda: s)
    return TestClient(m.app, raise_server_exceptions=False)


def test_public_tech_summary_includes_site_type_and_subcount(client):
    body = client.get("/public/tech-summary/hotel.com.br").json()
    assert body["site_type"] == "saas" and body["subdomain_count"] == 3
    # Público NUNCA vê a lista de subdomínios
    assert "subdomains" not in body


def test_public_tech_summary_unknown_has_null_site_type(client):
    body = client.get("/public/tech-summary/naoexiste.com.br").json()
    assert body["site_type"] is None and body["subdomain_count"] == 0


def test_api_site_subdomains(client):
    res = asyncio.run(m.api_site_subdomains("hotel.com.br"))
    assert res["count"] == 3 and res["subdomains"][0]["type"] == "app"


def test_api_site_subdomains_not_found(client):
    assert asyncio.run(m.api_site_subdomains("naoexiste.com.br"))["status_code"] == 404


def test_api_site_tech_stack_has_site_type(client):
    res = asyncio.run(m.api_site_tech_stack("hotel.com.br"))
    assert res["site_type"] == "saas" and res["subdomain_count"] == 3
