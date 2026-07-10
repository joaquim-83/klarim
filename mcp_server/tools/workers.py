"""Tools MCP de controle dos workers (KL-32) — pausar/retomar e ajustar throttle
de discovery/alert/rescan/scan sem redeploy (arquivo worker_control.json)."""

from __future__ import annotations

import os
from typing import Optional

from mcp_server._base import mcp, _guard, _api
from discovery import worker_control

_HB_KEYS = {
    "discovery": os.environ.get("KLARIM_DISCOVERY_STATUS_KEY", "discovery:status"),
    "alert": "worker:alert:status",
    "rescan": "worker:rescan:status",
    "scan": "worker:scan:status",
}


@mcp.tool()
async def pause_worker(worker: str) -> dict:
    """Pausa um worker proativo: 'discovery', 'alert', 'rescan', 'scan' ou 'all'.
    O worker lê o estado no início de cada ciclo e pula o ciclo enquanto pausado.
    Aditivo ao STOP_ALERTS. Persiste entre restarts."""
    async def _impl():
        try:
            data = worker_control.pause(worker, by="mcp")
        except ValueError as exc:
            return {"error": str(exc)}
        return {"paused": worker, "control": data}

    return await _guard(_impl)


@mcp.tool()
async def resume_worker(worker: str) -> dict:
    """Retoma um worker: 'discovery', 'alert', 'rescan', 'scan' ou 'all'."""
    async def _impl():
        try:
            data = worker_control.resume(worker)
        except ValueError as exc:
            return {"error": str(exc)}
        return {"resumed": worker, "control": data}

    return await _guard(_impl)


@mcp.tool()
async def get_worker_control() -> dict:
    """Estado de controle (enabled/paused_at/paused_by/config) de cada worker,
    combinado com o estado operacional (alive/dead) do heartbeat no Redis."""
    async def _impl():
        m = _api()
        ctrl = worker_control.load()
        combined = {}
        for w in worker_control.WORKERS:
            try:
                hb = await m._redis_json(_HB_KEYS[w])
            except Exception:  # noqa: BLE001
                hb = None
            combined[w] = {**ctrl.get(w, {}), "alive": hb is not None}
        return {"workers": combined}

    return await _guard(_impl)


@mcp.tool()
async def set_alert_throttle(max_per_hour: int, batch_size: Optional[int] = None) -> dict:
    """Ajusta o throttle do alert worker: `max_per_hour` (limite de e-mails/hora) e,
    opcionalmente, `batch_size`. Lido no início de cada ciclo do alert worker."""
    async def _impl():
        cfg = {"max_per_hour": max_per_hour}
        if batch_size is not None:
            cfg["batch_size"] = batch_size
        data = worker_control.set_config("alert", **cfg)
        return {"worker": "alert", "config": worker_control.worker_config("alert"), "control": data}

    return await _guard(_impl)


@mcp.tool()
async def set_discovery_config(cycle_minutes: Optional[int] = None,
                               max_targets_per_cycle: Optional[int] = None) -> dict:
    """Ajusta o discovery worker: `cycle_minutes` (intervalo) e/ou
    `max_targets_per_cycle`. Lido no início de cada ciclo."""
    async def _impl():
        cfg = {}
        if cycle_minutes is not None:
            cfg["cycle_minutes"] = cycle_minutes
        if max_targets_per_cycle is not None:
            cfg["max_targets_per_cycle"] = max_targets_per_cycle
        data = worker_control.set_config("discovery", **cfg)
        return {"worker": "discovery", "config": worker_control.worker_config("discovery"),
                "control": data}

    return await _guard(_impl)


@mcp.tool()
async def set_scan_config(max_per_hour: Optional[int] = None) -> dict:
    """Ajusta o scan worker: `max_per_hour` (rate limit de scans/hora). Lido no
    início de cada ciclo do scan worker."""
    async def _impl():
        cfg = {}
        if max_per_hour is not None:
            cfg["max_per_hour"] = max_per_hour
        data = worker_control.set_config("scan", **cfg)
        return {"worker": "scan", "config": worker_control.worker_config("scan"), "control": data}

    return await _guard(_impl)
