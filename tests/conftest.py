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
    except Exception:  # noqa: BLE001 - testes que não tocam a API seguem normais
        pass
    yield
