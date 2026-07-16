"""Testes das vigílias avançadas (KL-44 P4): uptime, mudanças, typosquat/phishing +
config bool do boletim. Offline (sem rede, sem Postgres) — httpx e o store são fakes."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import api.vigilias as vig
from discovery import typosquat as ts


def _now():
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# A) typosquat — funções puras
# --------------------------------------------------------------------------- #

def test_levenshtein_basic():
    assert ts.levenshtein("abc", "abc") == 0
    assert ts.levenshtein("abc", "abd") == 1
    assert ts.levenshtein("gato", "rato") == 1
    assert ts.levenshtein("", "abc") == 3


def test_typosquat_levenshtein():
    hit = ts.is_typosquat("usecognato.com.br", "usecognatoo.com.br")
    assert hit and hit[0] == "levenshtein" and hit[1] == 1


def test_typosquat_homoglyph():
    hit = ts.is_typosquat("google.com.br", "g00gle.com.br")
    assert hit and hit[0] == "homoglyph"


def test_typosquat_tld_variant():
    hit = ts.is_typosquat("usecognato.com.br", "usecognato.net")
    assert hit and hit[0] == "tld_variant"


def test_typosquat_ignores_self_and_different():
    assert ts.is_typosquat("usecognato.com.br", "usecognato.com.br") is None
    assert ts.is_typosquat("usecognato.com.br", "totalmentediferente.com.br") is None


def test_typosquat_short_name_only_tld_variant():
    # nomes curtos (<4) só disparam por variação de TLD do mesmo nome exato
    assert ts.is_typosquat("abc.com.br", "abd.com.br") is None
    hit = ts.is_typosquat("abc.com.br", "abc.net")
    assert hit and hit[0] == "tld_variant"


def test_similarity_label():
    assert "digitação" in ts.similarity_label("levenshtein")
    assert ts.similarity_label("desconhecido") == "domínio parecido"


# --------------------------------------------------------------------------- #
# B) Fakes de rede/store
# --------------------------------------------------------------------------- #

class FakeResp:
    def __init__(self, status=200, text="", headers=None):
        self.status_code = status
        self.text = text
        self.headers = headers or {}


class FakeClient:
    """Client httpx fake — devolve uma resposta fixa (ou levanta)."""
    def __init__(self, resp=None, raise_exc=None):
        self._resp = resp
        self._raise = raise_exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, **kw):
        if self._raise:
            raise self._raise
        return self._resp


class UptimeStore:
    def __init__(self):
        self.targets = {"x.com.br": {"id": 1, "domain": "x.com.br", "url": "https://x.com.br"}}

    async def get_target_by_domain(self, d):
        return self.targets.get(d.lower().strip())


# --------------------------------------------------------------------------- #
# C) check_uptime_vigilia
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_uptime_three_failures_then_alert(monkeypatch):
    store = UptimeStore()

    async def down(url, timeout=10.0):
        return {"ok": False, "status_code": 0, "response_time_ms": 0, "error": "conn refused"}
    monkeypatch.setattr(vig, "check_uptime", down)

    data = {}
    # 1ª e 2ª falha: acumula, sem alerta
    r1 = await vig.check_uptime_vigilia(store, "x.com.br", data)
    assert r1["should_alert"] is False and r1["data"]["consecutive_failures"] == 1
    r2 = await vig.check_uptime_vigilia(store, "x.com.br", r1["data"])
    assert r2["should_alert"] is False and r2["data"]["consecutive_failures"] == 2
    # 3ª falha: alerta crítico
    r3 = await vig.check_uptime_vigilia(store, "x.com.br", r2["data"])
    assert r3["should_alert"] is True and r3["severity"] == "critical"
    assert r3["data"]["down_since"]
    # 4ª falha dentro de 1h: anti-spam, sem novo alerta
    r4 = await vig.check_uptime_vigilia(store, "x.com.br", r3["data"])
    assert r4["should_alert"] is False


@pytest.mark.asyncio
async def test_uptime_recovery_alert(monkeypatch):
    store = UptimeStore()
    down_since = (_now() - timedelta(minutes=45)).isoformat()
    data = {"consecutive_failures": 3, "down_since": down_since,
            "last_down_alert_at": down_since}

    async def up(url, timeout=10.0):
        return {"ok": True, "status_code": 200, "response_time_ms": 120, "error": None}
    monkeypatch.setattr(vig, "check_uptime", up)

    r = await vig.check_uptime_vigilia(store, "x.com.br", data)
    assert r["should_alert"] is True and r["severity"] == "info"
    assert "voltou ao ar" in r["title"]
    assert r["data"]["down_since"] is None and r["data"]["consecutive_failures"] == 0


@pytest.mark.asyncio
async def test_uptime_healthy_no_alert(monkeypatch):
    store = UptimeStore()

    async def up(url, timeout=10.0):
        return {"ok": True, "status_code": 200, "response_time_ms": 90, "error": None}
    monkeypatch.setattr(vig, "check_uptime", up)

    r = await vig.check_uptime_vigilia(store, "x.com.br", {})
    assert r["should_alert"] is False and r["status"] == "ok"


@pytest.mark.asyncio
async def test_check_uptime_never_raises(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("network down")
    monkeypatch.setattr(vig.httpx, "AsyncClient", lambda **k: FakeClient(raise_exc=RuntimeError("x")))
    r = await vig.check_uptime("https://x.com.br")
    assert r["ok"] is False and r["status_code"] == 0


# --------------------------------------------------------------------------- #
# D) check_changes + _snapshot
# --------------------------------------------------------------------------- #

def test_snapshot_fields():
    snap = vig._snapshot("<html><title>Oi</title><script>x</script><form></form></html>",
                         {"Server": "nginx", "X-Frame-Options": "DENY"}, 200)
    assert snap["title"] == "Oi" and snap["scripts_count"] == 1 and snap["forms_count"] == 1
    assert snap["status_code"] == 200 and len(snap["content_hash"]) == 16


@pytest.mark.asyncio
async def test_changes_baseline_no_alert(monkeypatch):
    store = UptimeStore()
    resp = FakeResp(200, "<html><title>A</title></html>", {"Server": "nginx"})
    monkeypatch.setattr(vig.httpx, "AsyncClient", lambda **k: FakeClient(resp))
    r = await vig.check_changes(store, "x.com.br", {})
    assert r["should_alert"] is False and r["data"]["snapshot"]["title"] == "A"


@pytest.mark.asyncio
async def test_changes_significant_alert(monkeypatch):
    store = UptimeStore()
    prev = vig._snapshot("<html><title>Loja A</title>" + "x" * 1000 + "</html>",
                         {"Server": "nginx"}, 200)
    # nova página: título mudou + form de phishing apareceu
    resp = FakeResp(200, "<html><title>Pagina Hackeada</title>" + "y" * 100
                    + "<form></form></html>", {"Server": "nginx"})
    monkeypatch.setattr(vig.httpx, "AsyncClient", lambda **k: FakeClient(resp))
    r = await vig.check_changes(store, "x.com.br", {"snapshot": prev})
    assert r["should_alert"] is True and r["severity"] == "warning"
    assert "formulários apareceram" in r["message"] or "título mudou" in r["message"]


@pytest.mark.asyncio
async def test_changes_no_change_no_alert(monkeypatch):
    store = UptimeStore()
    html = "<html><title>Estavel</title></html>"
    prev = vig._snapshot(html, {"Server": "nginx"}, 200)
    resp = FakeResp(200, html, {"Server": "nginx"})
    monkeypatch.setattr(vig.httpx, "AsyncClient", lambda **k: FakeClient(resp))
    r = await vig.check_changes(store, "x.com.br", {"snapshot": prev})
    assert r["should_alert"] is False


# --------------------------------------------------------------------------- #
# E) check_typosquat (phishing) — event-driven via store
# --------------------------------------------------------------------------- #

class TypoStore(UptimeStore):
    def __init__(self):
        super().__init__()
        self.pending = []
        self.notified = []

    async def get_pending_typosquats(self, target_id):
        return list(self.pending)

    async def mark_typosquats_notified(self, ids):
        self.notified.extend(ids)


@pytest.mark.asyncio
async def test_typosquat_no_pending_no_alert():
    store = TypoStore()
    r = await vig.check_typosquat(store, "x.com.br", {})
    assert r["should_alert"] is False and r["status"] == "ok"


@pytest.mark.asyncio
async def test_typosquat_alerts_and_marks_notified():
    store = TypoStore()
    store.pending = [{"id": 5, "suspicious_domain": "x-loja.com.br",
                      "similarity_type": "levenshtein", "distance": 1}]
    r = await vig.check_typosquat(store, "x.com.br", {})
    assert r["should_alert"] is True and r["severity"] == "critical"
    assert "x-loja.com.br" in r["message"]
    assert store.notified == [5]  # marcou como notificado


# --------------------------------------------------------------------------- #
# F) Uptime worker cycle
# --------------------------------------------------------------------------- #

def _async_ret(value):
    async def _fn(*a, **k):
        return value
    return _fn


class UptimeWorkerStore(UptimeStore):
    def __init__(self):
        super().__init__()
        self.updated = []
        self.alerts = []
        self.disabled_calls = []
        self._aid = 1

    async def get_due_uptime_vigilias(self, limit=200):
        return [{"id": 10, "user_id": 5, "site_domain": "x.com.br", "tipo": "uptime",
                 "last_status": "ok", "last_data": {"consecutive_failures": 2},
                 "alert_count": 0, "user_email": "u5@x.com", "interval_minutes": 5}]

    async def update_vigilia_after_check(self, vid, status, data, next_at, alerted=False):
        self.updated.append((vid, status, alerted))

    async def create_vigilia_alert(self, *a, **k):
        aid = self._aid
        self._aid += 1
        self.alerts.append(aid)
        return aid

    async def mark_vigilia_alert_sent(self, *a, **k):
        pass

    async def disable_user_vigilias_except(self, user_id, keep):
        self.disabled_calls.append(user_id)
        return 0


@pytest.mark.asyncio
async def test_uptime_cycle_alerts_on_third_failure(monkeypatch):
    from discovery import vigilia_worker as vw
    store = UptimeWorkerStore()

    async def down(url, timeout=10.0):
        return {"ok": False, "status_code": 0, "response_time_ms": 0, "error": "refused"}
    monkeypatch.setattr(vig, "check_uptime", down)
    monkeypatch.setattr(vw, "get_target_store", lambda: store)
    monkeypatch.setattr(vw._plans, "get_subscription",
                        _async_ret({"plan": {"vigilia_uptime": True}}))
    monkeypatch.setattr(vw.worker_control, "is_enabled", lambda w: True)
    worker = vw.VigiliaWorker()
    stats = await worker.run_uptime_cycle()
    assert stats["checked"] == 1 and stats["alerts"] == 1
    assert store.updated and store.updated[0][2] is True  # alerted


@pytest.mark.asyncio
async def test_uptime_cycle_plan_enforcement(monkeypatch):
    from discovery import vigilia_worker as vw
    store = UptimeWorkerStore()
    monkeypatch.setattr(vw, "get_target_store", lambda: store)
    monkeypatch.setattr(vw._plans, "get_subscription",
                        _async_ret({"plan": {"vigilia_uptime": False}}))
    monkeypatch.setattr(vw.worker_control, "is_enabled", lambda w: True)
    worker = vw.VigiliaWorker()
    stats = await worker.run_uptime_cycle()
    assert stats["skipped_plan"] == 1 and stats["checked"] == 0
    assert store.disabled_calls == [5]


@pytest.mark.asyncio
async def test_uptime_cycle_paused(monkeypatch):
    from discovery import vigilia_worker as vw
    store = UptimeWorkerStore()
    monkeypatch.setattr(vw, "get_target_store", lambda: store)
    monkeypatch.setattr(vw.worker_control, "is_enabled", lambda w: False)
    worker = vw.VigiliaWorker()
    stats = await worker.run_uptime_cycle()
    assert stats.get("disabled") is True


# --------------------------------------------------------------------------- #
# G) Template genérico + dispatcher conhece os novos tipos
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_generic_template_renders(monkeypatch):
    from notifier.email_client import KlarimMailer
    mailer = KlarimMailer("re_test", "Klarim <x@klarim.net>", store=None)
    sent = {}

    async def fake_send(params, **kw):
        sent["html"] = params["html"]
        sent["email_type"] = kw.get("email_type")
        return {"email_id": "e1"}
    monkeypatch.setattr(mailer, "_send", fake_send)

    for tipo in ("uptime", "changes", "phishing"):
        await mailer.send_vigilia_alert(
            to_email="dono@x.com", tipo=tipo, domain="x.com.br", subject="s",
            title="t", message="linha1\nlinha2", action_text="faça X",
            severity="critical", data={})
        assert "{{" not in sent["html"] and sent["email_type"] == f"vigilia_{tipo}"
        assert "x.com.br" in sent["html"]


def test_dispatcher_knows_new_types():
    assert "changes" in vig._CHECKERS and "phishing" in vig._CHECKERS
    assert "uptime" not in vig._CHECKERS  # roda no loop curto, fora do dispatcher
    assert set(vig.VIGILIA_TYPES) >= {"uptime", "changes", "phishing"}
