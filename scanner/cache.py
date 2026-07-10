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

    def _key(self, url: str, full: bool = True) -> str:
        # Namespace por tier (KL-27): o scan gratuito (15 checks) e o completo (29)
        # têm resultados diferentes e NÃO podem compartilhar chave. Ambos casam
        # `scan:*` para o flush operacional na VM.
        normalized = url.strip().lower().rstrip("/")
        tier = "full" if full else "free"
        return f"scan:{tier}:{hashlib.sha256(normalized.encode()).hexdigest()[:16]}"

    async def get(self, url: str, full: bool = True) -> Optional[ScanReport]:
        """Busca scan cacheado do tier. Retorna None em miss, erro ou dado inválido."""
        try:
            raw = await self.redis.get(self._key(url, full))
        except Exception:  # noqa: BLE001 - Redis indisponível -> sem cache
            return None
        if not raw:
            return None
        try:
            return ScanReport.from_dict(json.loads(raw))
        except Exception:  # noqa: BLE001 - dado corrompido/incompatível -> ignora
            return None

    async def set(self, url: str, report: ScanReport, full: bool = True) -> None:
        """Salva o scan no cache do tier com TTL. Falha silenciosa (best-effort)."""
        try:
            await self.redis.set(
                self._key(url, full), json.dumps(report.to_dict()), ex=self.ttl
            )
        except Exception:  # noqa: BLE001 - não deixar o cache quebrar o fluxo
            pass
