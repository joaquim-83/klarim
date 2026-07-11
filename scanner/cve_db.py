"""Base de CVEs para o check de componentes vulneráveis (KL-33).

Fonte primária: **Retire.js** (`jsrepository.json`) — mapeia biblioteca JS + versão
→ vulnerabilidades conhecidas (CVE/GHSA + severidade). É baixada em **runtime**
(nunca em build), cacheada em arquivo local com TTL de 24h e **fail-open**: qualquer
falha de download/parse degrada para "sem dados" (o check vira INCONCLUSO), nunca
derruba o scan.

Fonte opcional: **NVD/NIST** para produtos que o Retire.js não cobre (WordPress, PHP,
servidor). Desligada por padrão (`NVD_ENABLED=false`) — sem chave/rede confiável no
build, a integração fica pronta mas inerte até ser habilitada em produção.

O módulo é **leve** (só httpx + stdlib + packaging) e **não** importa WeasyPrint nem a
API — pode ser usado pelos workers.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Dict, List, Optional

import httpx

try:
    from packaging.version import Version
except Exception:  # noqa: BLE001 - packaging deve existir; fail-open se não
    Version = None  # type: ignore


# URL oficial do Retire.js (formato v1, ~500KB). Override por env se o repo mover.
RETIREJS_URL = os.environ.get(
    "KLARIM_RETIREJS_URL",
    "https://raw.githubusercontent.com/RetireJS/retire.js/master/repository/jsrepository.json",
)
# Cache local (writable). Padrão em /tmp para não depender de mount RW; monte um
# volume nesse caminho se quiser persistir entre restarts. Override por env.
CACHE_PATH = os.environ.get("KLARIM_CVE_CACHE", "/tmp/klarim_retirejs_cache.json")
CACHE_TTL_SECONDS = int(os.environ.get("KLARIM_CVE_CACHE_TTL", str(24 * 3600)))
DOWNLOAD_TIMEOUT = 20.0

# NVD (opcional, KL-33 Parte 2) — desligado por padrão.
NVD_ENABLED = os.environ.get("NVD_ENABLED", "false").lower() in ("1", "true", "yes")

_USER_AGENT = "KlarimScanner/0.1 (+https://klarim.net; CVE db fetch)"

# Nomes na base Retire.js diferem de alguns fingerprints; aliases best-effort.
_ALIASES = {
    "moment": "moment.js",
    "handlebars": "handlebars.js",
    "underscore": "underscore.js",
    "angular": "angularjs",
}

# Retire.js dá severidade textual (não CVSS). Mapa para a Severity do Klarim e um
# CVSS *representativo* só quando não há CVSS real (NVD) — nunca inventa número
# para exibir: o display usa a label textual; o CVSS real (NVD) tem prioridade.
_SEV_ORDER = {"critical": 4, "high": 3, "medium": 2, "low": 1, "none": 0, "": 0}


# --------------------------------------------------------------------------- #
# Helpers de versão / severidade (puros, testáveis)
# --------------------------------------------------------------------------- #

def _parse(v: str):
    if Version is None:
        return None
    try:
        return Version(str(v).strip())
    except Exception:  # noqa: BLE001 - versão fora do padrão semver -> ignora
        return None


def _vuln_matches(version: str, vuln: dict) -> bool:
    """True se ``version`` cai na faixa vulnerável (``below`` / ``atOrAbove``)."""
    v = _parse(version)
    if v is None:
        return False
    below = vuln.get("below")
    at = vuln.get("atOrAbove")
    if below is None and at is None:
        return False
    if below is not None:
        bv = _parse(below)
        if bv is None or not (v < bv):
            return False
    if at is not None:
        av = _parse(at)
        if av is None or not (v >= av):
            return False
    return True


def max_cvss(cves: List[dict]) -> Optional[float]:
    """Maior CVSS *real* de uma lista de CVEs (``None`` se nenhum tiver CVSS)."""
    vals = [c["cvss"] for c in cves if c.get("cvss") is not None]
    return max(vals) if vals else None


def severity_from_cves(cves: List[dict]) -> str:
    """Severity do Klarim a partir dos CVEs — CVSS real quando houver, senão a label.

    CVSS ≥9 CRITICA · ≥7 ALTA · ≥4 MEDIA · <4 BAIXA. Sem CVSS, usa a maior
    severidade textual do Retire.js (critical/high/medium/low).
    """
    from scanner.checks.base import Severity

    top = max_cvss(cves)
    if top is not None:
        if top >= 9.0:
            return Severity.CRITICA
        if top >= 7.0:
            return Severity.ALTA
        if top >= 4.0:
            return Severity.MEDIA
        return Severity.BAIXA
    # Fallback: maior severidade textual.
    best = max((_SEV_ORDER.get((c.get("severity") or "").lower(), 0) for c in cves),
               default=0)
    return {4: Severity.CRITICA, 3: Severity.ALTA, 2: Severity.MEDIA,
            1: Severity.BAIXA, 0: Severity.BAIXA}[best]


# --------------------------------------------------------------------------- #
# CVEDatabase
# --------------------------------------------------------------------------- #

class CVEDatabase:
    """Download, cache e consulta das bases de CVE (Retire.js + NVD opcional)."""

    def __init__(self, cache_path: str = CACHE_PATH, url: str = RETIREJS_URL,
                 ttl_seconds: int = CACHE_TTL_SECONDS) -> None:
        self.cache_path = cache_path
        self.url = url
        self.ttl = ttl_seconds
        self._data: Optional[Dict[str, dict]] = None
        self._lock = asyncio.Lock()

    # -- carga ------------------------------------------------------------- #

    async def ensure_loaded(self) -> Dict[str, dict]:
        """Carrega a base (cache fresco → download → cache velho). Fail-open ({})."""
        if self._data is not None:
            return self._data
        async with self._lock:
            if self._data is not None:  # dupla checagem sob lock
                return self._data
            data = self._read_cache(require_fresh=True)
            if data is None:
                data = await self._download()
                if data is not None:
                    self._write_cache(data)
                else:
                    data = self._read_cache(require_fresh=False)  # fallback velho
            self._data = data or {}
            return self._data

    def load_from_dict(self, data: Dict[str, dict]) -> None:
        """Injeta a base direto (usado em testes — evita rede)."""
        self._data = data or {}

    def _read_cache(self, require_fresh: bool) -> Optional[Dict[str, dict]]:
        try:
            if require_fresh:
                age = time.time() - os.path.getmtime(self.cache_path)
                if age > self.ttl:
                    return None
            with open(self.cache_path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:  # noqa: BLE001 - sem cache / corrompido / expirado
            return None

    def _write_cache(self, data: Dict[str, dict]) -> None:
        try:
            tmp = f"{self.cache_path}.tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
            os.replace(tmp, self.cache_path)  # escrita atômica
        except Exception:  # noqa: BLE001 - cache é best-effort
            pass

    async def _download(self) -> Optional[Dict[str, dict]]:
        try:
            async with httpx.AsyncClient(
                timeout=DOWNLOAD_TIMEOUT, follow_redirects=True,
                headers={"User-Agent": _USER_AGENT},
            ) as client:
                resp = await client.get(self.url)
            if resp.status_code != 200:
                return None
            data = resp.json()
            return data if isinstance(data, dict) else None
        except Exception:  # noqa: BLE001 - rede/parse -> fail-open
            return None

    # -- consulta ---------------------------------------------------------- #

    def _find_entry(self, library: str) -> Optional[dict]:
        data = self._data or {}
        lib = (library or "").lower()
        if not lib:
            return None
        if lib in data and isinstance(data[lib], dict):
            return data[lib]
        alias = _ALIASES.get(lib)
        if alias and isinstance(data.get(alias), dict):
            return data[alias]
        for key, entry in data.items():
            if not isinstance(entry, dict):
                continue
            if key.lower() == lib:
                return entry
            if (entry.get("npmname", "") or "").lower() == lib:
                return entry
            if (entry.get("bowername", "") or "").lower() == lib:
                return entry
        return None

    def covers(self, library: str) -> bool:
        """True se a base Retire.js conhece essa biblioteca (avaliável)."""
        return self._find_entry(library) is not None

    def lookup_js(self, library: str, version: str) -> List[dict]:
        """CVEs conhecidos de ``library``@``version`` na base Retire.js.

        Retorna ``[{"id","severity","cvss","summary"}]`` (``cvss`` None — Retire.js
        não fornece CVSS). Lista **vazia** = versão sem vulnerabilidade conhecida
        (ou biblioteca não encontrada na base).
        """
        entry = self._find_entry(library)
        if not entry or not version:
            return []
        issues: List[dict] = []
        seen = set()
        for vuln in entry.get("vulnerabilities", []) or []:
            if not _vuln_matches(version, vuln):
                continue
            sev = (vuln.get("severity") or "").lower()
            ident = vuln.get("identifiers", {}) or {}
            summary = ident.get("summary") or ""
            ids = list(ident.get("CVE") or [])
            if not ids and ident.get("githubID"):
                ids = [ident["githubID"]]
            if not ids:
                ids = ["(advisory)"]
            for cid in ids:
                if cid in seen:
                    continue
                seen.add(cid)
                issues.append({"id": cid, "severity": sev, "cvss": None,
                               "summary": summary})
        return issues

    def recommended_upgrade(self, library: str, version: str) -> Optional[str]:
        """Menor versão segura: o maior ``below`` entre as vulnerabilidades que casam."""
        entry = self._find_entry(library)
        if not entry or not version:
            return None
        floor = None
        floor_v = None
        for vuln in entry.get("vulnerabilities", []) or []:
            if not _vuln_matches(version, vuln):
                continue
            b = vuln.get("below")
            bv = _parse(b) if b else None
            if bv is not None and (floor_v is None or bv > floor_v):
                floor_v, floor = bv, b
        return floor

    # -- NVD (opcional, default off) --------------------------------------- #

    async def lookup_nvd(self, product: str, version: str) -> List[dict]:
        """CVEs de um produto genérico (WordPress/PHP/servidor) via NVD.

        Desligado por padrão (``NVD_ENABLED=false``) — retorna ``[]``. Quando
        habilitado, é best-effort e degrada para ``[]`` em qualquer erro/rate limit.
        """
        if not NVD_ENABLED or not product or not version:
            return []
        url = "https://services.nvd.nist.gov/rest/json/cves/2.0"
        params = {"keywordSearch": f"{product} {version}", "resultsPerPage": 20}
        headers = {"User-Agent": _USER_AGENT}
        api_key = os.environ.get("NVD_API_KEY")
        if api_key:
            headers["apiKey"] = api_key
        try:
            async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT, headers=headers) as client:
                resp = await client.get(url, params=params)
            if resp.status_code != 200:
                return []
            out: List[dict] = []
            for item in resp.json().get("vulnerabilities", []):
                cve = item.get("cve", {})
                cid = cve.get("id")
                if not cid:
                    continue
                cvss = _nvd_cvss(cve)
                summary = ""
                for d in cve.get("descriptions", []):
                    if d.get("lang") == "en":
                        summary = d.get("value", "")
                        break
                out.append({"id": cid, "severity": "", "cvss": cvss, "summary": summary})
            return out
        except Exception:  # noqa: BLE001 - fail-open
            return []


def _nvd_cvss(cve: dict) -> Optional[float]:
    metrics = cve.get("metrics", {}) or {}
    for key in ("cvssMetricV31", "cvssMetricV30", "cvssMetricV2"):
        arr = metrics.get(key) or []
        if arr:
            try:
                return float(arr[0]["cvssData"]["baseScore"])
            except Exception:  # noqa: BLE001
                continue
    return None


# Singleton compartilhado pelo check (carrega a base uma vez por processo).
_DB: Optional[CVEDatabase] = None


def get_cve_db() -> CVEDatabase:
    global _DB
    if _DB is None:
        _DB = CVEDatabase()
    return _DB
