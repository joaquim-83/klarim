"""KL-130 → KL-145 — limpeza retroativa de `unknown`+`power` (o Reoon saiu do fluxo de envio).

O KL-130 excluía status terminais (`unknown`+`power`, block-statuses) do `_ALERT_ELIGIBLE_WHERE`
porque o Reoon decidia o envio. **KL-145 removeu esses filtros do WHERE** — a decisão de envio é
local (sintaxe + MX + blocklist) e a blocklist (via webhook de bounce) faz o trabalho. O método
`retire_unknown_power_targets` FICA como limpeza retroativa (script), mas o WHERE não filtra mais
por status de verificação. Offline (SQL validado na VM).
"""
from __future__ import annotations

import asyncio

from discovery.store import TargetStore


# --------------------- WHERE não filtra mais por verificação -------------- #

def test_eligible_where_has_no_reoon_filters():
    w = TargetStore._ALERT_ELIGIBLE_WHERE
    # KL-145: os filtros por status/source de verificação SAÍRAM (o Reoon não decide envio).
    assert "email_verify_status" not in w
    assert "email_verify_source" not in w
    # Mantém os filtros legítimos (não-Reoon).
    assert "t.status = 'scanned'" in w
    assert "t.contact_email IS NOT NULL" in w
    assert "COALESCE(t.gate_fail_count, 0) = 0" in w
    assert "t.last_scan_score IS NOT NULL" in w


# --------------------- método de limpeza retroativa ----------------------- #

class _FakeCur:
    def __init__(self, rowcount=173):
        self.sql = ""
        self.rowcount = rowcount

    def execute(self, sql, params=None):
        self.sql = sql

    def fetchone(self):
        return (self.rowcount,)


class _RetireStore(TargetStore):
    def __init__(self, rowcount=173):
        self.cur = _FakeCur(rowcount)

    def _run(self, fn):
        return fn(self.cur)


def test_retire_unknown_power_targets_sql():
    s = _RetireStore(rowcount=173)
    n = asyncio.run(s.retire_unknown_power_targets())
    assert n == 173
    sql = s.cur.sql
    assert "SET status = 'sem_contato'" in sql
    assert "email_verify_status = 'unknown'" in sql and "email_verify_source = 'power'" in sql
    assert "NOT IN ('sem_contato', 'descartado', 'unsubscribed')" in sql
