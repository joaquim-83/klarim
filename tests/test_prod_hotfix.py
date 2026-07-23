"""Hotfix de produção (4 problemas simultâneos):

1. Scan worker escaneava sem persistir: `ensure_schema` concorrente no deploy dava
   DeadlockDetected → `store=None` permanente. Fix: retry de DDL transitório +
   worker não zera o store.
2. nginx_parser: `for line in f` + `f.tell()` → OSError('telling position disabled
   by next() call'). Fix: `readline()` (compatível com tell) + linha parcial.
3. ct_poller: `.json()` cru estourava JSONDecodeError a cada ciclo. Fix: `_get_json`
   com retry/backoff → None (sem exceção) quando o CT está instável.

Offline.
"""

from __future__ import annotations

import asyncio

import pytest

from discovery.store import TargetStore, _is_transient_ddl


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# --------------------------------------------------------------------------- #
# 1. ensure_schema — retry em DDL concorrente
# --------------------------------------------------------------------------- #

class _DeadlockDetected(Exception):
    """Simula psycopg2.errors.DeadlockDetected (casado por __class__.__name__)."""


_DeadlockDetected.__name__ = "DeadlockDetected"
_DeadlockDetected.__qualname__ = "DeadlockDetected"


def test_is_transient_ddl_matches_only_concurrency_errors():
    assert _is_transient_ddl(_DeadlockDetected("deadlock detected\nDETAIL: ..."))
    assert _is_transient_ddl(Exception("could not obtain lock on relation 16627"))
    assert _is_transient_ddl(Exception("tuple concurrently updated"))
    # erros REAIS não são retentados
    assert not _is_transient_ddl(ValueError("column x does not exist"))
    assert not _is_transient_ddl(Exception("permission denied for table users"))
    assert not _is_transient_ddl(Exception("syntax error at or near"))


def test_ensure_schema_retries_transient_then_succeeds(monkeypatch):
    store = TargetStore()
    calls = {"n": 0}

    def fake_run(fn):
        calls["n"] += 1
        if calls["n"] < 3:                      # falha 2x, sucede na 3ª
            raise _DeadlockDetected("deadlock detected")

    async def _instant(*a, **k):
        return None

    async def _noseed():
        return None

    monkeypatch.setattr(store, "_run", fake_run)
    monkeypatch.setattr(store, "seed_sectors", _noseed)
    monkeypatch.setattr(asyncio, "sleep", _instant)          # sem backoff real
    _run(store.ensure_schema())
    assert calls["n"] == 3


def test_ensure_schema_raises_on_real_error(monkeypatch):
    store = TargetStore()

    def fake_run(fn):
        raise ValueError("column does not exist")            # não-transitório

    monkeypatch.setattr(store, "_run", fake_run)
    with pytest.raises(ValueError):
        _run(store.ensure_schema())


# --------------------------------------------------------------------------- #
# 2. nginx_parser — readline (tell OK) + linha parcial
# --------------------------------------------------------------------------- #

def test_nginx_parser_reads_incrementally_and_holds_partial_line(tmp_path):
    import api.nginx_log_parser as nlp
    log = tmp_path / "access.log"
    log.write_text("aaa\nbbb\n")
    p = nlp.NginxLogParser(store=None, log_path=str(log))

    lines, _ = p._read_new_lines()                # tell() funciona (era OSError antes)
    assert lines == ["aaa", "bbb"]
    assert p._read_new_lines()[0] == []           # nada novo → não relê

    with open(log, "a") as f:
        f.write("ccc")                            # linha PARCIAL (Nginx escrevendo)
    assert p._read_new_lines()[0] == []           # não consome a parcial

    with open(log, "a") as f:
        f.write("\n")                             # completou
    assert p._read_new_lines()[0] == ["ccc"]


# --------------------------------------------------------------------------- #
# 3. ct_poller — _get_json com retry/backoff → None sem exceção
# --------------------------------------------------------------------------- #

class _Resp:
    def __init__(self, status, content, jsonval=None, bad_json=False):
        self.status_code = status
        self.content = content
        self._json = jsonval
        self._bad = bad_json

    def json(self):
        if self._bad:
            raise ValueError("Expecting value: line 1 column 1 (char 0)")
        return self._json


class _FakeClient:
    def __init__(self, *responses):
        self._responses = list(responses)
        self.calls = 0

    def get(self, url):
        r = self._responses[min(self.calls, len(self._responses) - 1)]
        self.calls += 1
        return r


def _poller():
    from discovery.ct_poller import CTLogPoller
    p = CTLogPoller()
    p._ct_attempts = 3
    p._ct_backoff = 0.0                            # sem espera nos testes
    return p


def test_get_json_returns_parsed_on_200():
    p = _poller()
    c = _FakeClient(_Resp(200, b"{}", {"tree_size": 9}))
    assert p._get_json(c, "u") == {"tree_size": 9}


def test_get_json_none_on_empty_body_and_429():
    p = _poller()
    # corpo vazio → 429 → nunca vira exceção; devolve None
    c = _FakeClient(_Resp(200, b"", None), _Resp(429, b"", None), _Resp(429, b"", None))
    assert p._get_json(c, "u") is None
    assert c.calls == 3                            # tentou 3x


def test_get_json_none_on_bad_json_never_raises():
    p = _poller()
    c = _FakeClient(_Resp(200, b"<html>", None, bad_json=True))
    assert p._get_json(c, "u") is None             # JSONDecodeError engolido


def test_get_json_stops_early_on_4xx():
    p = _poller()
    c = _FakeClient(_Resp(404, b"", None))
    assert p._get_json(c, "u") is None
    assert c.calls == 1                            # 4xx (não-429): não retenta


def test_poll_log_returns_false_when_ct_unstable(monkeypatch):
    p = _poller()
    monkeypatch.setattr(p, "_get_json", lambda c, u: None)
    assert p._poll_log(object(), "https://ct.googleapis.com/logs/x/") is False


def test_poll_log_true_and_ingests_entries(monkeypatch):
    p = _poller()
    seq = [{"tree_size": 100}, {"entries": [{"e": 1}, {"e": 2}]}]
    it = iter(seq)
    monkeypatch.setattr(p, "_get_json", lambda c, u: next(it))
    ingested = []
    monkeypatch.setattr(p, "_ingest", lambda entry: ingested.append(entry))
    assert p._poll_log(object(), "https://ct.googleapis.com/logs/x/") is True
    assert len(ingested) == 2
