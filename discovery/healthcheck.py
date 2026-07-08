#!/usr/bin/env python3
"""Healthcheck do container discovery (KL-19).

Verifica no Redis se o event loop dos workers está vivo. Os três workers
(discovery/alert/rescan) rodam no MESMO event loop e cada um publica um heartbeat
com TTL 600s: se o loop trava (como no incidente de 08/07 03:20), todos os
heartbeats expiram. Se NENHUM existe, o Docker reinicia o container (via
HEALTHCHECK + restart:unless-stopped).

Exit 0 = saudável (ou Redis inacessível — não reinicia por causa disso).
Exit 1 = nenhum heartbeat → loop travado → reiniciar.
"""

import os
import sys

# Heartbeats publicados pelo event loop do container discovery.
KEYS = ("discovery:status", "worker:alert:status", "worker:rescan:status")


def main() -> int:
    try:
        import redis

        r = redis.from_url(
            os.environ.get("REDIS_URL", "redis://redis:6379/0"),
            socket_connect_timeout=5, socket_timeout=5,
        )
        for key in KEYS:
            if r.exists(key):
                return 0
        return 1
    except Exception:  # noqa: BLE001 - Redis fora do ar não deve reiniciar o worker
        return 0


if __name__ == "__main__":
    sys.exit(main())
