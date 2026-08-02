"""KL-110 — testes da verificação de deliverability de e-mail (local + API Reoon)."""
import asyncio

import pytest

from notifier import email_verifier as ev
from discovery.alert_scoring import calculate_alert_score
from discovery import contact as contact_mod
from discovery.store import TargetStore


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #

class FakeRedis:
    def __init__(self):
        self.d = {}

    async def get(self, k):
        return self.d.get(k)

    async def set(self, k, v, ex=None):
        self.d[k] = v


class FakeResp:
    def __init__(self, data, status=200):
        self._data = data
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx
            raise httpx.HTTPStatusError("err", request=None, response=None)

    def json(self):
        return self._data


class FakeClient:
    """Cliente httpx falso: devolve `data` fixo em .get()."""
    def __init__(self, data):
        self.data = data
        self.calls = 0

    async def get(self, url, params=None, timeout=None):
        self.calls += 1
        return FakeResp(self.data)


# --------------------------------------------------------------------------- #
# Camada 0 — verify_local
# --------------------------------------------------------------------------- #

def test_verify_local_valid(monkeypatch):
    monkeypatch.setattr(ev, "_resolve_mx_sync", lambda d: "ok")
    r = asyncio.run(ev.verify_local("joao@empresa.com.br"))
    assert r.status == "valid" and r.reason == "ok" and not r.is_role_based


def test_verify_local_invalid_syntax():
    r = asyncio.run(ev.verify_local("nao-eh-email"))
    assert r.status == "invalid" and r.reason == "syntax"


def test_verify_local_disposable():
    # mailinator.com está na lista curada (KL-85). Não precisa de MX.
    r = asyncio.run(ev.verify_local("teste@mailinator.com"))
    assert r.status == "disposable" and r.reason == "disposable_domain"


def test_verify_local_no_mx(monkeypatch):
    monkeypatch.setattr(ev, "_resolve_mx_sync", lambda d: "no_mx")
    r = asyncio.run(ev.verify_local("contato@semmx.com"))
    assert r.status == "invalid" and r.reason == "no_mx"


def test_verify_local_role_based_flag(monkeypatch):
    monkeypatch.setattr(ev, "_resolve_mx_sync", lambda d: "ok")
    r = asyncio.run(ev.verify_local("contato@empresa.com.br"))
    assert r.status == "valid" and r.is_role_based is True


def test_verify_local_mx_unknown_is_fail_open(monkeypatch):
    # DNS incerto (timeout) → não rejeita (has_mx=True), status 'valid'.
    monkeypatch.setattr(ev, "_resolve_mx_sync", lambda d: "unknown")
    r = asyncio.run(ev.verify_local("x@dominio.com"))
    assert r.status == "valid"


def test_mx_cache_uses_redis(monkeypatch):
    calls = {"n": 0}

    def _resolve(d):
        calls["n"] += 1
        return "ok"

    monkeypatch.setattr(ev, "_resolve_mx_sync", _resolve)
    redis = FakeRedis()
    asyncio.run(ev.verify_local("a@dominio.com", redis))
    asyncio.run(ev.verify_local("b@dominio.com", redis))  # mesmo domínio → cache
    assert calls["n"] == 1  # só 1 resolução DNS


# --------------------------------------------------------------------------- #
# Camada 1 — Reoon parse + fallback
# --------------------------------------------------------------------------- #

def test_parse_reoon_power_safe():
    r = ev.parse_reoon_response({"status": "safe", "overall_score": 95}, "power")
    assert r.status == "safe" and r.score == 95 and r.source == "reoon"


def test_parse_reoon_role_account_maps_to_role():
    r = ev.parse_reoon_response({"status": "role_account", "is_role_account": True}, "power")
    assert r.status == "role" and r.is_role_based is True


def test_parse_reoon_catch_all():
    r = ev.parse_reoon_response({"status": "catch_all", "is_catch_all": True}, "power")
    assert r.status == "catch_all" and r.catch_all is True


# KL-128 — servidor catch-all engana o SMTP-check: o Reoon devolve safe/valid mas o servidor
# aceita QUALQUER caixa → rebaixa para catch_all (que passa pelo gate de score).
def test_parse_reoon_safe_plus_catch_all_demoted():
    r = ev.parse_reoon_response({"status": "safe", "overall_score": 95, "is_catch_all": True}, "power")
    assert r.status == "catch_all" and r.catch_all is True


def test_parse_reoon_valid_plus_catch_all_demoted():
    r = ev.parse_reoon_response({"status": "valid", "is_catch_all": True}, "power")
    assert r.status == "catch_all" and r.catch_all is True


def test_parse_reoon_safe_without_catch_all_unchanged():
    r = ev.parse_reoon_response({"status": "safe", "overall_score": 95, "is_catch_all": False}, "power")
    assert r.status == "safe" and r.catch_all is False


def test_parse_reoon_unknown_status():
    r = ev.parse_reoon_response({"status": "algo_estranho"}, "power")
    assert r.status == "unknown"


def test_verify_reoon_with_fake_client():
    client = FakeClient({"status": "safe", "overall_score": 90})
    r = asyncio.run(ev.verify_reoon("x@y.com", "power", api_key="k", client=client))
    assert r.status == "safe" and client.calls == 1


def test_verify_api_fallback_on_error(monkeypatch):
    async def _boom(*a, **k):
        raise TimeoutError("reoon down")

    monkeypatch.setattr(ev, "verify_reoon", _boom)
    r = asyncio.run(ev.verify_api("x@y.com", "power", api_key="k"))
    assert r.status == "unknown" and r.reason == "api_unavailable" and r.source == "fallback"


def test_verify_api_no_key_falls_back():
    # sem key, verify_reoon levanta RuntimeError → fallback unknown
    r = asyncio.run(ev.verify_api("x@y.com", "power", api_key=None))
    assert r.status == "unknown"


# --------------------------------------------------------------------------- #
# Pipeline verify_email + cache
# --------------------------------------------------------------------------- #

def test_verify_email_skip_api_returns_local(monkeypatch):
    monkeypatch.setattr(ev, "_resolve_mx_sync", lambda d: "ok")
    r = asyncio.run(ev.verify_email("joao@empresa.com", skip_api=True))
    assert r.status == "valid" and r.source == "local"


def test_verify_email_no_key_returns_local(monkeypatch):
    monkeypatch.setattr(ev, "_resolve_mx_sync", lambda d: "ok")
    r = asyncio.run(ev.verify_email("joao@empresa.com", mode="power", api_key=""))
    assert r.status == "valid"  # sem key → só Camada 0


def test_verify_email_invalid_short_circuits_api(monkeypatch):
    called = {"api": False}

    async def _api(*a, **k):
        called["api"] = True
        return ev.VerifyResult("safe", "x")

    monkeypatch.setattr(ev, "_resolve_mx_sync", lambda d: "no_mx")
    monkeypatch.setattr(ev, "verify_api", _api)
    r = asyncio.run(ev.verify_email("x@nomx.com", api_key="k"))
    assert r.status == "invalid" and called["api"] is False  # não gasta crédito


def test_verify_email_cache_hit(monkeypatch):
    monkeypatch.setattr(ev, "_resolve_mx_sync", lambda d: "ok")
    calls = {"api": 0}

    async def _api(email, mode, key, client, role_hint=False):
        calls["api"] += 1
        return ev.VerifyResult("safe", "reoon_power", source="reoon")

    monkeypatch.setattr(ev, "verify_api", _api)
    redis = FakeRedis()
    r1 = asyncio.run(ev.verify_email("z@empresa.com", redis=redis, api_key="k"))
    r2 = asyncio.run(ev.verify_email("z@empresa.com", redis=redis, api_key="k"))
    assert r1.status == "safe" and r2.status == "safe"
    assert r2.cached is True and calls["api"] == 1  # 2ª vez veio do cache


def test_verify_email_domain_catch_all_cache(monkeypatch):
    monkeypatch.setattr(ev, "_resolve_mx_sync", lambda d: "ok")

    async def _api(email, mode, key, client, role_hint=False):
        return ev.VerifyResult("catch_all", "reoon_power", source="reoon", catch_all=True)

    monkeypatch.setattr(ev, "verify_api", _api)
    redis = FakeRedis()
    asyncio.run(ev.verify_email("a@catchall.com", redis=redis, api_key="k"))
    # outro e-mail do MESMO domínio → resolvido pelo cache de domínio, sem nova chamada
    monkeypatch.setattr(ev, "verify_api",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("não deveria chamar")))
    r = asyncio.run(ev.verify_email("b@catchall.com", redis=redis, api_key="k"))
    assert r.status == "catch_all" and r.cached is True


# --------------------------------------------------------------------------- #
# is_safe_to_send
# --------------------------------------------------------------------------- #

# KL-137 — regra BINÁRIA: só safe/valid/role enviam; todo o resto (catch_all/unknown/inbox_full/
# block-statuses/vazio) NÃO envia. O score é IGNORADO na decisão (só ordena).
@pytest.mark.parametrize("status,score,expected", [
    ("safe", 0, True),
    ("valid", 0, True),
    ("role", 0, True),
    ("invalid", 90, False),
    ("disabled", 90, False),
    ("disposable", 90, False),
    ("spamtrap", 90, False),
    ("unknown", 100, False),
    ("unknown", 0, False),
    ("catch_all", 25, False),    # KL-137: catch_all nunca envia (score irrelevante)
    ("catch_all", 100, False),
    ("inbox_full", 51, False),   # KL-137: inbox_full nunca envia
    ("inbox_full", 21, False),
    ("", 100, False),
])
def test_is_safe_to_send(status, score, expected):
    assert ev.is_safe_to_send(ev.VerifyResult(status, "x"), score) is expected


def test_is_safe_to_send_ignores_score(monkeypatch):
    # KL-137: o lead_score NÃO entra na decisão — safe sempre, catch_all nunca (qualquer score).
    assert ev.is_safe_to_send(ev.VerifyResult("safe", "x"), 0) is True
    assert ev.is_safe_to_send(ev.VerifyResult("catch_all", "x"), 999) is False
    assert ev.is_safe_to_send(ev.VerifyResult("unknown", "x"), 999) is False


def test_sendable_statuses_constant():
    # A regra binária vive na constante SENDABLE_STATUSES (sem gates/funções de env).
    assert ev.SENDABLE_STATUSES == frozenset({"safe", "valid", "role"})
    assert not hasattr(ev, "_unsafe_score_gate")
    assert not hasattr(ev, "_catch_all_gate")


# --------------------------------------------------------------------------- #
# Semáforo — máx 5 chamadas simultâneas à Reoon
# --------------------------------------------------------------------------- #

def test_reoon_semaphore_limits_concurrency():
    state = {"cur": 0, "peak": 0}

    class SlowClient:
        async def get(self, url, params=None, timeout=None):
            state["cur"] += 1
            state["peak"] = max(state["peak"], state["cur"])
            await asyncio.sleep(0.02)
            state["cur"] -= 1
            return FakeResp({"status": "safe"})

    async def _run():
        client = SlowClient()
        await asyncio.gather(*[
            ev.verify_reoon(f"u{i}@x.com", "power", api_key="k", client=client)
            for i in range(12)])

    asyncio.run(_run())
    assert state["peak"] <= 5  # nunca passa do semáforo


# --------------------------------------------------------------------------- #
# Lead scoring — penalidades por status de verificação (KL-110)
# --------------------------------------------------------------------------- #

def test_lead_scoring_catch_all_no_penalty():
    # KL-137: as penalidades de deliverability (catch_all/unknown) saíram — a deliverability é
    # decidida binariamente pelo is_safe_to_send, não pelo score. O score só ordena.
    t = {"domain": "x.com", "last_scan_score": 60, "email_verify_status": "catch_all"}
    out = calculate_alert_score(t, "pessoa@outrodominio.com")
    assert not any(s["signal"] == "email_catch_all" for s in out["signals"])


def test_lead_scoring_unknown_no_penalty():
    t = {"domain": "x.com", "last_scan_score": 60, "email_verify_status": "unknown"}
    out = calculate_alert_score(t, "pessoa@outrodominio.com")
    assert not any(s["signal"] == "email_unknown" for s in out["signals"])


def test_lead_scoring_role_status_no_double_penalty():
    # KL-136: prefixo 'contato' já penaliza -5; o status 'role' NÃO deve dobrar.
    t = {"domain": "x.com", "last_scan_score": 60, "email_verify_status": "role"}
    out = calculate_alert_score(t, "contato@x.com")
    role_signals = [s for s in out["signals"] if "role" in s["signal"]]
    assert len(role_signals) == 1 and role_signals[0]["signal"] == "role_based_prefix"
    assert role_signals[0]["points"] == -5   # KL-136: penalidade reduzida


def test_lead_scoring_role_status_penalizes_when_prefix_absent():
    # KL-136: o status 'role' da Reoon (prefixo fora da lista) penaliza com a MESMA penalidade -5.
    t = {"domain": "x.com", "last_scan_score": 60, "email_verify_status": "role"}
    out = calculate_alert_score(t, "joao@x.com")  # prefixo não é role
    assert any(s["signal"] == "email_role_account" and s["points"] == -5 for s in out["signals"])


# --------------------------------------------------------------------------- #
# Extração — descartável nunca vira contact_email (Camada 0 preventiva)
# --------------------------------------------------------------------------- #

def test_contact_is_junk_rejects_disposable():
    assert contact_mod._is_junk("qualquer@mailinator.com") is True
    assert contact_mod._is_junk("contato@empresareal.com.br") is False


# --------------------------------------------------------------------------- #
# Store — email_verification_stats (offline via cursor falso)
# --------------------------------------------------------------------------- #

class _FakeCursor:
    """KL-125: `email_verification_stats` roda 2 queries (by_status + by_source). Devolve o
    conjunto certo pela SQL (`email_verify_source` na 2ª)."""
    def __init__(self, rows, source_rows=None):
        self._rows = rows
        self._source_rows = source_rows or []
        self.sql = ""

    def execute(self, sql, params=None):
        self.sql = sql

    def fetchall(self):
        return self._source_rows if "email_verify_source" in self.sql else self._rows


class _StatsStore(TargetStore):
    def __init__(self, rows, source_rows=None):
        self._rows = rows
        self._source_rows = source_rows or []

    def _run(self, fn):
        return fn(_FakeCursor(self._rows, self._source_rows))


def test_email_verification_stats_mapping():
    rows = [("safe", 4100, 0), ("invalid", 300, 10), ("catch_all", 500, 5),
            ("role", 200, 200), (None, 3523, 0)]
    source_rows = [("power", 1500), ("bulk", 3200), (None, 3923)]  # KL-125
    out = asyncio.run(_StatsStore(rows, source_rows).email_verification_stats())
    assert out["total_with_email"] == 4100 + 300 + 500 + 200 + 3523
    assert out["unverified"] == 3523
    assert out["verified"] == 4100 + 300 + 500 + 200
    assert out["by_status"]["safe"] == 4100
    assert out["by_status"]["unverified"] == 3523
    assert out["role_based_total"] == 215
    # KL-125: breakdown por fonte de verificação.
    assert out["by_source"]["power"] == 1500
    assert out["by_source"]["bulk"] == 3200
    assert out["by_source"]["unverified"] == 3923


# --------------------------------------------------------------------------- #
# Alert worker — _verify_and_filter (gate de deliverability pré-envio)
# --------------------------------------------------------------------------- #

class _MiniStore:
    def __init__(self):
        self.blocked, self.discarded, self.verified = [], [], []

    async def block_email(self, email, reason="bounced"):
        self.blocked.append((email, reason))

    async def update_status(self, target_id, status):
        self.discarded.append((target_id, status))

    async def update_target_email_verification(self, tid, status, is_role_based,
                                               verified=True, source=None):
        self.verified.append((tid, status, source))


def _mk_worker(store):
    from discovery.alert_worker import AlertWorker
    w = AlertWorker()
    w.store = store
    w._redis = False  # sem redis nos testes
    return w


def test_verify_and_filter_blocks_and_gates(monkeypatch):
    # KL-137: regra BINÁRIA. safe→envia; invalid→blocklist+descarta; catch_all→bloqueia (score
    # irrelevante — os DOIS catch_all bloqueiam mesmo com score alto).
    monkeypatch.setenv("REOON_API_KEY", "test-key")

    async def _fake_verify(email, mode="power", redis=None, api_key=None):
        table = {
            "safe@x.com": ev.VerifyResult("safe", "reoon_power", source="reoon"),
            "bad@x.com": ev.VerifyResult("invalid", "reoon_power", source="reoon"),
            "catch@x.com": ev.VerifyResult("catch_all", "reoon_power", source="reoon", catch_all=True),
        }
        return table[email]

    async def _bal(api_key=None, client=None):
        return 5000
    monkeypatch.setattr(ev, "verify_email", _fake_verify)
    monkeypatch.setattr(ev, "check_balance", _bal)
    store = _MiniStore()
    w = _mk_worker(store)
    targets = [
        {"id": 1, "contact_email": "safe@x.com", "_alert_score": 40},
        {"id": 2, "contact_email": "bad@x.com", "_alert_score": 40},
        {"id": 3, "contact_email": "catch@x.com", "_alert_score": 10},   # catch_all → bloqueia
        {"id": 4, "contact_email": "catch@x.com", "_alert_score": 60},   # catch_all → bloqueia (score alto NÃO salva)
    ]
    kept, stats = asyncio.run(w._verify_and_filter(targets))
    assert {t["id"] for t in kept} == {1}
    assert stats["sendable"] == 1 and stats["blocked"] == 3 and stats["verified"] == 4
    assert ("bad@x.com", "power_verify_invalid") in store.blocked  # prefixo power_
    assert (2, "descartado") in store.discarded
    assert (1, "safe", "power") in store.verified  # grava source='power'


def test_verify_and_filter_noop_without_key(monkeypatch):
    # KL-137: sem REOON_API_KEY (modo degradado) — não-verificado passa (MX já validado); o já
    # verificado segue a regra binária pelo status cacheado (unknown é bloqueado, não descartado).
    monkeypatch.delenv("REOON_API_KEY", raising=False)
    store = _MiniStore()
    w = _mk_worker(store)
    targets = [{"id": 1, "contact_email": "a@x.com", "_alert_score": 40},              # não verificado → passa
               {"id": 2, "contact_email": "b@x.com", "_alert_score": 40,               # verificado safe → passa
                "email_verified": True, "email_verify_status": "safe"},
               {"id": 3, "contact_email": "c@x.com", "_alert_score": 10,               # verificado unknown → bloqueia
                "email_verified": True, "email_verify_status": "unknown"}]
    kept, stats = asyncio.run(w._verify_and_filter(targets))
    assert {t["id"] for t in kept} == {1, 2}
    assert stats["from_cache"] == 2 and stats["blocked"] == 1 and not store.blocked


def test_verify_and_filter_fresh_skips_api(monkeypatch):
    from datetime import datetime, timezone
    monkeypatch.setenv("REOON_API_KEY", "test-key")

    async def _boom(*a, **k):
        raise AssertionError("não deveria chamar a API para alvo fresco")

    monkeypatch.setattr(ev, "verify_email", _boom)
    store = _MiniStore()
    w = _mk_worker(store)
    targets = [{"id": 1, "contact_email": "a@x.com", "_alert_score": 40,
                "email_verified": True, "email_verify_status": "safe",
                "email_verified_at": datetime.now(timezone.utc)}]
    kept, stats = asyncio.run(w._verify_and_filter(targets))
    assert {t["id"] for t in kept} == {1} and stats["from_cache"] == 1
