"""Poller de CT logs — fonte primária do Discovery Worker (KL-15).

Lê os CT logs públicos **direto** (RFC 6962: `get-sth` + `get-entries`), sem
depender de agregador de terceiros (o Certstream público da calidog está morto:
conecta e não envia nada; e o crt.sh é instável). Descobre os logs "usable" da
lista oficial do Google (auto-adapta à rotação de shards por ano), amostra o topo
de cada log a cada intervalo, extrai os domínios do SAN com `cryptography` (já é
dependência) e acumula os `.com.br` num buffer (set, dedup) que o worker drena.

Roda numa thread daemon (I/O bloqueante com httpx.Client), com a mesma interface
que o worker consome: ``start_listener`` / ``flush_buffer`` / ``get_stats``.
"""

from __future__ import annotations

import base64
import os
import threading
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

import httpx

from cryptography import x509

from scanner.checks.base import registrable_domain

from .ct_client import normalize_domain

DEFAULT_LOG_LIST = "https://www.gstatic.com/ct/log_list/v3/log_list.json"


def subdomain_of(name: str, suffix: str = ".com.br") -> Optional[str]:
    """Nome completo se ``name`` é um SUBdomínio ``.com.br`` (não o registrável), senão
    None (KL-75 P2). ``app.hotel.com.br`` → ``app.hotel.com.br``; ``hotel.com.br`` → None;
    ``mail.hotel.com.br`` → ``mail.hotel.com.br`` (subdomínios de infra CONTAM aqui, ao
    contrário de ``normalize_domain``). Puro/testável."""
    name = (name or "").strip().lower().lstrip("*.")
    if not name or " " in name or not name.endswith(suffix):
        return None
    reg = registrable_domain(name)
    if not reg.endswith(suffix) or name == reg:
        return None
    return name


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def load_current_logs(log_list_url: str, limit: int) -> List[str]:
    """Descobre os CT logs 'usable' cujo intervalo temporal cobre agora."""
    try:
        resp = httpx.get(log_list_url, timeout=20.0)
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        print(f"[ct-poll] falha ao baixar a lista de logs ({exc!r})", flush=True)
        return []
    now = _utcnow()
    urls: List[str] = []
    for operator in data.get("operators", []):
        for log in operator.get("logs", []):
            if "usable" not in (log.get("state") or {}):
                continue
            ti = log.get("temporal_interval") or {}
            try:
                start = datetime.fromisoformat(ti["start_inclusive"].replace("Z", "+00:00"))
                end = datetime.fromisoformat(ti["end_exclusive"].replace("Z", "+00:00"))
            except (KeyError, ValueError, AttributeError):
                continue
            if start <= now < end:
                urls.append(log["url"].rstrip("/") + "/")
    return urls[:limit]


def extract_san_and_issuer(entry: dict) -> tuple:
    """Extrai (domínios SAN+CN, issuer_cn) de uma entrada de get-entries.

    A `leaf_input` é um MerkleTreeLeaf (RFC 6962): version(1) leaf_type(1)
    timestamp(8) entry_type(2). Para x509 (0) o cert vem no próprio leaf; para
    precert (1) o cert completo vem no início do `extra_data`. O issuer (CN) alimenta
    o `cert_issuer` do registro de subdomínio (KL-75 P2)."""
    try:
        leaf = base64.b64decode(entry["leaf_input"])
        entry_type = int.from_bytes(leaf[10:12], "big")
        if entry_type == 0:  # x509_entry
            length = int.from_bytes(leaf[12:15], "big")
            der = leaf[15:15 + length]
        elif entry_type == 1:  # precert_entry — cert completo está no extra_data
            extra = base64.b64decode(entry["extra_data"])
            length = int.from_bytes(extra[0:3], "big")
            der = extra[3:3 + length]
        else:
            return [], None
        cert = x509.load_der_x509_certificate(der)
    except Exception:  # noqa: BLE001 - entrada malformada: ignora
        return [], None

    names: List[str] = []
    try:
        ext = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
        names.extend(ext.value.get_values_for_type(x509.DNSName))
    except x509.ExtensionNotFound:
        pass
    try:
        cn = cert.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)
        names.extend(a.value for a in cn)
    except Exception:  # noqa: BLE001
        pass
    issuer_cn = None
    try:
        ic = cert.issuer.get_attributes_for_oid(x509.NameOID.COMMON_NAME)
        issuer_cn = ic[0].value if ic else None
    except Exception:  # noqa: BLE001
        issuer_cn = None
    return names, issuer_cn


def extract_san_domains(entry: dict) -> List[str]:
    """Domínios (SAN + CN) de uma entrada de get-entries — wrapper de
    :func:`extract_san_and_issuer` (mantém o contrato usado no resto do worker)."""
    return extract_san_and_issuer(entry)[0]


class CTLogPoller:
    """Consome CT logs públicos direto e acumula domínios .com.br em tempo real."""

    def __init__(self) -> None:
        self.suffix = os.environ.get("CT_SUFFIX", ".com.br")
        self.log_list_url = os.environ.get("CT_LOG_LIST_URL", DEFAULT_LOG_LIST)
        self.max_logs = int(os.environ.get("CT_MAX_LOGS", "5"))
        self.batch = int(os.environ.get("CT_POLL_BATCH", "256"))
        self.poll_interval = float(os.environ.get("CT_POLL_INTERVAL_SECONDS", "20"))
        self.max_buffer = int(os.environ.get("CT_MAX_BUFFER", "5000"))
        self.buffer: set[str] = set()
        # KL-75 P2: buffer separado de subdomínios {nome_completo: issuer_cn}. O worker
        # o drena a cada ciclo e registra os que pertencem a domínios na base.
        self.max_subdomain_buffer = int(os.environ.get("CT_MAX_SUBDOMAIN_BUFFER", "5000"))
        self.subdomain_buffer: Dict[str, Optional[str]] = {}
        self.connected: bool = False
        self.last_event_at: Optional[datetime] = None
        self.total_seen: int = 0
        self.total_matched: int = 0
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None

    # --- ciclo de vida ----------------------------------------------------- #

    def start_listener(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="ct-poller", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        logs = load_current_logs(self.log_list_url, self.max_logs)
        if not logs:
            print("[ct-poll] nenhum CT log usable encontrado; worker cai no crt.sh", flush=True)
            return
        print(f"[ct-poll] conectado — amostrando {len(logs)} CT logs: "
              f"{', '.join(u.split('/')[2] for u in logs)}", flush=True)
        client = httpx.Client(timeout=30.0, headers={"User-Agent": "KlarimDiscovery/0.2"})
        while True:
            any_ok = False
            for url in logs:
                try:
                    self._poll_log(client, url)
                    any_ok = True
                except Exception as exc:  # noqa: BLE001 - um log ruim não derruba o poller
                    print(f"[ct-poll] erro em {url.split('/')[2]} ({exc!r})", flush=True)
            self.connected = any_ok
            time.sleep(self.poll_interval)

    def _poll_log(self, client: httpx.Client, url: str) -> None:
        sth = client.get(url + "ct/v1/get-sth").json()
        tree_size = int(sth["tree_size"])
        if tree_size <= 0:
            return
        # Amostra o TOPO do log (certs mais recentes); o buffer (set) deduplica
        # a sobreposição entre polls.
        start = max(0, tree_size - self.batch)
        end = tree_size - 1
        entries = client.get(f"{url}ct/v1/get-entries?start={start}&end={end}").json().get("entries", [])
        for entry in entries:
            self._ingest(entry)
        if entries:
            self.last_event_at = _utcnow()

    def _ingest(self, entry: dict) -> None:
        """Processa uma entrada de CT: extrai SANs, filtra .com.br → buffer de raízes;
        e captura subdomínios .com.br → buffer de subdomínios (KL-75 P2). Rápido e
        in-thread (só ops de string + set/dict sob lock) — não bloqueia o stream."""
        self.total_seen += 1
        names, issuer = extract_san_and_issuer(entry)
        for raw in names:
            reg = normalize_domain(raw, self.suffix)
            if reg is not None:
                self.total_matched += 1
                with self._lock:
                    if len(self.buffer) < self.max_buffer:
                        self.buffer.add(reg)
            # KL-75 P2: subdomínios (inclui infra que normalize_domain descarta). O worker
            # decide depois se o domínio raiz está na base — aqui só acumula candidatos.
            sub = subdomain_of(raw, self.suffix)
            if sub is not None:
                with self._lock:
                    if len(self.subdomain_buffer) < self.max_subdomain_buffer:
                        self.subdomain_buffer.setdefault(sub, issuer)

    # --- consumo ----------------------------------------------------------- #

    def flush_buffer(self) -> List[str]:
        with self._lock:
            domains = list(self.buffer)
            self.buffer.clear()
        return domains

    def flush_subdomains(self) -> Dict[str, Optional[str]]:
        """Drena e limpa o buffer de subdomínios (KL-75 P2): ``{nome_completo: issuer}``."""
        with self._lock:
            subs = dict(self.subdomain_buffer)
            self.subdomain_buffer.clear()
        return subs

    def get_stats(self) -> Dict[str, object]:
        return {
            "connected": self.connected,
            "last_event_at": self.last_event_at.isoformat() if self.last_event_at else None,
            "total_seen": self.total_seen,
            "total_matched": self.total_matched,
            "buffer_size": len(self.buffer),
            "subdomain_buffer_size": len(self.subdomain_buffer),
        }
