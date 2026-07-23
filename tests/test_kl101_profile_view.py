"""KL-101 — profile_view isolado no subdomínio dedicado `perfil.klarim.net`:
remetente próprio, texto puro sem links, opt-out por resposta, dedup por dono (1/dia) +
teto diário de warmup. O `klarim.net` fica 100% transacional. Offline.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import api.main as m
from notifier.email_client import KlarimMailer, build_profile_view_text


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# --- remetente + template ---------------------------------------------------- #

def test_profile_view_from_default_and_override(monkeypatch):
    monkeypatch.delenv("PROFILE_VIEW_FROM_EMAIL", raising=False)
    monkeypatch.delenv("PROFILE_VIEW_FROM_NAME", raising=False)
    assert KlarimMailer("re_x", "Klarim <klarim@klarim.net>")._profile_view_from() \
        == "Klarim <notifica@perfil.klarim.net>"
    monkeypatch.setenv("PROFILE_VIEW_FROM_EMAIL", "avisos@perfil.klarim.net")
    assert "avisos@perfil.klarim.net" in KlarimMailer("re_x")._profile_view_from()


def test_profile_view_text_has_no_links():
    t = build_profile_view_text("igoove.com.br")
    assert "igoove.com.br foi consultado" in t
    for marker in ("http://", "https://", "www.", "<a ", "href", "utm_"):
        assert marker not in t.lower()
    assert '"remover"' in t and "klarim.net" in t


# --- _profile_view_notify: dedup por dono + teto diário ---------------------- #

class _FakeRedis:
    def __init__(self):
        self.d: dict = {}

    async def get(self, k):
        return self.d.get(k)

    async def set(self, k, v, nx=False, ex=None):
        if nx and k in self.d:
            return None
        self.d[k] = v
        return True

    async def incr(self, k):
        self.d[k] = int(self.d.get(k, 0)) + 1
        return self.d[k]

    async def expire(self, k, ttl):
        return True


class _FakeStore:
    def __init__(self, by_domain, setting="200"):
        self._by_domain = by_domain
        self._setting = setting

    async def get_target_by_domain(self, d):
        return self._by_domain.get(d)

    async def get_setting(self, k, default=None):
        return self._setting if k == "PROFILE_VIEW_DAILY_LIMIT" else default

    async def get_user_by_email(self, e):
        return None


class _FakeMailer:
    def __init__(self):
        self.calls = []

    async def send_profile_view(self, email, domain, score, semaphore, cta, target_id=None):
        self.calls.append((email, domain))
        return {"email_id": f"e{len(self.calls)}"}


def _wire(monkeypatch, by_domain, setting="200"):
    redis = _FakeRedis()
    store = _FakeStore(by_domain, setting)
    mailer = _FakeMailer()
    monkeypatch.setattr(m, "_cache", SimpleNamespace(redis=redis))
    monkeypatch.setattr(m, "get_target_store", lambda: store)
    monkeypatch.setattr(m, "_mailer", lambda: mailer)
    monkeypatch.setattr(m, "_email_enabled", lambda: True)
    return mailer, redis


def test_notify_sends_once_then_dedupes_same_domain(monkeypatch):
    t = {"contact_email": "dono@x.com.br", "status": "scanned", "id": 1, "last_scan_score": 66}
    mailer, _ = _wire(monkeypatch, {"x.com.br": t})
    _run(m._profile_view_notify("x.com.br"))
    _run(m._profile_view_notify("x.com.br"))   # 2ª consulta no mesmo dia → deduped
    assert mailer.calls == [("dono@x.com.br", "x.com.br")]


def test_notify_dedupes_per_owner_across_domains(monkeypatch):
    # mesmo dono, 2 domínios → 1 e-mail só (dedup por DONO, KL-101)
    t1 = {"contact_email": "dono@x.com.br", "status": "scanned", "id": 1, "last_scan_score": 66}
    t2 = {"contact_email": "dono@x.com.br", "status": "scanned", "id": 2, "last_scan_score": 70}
    mailer, _ = _wire(monkeypatch, {"a.com.br": t1, "b.com.br": t2})
    _run(m._profile_view_notify("a.com.br"))
    _run(m._profile_view_notify("b.com.br"))
    assert len(mailer.calls) == 1


def test_notify_respects_daily_cap(monkeypatch):
    t = {"contact_email": "dono@x.com.br", "status": "scanned", "id": 1, "last_scan_score": 66}
    mailer, redis = _wire(monkeypatch, {"x.com.br": t}, setting="0")   # teto 0 → nada sai
    _run(m._profile_view_notify("x.com.br"))
    assert mailer.calls == []


def test_notify_increments_daily_counter_on_send(monkeypatch):
    import datetime as _dt
    t = {"contact_email": "dono@x.com.br", "status": "scanned", "id": 1, "last_scan_score": 66}
    mailer, redis = _wire(monkeypatch, {"x.com.br": t})
    _run(m._profile_view_notify("x.com.br"))
    daykey = "profileview:daily:" + _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%d")
    assert redis.d.get(daykey) == 1 and len(mailer.calls) == 1


def test_profile_view_daily_limit_is_editable():
    meta = m._CONFIG_PARAMS["PROFILE_VIEW_DAILY_LIMIT"]
    assert meta["default"] == "200" and meta["min"] == 0
