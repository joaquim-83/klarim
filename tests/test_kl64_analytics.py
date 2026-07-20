"""KL-64 — analytics correto: filtro `is_human` (bots/pre-fetch fora por padrão), export CSV
server-side, e o gatilho do e-mail `profile_view` humano-verificado (o SSR não dispara mais).

Offline: as queries `aa_*` são inspecionadas com um cursor que grava o SQL executado (sem DB);
os fluxos do `/events` são testados por unidade (sem `_spawn` real)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import api.main as m
from discovery.store import TargetStore

NOW = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
HUMAN = "is_human = TRUE OR is_human IS NULL"


# --------------------------------------------------------------------------- #
# 1. Filtro is_human nas queries aa_* (cursor que grava o SQL) + Part 1 (bound do email)
# --------------------------------------------------------------------------- #

class RecCursor:
    description: list = []   # p/ _rows_to_dicts → [] (sem colunas)

    def __init__(self):
        self.sqls: list = []

    def execute(self, sql, params=None):
        self.sqls.append(sql)   # grava o SQL ANTES de qualquer fetch

    def fetchall(self):
        return []

    def fetchone(self):
        return [0]

    def fetchmany(self, n):
        return []


def _capturing_store():
    s = TargetStore.__new__(TargetStore)   # sem __init__ (sem pool/DB)
    cur = RecCursor()
    s._run = lambda fn: fn(cur)            # aa_* chamam self._run(_fn) numa thread
    return s, cur


def _sql(coro):
    # Só nos importa o SQL gravado (via execute); resultados fake podem quebrar o unpack a
    # jusante — o SQL já está em cur.sqls quando isso acontece.
    try:
        return asyncio.run(coro)
    except Exception:
        return None


def test_aa_metrics_filters_human_by_default():
    s, cur = _capturing_store()
    _sql(s.aa_metrics_raw(NOW, NOW))
    site = [q for q in cur.sqls if "site_events" in q]
    assert site and all(HUMAN in q for q in site)  # visitors/pageviews/scans/alert_clicks


def test_aa_metrics_include_bots_drops_human_filter():
    s, cur = _capturing_store()
    _sql(s.aa_metrics_raw(NOW, NOW, include_bots=True))
    assert HUMAN not in " ".join(cur.sqls)


def test_aa_metrics_accounts_and_alerts_not_human_filtered():
    # users/alert_log não têm is_human — o filtro só entra nas queries de site_events.
    s, cur = _capturing_store()
    _sql(s.aa_metrics_raw(NOW, NOW))
    for q in cur.sqls:
        if "FROM users" in q or "FROM alert_log" in q:
            assert "is_human" not in q


def test_aa_funnel_emails_bounded_and_no_human_on_email_log():
    # Part 1: a etapa emails_sent filtra sent_at >= start AND < end (janela fechada) e NÃO
    # aplica is_human (email_log não tem o campo). As etapas de site_events aplicam.
    s, cur = _capturing_store()
    _sql(s.aa_funnel_raw(NOW, NOW))
    email_sql = next(q for q in cur.sqls if "email_log" in q)
    assert "sent_at >= %s AND sent_at < %s" in email_sql
    assert "is_human" not in email_sql
    site = [q for q in cur.sqls if "site_events" in q]
    assert site and all(HUMAN in q for q in site)


def test_aa_funnel_include_bots_drops_human():
    s, cur = _capturing_store()
    _sql(s.aa_funnel_raw(NOW, NOW, include_bots=True))
    assert HUMAN not in " ".join(cur.sqls)


def test_aa_events_human_filter():
    s, cur = _capturing_store()
    _sql(s.aa_events(NOW, NOW, None, None, None, None, 0, 50))
    assert all(HUMAN in q for q in cur.sqls if "site_events" in q)
    s2, cur2 = _capturing_store()
    _sql(s2.aa_events(NOW, NOW, None, None, None, None, 0, 50, include_bots=True))
    assert HUMAN not in " ".join(cur2.sqls)


def test_aa_pages_journeys_sessions_sector_human_filter():
    for coro_name, args in [("aa_pages_raw", (NOW, NOW, None)),
                            ("aa_journeys_raw", (NOW, NOW)),
                            ("aa_sessions", (NOW, NOW, 0, 20)),
                            ("aa_funnel_by_sector", (NOW, NOW))]:
        s, cur = _capturing_store()
        _sql(getattr(s, coro_name)(*args))
        joined = " ".join(cur.sqls)
        # aa_funnel_by_sector usa alias e.is_human; os demais is_human direto.
        assert "is_human" in joined, coro_name


def test_aa_events_export_human_filter_and_limit():
    s, cur = _capturing_store()
    _sql(s.aa_events_export(NOW, NOW, None, None, None, None, limit=10000))
    joined = " ".join(cur.sqls)
    assert HUMAN in joined
    assert "LIMIT %s" in joined  # busca até limit+1 (o +1 detecta truncamento)


def test_human_and_helper():
    assert TargetStore._human_and(False) == f" AND {TargetStore._AA_HUMAN}"
    assert TargetStore._human_and(True) == ""


# --------------------------------------------------------------------------- #
# 2. /events — is_human gravado + gatilho do e-mail profile_view SÓ humano
# --------------------------------------------------------------------------- #

class _RecStore:
    def __init__(self):
        self.events: list = []

    async def log_event(self, event_type, session_id, **kw):
        self.events.append({"event_type": event_type, **kw})
        return 1


def test_log_event_bg_passes_is_human(monkeypatch):
    st = _RecStore()
    monkeypatch.setattr(m, "get_target_store", lambda: st)
    body = m.EventBody(event_type="page_view", session_id="s1", verified_human=True)
    asyncio.run(m._log_event_bg(body, None))
    assert st.events and st.events[0]["is_human"] is True


def _events_client(monkeypatch, notify_calls):
    monkeypatch.setattr(m, "get_target_store", lambda: _RecStore())
    monkeypatch.setattr(m, "_spawn", lambda coro: coro.close())

    def fake_notify(domain, utm_campaign=""):
        notify_calls.append((domain, utm_campaign))
        async def _noop():
            return None
        return _noop()
    monkeypatch.setattr(m, "_profile_view_notify", fake_notify)
    from fastapi.testclient import TestClient
    return TestClient(m.app, raise_server_exceptions=False)


def test_profile_view_human_triggers_owner_email(monkeypatch):
    calls: list = []
    c = _events_client(monkeypatch, calls)
    r = c.post("/events", json={"event_type": "profile_view", "session_id": "s1",
                                "verified_human": True, "metadata": {"domain": "hotel.com.br"}})
    assert r.status_code == 200
    assert calls == [("hotel.com.br", "")]  # e-mail ao dono disparado (humano)


def test_profile_view_bot_does_not_email(monkeypatch):
    calls: list = []
    c = _events_client(monkeypatch, calls)
    # sem verified_human (bot/pre-fetch, sem interação) → NÃO notifica o dono.
    r = c.post("/events", json={"event_type": "profile_view", "session_id": "s1",
                                "metadata": {"domain": "hotel.com.br"}})
    assert r.status_code == 200 and calls == []


def test_page_view_never_triggers_email(monkeypatch):
    calls: list = []
    c = _events_client(monkeypatch, calls)
    c.post("/events", json={"event_type": "page_view", "session_id": "s1",
                            "verified_human": True, "page_url": "/site/hotel.com.br"})
    assert calls == []  # só profile_view dispara o e-mail


def test_domain_from_site_path():
    assert m._domain_from_site_path("/site/hotel.com.br") == "hotel.com.br"
    assert m._domain_from_site_path("/site/www.x.com/extra") == "x.com"
    assert m._domain_from_site_path("/scan") == ""
