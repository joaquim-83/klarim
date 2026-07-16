"""Controle centralizado dos workers (KL-32) — pausa/retoma cada worker e ajusta
o throttle via um único arquivo JSON, sem redeploy.

O arquivo (`WORKER_CONTROL_FILE`, padrão `/klarim-control/worker_control.json`) vive
em `/opt/klarim/` (mesmo dir do `.env`/`STOP_ALERTS`), montado nos containers por
volume: a API grava (via MCP/painel), os workers leem no **início de cada ciclo**.

**Fail-open:** arquivo ausente/corrompido/incompleto ⇒ `enabled: true` (nunca trava o
sistema). É **aditivo** ao kill-switch `STOP_ALERTS` do KL-27 (o alert respeita ambos).
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

WORKERS: List[str] = ["discovery", "alert", "rescan", "scan", "vigilia", "bulletin"]

# Chaves de config (override do env) por worker.
_CONFIG_KEYS = {
    "discovery": ("cycle_minutes", "max_targets_per_cycle"),
    "alert": ("max_per_hour", "batch_size"),
    "rescan": (),
    "scan": ("max_per_hour",),
    "vigilia": ("cycle_hours", "max_per_cycle"),  # KL-44 P2
    "bulletin": ("hour_utc", "batch_size"),       # KL-44 P3
}


def control_path() -> str:
    return os.environ.get("WORKER_CONTROL_FILE", "/klarim-control/worker_control.json")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_worker(name: str) -> Dict[str, Any]:
    d: Dict[str, Any] = {"enabled": True, "paused_at": None, "paused_by": None}
    for k in _CONFIG_KEYS.get(name, ()):
        d[k] = None
    return d


def default_control() -> Dict[str, Any]:
    return {w: _default_worker(w) for w in WORKERS}


def load() -> Dict[str, Any]:
    """Lê o controle, sempre devolvendo os 4 workers preenchidos (fail-open).

    Merge defensivo: arquivo/campo ausente ⇒ default (`enabled: true`).
    """
    base = default_control()
    try:
        with open(control_path(), "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (FileNotFoundError, ValueError, OSError):
        return base
    if not isinstance(raw, dict):
        return base
    for w in WORKERS:
        node = raw.get(w)
        if isinstance(node, dict):
            merged = base[w]
            # 'enabled' só é False se explicitamente False; qualquer outra coisa = True.
            merged["enabled"] = node.get("enabled", True) is not False
            merged["paused_at"] = node.get("paused_at")
            merged["paused_by"] = node.get("paused_by")
            for k in _CONFIG_KEYS.get(w, ()):
                if node.get(k) is not None:
                    merged[k] = node[k]
    return base


def is_enabled(worker: str) -> bool:
    """True se o worker deve rodar (fail-open: default True)."""
    try:
        return bool(load().get(worker, {}).get("enabled", True))
    except Exception:  # noqa: BLE001 - nunca deixar o controle travar o worker
        return True


def worker_config(worker: str) -> Dict[str, Any]:
    """Overrides de config do worker (só as chaves não-nulas)."""
    node = load().get(worker, {})
    return {k: node.get(k) for k in _CONFIG_KEYS.get(worker, ()) if node.get(k) is not None}


def save(data: Dict[str, Any]) -> None:
    """Grava atômico (tmp + os.replace) para o leitor nunca ver um JSON pela metade."""
    path = control_path()
    d = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".wc_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _targets(worker: str) -> List[str]:
    if worker == "all":
        return list(WORKERS)
    if worker not in WORKERS:
        raise ValueError(f"worker inválido: {worker} (use {', '.join(WORKERS)} ou 'all')")
    return [worker]


def pause(worker: str, by: str = "mcp") -> Dict[str, Any]:
    data = load()
    ts = _now()
    for w in _targets(worker):
        data[w]["enabled"] = False
        data[w]["paused_at"] = ts
        data[w]["paused_by"] = by
    save(data)
    return data


def resume(worker: str) -> Dict[str, Any]:
    data = load()
    for w in _targets(worker):
        data[w]["enabled"] = True
        data[w]["paused_at"] = None
        data[w]["paused_by"] = None
    save(data)
    return data


def set_config(worker: str, **cfg: Any) -> Dict[str, Any]:
    """Grava overrides de config (só chaves válidas do worker; None é ignorado)."""
    if worker not in WORKERS:
        raise ValueError(f"worker inválido: {worker}")
    valid = _CONFIG_KEYS.get(worker, ())
    data = load()
    for k, v in cfg.items():
        if k in valid and v is not None:
            data[worker][k] = v
    save(data)
    return data
