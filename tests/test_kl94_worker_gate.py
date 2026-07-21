"""KL-94 (complemento) — tratamento de gate failures no scan worker. Testa o dispatch
`_persist_scan_report` por status (ok/unreachable/domain_not_found/dns_error) com stores/caches
fakes, o contrato dos métodos de store e a exclusão de inacessíveis do alerta. Offline."""

from __future__ import annotations

import pytest

import scanner.main as sm
from scanner.runner import ScanReport


class _FakeScore:
    score, semaphore, passed, failed, inconclusive = 80, "amarelo", 10, 2, 3

    def to_dict(self):
        return {"score": 80}


class _FakeStore:
    def __init__(self, record_result=None):
        self.calls = []
        self._record = record_result or {"gate_fail_count": 1, "discarded": False, "had_score": False}

    async def reset_gate_failure(self, tid):
        self.calls.append(("reset", tid))

    async def save_scan(self, *a, **k):
        self.calls.append(("save_scan", k.get("status", "ok")))
        return 99

    async def update_scan_result(self, *a):
        self.calls.append(("update_scan_result",))

    async def record_gate_failure(self, tid, st):
        self.calls.append(("record", st))
        return self._record


class _FakeCache:
    def __init__(self):
        self.sets = []

    async def set(self, url, report, full=True):
        self.sets.append(url)


def _report(status, score=None):
    return ScanReport(url="https://x.com.br", started_at="a", finished_at="b", duration_s=1.0,
                      results=[], score=score, status=status)


@pytest.fixture(autouse=True)
def _no_enrich(monkeypatch):
    async def _noop(*a, **k):
        return None  # sem perfil/raw → pula o bloco GCS
    monkeypatch.setattr(sm, "enrich_profile", _noop)


@pytest.mark.asyncio
async def test_dispatch_ok_saves_and_resets():
    store, cache = _FakeStore(), _FakeCache()
    await sm._persist_scan_report(store, cache, None, _report("ok", _FakeScore()),
                                  "https://x.com.br", 5, "discovery", full=False)
    names = [c[0] for c in store.calls]
    assert cache.sets == ["https://x.com.br"]       # cacheou
    assert "reset" in names                          # zerou falhas de gate (site voltou)
    assert "save_scan" in names and "update_scan_result" in names
    assert ("record",) not in [(c[0],) for c in store.calls]  # não conta falha no ok


@pytest.mark.asyncio
async def test_dispatch_domain_not_found_records_no_save():
    store, cache = _FakeStore(), _FakeCache()
    await sm._persist_scan_report(store, cache, None, _report("domain_not_found"),
                                  "https://x.com.br", 5, "discovery", full=False)
    assert cache.sets == []                          # não cacheia
    assert ("record", "domain_not_found") in store.calls
    assert not any(c[0] == "save_scan" for c in store.calls)  # NÃO salva scan


@pytest.mark.asyncio
async def test_dispatch_unreachable_saves_and_records():
    store, cache = _FakeStore(), _FakeCache()
    await sm._persist_scan_report(store, cache, None, _report("unreachable"),
                                  "https://x.com.br", 5, "public", full=False)
    assert cache.sets == []                          # não cacheia (deve re-testar)
    assert ("save_scan", "unreachable") in store.calls   # registra indisponibilidade
    assert ("record", "unreachable") in store.calls      # conta falha de gate
    assert not any(c[0] == "update_scan_result" for c in store.calls)  # NÃO toca last_scan_score


@pytest.mark.asyncio
async def test_dispatch_dns_error_is_noop():
    store, cache = _FakeStore(), _FakeCache()
    await sm._persist_scan_report(store, cache, None, _report("dns_error"),
                                  "https://x.com.br", 5, "discovery", full=False)
    assert cache.sets == [] and store.calls == []    # transitório: nada salvo, nada contado


@pytest.mark.asyncio
async def test_dispatch_discarded_on_third_failure():
    # record_gate_failure devolve discarded=True (3ª falha, sem score) → o dispatch aceita sem erro.
    store = _FakeStore(record_result={"gate_fail_count": 3, "discarded": True, "had_score": False})
    await sm._persist_scan_report(store, _FakeCache(), None, _report("unreachable"),
                                  "https://x.com.br", 5, "discovery", full=False)
    assert ("record", "unreachable") in store.calls


def test_store_has_gate_methods():
    from discovery.store import TargetStore
    for name in ("record_gate_failure", "reset_gate_failure", "gate_retry_pending"):
        assert callable(getattr(TargetStore, name)), name


def test_alert_eligible_excludes_inaccessible():
    # a query de alerta exclui alvos em falha de gate / sem score (a vigília cobre uptime).
    from discovery.store import TargetStore
    where = TargetStore._ALERT_ELIGIBLE_WHERE
    assert "gate_fail_count" in where and "last_scan_score IS NOT NULL" in where
