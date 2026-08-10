"""KL-150 fix — `resolve_period('today')` usa o dia-calendário de BRASÍLIA (BRT), não o dia UTC.
Antes, uma conta criada às 23:28 BRT (02:28 UTC do dia seguinte) contava como "hoje" no painel."""
from __future__ import annotations

from datetime import datetime, timezone

from api.admin_analytics import resolve_period


def _utc(y, m, d, h, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=timezone.utc)


def test_today_is_brasilia_calendar_day():
    now = _utc(2026, 8, 10, 14, 35)          # 11:35 BRT em 10/08
    pr = resolve_period("today", None, None, now=now)
    # meia-noite BRT de 10/08 = 03:00 UTC de 10/08
    assert pr["start"] == _utc(2026, 8, 10, 3, 0)
    assert pr["end"] == _utc(2026, 8, 11, 3, 0)
    assert pr["days"] == 1
    # conta às 02:28 UTC (23:28 BRT de 09/08) → NÃO é "hoje" (é ontem em Brasília)
    assert not (pr["start"] <= _utc(2026, 8, 10, 2, 28) < pr["end"])
    # conta às 13:13 UTC (10:13 BRT de 10/08) → É "hoje"
    assert pr["start"] <= _utc(2026, 8, 10, 13, 13) < pr["end"]


def test_today_before_utc_midnight_is_prev_brt_day():
    now = _utc(2026, 8, 10, 2, 0)            # 23:00 BRT de 09/08 → "hoje" BRT = 09/08
    pr = resolve_period("today", None, None, now=now)
    assert pr["start"] == _utc(2026, 8, 9, 3, 0)   # meia-noite BRT de 09/08
    assert pr["end"] == _utc(2026, 8, 10, 3, 0)


def test_period_meta_dates_reflect_brt_day():
    # o `period` do response (via _period_meta) deve rotular o dia de Brasília, não o UTC.
    from api.admin_analytics import _period_meta
    pr = resolve_period("today", None, None, now=_utc(2026, 8, 10, 14, 35))
    meta = _period_meta(pr)
    assert meta["start"] == "2026-08-10" and meta["end"] == "2026-08-10" and meta["days"] == 1


def test_7d_is_rolling_window_from_now():
    now = _utc(2026, 8, 10, 14, 0)
    pr = resolve_period("7d", None, None, now=now)
    assert pr["end"] == now
    assert (pr["end"] - pr["start"]).days == 7
