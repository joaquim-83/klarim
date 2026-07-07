"""Cache de ScanReport no Redis (TTL 1h).

Cada scan leva ~30s (rate limit 1 req/s por domínio). Como o resultado não muda
em minutos, cacheamos o `ScanReport` no Redis para que os pedidos subsequentes
(summary, PDFs, e-mail) sejam instantâneos. Falhas no Redis degradam com
elegância: o scan simplesmente roda de novo.
"""

from __future__ import annotations

import hashlib
import json
from typing import Optional

from scanner.runner import ScanReport

DEFAULT_TTL = 3600  # 1 hora


class ScanCache:
    def __init__(self, redis_client, ttl: int = DEFAULT_TTL) -> None:
        self.redis = redis_client
        self.ttl = ttl

    def _key(self, url: str) -> str:
        normalized = url.strip().lower().rstrip("/")
        return f"scan:{hashlib.sha256(normalized.encode()).hexdigest()[:16]}"

    async def get(self, url: str) -> Optional[ScanReport]:
        """Busca scan cacheado. Retorna None em miss, erro ou dado inválido."""
        try:
            raw = await self.redis.get(self._key(url))
        except Exception:  # noqa: BLE001 - Redis indisponível -> sem cache
            return None
        if not raw:
            return None
        try:
            return ScanReport.from_dict(json.loads(raw))
        except Exception:  # noqa: BLE001 - dado corrompido/incompatível -> ignora
            return None

    async def set(self, url: str, report: ScanReport) -> None:
        """Salva o scan no cache com TTL. Falha silenciosa (cache é best-effort)."""
        try:
            await self.redis.set(
                self._key(url), json.dumps(report.to_dict()), ex=self.ttl
            )
        except Exception:  # noqa: BLE001 - não deixar o cache quebrar o fluxo
            pass
