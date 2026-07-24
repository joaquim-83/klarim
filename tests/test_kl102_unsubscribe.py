"""KL-102 — List-Unsubscribe (RFC 8058) + endpoint /remover. Token HMAC (propósito
'unsubscribe', sem expiração), headers nos senders cold (não no transacional), página de
confirmação + one-click do Gmail, rate limit anti brute-force, anti-enumeração. Offline.
"""

from __future__ import annotations

import asyncio

import api.main as m
from notifier import (generate_unsubscribe_token, verify_unsubscribe_token,
                      build_cold_unsubscribe_headers, KlarimMailer)

_SECRET = "s" * 40


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# --- token HMAC -------------------------------------------------------------- #

def test_token_deterministic_and_roundtrip():
    t1 = generate_unsubscribe_token("Contato@Hotel.com.BR", "Hotel.com.br", _SECRET, "alertas.klarim.net")
    t2 = generate_unsubscribe_token("contato@hotel.com.br", "hotel.com.br", _SECRET, "alertas.klarim.net")
    assert t1 == t2                                    # determinístico + normaliza case
    info = verify_unsubscribe_token(t1, _SECRET)
    assert info == {"email": "contato@hotel.com.br", "domain": "hotel.com.br",
                    "sender": "alertas.klarim.net"}


def test_token_rejects_tamper_and_wrong_secret():
    t = generate_unsubscribe_token("a@b.com", "b.com", _SECRET)
    assert verify_unsubscribe_token(t[:-1] + ("0" if t[-1] != "0" else "1"), _SECRET) is None
    assert verify_unsubscribe_token(t, "other-secret") is None
    assert verify_unsubscribe_token("garbage", _SECRET) is None
    assert verify_unsubscribe_token("", _SECRET) is None


def test_token_url_safe():
    t = generate_unsubscribe_token("a+tag@b.com", "b.com", _SECRET, "aviso.klarim.net")
    # base64url + '.' + hex — sem chars que quebram header/URL de e-mail
    assert all(c.isalnum() or c in "-_." for c in t)


# --- headers cold ------------------------------------------------------------ #

def test_cold_headers_full_rfc8058_with_secret():
    h = build_cold_unsubscribe_headers("a@b.com", "b.com", "alertas.klarim.net", _SECRET)
    lu = h["List-Unsubscribe"]
    assert lu.startswith("<mailto:scan@klarim.net?subject=remover>, <https://klarim.net/remover?token=")
    assert h["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    # o token no header decodifica de volta
    token = lu.split("token=")[1].rstrip(">")
    from urllib.parse import unquote
    assert verify_unsubscribe_token(unquote(token), _SECRET)["domain"] == "b.com"


def test_cold_headers_fallback_mailto_without_secret():
    h = build_cold_unsubscribe_headers("a@b.com", "b.com", "alertas.klarim.net", "")
    assert h == {"List-Unsubscribe": "<mailto:scan@klarim.net?subject=remover>"}


def test_send_cold_alert_has_oneclick_header(monkeypatch):
    monkeypatch.setenv("UNSUBSCRIBE_SECRET", _SECRET)
    mailer = KlarimMailer("re_x", "Klarim <klarim@klarim.net>", store=None)
    cap = {}
    monkeypatch.setattr(mailer, "_send_sync", lambda p: cap.update(p) or {"email_id": "e1"})

    async def _noblock(_e):
        return False

    monkeypatch.setattr(mailer, "_is_blocked", _noblock)
    monkeypatch.setattr(mailer, "_log_email", lambda **k: asyncio.sleep(0))
    _run(mailer.send_cold_alert(to_email="d@e.com", from_address="Klarim <scan@aviso.klarim.net>",
                                subject="s", text="t", template_variant=2, domain="e.com"))
    lu = cap["headers"]["List-Unsubscribe"]
    assert "mailto:scan@klarim.net" in lu and "https://klarim.net/remover?token=" in lu
    assert cap["headers"]["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"


def test_transactional_has_no_list_unsubscribe(monkeypatch):
    monkeypatch.setenv("UNSUBSCRIBE_SECRET", _SECRET)
    mailer = KlarimMailer("re_x", "Klarim <klarim@klarim.net>", store=None)
    cap = {}
    monkeypatch.setattr(mailer, "_send_sync", lambda p: cap.update(p) or {"email_id": "e1"})
    _run(mailer.send_welcome_confirmation("d@e.com", "https://klarim.net/confirmado?token=x"))
    assert "List-Unsubscribe" not in (cap.get("headers") or {})


# --- endpoint /remover ------------------------------------------------------- #

class _FakeStore:
    def __init__(self, blocked=False):
        self._blocked = blocked
        self.unsubscribed = []
        self.blocklisted = []
        self.logged = []

    async def is_email_blocked(self, email):
        return self._blocked

    async def mark_unsubscribed(self, email):
        self.unsubscribed.append(email)
        return 1

    async def block_email(self, email, reason="bounced"):
        self.blocklisted.append((email, reason))

    async def get_target_by_domain(self, domain):
        return {"id": 42, "domain": domain}

    async def log_email(self, **kw):
        self.logged.append(kw)


class _Req:
    def __init__(self, ip="9.9.9.9"):
        self.headers = {"CF-Connecting-IP": ip}
        self.client = None


def _wire(monkeypatch, store):
    monkeypatch.setenv("UNSUBSCRIBE_SECRET", _SECRET)
    monkeypatch.setattr(m, "get_target_store", lambda: store)


def test_remover_get_valid_shows_confirm(monkeypatch):
    store = _FakeStore(blocked=False)
    _wire(monkeypatch, store)
    tok = generate_unsubscribe_token("a@b.com", "b.com", _SECRET, "alertas.klarim.net")
    resp = _run(m.api_remover_page(_Req(), token=tok))
    assert resp.status_code == 200
    body = resp.body.decode()
    assert "Confirmar remoção" in body and "form" in body


def test_remover_get_invalid_is_200_generic(monkeypatch):
    store = _FakeStore()
    _wire(monkeypatch, store)
    resp = _run(m.api_remover_page(_Req(), token="bogus.token"))
    assert resp.status_code == 200                      # 200 (UX), não 4xx
    assert "inválido" in resp.body.decode()


def test_remover_post_valid_unsubscribes(monkeypatch):
    store = _FakeStore(blocked=False)
    _wire(monkeypatch, store)
    tok = generate_unsubscribe_token("dono@x.com.br", "x.com.br", _SECRET, "perfil.klarim.net")
    resp = _run(m.api_remover_confirm(_Req(), token=tok))
    assert resp.status_code == 200 and "Removido" in resp.body.decode()
    assert store.unsubscribed == ["dono@x.com.br"]
    assert store.blocklisted == [("dono@x.com.br", "unsubscribe")]
    ev = store.logged[-1]
    assert ev["email_type"] == "unsubscribe" and ev["from_domain"] == "perfil.klarim.net"
    assert ev["target_id"] == 42 and ev["status"] == "unsubscribe"


def test_remover_post_invalid_is_400(monkeypatch):
    store = _FakeStore()
    _wire(monkeypatch, store)
    resp = _run(m.api_remover_confirm(_Req(), token="nope"))
    assert resp.status_code == 400 and store.unsubscribed == []


def test_remover_post_idempotent_already(monkeypatch):
    store = _FakeStore(blocked=True)                    # já na blocklist = já removido
    _wire(monkeypatch, store)
    tok = generate_unsubscribe_token("a@b.com", "b.com", _SECRET)
    resp = _run(m.api_remover_confirm(_Req(), token=tok))
    assert resp.status_code == 200 and "Já removido" in resp.body.decode()


def test_remover_post_rate_limits_invalid_tokens(monkeypatch):
    store = _FakeStore()
    _wire(monkeypatch, store)
    m._REMOVER_RL.clear()
    monkeypatch.setattr(m, "_cache", None)              # força o fallback in-memory
    codes = [_run(m.api_remover_confirm(_Req("5.5.5.5"), token="bad")).status_code
             for _ in range(12)]
    assert codes[:10] == [400] * 10 and 429 in codes[10:]


def test_remover_post_valid_never_rate_limited(monkeypatch):
    # tokens VÁLIDOS não são limitados (one-click do Gmail vem de IP compartilhado)
    store = _FakeStore(blocked=False)
    _wire(monkeypatch, store)
    monkeypatch.setattr(m, "_cache", None)
    tok = generate_unsubscribe_token("a@b.com", "b.com", _SECRET)
    codes = [_run(m.api_remover_confirm(_Req("7.7.7.7"), token=tok)).status_code for _ in range(15)]
    assert all(c == 200 for c in codes)
