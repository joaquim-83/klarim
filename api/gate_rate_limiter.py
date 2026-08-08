"""KL-153 — rate limiting de 3 camadas + intervalo entre domínios + detecção de abuso para o
`POST /gate/scan`. Tudo em Redis (contadores atômicos por janela). SEM Redis → **fail-open**
(não bloqueia — igual ao `_enforce_rpm`); os testes injetam um fake Redis para exercitar as regras.

Camadas (na ordem em que o endpoint verifica — fail-fast no 1º limite violado):
  1. IP    — `gate:rl:ip:{ip}`         — 10/hora (qualquer conta).
  2. user  — `gate:rl:user:{acct}`     — por plano (free 5 · pro 50 · team 200 · enterprise ∞) /hora.
  3. domain— `gate:rl:domain:{domain}` — 1 scan por domínio a cada 30 min (qualquer conta).
  4. interval — `gate:rl:last:{acct}`  — intervalo mínimo entre domínios DIFERENTES por conta
                                          (free 5min · pro 1min · team/ent 0).
Abuso: `gate:rl:distinct:{acct}` (SADD do domínio, TTL 24h). > 20 domínios distintos/24h → suspende.

Cada `enforce_*` devolve `None` (ok) ou um **payload de 429** (dict, com `retry_after_seconds`),
que o endpoint transforma em `JSONResponse(429, headers={"Retry-After": …})`."""
from __future__ import annotations

import time
from typing import Optional

# Camada 2 — teto por hora por conta (por slug de plano). -1 = ilimitado.
USER_HOURLY_LIMITS = {"free": 5, "pro": 50, "team": 200, "enterprise": -1}
# Camada 1 — teto por hora por IP (fixo).
IP_HOURLY_LIMIT = 10
# Camada 3 — 1 scan por domínio a cada 30 min.
DOMAIN_WINDOW_SEC = 1800
# Camada 4 — intervalo mínimo entre domínios DIFERENTES, por conta (segundos).
INTERVAL_BY_PLAN = {"free": 300, "pro": 60, "team": 0, "enterprise": 0}
# Abuso — teto de domínios distintos em 24h.
DISTINCT_DOMAIN_LIMIT = 20
DISTINCT_WINDOW_SEC = 86400

_HOUR = 3600
UPGRADE_URL = "/dashboard/gate#upgrade"


def _payload(retry_after: int, limit_type: str, plan_slug: str) -> dict:
    minutes = max(1, round((retry_after or 0) / 60))
    return {
        "detail": f"Limite de consultas excedido. Tente novamente em {minutes} minuto(s).",
        "retry_after_seconds": int(retry_after or 0),
        "limit_type": limit_type,
        "current_plan": plan_slug,
        "upgrade_url": UPGRADE_URL,
    }


async def _incr_window(redis, key: str, window: int) -> tuple[int, int]:
    """INCR `key` com EXPIRE `window` na 1ª vez. Devolve `(contagem, ttl_restante)`."""
    n = int(await redis.incr(key))
    if n == 1:
        await redis.expire(key, window)
        return n, window
    ttl = await redis.ttl(key)
    return n, (int(ttl) if ttl and int(ttl) > 0 else window)


async def check_ip(redis, ip: str) -> Optional[int]:
    """Camada 1. Devolve `retry_after` (s) se bloqueado, senão None."""
    n, ttl = await _incr_window(redis, f"gate:rl:ip:{ip}", _HOUR)
    return ttl if n > IP_HOURLY_LIMIT else None


async def check_user(redis, account_id: int, plan_slug: str) -> Optional[int]:
    """Camada 2. `enterprise` (-1) é ilimitado."""
    limit = USER_HOURLY_LIMITS.get(plan_slug, USER_HOURLY_LIMITS["free"])
    if limit == -1:
        return None
    n, ttl = await _incr_window(redis, f"gate:rl:user:{account_id}", _HOUR)
    return ttl if n > limit else None


async def check_domain(redis, domain: str) -> Optional[int]:
    """Camada 3. SET NX EX — a 1ª ocorrência do domínio na janela grava a chave; as seguintes
    (dentro de 30 min) batem no lock → bloqueado."""
    key = f"gate:rl:domain:{domain}"
    ok = await redis.set(key, "1", nx=True, ex=DOMAIN_WINDOW_SEC)
    if ok:
        return None
    ttl = await redis.ttl(key)
    return int(ttl) if ttl and int(ttl) > 0 else DOMAIN_WINDOW_SEC


async def check_interval(redis, account_id: int, domain: str, plan_slug: str) -> Optional[int]:
    """Camada 4. Intervalo mínimo entre domínios DIFERENTES por conta. Mesmo domínio → sem
    restrição aqui (a camada 3 já cobre). Atualiza o marcador a cada scan permitido."""
    interval = INTERVAL_BY_PLAN.get(plan_slug, INTERVAL_BY_PLAN["free"])
    key = f"gate:rl:last:{account_id}"
    now = int(time.time())
    raw = await redis.get(key)
    if raw and interval > 0:
        try:
            last_domain, ts = str(raw).rsplit(":", 1)
            elapsed = now - int(ts)
            if last_domain != domain and elapsed < interval:
                return interval - elapsed
        except (ValueError, TypeError):
            pass
    await redis.set(key, f"{domain}:{now}", ex=DISTINCT_WINDOW_SEC)
    return None


async def record_distinct_domain(redis, account_id: int, domain: str) -> int:
    """Adiciona o domínio ao set de domínios distintos da conta (24h) e devolve o SCARD."""
    key = f"gate:rl:distinct:{account_id}"
    await redis.sadd(key, domain)
    await redis.expire(key, DISTINCT_WINDOW_SEC)
    return int(await redis.scard(key))


async def enforce(redis, ip: str, account_id: int, plan: Optional[dict],
                  domain: str) -> Optional[dict]:
    """Roda as 4 camadas na ordem IP → user → domain → interval. Devolve o payload de 429 do 1º
    limite violado, ou None se tudo passou. `redis` None → fail-open (None)."""
    if redis is None:
        return None
    slug = (plan or {}).get("slug") or "free"
    ra = await check_ip(redis, ip)
    if ra is not None:
        return _payload(ra, "ip", slug)
    ra = await check_user(redis, account_id, slug)
    if ra is not None:
        return _payload(ra, "user", slug)
    ra = await check_domain(redis, domain)
    if ra is not None:
        return _payload(ra, "domain", slug)
    ra = await check_interval(redis, account_id, domain, slug)
    if ra is not None:
        return _payload(ra, "interval", slug)
    return None


async def get_distinct_domains(redis, account_id: int) -> list:
    """Lista os domínios distintos que a conta escaneou nas últimas 24h (para o audit de abuso)."""
    if redis is None:
        return []
    try:
        members = await redis.smembers(f"gate:rl:distinct:{account_id}")
        return sorted(str(m) for m in (members or []))
    except Exception:  # noqa: BLE001
        return []


async def is_abuse(redis, account_id: int, domain: str) -> bool:
    """Registra o domínio e devolve True se a conta passou de 20 domínios distintos em 24h."""
    if redis is None:
        return False
    try:
        return await record_distinct_domain(redis, account_id, domain) > DISTINCT_DOMAIN_LIMIT
    except Exception:  # noqa: BLE001 - detecção de abuso é best-effort
        return False


async def user_hour_usage(redis, account_id: int, plan_slug: str) -> tuple[int, int]:
    """(scans_usados_na_hora, teto_da_hora) para o endpoint de status. Sem Redis → (0, teto)."""
    limit = USER_HOURLY_LIMITS.get(plan_slug, USER_HOURLY_LIMITS["free"])
    if redis is None:
        return 0, limit
    try:
        used = await redis.get(f"gate:rl:user:{account_id}")
        return (int(used) if used else 0), limit
    except Exception:  # noqa: BLE001
        return 0, limit
