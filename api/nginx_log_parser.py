"""KL-92 Prompt 3 — parser do access_log do Nginx (cobertura completa do tráfego).

O middleware FastAPI (Prompt 1) só enxerga o tráfego que chega à API Python (`/api`, `/mcp`) —
~12% do total. As páginas SSR do Astro (landing, `/scan`, `/site/*`, `/setor/*`) passam pelo
Nginx **direto** ao container Astro, sem tocar no FastAPI. Como o Nginx vê 100% do tráfego, este
parser lê o access_log dele e insere na MESMA tabela `access_log` do Postgres.

**Cobertura sem duplicar (hybrid):** o parser processa APENAS páginas não-`/api`/`/mcp` (o
middleware já cobre essas, com `user_id` + retroatividade). Conjuntos disjuntos → nenhuma
duplicata. Os registros do parser levam `source='nginx'`; os do middleware, `source='middleware'`.

Design: roda como task periódica (30s) no lifespan da API. Lê **incrementalmente** (offset +
inode p/ detectar rotação); quando o arquivo passa de `MAX_BYTES` o parser **trunca** (seguro:
o Nginx abre logs em `O_APPEND`, então a próxima escrita vai para o offset 0). Fail-safe: erro
de I/O/DB é logado e engolido — nunca derruba a API. Ver KL-92 Prompt 3.

`parse_line` é PURA e testada offline; a leitura de arquivo roda em thread (não bloqueia o loop).
"""

from __future__ import annotations

import asyncio
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from api.access_log_middleware import extract_domain, is_valid_ip, should_log
from api.bot_classifier import classify_bot_simple

DEFAULT_LOG_PATH = "/var/log/klarim/access.log"
MAX_LINES_PER_CYCLE = 20000          # teto por ciclo (evita batch gigante após downtime)
MAX_BYTES = 50 * 1024 * 1024         # 50MB → trunca (bound do arquivo; sem logrotate no host)

# Casa o `log_format klarim` (ver frontend/nginx/log_format.conf):
#   $http_cf_connecting_ip - $remote_user [$time_local] "$request" $status $body_bytes_sent
#   "$http_referer" "$http_user_agent" country=$http_cf_ipcountry rt=$request_time
LOG_PATTERN = re.compile(
    r'(?P<ip>\S+) - (?P<user>\S+) \[(?P<time>[^\]]+)\] '
    r'"(?P<method>\S+) (?P<path>\S+) [^"]*" (?P<status>\d+) (?P<bytes>\S+) '
    r'"(?P<referrer>[^"]*)" "(?P<ua>[^"]*)" '
    r'country=(?P<country>\S+) rt=(?P<rt>\S+)'
)

# O middleware já cobre a API e o MCP — o parser os PULA para não duplicar.
_MIDDLEWARE_PREFIXES = ("/api", "/mcp")


def _middleware_covered(path: str) -> bool:
    return path == "/api" or path == "/mcp" or path.startswith("/api/") or path.startswith("/mcp/")


def _url_param(raw_path: str) -> Optional[str]:
    """Extrai `?url=` do path bruto (para `/scan?url=…`)."""
    if "?" not in raw_path or "url=" not in raw_path:
        return None
    try:
        from urllib.parse import parse_qs
        return parse_qs(raw_path.split("?", 1)[1]).get("url", [None])[0]
    except Exception:  # noqa: BLE001
        return None


def parse_line(line: str) -> Optional[Dict[str, Any]]:
    """Parseia uma linha do access_log (formato `klarim`) → dict pronto para
    `log_access_batch`, ou None se deve ser ignorada (não casa, asset estático, coberto pelo
    middleware, ou IP inválido). PURA e testável."""
    m = LOG_PATTERN.match(line or "")
    if not m:
        return None
    raw_path = m.group("path") or ""
    path = raw_path.split("?")[0]
    if _middleware_covered(path):
        return None                       # /api ou /mcp → o middleware já loga
    if not should_log(path):
        return None                       # asset estático (mesma regra do middleware)
    ip = (m.group("ip") or "").strip()
    if not is_valid_ip(ip):
        return None                       # ip_address é INET NOT NULL

    country = m.group("country")
    country = country.strip().upper()[:2] if country and country not in ("-", "") else None
    ua = m.group("ua")
    ua = ua if ua and ua != "-" else None
    referrer = m.group("referrer")
    referrer = referrer[:500] if referrer and referrer != "-" else None
    try:
        status = int(m.group("status"))
    except (TypeError, ValueError):
        status = None
    rt = m.group("rt")
    try:
        response_time_ms = int(float(rt) * 1000) if rt and rt != "-" else None
    except (TypeError, ValueError):
        response_time_ms = None

    domain = extract_domain(path, _url_param(raw_path))
    is_bot, reason = classify_bot_simple(ip, ua, country)
    return {
        "ip_address": ip, "country_code": country, "endpoint": path[:200],
        "http_method": (m.group("method") or "")[:10], "http_status": status,
        "domain_queried": domain, "user_id": None, "user_agent": ua,
        "referrer": referrer, "response_time_ms": response_time_ms,
        "is_bot": is_bot, "bot_reason": reason, "source": "nginx",
    }


class NginxLogParser:
    """Lê incrementalmente o access_log do Nginx e insere em batch na tabela access_log."""

    def __init__(self, store: Any = None, log_path: Optional[str] = None):
        self._store = store
        self.log_path = log_path or os.environ.get("NGINX_ACCESS_LOG", DEFAULT_LOG_PATH)
        self.offset = 0
        self.inode: Optional[int] = None

    def _store_or_default(self):
        if self._store is not None:
            return self._store
        from discovery.store import get_target_store
        return get_target_store()

    def _read_new_lines(self) -> Tuple[List[str], bool]:
        """SYNC — lê linhas novas desde `offset`, atualiza offset/inode e sinaliza se o
        arquivo deve ser truncado (passou de MAX_BYTES). Detecta rotação (inode) e
        truncação externa (tamanho < offset). Roda em thread (não bloqueia o loop)."""
        try:
            st = os.stat(self.log_path)
        except OSError:
            return [], False               # sem arquivo ainda (volume não montado / dev)
        if self.inode is not None and st.st_ino != self.inode:
            self.offset = 0                # log rotacionado (inode mudou)
        self.inode = st.st_ino
        if st.st_size < self.offset:
            self.offset = 0                # truncado externamente
        if st.st_size <= self.offset:
            return [], False               # nada novo
        lines: List[str] = []
        with open(self.log_path, "r", errors="replace") as f:
            f.seek(self.offset)
            # ⚠️ `for line in f` usa o protocolo de iterador (readahead) e DESABILITA
            # `f.tell()` → OSError('telling position disabled by next() call') a cada ciclo.
            # `readline()` é compatível com `tell()`. Uma linha SEM '\n' final é o Nginx
            # escrevendo no meio: deixa para o próximo ciclo (não avança o offset por ela).
            while len(lines) < MAX_LINES_PER_CYCLE:
                pos = f.tell()
                line = f.readline()
                if not line:
                    break                                  # fim do arquivo
                if not line.endswith("\n"):
                    f.seek(pos)                            # linha parcial → reprocessa depois
                    break
                lines.append(line.rstrip("\n"))
            self.offset = f.tell()
        truncate = st.st_size >= MAX_BYTES and self.offset >= st.st_size
        return lines, truncate

    def _truncate(self) -> None:
        try:
            os.truncate(self.log_path, 0)  # seguro: Nginx abre logs em O_APPEND
            self.offset = 0
        except OSError as exc:
            print(f"[nginx_parser] truncate falhou ({exc!r})", flush=True)

    async def parse_new_lines(self) -> int:
        """Lê + parseia + insere as linhas novas. Retorna o nº de registros inseridos.
        Fail-safe: qualquer erro é logado e engolido."""
        lines, truncate = await asyncio.to_thread(self._read_new_lines)
        records = [r for r in (parse_line(ln) for ln in lines) if r]
        if records:
            try:
                await self._store_or_default().log_access_batch(records)
            except Exception as exc:  # noqa: BLE001 - best-effort; nunca derruba a API
                print(f"[nginx_parser] insert falhou; {len(records)} perdidos ({exc!r})",
                      flush=True)
        if truncate:
            await asyncio.to_thread(self._truncate)
        return len(records)


# --------------------------------------------------------------------------- #
# Loop periódico (iniciado no lifespan da API)
# --------------------------------------------------------------------------- #
_PARSE_INTERVAL = 30
_parser: Optional[NginxLogParser] = None
_parse_task: Optional[asyncio.Task] = None


async def _parse_loop() -> None:
    parser = _parser or NginxLogParser()
    while True:
        try:
            await asyncio.sleep(_PARSE_INTERVAL)
            n = await parser.parse_new_lines()
            if n:
                print(f"[nginx_parser] {n} registros do Nginx", flush=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - o loop nunca morre por um ciclo ruim
            print(f"[nginx_parser] ciclo falhou ({exc!r})", flush=True)


def start_parse_task() -> Optional[asyncio.Task]:
    """Inicia (idempotente) o loop de parsing do access_log do Nginx. Chamado no lifespan.
    Se o arquivo não existir (sem o volume), o parser simplesmente não acha nada (no-op)."""
    global _parser, _parse_task
    try:
        if _parser is None:
            _parser = NginxLogParser()
        if _parse_task is None or _parse_task.done():
            _parse_task = asyncio.create_task(_parse_loop())
    except RuntimeError:  # sem event loop (import/test)
        return None
    return _parse_task
