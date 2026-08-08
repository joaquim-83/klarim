"""KL-155 — camada 3 do rate limiter (cooldown por domínio) agora é POR CONTA + TTL por plano
(free 30min · pro 5min · team/ent SKIP). Testa a lógica de `check_domain` com um fake Redis que
simula a passagem do tempo (`tick`) — determinístico, sem relógio real."""
from __future__ import annotations

import asyncio

from api import gate_rate_limiter as rl


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class FakeRedis:
    """Só o que `check_domain` usa: SET(nx, ex) + TTL. `tick(s)` simula `s` segundos passando
    (decrementa os TTLs e expira as chaves vencidas)."""
    def __init__(self):
        self.kv, self.ttls = {}, {}

    async def set(self, k, v, nx=False, ex=None):
        if nx and k in self.kv:
            return False
        self.kv[k] = v
        if ex is not None:
            self.ttls[k] = int(ex)
        return True

    async def ttl(self, k):
        return self.ttls.get(k, -1)

    def tick(self, seconds):
        for k in list(self.ttls):
            self.ttls[k] -= seconds
            if self.ttls[k] <= 0:
                self.kv.pop(k, None)
                self.ttls.pop(k, None)


def test_pro_same_domain_after_5min_ok():
    r = FakeRedis()
    assert _run(rl.check_domain(r, 10, "acme.com.br", "pro")) is None   # 1º OK (TTL 300s)
    r.tick(300)                                                          # 5 min passaram → expira
    assert _run(rl.check_domain(r, 10, "acme.com.br", "pro")) is None   # liberado de novo


def test_free_same_domain_after_5min_still_blocked():
    r = FakeRedis()
    assert _run(rl.check_domain(r, 10, "acme.com.br", "free")) is None  # 1º OK (TTL 1800s)
    r.tick(300)                                                          # 5 min < 30 min → ainda ativo
    assert _run(rl.check_domain(r, 10, "acme.com.br", "free")) is not None   # 429


def test_team_same_domain_immediate_ok():
    r = FakeRedis()
    assert _run(rl.check_domain(r, 10, "acme.com.br", "team")) is None
    assert _run(rl.check_domain(r, 10, "acme.com.br", "team")) is None   # camada 3 não se aplica
    assert r.kv == {}   # nenhuma key setada


def test_enterprise_same_domain_immediate_ok():
    r = FakeRedis()
    assert _run(rl.check_domain(r, 10, "acme.com.br", "enterprise")) is None
    assert _run(rl.check_domain(r, 10, "acme.com.br", "enterprise")) is None
    assert r.kv == {}


def test_pro_and_free_same_domain_do_not_interfere():
    r = FakeRedis()
    # Duas contas escaneando o MESMO domínio → keys separadas por account_id → sem bloqueio cruzado.
    assert _run(rl.check_domain(r, 1, "acme.com.br", "pro")) is None
    assert _run(rl.check_domain(r, 2, "acme.com.br", "free")) is None
    assert "gate:rl:domain:1:acme.com.br" in r.kv
    assert "gate:rl:domain:2:acme.com.br" in r.kv
    # E cada uma bloqueia a SI MESMA num 2º scan imediato.
    assert _run(rl.check_domain(r, 1, "acme.com.br", "pro")) is not None
    assert _run(rl.check_domain(r, 2, "acme.com.br", "free")) is not None


def test_ttl_map_by_plan():
    assert rl.DOMAIN_TTL_BY_PLAN == {"free": 1800, "pro": 300, "team": 0, "enterprise": 0}
    assert rl.DOMAIN_WINDOW_SEC == 1800   # default Free (compat)
