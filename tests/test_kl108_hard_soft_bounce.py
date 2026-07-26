"""KL-108 — circuit breaker separa HARD bounce (permanente) de SOFT (transitório).

Soft bounces (caixa cheia, servidor fora, delivery_delayed) NÃO devem pausar um
remetente. Antes, `email_health_by_domain` somava hard+soft no `bounce_rate` e o
circuit breaker do KL-91 pausava remetentes saudáveis (caso real 26/07:
perfil.klarim.net com 1,3% hard + 5,6% soft = 6,9% combinado, quase pausado).

Testes:
- `email_health_by_domain` devolve `hard_bounced`/`soft_bounced` separados,
  `bounce_rate` só-hard e `soft_bounce_rate` separado (mapeamento SQL→dict offline).
- `flag_high_bounce` pausa por hard, ignora soft (coberto também em test_kl91).
"""
import asyncio

from discovery.store import TargetStore
from notifier import cold_alert as c


class _FakeCursor:
    """Cursor mínimo: registra o SQL e devolve linhas pré-definidas no `fetchall`."""
    def __init__(self, rows):
        self._rows = rows
        self.sql = None

    def execute(self, sql, params=None):
        self.sql = sql

    def fetchall(self):
        return self._rows


class _StoreWithRows(TargetStore):
    """Sobrescreve `_run` p/ rodar o `_fn` interno com um cursor falso (offline)."""
    def __init__(self, rows):
        self._rows = rows
        self.last_sql = None

    def _run(self, fn):
        cur = _FakeCursor(self._rows)
        out = fn(cur)
        self.last_sql = cur.sql
        return out


def test_email_health_by_domain_separates_hard_and_soft():
    # (from_domain, total, hard_bounced, soft_bounced, complained)
    rows = [
        ("alertas.klarim.net", 353, 22, 3, 0),
        ("perfil.klarim.net", 677, 9, 38, 0),
    ]
    store = _StoreWithRows(rows)
    out = asyncio.run(store.email_health_by_domain(days=7))

    a = out["alertas.klarim.net"]
    assert a["hard_bounced"] == 22
    assert a["soft_bounced"] == 3
    assert a["bounced"] == 25            # compat: hard+soft
    assert a["total"] == 353
    assert a["bounce_rate"] == round(100.0 * 22 / 353, 2)        # 6.23 — HARD only
    assert a["soft_bounce_rate"] == round(100.0 * 3 / 353, 2)    # 0.85 — informativo
    assert a["delivered"] == 353 - 22 - 3 - 0

    # perfil.klarim.net: 1,33% hard (saudável) mas 5,61% soft — o bounce_rate NÃO
    # deve refletir o combinado (6,94%).
    p = out["perfil.klarim.net"]
    assert p["bounce_rate"] == round(100.0 * 9 / 677, 2)         # ~1.33 — hard only
    assert p["soft_bounce_rate"] == round(100.0 * 38 / 677, 2)   # ~5.61
    assert p["bounce_rate"] < 5.0 and p["soft_bounce_rate"] > 5.0


def test_email_health_by_domain_sql_uses_separate_filters():
    store = _StoreWithRows([])
    asyncio.run(store.email_health_by_domain())
    sql = store.last_sql
    # HARD e SOFT contados em FILTERs distintos (não somados num único IN).
    assert "FILTER (WHERE status = 'bounced')" in sql
    assert "FILTER (WHERE status = 'soft_bounced')" in sql


def test_email_health_by_domain_zero_total_is_safe():
    rows = [("aviso.klarim.net", 0, 0, 0, 0)]
    out = asyncio.run(_StoreWithRows(rows).email_health_by_domain())
    d = out["aviso.klarim.net"]
    assert d["bounce_rate"] == 0.0 and d["soft_bounce_rate"] == 0.0


def test_circuit_breaker_soft_only_stays_active():
    # 4% hard + 10% soft → NÃO pausa (hard < 5%); o combinado (14%) é irrelevante.
    s = c.load_senders({})
    by_domain = {"alertas.klarim.net": {"total": 100, "hard_bounced": 4, "soft_bounced": 10}}
    paused = c.flag_high_bounce(s, by_domain, max_rate=5.0, min_sample=20)
    assert paused == [] and s[0].status == "active"


def test_circuit_breaker_hard_pauses():
    # 6% hard + 0% soft → PAUSA.
    s = c.load_senders({})
    by_domain = {"alertas.klarim.net": {"total": 100, "hard_bounced": 6, "soft_bounced": 0},
                 "aviso.klarim.net": {"total": 100, "hard_bounced": 0, "soft_bounced": 0}}
    paused = c.flag_high_bounce(s, by_domain, max_rate=5.0, min_sample=20)
    assert paused == [("alertas.klarim.net", 6.0)] and s[0].status == "paused"
