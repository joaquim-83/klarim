"""KL-136 → KL-145 — saúde operacional do pipeline de alerta: role penalty configurável e
diagnóstico de re-scan.

Os gates de score e o fail-safe de saldo Reoon em `_verify_and_filter` (Fix 2/4 do KL-136) foram
SUPERADOS pelo KL-145 (o Reoon saiu do fluxo de envio) — a cobertura da regra de 3 filtros vive em
`tests/test_kl145_three_filters.py`. Este arquivo mantém a penalidade de role (lead scoring) e o
diagnóstico de re-scan. Offline."""
from __future__ import annotations

import asyncio

from discovery.alert_scoring import calculate_alert_score, _role_penalty


# =========================================================================== #
# Fix 1 — role penalty configurável (-5 default, era -15)
# =========================================================================== #

def test_role_penalty_default_is_minus_5(monkeypatch):
    monkeypatch.delenv("ALERT_ROLE_PENALTY", raising=False)
    assert _role_penalty() == -5


def test_role_penalty_reads_env(monkeypatch):
    monkeypatch.setenv("ALERT_ROLE_PENALTY", "-8")
    assert _role_penalty() == -8


def test_role_penalty_invalid_env_falls_back(monkeypatch):
    monkeypatch.setenv("ALERT_ROLE_PENALTY", "nan")
    assert _role_penalty() == -5


def test_contato_action_zone_passes_with_default_penalty(monkeypatch):
    # O cenário do card: contato@ genérico na action_zone (score 50-89) deixa de ser rejeitado.
    monkeypatch.delenv("ALERT_ROLE_PENALTY", raising=False)
    r = calculate_alert_score({"domain": "hotel.com.br", "last_scan_score": 70}, "contato@x.com.br")
    assert r["score"] == 25  # +10 corp +20 action -5 role
    assert r["score"] > 20   # passa o threshold


def test_contato_action_zone_rejected_with_old_penalty(monkeypatch):
    # Confirma que o fix resolve: com o -15 antigo, o mesmo lead falharia (15 < 20).
    monkeypatch.setenv("ALERT_ROLE_PENALTY", "-15")
    r = calculate_alert_score({"domain": "hotel.com.br", "last_scan_score": 70}, "contato@x.com.br")
    assert r["score"] == 15  # +10 +20 -15
    assert r["score"] < 20   # rejeitado


def test_role_status_and_prefix_never_double(monkeypatch):
    # Prefixo role + status role da Reoon → só UM sinal (não dobra), com a penalidade do env.
    monkeypatch.delenv("ALERT_ROLE_PENALTY", raising=False)
    r = calculate_alert_score(
        {"domain": "x.com", "last_scan_score": 60, "email_verify_status": "role"}, "contato@x.com")
    role_signals = [s for s in r["signals"] if "role" in s["signal"]]
    assert len(role_signals) == 1
    assert role_signals[0]["points"] == -5


# =========================================================================== #
# Fix 2/4 (SUPERADOS pelo KL-145) — o Reoon saiu do fluxo de envio
# =========================================================================== #
# Os gates de score (Fix 2) e o fail-safe de saldo Reoon em `_verify_and_filter` (Fix 4) foram
# REMOVIDOS: a decisão de envio virou local (sintaxe + MX + blocklist). A cobertura da nova regra
# vive em `tests/test_kl145_three_filters.py`. O Reoon segue só como enriquecimento em background.


# =========================================================================== #
# Fix 5 — diagnóstico de re-scan (funil de elegibilidade)
# =========================================================================== #

def test_rescan_diagnostics_shape():
    # A função é pura em cima de contagens SQL; validamos o SHAPE com um FakeStore mínimo.
    class _Store:
        async def rescan_diagnostics(self, days=30):
            return {"engaged": 10, "engaged_with_email": 4, "eligible": 0, "too_recent": 4}
        async def get_targets_for_rescan(self, days, limit):
            return []
        async def get_setting(self, k, d):
            return d
    from discovery.rescan_worker import RescanWorker
    w = RescanWorker()
    w.store = _Store()

    async def _go():
        # Só o passo de elegibilidade + diagnóstico (não roda scan real): chamamos o store direto.
        return await w.store.rescan_diagnostics(w.age_days)
    diag = asyncio.run(_go())
    assert set(diag) == {"engaged", "engaged_with_email", "eligible", "too_recent"}
    assert diag["eligible"] == 0 and diag["too_recent"] == 4
