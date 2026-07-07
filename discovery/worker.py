"""Discovery Worker — motor de aquisição do Klarim.

Ciclo (a cada 6h): CT logs (crt.sh) → filtra ruído/já-registrados → para cada
domínio: fetch + fingerprint + extrai e-mail + classifica setor → registra e
enfileira para scan. Sem e-mail extraível: registra como 'sem_contato' e NÃO
enfileira (sem contato = sem conversão = não vale o custo de scan).
"""

from __future__ import annotations

import asyncio
import json
import os

import httpx

from scanner.checks.base import fetch, domain_of
from .ct_client import CTClient
from .fingerprint import detect_platform
from .contact import extract_email
from .classifier import classify_sector
from .store import get_target_store

SCAN_QUEUE = os.environ.get("KLARIM_SCAN_QUEUE", "klarim:scan_queue")


class DiscoveryWorker:
    def __init__(self) -> None:
        self.batch_size = int(os.environ.get("DISCOVERY_BATCH_SIZE", "100"))
        self.interval_hours = int(os.environ.get("DISCOVERY_INTERVAL_HOURS", "6"))
        self.pause_s = float(os.environ.get("DISCOVERY_PAUSE_SECONDS", "2"))
        self.store = get_target_store()
        self.ct = CTClient()
        self._redis = None

    async def _redis_client(self):
        if self._redis is None:
            import redis.asyncio as aioredis

            self._redis = aioredis.from_url(
                os.environ.get("REDIS_URL", "redis://redis:6379/0"), decode_responses=True
            )
        return self._redis

    async def _fetch_html(self, url: str):
        try:
            resp = await fetch(url, method="GET", follow_redirects=True)
        except (httpx.HTTPError, OSError):
            return None
        return resp.text if resp.status_code < 400 else None

    async def _enqueue(self, target_id: int, url: str) -> None:
        r = await self._redis_client()
        await r.rpush(SCAN_QUEUE, json.dumps({"target_id": target_id, "url": url}))

    async def run_cycle(self) -> dict:
        stats = {
            "ct_domains": 0, "processed": 0, "skipped_existing": 0, "no_contact": 0,
            "registered": 0, "enqueued": 0, "unreachable": 0, "errors": 0,
        }
        domains = await self.ct.get_recent_domains(limit=self.batch_size * 3)
        stats["ct_domains"] = len(domains)
        print(f"[discovery] {len(domains)} domínios do crt.sh; processando até {self.batch_size}", flush=True)

        for domain in domains:
            if stats["processed"] >= self.batch_size:
                break
            try:
                if await self.store.domain_exists(domain):
                    stats["skipped_existing"] += 1
                    continue
                stats["processed"] += 1
                url = f"https://{domain}"
                html = await self._fetch_html(url)
                if html is None:
                    await self.store.register_target(
                        url, domain, "unknown", "outro", "standard", None, status="descartado")
                    stats["unreachable"] += 1
                    await asyncio.sleep(self.pause_s)
                    continue

                platform = detect_platform(url, html)
                email = await extract_email(html, url)
                sector, tier = classify_sector(html)

                if not email:
                    await self.store.register_target(
                        url, domain, platform, sector, tier, None, status="sem_contato")
                    stats["no_contact"] += 1
                else:
                    tid = await self.store.register_target(
                        url, domain, platform, sector, tier, email, status="discovered")
                    stats["registered"] += 1
                    await self._enqueue(tid, url)
                    stats["enqueued"] += 1
            except Exception as exc:  # noqa: BLE001 - um domínio ruim não derruba o ciclo
                stats["errors"] += 1
                print(f"[discovery] erro em {domain}: {exc!r}", flush=True)
            await asyncio.sleep(self.pause_s)

        print(f"[discovery] ciclo concluído: {stats}", flush=True)
        return stats

    async def start(self) -> None:
        try:
            await self.store.ensure_schema()
        except Exception as exc:  # noqa: BLE001 - tabelas podem já existir (outro container)
            print(f"[discovery] ensure_schema: {exc!r} (seguindo)", flush=True)
        print(f"[discovery] iniciado (batch={self.batch_size}, intervalo={self.interval_hours}h)", flush=True)
        while True:
            try:
                await self.run_cycle()
            except Exception as exc:  # noqa: BLE001
                print(f"[discovery] ciclo falhou: {exc!r}", flush=True)
            await asyncio.sleep(self.interval_hours * 3600)


async def _run_all() -> None:
    from .alert_worker import AlertWorker
    from .rescan_worker import RescanWorker

    # Um único container roda os três loops: descoberta (6h), alertas (1h) e
    # re-scan de evolução (24h).
    await asyncio.gather(
        DiscoveryWorker().start(),
        AlertWorker().start(),
        RescanWorker().start(),
    )


def main() -> None:
    asyncio.run(_run_all())


if __name__ == "__main__":
    main()
