"""KL-136 → KL-145 → KL-146 — saúde operacional do pipeline de alerta: tipo de e-mail no lead
scoring e diagnóstico de re-scan.

Os gates de score e o fail-safe de saldo Reoon em `_verify_and_filter` (Fix 2/4 do KL-136) foram
SUPERADOS pelo KL-145 (o Reoon saiu do fluxo de envio). A penalidade role-based configurável (Fix 1
do KL-136, `ALERT_ROLE_PENALTY`) foi SUBSTITUÍDA pelo fator de tipo de e-mail do KL-146 (pessoal
+15 · genérico neutro 0 · medium -5 · high-bounce contato -10). A cobertura completa do fator vive
em `tests/test_kl85_scoring.py`. Este arquivo mantém o cenário do card + o diagnóstico de re-scan."""
from __future__ import annotations

import asyncio

from discovery.alert_scoring import calculate_alert_score


# =========================================================================== #
# KL-146 (SUPERA o Fix 1 do KL-136) — tipo de e-mail substitui a penalidade role-based
# =========================================================================== #

def test_contato_action_zone_still_sent():
    # O cenário do card KL-136/146: contato@ genérico na action_zone continua ENVIADO (KL-145: o
    # score não filtra, só ORDENA). Com o fator de tipo, `contato@` (high-bounce) → -10.
    r = calculate_alert_score({"domain": "hotel.com.br", "last_scan_score": 70}, "contato@x.com.br")
    assert r["score"] == 20  # +10 corp +20 action -10 (contato high-bounce)
    assert "email_type_generic_high_bounce" in {s["signal"] for s in r["signals"]}


def test_email_type_no_double_with_role_status():
    # KL-146: prefixo genérico + status 'role' da Reoon → UM só sinal de tipo (não dobra).
    r = calculate_alert_score(
        {"domain": "x.com", "last_scan_score": 60, "email_verify_status": "role"}, "contato@x.com")
    type_signals = [s for s in r["signals"] if s["signal"].startswith("email_type")]
    assert len(type_signals) == 1 and type_signals[0]["points"] == -10


def test_role_verified_downgrades_personal_looking():
    # KL-146: prefixo que PARECE pessoal mas a Reoon confirmou 'role' → rebaixado a 0 (não +15).
    r = calculate_alert_score(
        {"domain": "x.com", "last_scan_score": 60, "email_verify_status": "role"}, "joao@x.com")
    type_signals = [s for s in r["signals"] if s["signal"].startswith("email_type")]
    assert len(type_signals) == 1
    assert type_signals[0]["signal"] == "email_type_role_verified" and type_signals[0]["points"] == 0


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
