"""KL-95 — corrige divergências de métricas do dashboard Analytics: "Contas criadas" (tabela
`users`), "Scans" (tabela `scans` manuais), reclassificação retroativa de pre-fetch bots, e
jornada pré-signup sem polling/admin. Offline (SQL validado contra Postgres 16 na VM)."""

from __future__ import annotations

import inspect

import api.admin_analytics as aa
from discovery.store import TargetStore


# =========================================================================== #
# 1 + 2. Métricas contam AÇÕES REAIS, não requests do access_log
# =========================================================================== #

def test_server_metrics_scans_from_scans_table():
    src = inspect.getsource(TargetStore.al_server_metrics)
    # "Contas criadas" = tabela users; "Scans" = tabela scans (menos o worker discovery).
    assert "FROM users WHERE created_at" in src
    assert "FROM scans WHERE scanned_at" in src and "source IS DISTINCT FROM 'discovery'" in src
    # não conta mais accounts/scans pelo endpoint do access_log
    assert "http_method = 'POST'" not in src  # a antiga contagem de POST /signup saiu


def test_daily_series_uses_authoritative_sources():
    src = inspect.getsource(TargetStore.al_daily_series)
    assert "FROM scans" in src and "source IS DISTINCT FROM 'discovery'" in src
    assert "FROM users" in src


# =========================================================================== #
# 3. Reclassificação retroativa de pre-fetch bots
# =========================================================================== #

def test_reclassify_prefetch_bots_method_exists():
    assert callable(getattr(TargetStore, "reclassify_prefetch_bots"))
    src = inspect.getsource(TargetStore.reclassify_prefetch_bots)
    # idempotente (só toca is_bot=false) + usa contenção de CIDR (<<=).
    assert "is_bot = false" in src and "<<= ANY(%s::cidr[])" in src
    assert "'email_prefetch'" in src


def test_reclassify_script_uses_shared_cidrs():
    import scripts.reclassify_prefetch_bots as scr
    src = inspect.getsource(scr)
    assert "_EMAIL_PREFETCH_CIDRS" in src  # fonte única com o classificador


# =========================================================================== #
# 4. Jornada pré-signup — sem polling/admin
# =========================================================================== #

def test_journey_excludes_admin_and_polling():
    excl = TargetStore._JOURNEY_EXCLUDE
    assert "/admin/" in excl and "/painel/" in excl and "/mcp/" in excl
    assert "/account/me" in excl and "/events" in excl and "/health" in excl


def test_journey_dedups_consecutive_polling():
    # 10x o mesmo endpoint (polling) → 1 passo (dedup de consecutivos).
    now = 0
    rows = [{"ip_address": "1.1.1.1", "endpoint": "/account/dashboard",
             "domain_queried": None, "referrer": None, "user_id": 7,
             "created_at": None, "minutes_relative": i} for i in range(1, 11)]
    # + um signup no minuto 0 e um /site antes
    rows = ([{"ip_address": "1.1.1.1", "endpoint": "/site/x.com", "domain_queried": "x.com",
              "referrer": None, "user_id": None, "created_at": None, "minutes_relative": -5},
             {"ip_address": "1.1.1.1", "endpoint": "/account/signup", "domain_queried": None,
              "referrer": None, "user_id": None, "created_at": None, "minutes_relative": 0}]
            + rows)
    out = aa.assemble_pre_signup_journeys(rows)
    j = out["pre_signup_journey"][0]
    after_endpoints = [s["endpoint"] for s in j["steps_after"]]
    assert after_endpoints == ["/account/dashboard"]      # 10x colapsados em 1
    assert [s["endpoint"] for s in j["steps_before"]] == ["/site/x.com", "/account/signup"]


def test_dedup_consecutive_helper():
    steps = [{"endpoint": "/a"}, {"endpoint": "/a"}, {"endpoint": "/b"}, {"endpoint": "/a"}]
    out = aa._dedup_consecutive(steps)
    assert [s["endpoint"] for s in out] == ["/a", "/b", "/a"]  # colapsa só consecutivos
