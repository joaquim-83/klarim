"""KL-104 P3 — Visão 360° do alvo (painel de inteligência do admin).

Junta numa única resposta o que hoje exige abrir 4-5 páginas do painel: quem monitora,
onde o alvo está no funil, quem pesquisou o domínio (comportamento por IP, KL-92) e uma
timeline unificada. Arquitetura testável: as **agregações brutas** (SQL) vivem em
`discovery/store.py` (`ti_*`); aqui ficam as **montagens PURAS** (derivação do funil,
classificação de fonte de tráfego, mascaramento de IP, merge/paginação da timeline) e o
**orquestrador** com degradação graciosa (uma seção/tabela ausente vira `null`/`error`,
nunca derruba o response).

Regras de segurança: IPs saem MASCARADOS a /24 (LGPD, KL-92) — o IP completo nunca deixa o
backend; o cross-site expõe só domínios (nunca IPs). Endpoint sob JWT admin (prefixo `/admin`).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

# --------------------------------------------------------------------------- #
# Helpers puros
# --------------------------------------------------------------------------- #

def _iso(dt: Any) -> Optional[str]:
    """datetime → ISO-8601 UTC com sufixo `Z` (naive-UTC no banco). None → None."""
    if dt is None:
        return None
    if isinstance(dt, str):
        return dt
    if getattr(dt, "tzinfo", None) is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.isoformat() + "Z"


def _sortkey(dt: Any) -> datetime:
    """Chave de ordenação uniforme (naive-UTC) — protege o merge da timeline contra
    qualquer datetime aware que escape."""
    if dt is None:
        return datetime.min
    if getattr(dt, "tzinfo", None) is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def parse_cursor(value: Optional[str]) -> Optional[datetime]:
    """`?before=<iso>` → datetime naive-UTC (ou None se ausente/inválido — nunca levanta)."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _default_mask(ip: Optional[str], octets: int = 3) -> str:
    """Fallback caso o `mask` do middleware não seja injetado (mantém `octets` octetos)."""
    if not ip:
        return ""
    if ":" in ip:
        groups = [g for g in ip.split(":")[:2] if g]
        return ":".join(groups) + "::x" if groups else "::x"
    parts = ip.split(".")
    if len(parts) != 4:
        return ip
    octets = max(1, min(octets, 3))
    return ".".join(parts[:octets] + ["x"] * (4 - octets))


# --- Funil ----------------------------------------------------------------- #

_FUNNEL = [
    ("discovered", "discovered_at"),
    ("scanned", "last_scan_at"),
    ("alerted", "first_alert_at"),
    ("account_created", "account_at"),
    ("monitoring", "monitoring_at"),
    ("paid", "paid_at"),
]


def build_funnel(target: Dict[str, Any], flags: Dict[str, Any]) -> Dict[str, Any]:
    """Deriva as 6 etapas do funil (ativa = tem timestamp). `funnel_stage` = etapa mais
    avançada atingida."""
    src = {
        "discovered_at": target.get("discovered_at"),
        "last_scan_at": target.get("last_scan_at"),
        "first_alert_at": (flags or {}).get("first_alert_at"),
        "account_at": (flags or {}).get("account_at"),
        "monitoring_at": (flags or {}).get("monitoring_at"),
        "paid_at": (flags or {}).get("paid_at"),
    }
    stages, last_active = [], None
    for name, key in _FUNNEL:
        at = src.get(key)
        active = at is not None
        if active:
            last_active = name
        stages.append({"stage": name, "at": _iso(at), "active": active})
    return {"funnel_stage": last_active or "discovered", "funnel_stages": stages}


def _lead_class(score: Optional[int]) -> Optional[str]:
    """Classificação do lead pela faixa do alert_quality_score (KL-85: alto ≥60)."""
    if score is None:
        return None
    if score >= 60:
        return "hot"
    if score >= 30:
        return "warm"
    return "cold"


# --- Fontes de tráfego ----------------------------------------------------- #

def classify_traffic_source(referrer: Optional[str]) -> str:
    r = (referrer or "").strip().lower()
    if not r:
        return "direct"
    if "alertas.klarim.net" in r or "aviso.klarim.net" in r:
        return "alert_email"
    if "perfil.klarim.net" in r:
        return "profile_view"
    if "google." in r:
        return "google"
    if "klarim.net" in r:
        return "internal"
    return "other"


def assemble_traffic_sources(rows: Optional[List[Dict[str, Any]]]) -> Optional[Dict[str, int]]:
    if not rows:
        return None
    out: Dict[str, int] = {}
    for row in rows:
        key = classify_traffic_source(row.get("referrer"))
        out[key] = out.get(key, 0) + int(row.get("count") or 0)
    return out or None


# --- Visitantes ------------------------------------------------------------ #

def assemble_visitors(raw: Optional[Dict[str, Any]], cross_rows: Optional[List[Dict[str, Any]]],
                      domain_ids: Optional[Dict[str, int]], mask: Callable,
                      days: int = 30, per_ip: int = 5) -> Optional[Dict[str, Any]]:
    """Monta a seção de visitantes: IPs MASCARADOS (/24) + cross-site (só domínios, com
    target_id p/ o DomainLink quando existir). Nunca expõe IP completo."""
    if raw is None:
        return None
    domain_ids = domain_ids or {}
    cross: Dict[str, List[Dict[str, Any]]] = {}
    for r in (cross_rows or []):
        ip, dom = r.get("ip"), r.get("domain_queried")
        if not ip or not dom:
            continue
        bucket = cross.setdefault(ip, [])
        if len(bucket) < per_ip and dom not in [c["domain"] for c in bucket]:
            bucket.append({"domain": dom, "target_id": domain_ids.get(dom)})
    top = []
    for r in raw.get("top_ips", []):
        ip = r.get("ip")
        top.append({
            "ip_masked": mask(ip, 3),
            "queries": int(r.get("queries") or 0),
            "first_seen": _iso(r.get("first_seen")),
            "last_seen": _iso(r.get("last_seen")),
            "country": r.get("country"),
            "other_domains_queried": cross.get(ip, []),
        })
    return {
        "total_queries": int(raw.get("total_queries") or 0),
        "unique_ips": int(raw.get("unique_ips") or 0),
        "period": f"last_{days}_days",
        "top_ips": top,
    }


# --- Timeline -------------------------------------------------------------- #

def assemble_timeline(scans, alerts, profile_views, status_rows, target, mask,
                      limit: int = 30, include_discovered: bool = True) -> Dict[str, Any]:
    """Une eventos de múltiplas fontes, ordena por data DESC, pagina por cursor. IPs
    mascarados nos eventos de perfil consultado."""
    events: List[Dict[str, Any]] = []
    for s in (scans or []):
        sem = s.get("semaphore") or ""
        events.append({
            "type": "scan_complete", "icon": "🔍", "at": _iso(s.get("at")),
            "description": f"Scan — score {s.get('score')} {sem}".strip(),
            "detail": f"{s.get('pass_count') or 0}✓ / {s.get('fail_count') or 0}✗",
            "link": f"/painel/scans/{s['id']}" if s.get("id") else None,
            "_at": s.get("at"),
        })
    for a in (alerts or []):
        events.append({
            "type": "alert_sent", "icon": "📧", "at": _iso(a.get("at")),
            "description": f"Alerta enviado via {a.get('from_domain') or 'e-mail'}",
            "detail": f"status: {a.get('status') or '-'}", "link": None, "_at": a.get("at"),
        })
    for p in (profile_views or []):
        country = f" ({p.get('country_code')})" if p.get("country_code") else ""
        events.append({
            "type": "profile_viewed", "icon": "👁️", "at": _iso(p.get("at")),
            "description": "Perfil consultado",
            "detail": f"IP {mask(p.get('ip'), 3)}{country}", "link": None, "_at": p.get("at"),
        })
    for st in (status_rows or []):
        http = f"HTTP {st.get('http_code')}" if st.get("http_code") else ""
        events.append({
            "type": "status_detected", "icon": "📊", "at": _iso(st.get("at")),
            "description": f"Status do site: {st.get('status')}",
            "detail": http, "link": None, "_at": st.get("at"),
        })
    if include_discovered and target.get("discovered_at") is not None:
        events.append({
            "type": "discovered", "icon": "📡", "at": _iso(target.get("discovered_at")),
            "description": f"Descoberto via {target.get('source') or 'CT log'}",
            "detail": "", "link": None, "_at": target.get("discovered_at"),
        })
    events.sort(key=lambda e: _sortkey(e.get("_at")), reverse=True)
    has_more = len(events) > limit
    events = events[:limit]
    next_cursor = _iso(events[-1]["_at"]) if events and has_more else None
    for e in events:
        e.pop("_at", None)
    return {"events": events, "has_more": has_more, "next_cursor": next_cursor}


# --------------------------------------------------------------------------- #
# Orquestrador (com degradação graciosa por seção e por sub-query)
# --------------------------------------------------------------------------- #

async def _try(fn, *args, default=None):
    """Executa uma coroutine do store; qualquer erro (ex.: tabela ausente) → default."""
    try:
        return await fn(*args)
    except Exception:  # noqa: BLE001
        return default


async def _safe_section(builder, *args):
    """Uma seção que falha inteira vira `{"error": ...}` (nunca derruba o response)."""
    try:
        return await builder(*args)
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)[:200]}


async def _build_monitoring(store, tid, domain, target):
    monitors_raw = await _try(store.ti_monitors, tid, default=[])
    vigilias_raw = await _try(store.ti_vigilias, domain, default=[])
    ownership_raw = await _try(store.ti_ownership, tid, default=None)
    tech_raw = await _try(store.ti_technician, tid, default=None)
    monitors = [{
        "user_email": m.get("email"), "plan": m.get("plan"),
        "level": m.get("account_level"), "is_owner": m.get("is_owner"),
        "since": _iso(m.get("added_at")),
    } for m in (monitors_raw or [])]
    vigilias = [{
        "type": v.get("tipo"), "status": v.get("last_status"), "enabled": v.get("enabled"),
        "last_check": _iso(v.get("last_check_at")), "next_check": _iso(v.get("next_check_at")),
    } for v in (vigilias_raw or [])]
    if target.get("owner_verified"):
        owner_verified = {
            "verified": True,
            "method": ownership_raw.get("method") if ownership_raw else None,
            "verified_at": _iso(ownership_raw.get("verified_at")) if ownership_raw else None,
        }
    else:
        owner_verified = {"verified": False, "method": None, "verified_at": None}
    technician = None
    if tech_raw:
        technician = {
            "email": tech_raw.get("email"), "status": tech_raw.get("status"),
            "linked_at": _iso(tech_raw.get("linked_at") or tech_raw.get("invited_at")),
        }
    return {"monitors": monitors, "vigilias": vigilias,
            "owner_verified": owner_verified, "technician": technician}


async def _build_funnel(store, tid, url, domain, target):
    flags = await _try(store.ti_funnel_flags, tid, url, domain, default={})
    emails_raw = await _try(store.ti_emails, tid, default=[])
    summary = await _try(store.ti_emails_summary, tid, default=None)
    section = build_funnel(target, flags or {})
    section["emails_sent"] = [{
        "type": e.get("email_type"), "sent_at": _iso(e.get("sent_at")),
        "sender_domain": e.get("from_domain"), "status": e.get("status"),
        "email_id": e.get("email_id"),
    } for e in (emails_raw or [])]
    section["emails_summary"] = None
    if summary:
        section["emails_summary"] = {
            "total": summary.get("total"), "by_type": summary.get("by_type"),
            "by_status": summary.get("by_status"), "last_sent_at": _iso(summary.get("last_sent_at")),
        }
    score = target.get("alert_quality_score")
    section["lead_score"] = ({"score": score, "classification": _lead_class(score)}
                             if score is not None else None)
    return section


async def _build_visitors(store, domain, mask):
    raw = await _try(store.ti_visitors, domain, default=None)
    if raw is None:
        return None
    ips = [r["ip"] for r in raw.get("top_ips", []) if r.get("ip")]
    cross_rows = await _try(store.ti_cross_site, ips, domain, default=[])
    traffic_rows = await _try(store.ti_traffic_sources, domain, default=[])
    cross_domains = sorted({r["domain_queried"] for r in (cross_rows or [])
                            if r.get("domain_queried")})
    domain_ids = await _try(store.ti_domain_ids, cross_domains, default={}) if cross_domains else {}
    visitors = assemble_visitors(raw, cross_rows, domain_ids, mask)
    if visitors is not None:
        visitors["traffic_sources"] = assemble_traffic_sources(traffic_rows)
    return visitors


async def _build_timeline(store, tid, domain, target, before, limit, mask):
    scans = await _try(store.ti_tl_scans, tid, before, limit, default=[])
    alerts = await _try(store.ti_tl_alerts, tid, before, limit, default=[])
    pviews = await _try(store.ti_tl_profile_views, domain, before, limit, default=[])
    status = await _try(store.ti_tl_status, tid, before, limit, default=[])
    return assemble_timeline(scans, alerts, pviews, status, target, mask,
                             limit=limit, include_discovered=(before is None))


async def build_intelligence(store, target: Dict[str, Any], before=None,
                             limit: int = 30, mask: Optional[Callable] = None) -> Dict[str, Any]:
    """Orquestra as 4 seções. Cada uma é isolada — falha vira `{"error": ...}`."""
    mask = mask or _default_mask
    tid = target["id"]
    domain = target.get("domain") or ""
    url = target.get("url") or (f"https://{domain}" if domain else "")
    return {
        "monitoring": await _safe_section(_build_monitoring, store, tid, domain, target),
        "funnel": await _safe_section(_build_funnel, store, tid, url, domain, target),
        "visitors": await _safe_section(_build_visitors, store, domain, mask),
        "timeline": await _safe_section(_build_timeline, store, tid, domain, target, before, limit, mask),
    }
