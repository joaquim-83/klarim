"""KL-104 Parte 1 — deep linking no admin: os responses de eventos passam a incluir
`target_id` para que o domínio vire link ao detalhe do alvo (`DomainLink`). Offline.
"""

from __future__ import annotations

import asyncio

from discovery.store import TargetStore


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class _RecCur:
    def __init__(self, ones=None, rows=None, desc=None):
        self.executed = []
        self._ones = list(ones or [])
        self._rows = rows or []
        self.description = desc or [("id",)]

    def execute(self, sql, params=None):
        self.executed.append(sql)

    def fetchone(self):
        return self._ones.pop(0) if self._ones else (0,)

    def fetchall(self):
        return self._rows


def test_analytics_events_returns_target_id(monkeypatch):
    cur = _RecCur(desc=[("event_type",), ("target_id",), ("target_url",)],
                  rows=[("profile_view", 42, "https://x.com.br")])
    store = TargetStore()
    monkeypatch.setattr(store, "_run", lambda fn: fn(cur))
    out = _run(store.analytics_events(50, "profile_view"))
    assert "target_id" in " ".join(cur.executed)
    assert out and out[0]["target_id"] == 42


def test_aa_events_row_select_includes_target_id(monkeypatch):
    cur = _RecCur(ones=[(0,), (0, 0, 0, 0)], rows=[], desc=[("id",)])
    store = TargetStore()
    monkeypatch.setattr(store, "_run", lambda fn: fn(cur))
    _run(store.aa_events(start=1, end=2, types=None, domain=None, campaign=None,
                         path=None, offset=0, limit=10))
    # o SELECT das linhas de evento (o 3º) inclui target_id (KL-104)
    assert any("target_id" in s and "ORDER BY created_at DESC" in s for s in cur.executed)
