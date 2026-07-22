"""Fixtures compartilhadas dos testes.

O rate limit de login e o de eventos (api/main) guardam estado global em memória.
Como o TestClient usa sempre o mesmo IP/sessão, sem resetar entre testes um teste
poderia estourar o limite do outro. Este autouse zera o estado antes de cada teste.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_api_rate_limits():
    try:
        import api.main as m
        m._login_attempts.clear()
        m._event_rl.clear()
        m._contact_attempts.clear()
        m._signup_attempts.clear()   # KL-51 f3
        m._forgot_attempts.clear()
        m._reset_attempts.clear()
        m._send_report_attempts.clear()   # KL-51 f3 fix UX
        m._pending_signups.clear()   # KL-44 F-03b
        m._vigilia_rl.clear()        # KL-44 P2
        m._config_attempts.clear()   # KL-44 config
        m._password_attempts.clear()
        m._rotate_attempts.clear()
        m._public_content_attempts.clear()   # KL-74 endpoints públicos de conteúdo
        m._scan_get_attempts.clear()         # KL-78 item 8: rate limit do GET /scan
        m._payment_create_hits.clear()       # KL-93: cobrança PIX 3/h por IP
        m._notify_view_hits.clear()          # KL-93: notify/profile-view 1/h por (IP,domínio)
        m._report_dl_hits.clear()            # KL-93: /report/* 5/h por IP
        m._monitor_hits.clear()              # KL-93: monitoring/offer 3/h por IP
        m._alert_autocreate_hits.clear()     # KL-99 Fluxo C: auto-criação via alerta
        m._signup_inline_hits.clear()        # KL-99 Fluxo D: signup inline
        m._verify_check_hits.clear()         # KL-99: verificação de domínio
        import api.admin_analytics as _aa     # KL-83/KL-92: rate bucket do analytics admin
        _aa._rl_bucket.clear()
    except Exception:  # noqa: BLE001 - testes que não tocam a API seguem normais
        pass
    yield
