"""Discovery Worker — motor de aquisição do Klarim.

Modelo contínuo (KL-15): um **poller de CT logs** (lê os CT logs públicos direto,
em tempo real) filtra domínios `.com.br` e os acumula num buffer; a cada
`DISCOVERY_INTERVAL_MINUTES` (padrão 30) o worker drena o buffer, deduplica
contra o banco e, para cada domínio novo: fetch → fingerprint → extrai e-mail →
classifica setor → registra e enfileira para scan. Sem e-mail extraível: registra
como 'sem_contato' e NÃO enfileira (sem contato = sem conversão).

Se o poller não coletou nada no ciclo (rede/logs fora?), o worker faz **uma
tentativa de fallback no crt.sh** (KL-11). Redundância — a descoberta nunca para.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone

import httpx

from scanner.checks.base import fetch, domain_of
from .ct_client import CTClient
from .ct_poller import CTLogPoller
from .fingerprint import detect_platform
from .contact import extract_email
from .classifier import classify_sector
from .store import get_target_store
from . import worker_control

SCAN_QUEUE = os.environ.get("KLARIM_SCAN_QUEUE", "klarim:scan_queue")
STATUS_KEY = os.environ.get("KLARIM_DISCOVERY_STATUS_KEY", "discovery:status")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DiscoveryWorker:
    def __init__(self) -> None:
        self.batch_size = int(os.environ.get("DISCOVERY_BATCH_SIZE", "100"))
        self.interval_minutes = int(os.environ.get("DISCOVERY_INTERVAL_MINUTES", "30"))
        self.pause_s = float(os.environ.get("DISCOVERY_PAUSE_SECONDS", "2"))
        # Timeout TOTAL por domínio (KL-19): impede que um site travado
        # (redirect infinito, servidor que aceita mas não responde) congele o
        # event loop inteiro — que era compartilhado por discovery/alert/rescan.
        self.domain_timeout = int(os.environ.get("DISCOVERY_DOMAIN_TIMEOUT", "30"))
        # Watchdog (KL-19): reinicia o processo se o event loop não progredir.
        self.watchdog_timeout = int(os.environ.get("DISCOVERY_WATCHDOG_SECONDS", "600"))
        self._last_progress = time.monotonic()
        self.store = get_target_store()
        self.ct = CTClient()
        self.source = CTLogPoller()
        self._redis = None
        # Estado para o /api/discovery/status (publicado no Redis).
        self._started_at = None
        self._last_cycle_at = None
        self._next_cycle_at = None
        self._cycles_completed = 0
        self._last_cycle_stats: dict = {}

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
        await r.rpush(SCAN_QUEUE, json.dumps(
            {"target_id": target_id, "url": url, "source": "discovery"}))

    # --- status (ponte Redis para a API, que roda noutro container) -------- #

    def _status_payload(self) -> dict:
        return {
            "source": self.source.get_stats(),
            "source_kind": "ct_poller",
            "started_at": self._started_at.isoformat() if self._started_at else None,
            "last_cycle_at": self._last_cycle_at.isoformat() if self._last_cycle_at else None,
            "next_cycle_at": self._next_cycle_at.isoformat() if self._next_cycle_at else None,
            "cycles_completed": self._cycles_completed,
            "last_cycle_stats": self._last_cycle_stats,
        }

    async def _write_status(self) -> None:
        try:
            r = await self._redis_client()
            # TTL 600s (KL-16): se o worker morrer, o status expira e o painel mostra 🔴.
            await r.set(STATUS_KEY, json.dumps(self._status_payload()), ex=600)
        except Exception as exc:  # noqa: BLE001 - status é best-effort
            print(f"[discovery] falha ao publicar status ({exc!r})", flush=True)

    async def _status_heartbeat(self) -> None:
        # Mantém o /api/discovery/status fresco entre ciclos (buffer/total mudam
        # o tempo todo no listener) e marca "progresso" para o watchdog (KL-19):
        # se o event loop travar, este loop para e o watchdog reinicia o processo.
        while True:
            self._last_progress = time.monotonic()
            await self._write_status()
            await asyncio.sleep(20)

    def _start_watchdog(self) -> None:
        """Thread separada (KL-19): se o event loop não progride há
        `DISCOVERY_WATCHDOG_SECONDS`, encerra o processo (os._exit) — o Docker
        (`restart: unless-stopped`) sobe um novo. Roda numa THREAD para funcionar
        mesmo com o loop asyncio 100% travado."""
        def _run():
            while True:
                time.sleep(60)
                stale = time.monotonic() - self._last_progress
                if stale > self.watchdog_timeout:
                    print(f"[discovery] WATCHDOG: sem progresso há {stale:.0f}s "
                          f"(> {self.watchdog_timeout}s) — reiniciando o processo", flush=True)
                    os._exit(1)

        threading.Thread(target=_run, name="discovery-watchdog", daemon=True).start()

    # --- ciclo ------------------------------------------------------------- #

    async def _get_domains(self, stats: dict) -> list:
        domains = self.source.flush_buffer()
        stats["source"] = "ct_poller"
        stats["buffer"] = len(domains)
        if not domains:
            print("[discovery] poller de CT vazio, tentando crt.sh como fallback", flush=True)
            domains = await self.ct.get_recent_domains(
                suffix=".com.br", days=7, limit=self.batch_size * 3)
            stats["source"] = "crt.sh" if domains else "none"
        return domains

    async def _process_domain(self, domain: str, stats: dict) -> None:
        """Processa um domínio: fetch → fingerprint → e-mail → setor → registra.

        Sempre chamado sob `asyncio.wait_for` (KL-19), então qualquer await interno
        (fetch, /contato, DB) é interrompível se estourar o timeout do domínio.
        """
        url = f"https://{domain}"
        html = await self._fetch_html(url)
        if html is None:
            # Site fora do ar: ainda tenta classificar pela pista do domínio.
            sector, tier, confidence = classify_sector(None, url)
            await self.store.register_target(
                url, domain, "unknown", sector, tier, None, status="descartado",
                confidence=confidence)
            stats["unreachable"] += 1
            return

        platform = detect_platform(url, html)
        email = await extract_email(html, url)
        sector, tier, confidence = classify_sector(html, url)

        if not email:
            await self.store.register_target(
                url, domain, platform, sector, tier, None, status="sem_contato",
                confidence=confidence)
            stats["no_contact"] += 1
        else:
            tid = await self.store.register_target(
                url, domain, platform, sector, tier, email, status="discovered",
                confidence=confidence)
            stats["registered"] += 1
            await self._enqueue(tid, url)
            stats["enqueued"] += 1

    async def run_cycle(self) -> dict:
        stats = {
            "source": None, "buffer": 0, "processed": 0, "skipped_existing": 0,
            "no_contact": 0, "registered": 0, "enqueued": 0, "unreachable": 0,
            "timeouts": 0, "errors": 0,
        }
        # Controle centralizado (KL-32): pausa por MCP/painel.
        if not worker_control.is_enabled("discovery"):
            print("[discovery] worker pausado (worker_control); pulando ciclo", flush=True)
            stats["disabled"] = True
            self._last_cycle_stats = stats
            return stats
        # Override de tamanho do ciclo (KL-32).
        batch = int(worker_control.worker_config("discovery").get("max_targets_per_cycle")
                    or self.batch_size)

        domains = await self._get_domains(stats)
        if not domains:
            print("[discovery] nenhuma fonte retornou domínios neste ciclo", flush=True)
            self._last_cycle_stats = stats
            return stats

        print(f"[discovery] buffer: {len(domains)} domínios .com.br → processando "
              f"até {batch} (fonte={stats['source']})", flush=True)

        for domain in domains:
            if stats["processed"] >= batch:
                break
            try:
                if await self.store.domain_exists(domain):
                    stats["skipped_existing"] += 1
                    continue
            except Exception as exc:  # noqa: BLE001
                stats["errors"] += 1
                print(f"[discovery] erro no dedup de {domain}: {exc!r}", flush=True)
                continue

            stats["processed"] += 1
            self._last_progress = time.monotonic()  # progresso p/ o watchdog (KL-19)
            t0 = time.monotonic()
            try:
                await asyncio.wait_for(
                    self._process_domain(domain, stats), timeout=self.domain_timeout)
                elapsed = time.monotonic() - t0
                if elapsed > 10:
                    print(f"[discovery] {domain} processado em {elapsed:.1f}s (lento)", flush=True)
            except asyncio.TimeoutError:
                stats["timeouts"] += 1
                print(f"[discovery] {domain} timeout após {self.domain_timeout}s — pulando", flush=True)
            except Exception as exc:  # noqa: BLE001 - um domínio ruim não derruba o ciclo
                stats["errors"] += 1
                print(f"[discovery] erro em {domain}: {exc!r}", flush=True)
            await asyncio.sleep(self.pause_s)

        print(f"[discovery] ciclo completo: {stats['processed']} processados, "
              f"{stats['registered']} com email, {stats['no_contact']} sem contato, "
              f"{stats['timeouts']} timeouts, {stats['errors']} erros, "
              f"{stats['skipped_existing']} já registrados", flush=True)
        self._last_cycle_stats = stats
        return stats

    async def start(self) -> None:
        try:
            await self.store.ensure_schema()
        except Exception as exc:  # noqa: BLE001 - tabelas podem já existir (outro container)
            print(f"[discovery] ensure_schema: {exc!r} (seguindo)", flush=True)

        self.source.start_listener()
        self._started_at = _utcnow()
        self._last_progress = time.monotonic()
        self._start_watchdog()
        asyncio.create_task(self._status_heartbeat())
        print(f"[discovery] iniciado (poller de CT logs + fallback crt.sh, "
              f"batch={self.batch_size}, intervalo={self.interval_minutes}min, "
              f"timeout/domínio={self.domain_timeout}s, watchdog={self.watchdog_timeout}s)", flush=True)

        # Aquecimento: deixa o poller encher o buffer antes do 1º ciclo (senão a
        # primeira drenagem sai vazia e cai no crt.sh à toa).
        warmup = int(os.environ.get("DISCOVERY_WARMUP_SECONDS", "90"))
        await asyncio.sleep(warmup)

        while True:
            try:
                await self.run_cycle()
            except Exception as exc:  # noqa: BLE001
                print(f"[discovery] ciclo falhou: {exc!r}", flush=True)
            self._cycles_completed += 1
            # Intervalo dinâmico (KL-32): cycle_minutes do controle, senão o do env.
            interval = int(worker_control.worker_config("discovery").get("cycle_minutes")
                           or self.interval_minutes)
            self._last_cycle_at = _utcnow()
            self._next_cycle_at = self._last_cycle_at + timedelta(minutes=interval)
            await self._write_status()
            await asyncio.sleep(interval * 60)


async def _run_all() -> None:
    from .alert_worker import AlertWorker
    from .rescan_worker import RescanWorker

    # Um único container roda os três loops: descoberta (Certstream, ~30min),
    # alertas (1h) e re-scan de evolução (24h).
    await asyncio.gather(
        DiscoveryWorker().start(),
        AlertWorker().start(),
        RescanWorker().start(),
    )


def main() -> None:
    asyncio.run(_run_all())


if __name__ == "__main__":
    main()
