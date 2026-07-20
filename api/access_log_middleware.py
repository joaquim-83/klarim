"""KL-92 — Middleware de access log server-side.

Grava CADA request relevante (não-estático) que chega à API com o IP REAL do visitante
(``CF-Connecting-IP``), o país (``CF-IPCountry``) e a classificação bot/humano do
`api.bot_classifier`. É a **fonte de verdade** das métricas de visitante — o tracker.js
(client-side) infla ~5x porque pre-fetches de e-mail executam JavaScript no browser do bot.

Design (fail-safe, nunca atrasa o response):
- Assets estáticos (JS/CSS/imagens/fontes) são ignorados (`should_log`).
- A extração dos campos é síncrona e barata; o processamento pesado (classificação +
  gravação) roda em **background task** (`_spawn`) — o response volta na hora.
- A gravação é **bufferizada** e drenada por um flush periódico em **batch INSERT**
  (volume estimado 200-500 req/min → ~300-700k linhas/dia). Erro de banco = log perdido,
  nunca bloqueia o request.
- Rate por IP num contador Redis (``access_rate:{ip}``, TTL 1h, INCR atômico); se o Redis
  estiver fora, a classificação de rate/pre-fetch simplesmente pula (fail-open).

As funções `should_log`/`extract_domain`/`mask_ip`/`extract_ip` são PURAS e testadas
offline. Ver `api.bot_classifier` e KL-92.
"""

from __future__ import annotations

import asyncio
import ipaddress
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from starlette.requests import Request

from api.bot_classifier import classify_bot, is_human_action
from discovery.store import get_target_store

# monotonic() é o relógio para medir latência (imune a ajuste de horário do sistema).
import time

# --------------------------------------------------------------------------- #
# Skip de assets estáticos — o access log é sobre navegação, não sobre bytes servidos.
# --------------------------------------------------------------------------- #
SKIP_PREFIXES = (
    "/_astro/", "/assets/", "/fonts/", "/images/",
    "/favicon", "/robots.txt", "/sitemap",
    "/track.js", "/theme.js", "/.well-known/", "/seal/",
)

SKIP_EXTENSIONS = (
    ".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".svg",
    ".ico", ".woff", ".woff2", ".ttf", ".eot", ".map", ".webp", ".avif",
)


def should_log(path: str) -> bool:
    """True se o path deve entrar no access log (NÃO é asset estático). Puro."""
    if not path:
        return False
    for pre in SKIP_PREFIXES:
        if path.startswith(pre):
            return False
    low = path.lower()
    for ext in SKIP_EXTENSIONS:
        if low.endswith(ext):
            return False
    return True


# --------------------------------------------------------------------------- #
# Extração (pura, testável)
# --------------------------------------------------------------------------- #

def is_valid_ip(ip: Optional[str]) -> bool:
    """True se `ip` é um IPv4/IPv6 válido. A coluna ``ip_address`` é ``INET NOT NULL`` — um
    valor inválido (ex.: ``unknown``, do fallback de peer) quebraria o batch INSERT inteiro,
    então registros com IP inválido NÃO são logados."""
    if not ip:
        return False
    try:
        ipaddress.ip_address(ip.strip())
        return True
    except ValueError:
        return False


def extract_ip(request: Request) -> str:
    """IP REAL do cliente. Atrás do Cloudflare, o real vem em ``CF-Connecting-IP``
    (o Nginx repassa o header). Ordem: CF-Connecting-IP → X-Real-IP → peer. Mesma
    lógica de `api.main._client_ip` (mantida local para o middleware ser autônomo)."""
    cf = request.headers.get("cf-connecting-ip")
    if cf:
        return cf.strip()
    xri = request.headers.get("x-real-ip")
    if xri:
        return xri.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _clean_domain(raw: Optional[str]) -> Optional[str]:
    """Normaliza um domínio: lowercase, sem ``www.``, sem porta/path, ≤255 chars.
    Descarta valores obviamente inválidos (sem ponto ou com char proibido)."""
    if not raw:
        return None
    d = raw.strip().lower().split("/")[0].split("?")[0].split("#")[0]
    if d.startswith("www."):
        d = d[4:]
    d = d.split(":")[0]
    if not d or "." not in d or len(d) > 255:
        return None
    if any(c not in "abcdefghijklmnopqrstuvwxyz0123456789.-" for c in d):
        return None
    return d


def _domain_of_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    u = url.strip()
    host = urlparse(u if "://" in u else "https://" + u).hostname
    return _clean_domain(host)


def extract_domain(path: str, query_url: Optional[str] = None,
                   state_domain: Optional[str] = None) -> Optional[str]:
    """Domínio consultado, pela rota:
      - handler já resolveu (``request.state.domain_queried``) → usa;
      - ``/site/{domain}`` → o segmento;
      - ``/scan`` / ``/scan/result`` (``?url=``) → hostname da URL;
      - demais (inclui ``/setor/{slug}``, que é slug e não domínio) → None. Puro."""
    if state_domain:
        return _clean_domain(state_domain)
    p = (path or "").split("?")[0]
    if p.startswith("/site/"):
        return _clean_domain(p[len("/site/"):])
    if p == "/scan" or p.startswith("/scan/") or p.endswith("/scan"):
        return _domain_of_url(query_url)
    return None


def _extract_user_id(request: Request) -> Optional[int]:
    """user_id do JWT de usuário (cookie ou Bearer). Um Bearer de ADMIN falha o
    `verify_user_token` (typ != user) → None. Nunca levanta."""
    try:
        from api import auth_users
        token = auth_users.token_from_request(request)
        if not token:
            return None
        payload = auth_users.verify_user_token(token)
        return int(payload["user_id"])
    except Exception:  # noqa: BLE001 - sem user → None
        return None


def mask_ip(ip: Optional[str], octets: int = 1) -> str:
    """Mascara o IP para exibição (LGPD): mantém os primeiros `octets` octetos, oculta o
    resto com ``x``. `189.28.1.42` → (1) ``189.x.x.x`` · (2) ``189.28.x.x``. IPv6 →
    mantém os 2 primeiros grupos + ``::x``. O IP COMPLETO fica só no banco."""
    if not ip:
        return ""
    ip = str(ip).strip()
    if ":" in ip:  # IPv6
        groups = ip.split(":")
        keep = [g for g in groups[:2] if g]
        return ":".join(keep) + "::x" if keep else "::x"
    parts = ip.split(".")
    if len(parts) != 4:
        return ip
    octets = max(1, min(octets, 3))
    return ".".join(parts[:octets] + ["x"] * (4 - octets))


# --------------------------------------------------------------------------- #
# Buffer + flush em batch (drenado por um loop periódico)
# --------------------------------------------------------------------------- #
_BUFFER: list = []
_MAX_BUFFER = 10000          # cap de segurança: descarta os mais antigos se estourar
_FLUSH_INTERVAL = 5          # segundos entre flushes
_flush_task: Optional[asyncio.Task] = None
_bg_tasks: set = set()


def _spawn(coro) -> None:
    """Fire-and-forget: processamento do log nunca bloqueia nem derruba o response."""
    try:
        task = asyncio.create_task(coro)
        _bg_tasks.add(task)
        task.add_done_callback(_bg_tasks.discard)
    except RuntimeError:  # sem event loop (não deveria acontecer num request) → descarta
        coro.close()


def _enqueue(record: Dict[str, Any]) -> None:
    _BUFFER.append(record)
    if len(_BUFFER) > _MAX_BUFFER:
        del _BUFFER[: len(_BUFFER) - _MAX_BUFFER]


async def flush_access_log(store: Any = None) -> int:
    """Drena o buffer para o banco (batch INSERT). Fail-safe: se o INSERT falhar, os
    registros do lote são descartados (evita loop infinito de re-tentativa). Retorna o
    nº gravado."""
    if not _BUFFER:
        return 0
    batch = _BUFFER[:]
    del _BUFFER[: len(batch)]
    try:
        store = store or get_target_store()
        return await store.log_access_batch(batch)
    except Exception as exc:  # noqa: BLE001 - log é best-effort; nunca derruba nada
        print(f"[access_log] flush falhou; {len(batch)} registros perdidos ({exc!r})",
              flush=True)
        return 0


async def _flush_loop() -> None:
    while True:
        try:
            await asyncio.sleep(_FLUSH_INTERVAL)
            await flush_access_log()
        except asyncio.CancelledError:
            await flush_access_log()  # drain final no shutdown
            raise
        except Exception as exc:  # noqa: BLE001 - o loop nunca morre por um flush ruim
            print(f"[access_log] flush loop erro ({exc!r})", flush=True)


def start_flush_task() -> Optional[asyncio.Task]:
    """Inicia (idempotente) o loop de flush. Chamado no lifespan da API."""
    global _flush_task
    try:
        if _flush_task is None or _flush_task.done():
            _flush_task = asyncio.create_task(_flush_loop())
    except RuntimeError:  # sem loop (import/test) → sem task
        return None
    return _flush_task


# --------------------------------------------------------------------------- #
# Rate counter (Redis) — INCR atômico, TTL 1h. Fail-open.
# --------------------------------------------------------------------------- #

def _redis():
    try:
        from api import main as _m
        return _m._cache.redis if _m._cache is not None else None
    except Exception:  # noqa: BLE001
        return None


async def _incr_rate(ip: str) -> int:
    """Incrementa e devolve o nº de requests do IP na última hora. 0 = sem info (Redis
    fora ou IP ausente) → a classificação de rate/pre-fetch simplesmente pula."""
    redis = _redis()
    if redis is None or not ip:
        return 0
    try:
        key = f"access_rate:{ip}"
        n = await redis.incr(key)
        if n == 1:
            await redis.expire(key, 3600)
        return int(n)
    except Exception:  # noqa: BLE001 - Redis instável → fail-open
        return 0


# --------------------------------------------------------------------------- #
# Processamento em background + o middleware
# --------------------------------------------------------------------------- #

def _capture(request: Request, response: Any, path: str, elapsed_ms: int) -> Dict[str, Any]:
    """Extrai (síncrono, barato) tudo que o log precisa. NUNCA lê o body (consumiria o
    stream). O domínio de um POST vem de ``request.state.domain_queried`` (handler)."""
    country = request.headers.get("cf-ipcountry")
    country = country.strip().upper()[:2] if country else None
    referrer = request.headers.get("referer")
    ua = request.headers.get("user-agent")
    query_url = request.query_params.get("url")
    state_domain = getattr(request.state, "domain_queried", None)
    return {
        "ip_address": extract_ip(request),
        "country_code": country,
        "endpoint": (path or "")[:200],
        "http_method": (request.method or "")[:10],
        "http_status": getattr(response, "status_code", None),
        "domain_queried": extract_domain(path, query_url, state_domain),
        "user_id": _extract_user_id(request),
        "user_agent": ua,
        "referrer": (referrer[:500] if referrer else None),
        "response_time_ms": elapsed_ms,
    }


async def _process_access(ctx: Dict[str, Any]) -> None:
    """Classifica bot/humano e enfileira o registro. Roda em background (não bloqueia o
    response). Se for uma AÇÃO HUMANA, marca o registro como humano e corrige
    retroativamente os registros anteriores do mesmo IP no dia."""
    try:
        ip = ctx.get("ip_address")
        if not is_valid_ip(ip):
            return  # sem IP válido → não loga (ip_address é INET NOT NULL)
        count = await _incr_rate(ip)
        is_bot, reason = classify_bot(
            ip, ctx.get("user_agent"), ctx.get("country_code"), ctx.get("endpoint"),
            request_count_last_hour=count, user_id=ctx.get("user_id"),
            has_other_requests=(count > 1))
        human = is_human_action(ctx.get("http_method"), ctx.get("endpoint"))
        if human:
            is_bot, reason = False, None
        ctx["is_bot"], ctx["bot_reason"] = is_bot, reason
        _enqueue(ctx)
        if human and ip and ip != "unknown":
            try:
                await get_target_store().mark_ip_human_today(ip)
            except Exception:  # noqa: BLE001 - retroatividade é best-effort
                pass
    except Exception as exc:  # noqa: BLE001 - processamento nunca propaga
        print(f"[access_log] processamento falhou ({exc!r})", flush=True)


async def access_log_middleware(request: Request, call_next):
    """Middleware HTTP: mede a latência, deixa o request seguir, e agenda (fire-and-forget)
    a gravação do access log. NUNCA atrasa nem quebra o response — toda a lógica de log
    está protegida por try/except e roda fora do caminho síncrono."""
    path = request.url.path
    if not should_log(path):
        return await call_next(request)
    start = time.monotonic()
    response = await call_next(request)
    try:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        ctx = _capture(request, response, path, elapsed_ms)
        _spawn(_process_access(ctx))
    except Exception as exc:  # noqa: BLE001 - log jamais afeta o response
        print(f"[access_log] captura falhou ({exc!r})", flush=True)
    return response
