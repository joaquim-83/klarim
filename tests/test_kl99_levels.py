"""KL-99 — conta sem senha + 3 níveis de confiança + verificação de domínio. Offline
(TestClient + FakeStore).

Cobre:
  * signup com senha opcional: sem senha → nível 1; com senha → nível 2.
  * Fluxo D (/account/signup-inline): passwordless nível 1, source 'inline', vincula domínio,
    confirmation_sent / already_exists / disposable / rate limit.
  * confirmação ativa o monitoramento (vigílias) + auto-login de conta sem senha.
  * /account/set-password (nível 1 → 2): ok / já tem senha (400) / não conferem (422) / curta.
  * @require_level: nível 1 tenta ação nível 2 → 403 estruturado; sobe de nível e passa.
  * Fluxo C (/alert-access): auto-cria conta sem senha + loga; conta existente → loga.
  * verificação de domínio (start/check) por meta_tag / html_file / dns_txt → nível 3.
  * _check_domain_control (puro, com fetchers monkeypatch).
  * segurança: nenhum vazamento de password_hash; corpo 403 tem required/current level.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import api.main as m
from api import auth_users
from notifier.email_client import alert_access_token

SECRET = "k" * 64


class FakeStore:
    """FakeStore rico para os fluxos do KL-99 (usuários, sites, verificação de domínio)."""

    def __init__(self):
        self.users = {}          # email -> user (com password_hash + account_level + source)
        self.by_id = {}
        self.next_id = 1
        self.targets = {}        # id -> {id, url, domain, contact_email}
        self.by_url = {}         # url -> id
        self.next_tid = 1
        self.links = {}          # (uid, tid) -> {is_owner, verified_at, method}
        self.verifs = []         # verificações de domínio
        self.next_vid = 1
        self.alert_sessions = []
        self.vigilias = []       # (uid, domain, tipo) upserts
        self.verified_scan_emails = set()
        self.owner_verified = set()  # target ids marcados owner_verified

    # --- usuários ------------------------------------------------------------ #
    async def create_user(self, email, password_hash, name=None, role="owner",
                          email_confirmed=True, confirmation_source=None, source="signup"):
        email = email.lower().strip()
        if email in self.users:
            return None
        level = 2 if password_hash else 1  # KL-99: nível deriva da senha
        u = {"id": self.next_id, "email": email, "name": name, "plan": "free",
             "max_sites": 5, "is_active": True, "role": role,
             "email_confirmed": email_confirmed, "password_hash": password_hash,
             "confirmation_source": confirmation_source, "account_level": level,
             "source": source if source in ("signup", "hmac", "inline") else "signup"}
        self.users[email] = u
        self.by_id[u["id"]] = u
        self.next_id += 1
        return {k: v for k, v in u.items() if k != "password_hash"}

    async def get_user_by_email(self, email, with_hash=False):
        u = self.users.get((email or "").lower().strip())
        if not u:
            return None
        return dict(u) if with_hash else {k: v for k, v in u.items() if k != "password_hash"}

    async def get_user_by_id(self, uid):
        u = self.by_id.get(int(uid))
        return {k: v for k, v in u.items() if k != "password_hash"} if u else None

    async def email_has_verified_scan(self, email):
        return (email or "").lower().strip() in self.verified_scan_emails

    async def confirm_user_email(self, user_id, source="link"):
        u = self.by_id.get(int(user_id))
        if not u or u.get("email_confirmed") is True:
            return False
        u["email_confirmed"] = True
        u["confirmation_source"] = source
        return True

    async def touch_user_login(self, uid):
        pass

    async def set_user_password(self, email, password_hash):
        u = self.users.get((email or "").lower().strip())
        if not u:
            return False
        u["password_hash"] = password_hash
        return True

    async def set_user_account_level(self, uid, level):
        u = self.by_id.get(int(uid))
        if not u:
            return False
        u["account_level"] = max(int(u.get("account_level") or 1), int(level))
        return True

    async def update_user_name(self, uid, name):
        u = self.by_id.get(int(uid))
        if u:
            u["name"] = name
        return bool(u)

    async def auto_link_technician_by_email(self, email, tuid):
        return 0

    async def set_lead_account(self, email, account_id):
        pass

    async def set_lead_monitoring(self, email):
        pass

    async def get_targets_scanned_by_email(self, email, limit=1):
        return []

    # --- sites / targets ----------------------------------------------------- #
    async def get_target_by_url(self, url):
        tid = self.by_url.get(url)
        return self.targets.get(tid) if tid else None

    async def register_target(self, url, domain, **kw):
        tid = self.next_tid
        self.next_tid += 1
        self.targets[tid] = {"id": tid, "url": url, "domain": domain,
                             "contact_email": kw.get("contact_email")}
        self.by_url[url] = tid
        return tid

    def add_target(self, domain, contact_email=None):
        tid = self.next_tid
        self.next_tid += 1
        url = f"https://{domain}"
        self.targets[tid] = {"id": tid, "url": url, "domain": domain, "contact_email": contact_email}
        self.by_url[url] = tid
        return tid

    async def get_target(self, tid):
        return self.targets.get(int(tid))

    async def get_user_site(self, uid, tid):
        link = self.links.get((int(uid), int(tid)))
        return {"user_id": uid, "target_id": tid, "is_owner": link["is_owner"]} if link else None

    async def link_user_site(self, uid, tid, is_owner=False):
        key = (int(uid), int(tid))
        if key in self.links:
            return False
        self.links[key] = {"is_owner": is_owner, "verified_at": None, "method": None}
        return True

    async def list_user_sites(self, uid):
        out = []
        for (u, t), link in self.links.items():
            if u == int(uid):
                tgt = self.targets.get(t, {})
                out.append({"target_id": t, "domain": tgt.get("domain"), "is_owner": link["is_owner"]})
        return out

    async def count_user_sites(self, uid):
        return sum(1 for (u, _t) in self.links if u == int(uid))

    async def site_has_owner(self, tid, exclude_user_id=None):
        for (u, t), link in self.links.items():
            if t == int(tid) and link["is_owner"] and u != exclude_user_id:
                return True
        return False

    async def mark_site_verified(self, uid, tid, method):
        link = self.links.get((int(uid), int(tid)))
        if not link:
            return False
        link["is_owner"] = True
        link["method"] = method
        return True

    async def set_target_owner_verified(self, tid, verified=True):
        if verified:
            self.owner_verified.add(int(tid))
        else:
            self.owner_verified.discard(int(tid))
        return True

    async def upsert_vigilia(self, uid, domain, tipo, **kw):
        self.vigilias.append((int(uid), domain, tipo))

    # --- verificação de domínio (KL-99) -------------------------------------- #
    async def create_domain_verification(self, uid, tid, domain, method, token):
        for v in self.verifs:
            if (v["user_id"] == uid and v["target_id"] == tid and v["status"] == "pending"
                    and v["method"] in ("meta_tag", "html_file", "dns_txt")):
                v["status"] = "expired"
        v = {"id": self.next_vid, "user_id": uid, "target_id": tid, "domain": domain,
             "method": method, "token": token, "status": "pending", "expires_at": None}
        self.next_vid += 1
        self.verifs.append(v)
        return {"id": v["id"], "token": token, "expires_at": None}

    async def get_pending_domain_verification(self, uid, tid):
        for v in reversed(self.verifs):
            if (v["user_id"] == uid and v["target_id"] == tid and v["status"] == "pending"
                    and v["method"] in ("meta_tag", "html_file", "dns_txt")):
                return dict(v)
        return None

    async def mark_ownership_verified(self, vid):
        for v in self.verifs:
            if v["id"] == vid:
                v["status"] = "verified"

    # --- alerta -------------------------------------------------------------- #
    async def create_alert_session(self, token_hash, email, tid, expires_at):
        self.alert_sessions.append({"hash": token_hash, "email": email, "tid": tid})

    async def mark_alert_session_converted(self, token_hash):
        pass


@pytest.fixture
def store(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", SECRET)
    s = FakeStore()
    monkeypatch.setattr(m, "get_target_store", lambda: s)
    monkeypatch.setattr("discovery.store.get_target_store", lambda: s)
    monkeypatch.setattr(auth_users, "_secret", lambda: SECRET)
    monkeypatch.setattr(m, "_email_enabled", lambda: True)
    monkeypatch.setattr(m, "_spawn", lambda coro: coro.close())  # nunca dispara e-mail/trial real
    monkeypatch.setattr(m, "_enqueue_scan", _noop_async)          # sem Redis/fila
    for b in (m._signup_attempts, m._signup_daily_attempts, m._signup_inline_hits,
              m._alert_autocreate_hits, m._alert_access_attempts, m._verify_check_hits,
              m._reset_attempts):
        b.clear()
    return s


async def _noop_async(*a, **k):
    return True


@pytest.fixture
def client(store):
    return TestClient(m.app, raise_server_exceptions=False)


def _bearer(user):
    return {"Authorization": f"Bearer {auth_users.create_user_token(user)}"}


# --------------------------------------------------------------------------- #
# 1. Signup com senha opcional (nível 1 vs 2)
# --------------------------------------------------------------------------- #

def test_signup_without_password_creates_level1(client, store):
    r = client.post("/account/signup", json={"email": "sem@empresa.com.br"})
    assert r.status_code == 200
    body = r.json()["user"]
    assert body["account_level"] == 1 and body["email_confirmed"] is False
    assert store.users["sem@empresa.com.br"]["password_hash"] is None


def test_signup_with_password_creates_level2(client, store):
    r = client.post("/account/signup", json={"email": "com@empresa.com.br", "password": "segredo123"})
    assert r.status_code == 200 and r.json()["user"]["account_level"] == 2


# --------------------------------------------------------------------------- #
# 2. Fluxo D — /account/signup-inline (passwordless + vincula domínio)
# --------------------------------------------------------------------------- #

def test_signup_inline_activates_monitoring_and_logs_in(client, store, monkeypatch):
    # KL-105 — monitoramento ATIVADO na hora (sem confirmação) + login imediato.
    monkeypatch.setattr(m, "_vigilia_allowed_types", _fake_allowed_types)
    r = client.post("/account/signup-inline",
                    json={"email": "dono@hotelx.com.br", "domain": "hotelx.com.br"})
    assert r.status_code == 200 and r.json()["status"] == "monitoring_active"
    u = store.users["dono@hotelx.com.br"]
    assert u["account_level"] == 1 and u["source"] == "inline" and u["email_confirmed"] is False
    # LOGA na hora (cookie de sessão de usuário)
    assert auth_users.USER_COOKIE in r.cookies
    # domínio vinculado + posse Tier 1 (e-mail == domínio) + vigílias criadas
    tid = store.by_url["https://hotelx.com.br"]
    assert (u["id"], tid) in store.links and store.links[(u["id"], tid)]["is_owner"] is True
    assert "hotelx.com.br" in {d for (_u, d, _t) in store.vigilias}


def test_signup_inline_existing_email(client, store):
    store.users["ja@x.com.br"] = {"id": 99, "email": "ja@x.com.br", "password_hash": "h",
                                  "account_level": 2}
    r = client.post("/account/signup-inline", json={"email": "ja@x.com.br", "domain": "x.com.br"})
    assert r.status_code == 200 and r.json()["status"] == "already_exists"


def test_signup_inline_disposable_blocked(client, store):
    r = client.post("/account/signup-inline",
                    json={"email": "x@mailinator.com", "domain": "x.com.br"})
    assert r.status_code == 400


def test_signup_inline_rate_limit(client, store, monkeypatch):
    monkeypatch.setattr(m, "_vigilia_allowed_types", _fake_allowed_types)
    for i in range(5):  # KL-105 — 5/min por IP
        assert client.post("/account/signup-inline",
                           json={"email": f"u{i}@x{i}.com.br", "domain": f"x{i}.com.br"}).status_code == 200
    r = client.post("/account/signup-inline", json={"email": "u9@x9.com.br", "domain": "x9.com.br"})
    assert r.status_code == 429


def test_signup_inline_existing_fires_no_cookie(client, store):
    # conta já existe → 200 already_exists, NÃO loga (o front dispara o magic link)
    store.users["ja2@x.com.br"] = {"id": 77, "email": "ja2@x.com.br", "password_hash": "h",
                                   "account_level": 2}
    r = client.post("/account/signup-inline", json={"email": "ja2@x.com.br", "domain": "x.com.br"})
    assert r.status_code == 200 and r.json()["status"] == "already_exists"
    assert auth_users.USER_COOKIE not in r.cookies


# --------------------------------------------------------------------------- #
# 2b. KL-105 — GET /account/monitoring-status (auth opcional)
# --------------------------------------------------------------------------- #

def test_monitoring_status_anonymous(client, store):
    r = client.get("/account/monitoring-status?domain=x.com.br")
    assert r.status_code == 200
    assert r.json() == {"logged_in": False, "monitoring": False}


def test_monitoring_status_logged_in_monitoring(client, store):
    u = {"id": 610, "email": "m@meusite.com.br", "password_hash": "h", "account_level": 2,
         "is_active": True}
    store.users[u["email"]] = u
    store.by_id[610] = u
    tid = store.add_target("meusite.com.br")
    store.links[(610, tid)] = {"is_owner": True, "verified_at": None, "method": None}
    r = client.get("/account/monitoring-status?domain=meusite.com.br", headers=_bearer(u))
    assert r.status_code == 200
    d = r.json()
    assert d["logged_in"] is True and d["monitoring"] is True and d["user_email"] == u["email"]


def test_monitoring_status_logged_in_not_monitoring(client, store):
    u = {"id": 611, "email": "n@outro.com.br", "password_hash": "h", "account_level": 2,
         "is_active": True}
    store.users[u["email"]] = u
    store.by_id[611] = u
    r = client.get("/account/monitoring-status?domain=naomonitoro.com.br", headers=_bearer(u))
    assert r.status_code == 200
    assert r.json()["logged_in"] is True and r.json()["monitoring"] is False


# --------------------------------------------------------------------------- #
# 3. Confirmação: ativa monitoramento + auto-login de conta sem senha
# --------------------------------------------------------------------------- #

def test_confirm_activates_monitoring(client, store, monkeypatch):
    # conta inline com um site pendente
    client.post("/account/signup-inline", json={"email": "d@hotelz.com.br", "domain": "hotelz.com.br"})
    uid = store.users["d@hotelz.com.br"]["id"]
    monkeypatch.setattr(m, "_vigilia_allowed_types", _fake_allowed_types)
    # chama o ativador diretamente (o _spawn dos testes fecha o coroutine)
    import asyncio
    asyncio.get_event_loop().run_until_complete(m._activate_monitoring_on_confirm(uid))
    assert ("hotelz.com.br" in {d for (_u, d, _t) in store.vigilias})


async def _fake_allowed_types(uid):
    return ["ssl", "score"]


def test_confirm_passwordless_autologin_to_dashboard(client, store):
    client.post("/account/signup-inline", json={"email": "p@site.com.br", "domain": "site.com.br"})
    u = store.users["p@site.com.br"]
    tok = m._make_confirm_token(u["id"], "p@site.com.br")
    r = client.post("/account/confirm", data={"token": tok}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/dashboard?confirmed=1"
    assert auth_users.USER_COOKIE in r.cookies  # logou


def test_confirm_password_account_normal_flow(client, store):
    u = client.post("/account/signup",
                    json={"email": "pw@site.com.br", "password": "segredo123"}).json()["user"]
    tok = m._make_confirm_token(u["id"], "pw@site.com.br")
    r = client.post("/account/confirm", data={"token": tok}, follow_redirects=False)
    assert r.headers["location"] == "/confirmado?status=ok"  # conta com senha: fluxo normal


# --------------------------------------------------------------------------- #
# 4. /account/set-password (nível 1 → 2)
# --------------------------------------------------------------------------- #

def _level1_user(store):
    return store.by_id[store.next_id - 1]


def test_set_password_promotes_to_level2(client, store):
    client.post("/account/signup", json={"email": "s@x.com.br"})   # nível 1
    u = store.users["s@x.com.br"]
    r = client.post("/account/set-password",
                    json={"password": "novaSenha123", "confirm": "novaSenha123"}, headers=_bearer(u))
    assert r.status_code == 200 and r.json()["account_level"] == 2
    assert u["account_level"] == 2 and u["password_hash"] is not None


def test_set_password_mismatch_422(client, store):
    client.post("/account/signup", json={"email": "s2@x.com.br"})
    u = store.users["s2@x.com.br"]
    r = client.post("/account/set-password",
                    json={"password": "aaaaaaaa1", "confirm": "bbbbbbbb1"}, headers=_bearer(u))
    assert r.status_code == 422


def test_set_password_already_has_password_400(client, store):
    u = client.post("/account/signup",
                    json={"email": "s3@x.com.br", "password": "segredo123"}).json()["user"]
    full = store.users["s3@x.com.br"]
    r = client.post("/account/set-password",
                    json={"password": "outraSenha1", "confirm": "outraSenha1"}, headers=_bearer(full))
    assert r.status_code == 400


# --------------------------------------------------------------------------- #
# 5. @require_level — 403 estruturado
# --------------------------------------------------------------------------- #

def test_require_level_blocks_level1_then_allows(client, store):
    client.post("/account/signup", json={"email": "g@x.com.br"})  # nível 1
    u = store.users["g@x.com.br"]
    r = client.put("/account/me", json={"name": "Novo"}, headers=_bearer(u))
    assert r.status_code == 403
    detail = r.json()["detail"]
    assert detail["error"] == "insufficient_level"
    assert detail["required_level"] == 2 and detail["current_level"] == 1
    # define senha → nível 2 → agora passa
    client.post("/account/set-password",
                json={"password": "segredo123", "confirm": "segredo123"}, headers=_bearer(u))
    assert client.put("/account/me", json={"name": "Novo"}, headers=_bearer(u)).status_code == 200


# --------------------------------------------------------------------------- #
# 6. Bug 1 fix — /alert-access NÃO cria conta; monitor-from-alert é quem cria
# --------------------------------------------------------------------------- #

def test_alert_access_does_not_create_account(client, store):
    tid = store.add_target("cliente.com.br")
    tok = alert_access_token("novo@cliente.com.br", tid, "cliente.com.br", SECRET)
    r = client.get(f"/alert-access?token={tok}", follow_redirects=False)
    assert r.status_code == 302 and "cliente.com.br" in r.headers["location"]
    # sessão de VISUALIZAÇÃO (cookie de alerta), SEM conta e SEM login
    assert m._ALERT_COOKIE in r.cookies
    assert auth_users.USER_COOKIE not in r.cookies
    assert "novo@cliente.com.br" not in store.users
    assert store.vigilias == []


def _alert_cookie(email, tid, domain):
    return {m._ALERT_COOKIE: m._make_alert_session_token(email, tid, domain)}


def test_monitor_from_alert_creates_account_and_monitors(client, store, monkeypatch):
    monkeypatch.setattr(m, "_vigilia_allowed_types", _fake_allowed_types)
    tid = store.add_target("cliente.com.br", contact_email="dono@cliente.com.br")
    r = client.post("/account/monitor-from-alert",
                    cookies=_alert_cookie("dono@cliente.com.br", tid, "cliente.com.br"))
    assert r.status_code == 200 and auth_users.USER_COOKIE in r.cookies  # logou
    u = store.users["dono@cliente.com.br"]
    assert u["account_level"] == 1 and u["source"] == "hmac" and u["email_confirmed"] is True
    assert u["password_hash"] is None
    # monitoramento ATIVO (site vinculado + vigílias) + dono verificado (e-mail == contato)
    assert (u["id"], tid) in store.links and store.links[(u["id"], tid)]["is_owner"] is True
    assert "cliente.com.br" in {d for (_u, d, _t) in store.vigilias}


def test_monitor_from_alert_existing_account(client, store):
    tid = store.add_target("cliente2.com.br")
    store.users["ja@cliente2.com.br"] = {"id": 500, "email": "ja@cliente2.com.br",
                                         "password_hash": "h", "account_level": 2}
    store.by_id[500] = store.users["ja@cliente2.com.br"]
    r = client.post("/account/monitor-from-alert",
                    cookies=_alert_cookie("ja@cliente2.com.br", tid, "cliente2.com.br"))
    assert r.status_code == 200 and r.json()["status"] == "existing_account"
    assert auth_users.USER_COOKIE not in r.cookies  # NÃO auto-loga conta existente


def test_monitor_from_alert_no_session_401(client, store):
    assert client.post("/account/monitor-from-alert").status_code == 401


def test_alert_access_invalid_token_home(client, store):
    r = client.get("/alert-access?token=lixo", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/"


# --------------------------------------------------------------------------- #
# 6b. Bug 2 — magic link (login sem senha)
# --------------------------------------------------------------------------- #

def test_magic_link_sent_for_existing_email(client, store):
    store.users["m@x.com.br"] = {"id": 700, "email": "m@x.com.br", "password_hash": None,
                                 "account_level": 1, "is_active": True}
    store.by_id[700] = store.users["m@x.com.br"]
    r = client.post("/account/magic-link", json={"email": "m@x.com.br"})
    assert r.status_code == 200 and r.json()["status"] == "sent"


def test_magic_link_not_found(client, store):
    r = client.post("/account/magic-link", json={"email": "naoexiste@x.com.br"})
    assert r.status_code == 200 and r.json()["status"] == "not_found"


def test_magic_link_rate_limit_per_email(client, store):
    store.users["r@x.com.br"] = {"id": 701, "email": "r@x.com.br", "account_level": 1, "is_active": True}
    store.by_id[701] = store.users["r@x.com.br"]
    for _ in range(3):
        assert client.post("/account/magic-link", json={"email": "r@x.com.br"}).status_code == 200
    assert client.post("/account/magic-link", json={"email": "r@x.com.br"}).status_code == 429


def test_magic_access_logs_in_and_redirects_dashboard(client, store):
    store.users["a@x.com.br"] = {"id": 702, "email": "a@x.com.br", "account_level": 1,
                                 "is_active": True, "plan": "free"}
    store.by_id[702] = store.users["a@x.com.br"]
    tok = m._make_magic_token("a@x.com.br")
    r = client.get(f"/account/magic-access?token={tok}", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/dashboard"
    assert auth_users.USER_COOKIE in r.cookies


def test_magic_access_invalid_redirects_expired(client, store):
    r = client.get("/account/magic-access?token=lixo", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/entrar?magic=expired"


# --------------------------------------------------------------------------- #
# 7. Verificação de domínio (nível 2 → 3)
# --------------------------------------------------------------------------- #

def _level2_user_with_site(client, store, domain="meusite.com.br"):
    u = client.post("/account/signup",
                    json={"email": f"dono@{domain}", "password": "segredo123"}).json()["user"]
    full = store.users[f"dono@{domain}"]
    tid = store.add_target(domain)
    store.links[(full["id"], tid)] = {"is_owner": False, "verified_at": None, "method": None}
    return full, tid


def test_verify_start_requires_level2(client, store):
    client.post("/account/signup", json={"email": "l1@x.com.br"})  # nível 1
    u = store.users["l1@x.com.br"]
    tid = store.add_target("x.com.br")
    store.links[(u["id"], tid)] = {"is_owner": False, "verified_at": None, "method": None}
    r = client.post(f"/account/sites/{tid}/verify/start", json={"method": "dns_txt"}, headers=_bearer(u))
    assert r.status_code == 403 and r.json()["detail"]["required_level"] == 2


def test_verify_start_returns_instructions(client, store):
    u, tid = _level2_user_with_site(client, store)
    r = client.post(f"/account/sites/{tid}/verify/start", json={"method": "meta_tag"}, headers=_bearer(u))
    assert r.status_code == 200
    body = r.json()
    assert body["method"] == "meta_tag" and "kl-" in body["instructions"]["snippet"]
    assert body["token"] and store.verifs[-1]["token"] == body["token"]


@pytest.mark.parametrize("method", ["meta_tag", "html_file", "dns_txt"])
def test_verify_check_verified_promotes_to_level3(client, store, monkeypatch, method):
    u, tid = _level2_user_with_site(client, store, domain=f"{method.replace('_', '')}.com.br")
    client.post(f"/account/sites/{tid}/verify/start", json={"method": method}, headers=_bearer(u))
    monkeypatch.setattr(m, "_check_domain_control", _always_true)
    r = client.post(f"/account/sites/{tid}/verify/check", headers=_bearer(u))
    assert r.status_code == 200 and r.json()["status"] == "verified"
    assert u["account_level"] == 3
    assert store.links[(u["id"], tid)]["is_owner"] is True
    assert tid in store.owner_verified


async def _always_true(*a, **k):
    return True


async def _always_false(*a, **k):
    return False


def test_verify_check_not_found(client, store, monkeypatch):
    u, tid = _level2_user_with_site(client, store)
    client.post(f"/account/sites/{tid}/verify/start", json={"method": "dns_txt"}, headers=_bearer(u))
    monkeypatch.setattr(m, "_check_domain_control", _always_false)
    r = client.post(f"/account/sites/{tid}/verify/check", headers=_bearer(u))
    assert r.status_code == 200 and r.json()["status"] == "not_found"
    assert u["account_level"] == 2  # não subiu


def test_verify_check_no_pending(client, store):
    u, tid = _level2_user_with_site(client, store)
    r = client.post(f"/account/sites/{tid}/verify/check", headers=_bearer(u))
    assert r.status_code == 200 and r.json()["status"] == "no_pending"


# --------------------------------------------------------------------------- #
# 8. _check_domain_control (puro, com fetchers monkeypatch)
# --------------------------------------------------------------------------- #

def test_check_domain_control_meta_tag(monkeypatch):
    import asyncio

    async def fake_fetch(url):
        return '<html><head><meta name="klarim-verify" content="kl-TOK123"></head></html>'

    monkeypatch.setattr(m, "_fetch_verify_page", fake_fetch)
    assert asyncio.get_event_loop().run_until_complete(
        m._check_domain_control("meta_tag", "TOK123", "site.com.br")) is True
    assert asyncio.get_event_loop().run_until_complete(
        m._check_domain_control("meta_tag", "OUTRO", "site.com.br")) is False


def test_check_domain_control_dns_txt(monkeypatch):
    import asyncio

    async def fake_txt(domain):
        return ["v=spf1 -all", "klarim-verify=ABC999"]

    monkeypatch.setattr(m, "_dns_txt_records", fake_txt)
    assert asyncio.get_event_loop().run_until_complete(
        m._check_domain_control("dns_txt", "ABC999", "site.com.br")) is True
    assert asyncio.get_event_loop().run_until_complete(
        m._check_domain_control("dns_txt", "ZZZ", "site.com.br")) is False


# --------------------------------------------------------------------------- #
# 9. Segurança — nível helper + sem vazamento
# --------------------------------------------------------------------------- #

def test_account_level_helper():
    assert m._account_level({"account_level": 1}) == 1
    assert m._account_level({"account_level": 3}) == 3
    assert m._account_level({}) == 2   # legado (sem coluna) → 2


def test_user_public_exposes_level_no_hash():
    pub = m._user_public({"id": 1, "email": "a@x.com", "account_level": 1, "password_hash": "SECRET"})
    assert pub["account_level"] == 1 and "password_hash" not in pub
