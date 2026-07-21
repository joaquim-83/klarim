"""KL-95 — reclassifica retroativamente pre-fetchers de e-mail no access_log.

O classificador de pre-fetch (KL-92 P4) só marca IPs NOVOS; os registros anteriores de IPs
`66.x`/`40.x`/`104.47` (Gmail/Outlook/EOP) ficaram `is_bot=false` e apareciam como humanos
visitando centenas de sites. Este script os marca `is_bot=true`/`email_prefetch`. **Idempotente**
(só toca `is_bot=false`) — pode rodar quantas vezes quiser. Roda na VM:

    sudo docker exec klarim-api-1 python -m scripts.reclassify_prefetch_bots

Os ranges vêm do MESMO `_EMAIL_PREFETCH_CIDRS` do classificador (fonte única).
"""

from __future__ import annotations

import asyncio


async def main() -> None:
    from api.bot_classifier import _EMAIL_PREFETCH_CIDRS
    from discovery.store import get_target_store

    store = get_target_store()
    n = await store.reclassify_prefetch_bots(list(_EMAIL_PREFETCH_CIDRS))
    print(f"[reclassify] {n} registro(s) reclassificado(s) como email_prefetch "
          f"(ranges: {', '.join(_EMAIL_PREFETCH_CIDRS)})", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
