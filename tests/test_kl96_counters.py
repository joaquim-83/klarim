"""KL-96 — contadores de alertas + consultas de perfil unificados no `email_log`.

Antes a página Alertas contava de `alert_log` (tabela paralela) e divergia do funil do
Analytics (que já usa `email_log`). Agora tudo vem do `email_log` por tipo — fonte única.
Offline (cursor gravador, sem Postgres). O SQL é validado contra o Postgres na VM.
"""

from __future__ import annotations

import asyncio

import api.main as m
from discovery.store import TargetStore


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class _RecCur:
    """Cursor que grava os SQLs executados e devolve `one` em todo fetchone."""
    def __init__(self, one=(0, 0, 0)):  # fix 24/07: contadores devolvem (tentativas, sent, bounced)
        self.executed = []
        self._one = one
        self.description = [("id",), ("url",), ("from_domain",)]

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        return self._one

    def fetchall(self):
        return []


def _sql(cur):
    return " ".join(s for s, _ in cur.executed)


# --- §3: alert_stats agora vem do email_log (tipos de alerta) --------------- #

def test_alert_stats_reads_email_log_alert_types(monkeypatch):
    # fix 24/07: cada janela devolve (tentativas, sent, bounced) → cursor de 3 colunas.
    cur = _RecCur(one=(7, 5, 2))
    store = TargetStore()
    monkeypatch.setattr(store, "_run", lambda fn: fn(cur))
    out = _run(store.alert_stats())
    sql = _sql(cur)
    assert "FROM email_log" in sql and "alert_log" not in sql          # fonte única
    assert "email_type IN ('alert', 'alert_score100')" in sql
    assert "profile_view" not in sql                                    # não mistura perfis
    assert "date_trunc('day', NOW())" in sql                           # dia-calendário
    # fix 24/07: "enviados" = tentativas (inclui bounces), com breakdown sent/bounced
    assert "soft_bounced" in sql and "FILTER (WHERE status = 'sent')" in sql
    assert {"today", "today_sent", "today_bounced", "total", "total_bounced"} <= set(out)
    assert out["today"] == 7 and out["today_sent"] == 5 and out["today_bounced"] == 2


# --- §4: profile_view_stats — contadores PRÓPRIOS da aba de perfil ---------- #

def test_profile_view_stats_reads_email_log(monkeypatch):
    cur = _RecCur(one=(3, 3, 0))
    store = TargetStore()
    monkeypatch.setattr(store, "_run", lambda fn: fn(cur))
    out = _run(store.profile_view_stats())
    sql = _sql(cur)
    assert "FROM email_log" in sql and "email_type = 'profile_view'" in sql
    assert "alert_score100" not in sql                                 # separado dos alertas
    assert {"today", "today_bounced", "total"} <= set(out) and out["total"] == 3


def test_alert_and_profile_stats_use_distinct_filters(monkeypatch):
    """Cada aba conta o seu tipo — nunca os mesmos contadores (regra do §4)."""
    a, p = _RecCur(), _RecCur()
    store = TargetStore()
    monkeypatch.setattr(store, "_run", lambda fn: fn(a))
    _run(store.alert_stats())
    monkeypatch.setattr(store, "_run", lambda fn: fn(p))
    _run(store.profile_view_stats())
    assert "profile_view" in _sql(p) and "profile_view" not in _sql(a)


# --- §4: list_alerts traz o REMETENTE (from_domain) via email_log ---------- #

def test_list_alerts_joins_email_log_for_sender(monkeypatch):
    cur = _RecCur()
    store = TargetStore()
    monkeypatch.setattr(store, "_run", lambda fn: fn(cur))
    _run(store.list_alerts(limit=10))
    sql = _sql(cur)
    assert "el.from_domain" in sql and "LEFT JOIN email_log el ON el.email_id = a.email_id" in sql


# --- endpoint da aba Consultas de perfil ------------------------------------ #

def test_profile_view_stats_endpoint(monkeypatch):
    class FakeStore:
        async def profile_view_stats(self):
            return {"today": 1, "week": 2, "month": 3, "total": 4}

    monkeypatch.setattr(m, "get_target_store", lambda: FakeStore())
    out = _run(m.api_alerts_profile_view_stats())
    assert out == {"today": 1, "week": 2, "month": 3, "total": 4}
