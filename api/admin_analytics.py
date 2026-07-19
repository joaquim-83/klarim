"""KL-83 — Analytics admin redesenhado (Prompt 1). Módulo dedicado: 8 endpoints admin-only
sobre `site_events` (+ users/alert_log/email_log/targets). Não toca no analytics antigo.

Arquitetura testável: as **agregações brutas** (SQL) vivem em `discovery/store.py` (`aa_*`);
a **derivação** (validação de período, %, sparkline, conversão inter-etapa, paginação,
normalização de jornada) vive aqui como funções PURAS, unit-testadas offline. Os endpoints
orquestram store + derivação + cache (5 min) + rate limit (30/min/IP). Auth: prefixo `/admin`
já é protegido pelo middleware admin (JWT). Datas sempre parametrizadas (nunca interpolação).
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, HTTPException, Query, Request

from discovery.store import get_target_store

router = APIRouter(prefix="/admin/analytics")

_MAX_DAYS = 90
_CACHE_TTL = 300  # 5 min
_RL_MAX, _RL_WINDOW = 30, 60  # 30 req/min por IP
_rl_bucket: dict = {}
_FIXED_DAYS = {"today": 1, "7d": 7, "30d": 30, "90d": 90}


# --------------------------------------------------------------------------- #
# Período — resolução + validação (puro)
# --------------------------------------------------------------------------- #

def _now() -> datetime:
    return datetime.now(timezone.utc)


def resolve_period(period: str, start: Optional[str], end: Optional[str],
                   now: Optional[datetime] = None) -> Dict[str, Any]:
    """Resolve o período em bounds [start,end) + o período anterior de mesmo tamanho.
    Aceita `today`/`7d`/`30d`/`90d` ou `custom` (start/end ISO date, ≤90 dias, sem futuro).
    Levanta HTTPException(422) em entrada inválida. Retorna dict com datetimes + `days`."""
    now = now or _now()
    if period == "custom":
        if not start or not end:
            raise HTTPException(422, "Período custom exige start e end (ISO date).")
        try:
            s = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
            e = datetime.fromisoformat(end).replace(tzinfo=timezone.utc)
        except ValueError:
            raise HTTPException(422, "Datas inválidas (use YYYY-MM-DD).")
        # normaliza para o dia inteiro [s 00:00, e+1 00:00)
        s = s.replace(hour=0, minute=0, second=0, microsecond=0)
        e = e.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        if e <= s:
            raise HTTPException(422, "end deve ser >= start.")
        if s > now:
            raise HTTPException(422, "Período no futuro não é permitido.")
        days = (e - s).days
        if days > _MAX_DAYS:
            raise HTTPException(422, f"Período máximo é {_MAX_DAYS} dias.")
    elif period in _FIXED_DAYS:
        days = _FIXED_DAYS[period]
        if period == "today":
            s = now.replace(hour=0, minute=0, second=0, microsecond=0)
            e = s + timedelta(days=1)
        else:
            e = now
            s = e - timedelta(days=days)
    else:
        raise HTTPException(422, "Período inválido (today|7d|30d|90d|custom).")
    prev_e, prev_s = s, s - (e - s)
    return {"start": s, "end": e, "days": days, "prev_start": prev_s, "prev_end": prev_e}


def _period_key(period: str, start: Optional[str], end: Optional[str]) -> str:
    raw = f"{period}|{start or ''}|{end or ''}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def day_list(start: datetime, days: int) -> List[str]:
    """Lista de datas (YYYY-MM-DD) a partir de `start`, `days` dias."""
    d0 = start.date()
    return [(d0 + timedelta(days=i)).isoformat() for i in range(days)]


def pct_change(value: float, previous: float) -> Optional[float]:
    """Variação % vs anterior. None se previous == 0 (sem base de comparação)."""
    if not previous:
        return None
    return round((value - previous) / previous * 100, 1)


def _sparkline(daily: Dict[str, int], days: List[str]) -> List[int]:
    return [int(daily.get(d, 0)) for d in days]


def _ratio_sparkline(num: Dict[str, int], den: Dict[str, int], days: List[str],
                     scale: float = 1.0, nd: int = 1) -> List[float]:
    out = []
    for d in days:
        dv = den.get(d, 0)
        out.append(round((num.get(d, 0) / dv) * scale, nd) if dv else 0)
    return out


# --------------------------------------------------------------------------- #
# Assembly das métricas / funil / páginas / jornadas (puro)
# --------------------------------------------------------------------------- #

def assemble_metrics(cur: Dict[str, Any], prev: Dict[str, Any], days: List[str]) -> Dict[str, Any]:
    """Monta os 6 KPIs (value/previous/change_pct/sparkline) a partir das agregações brutas."""
    def m(k):
        return cur[k]["total"], prev[k]["total"], cur[k]["daily"]

    vv, vp, vd = m("visitors")
    sv, sp, _sd = m("scans")
    av, ap, ad = m("accounts")
    pv, pp, _pd = m("pageviews")
    alv, alp, _ald = m("alerts_sent")
    clv, clp, _cld = m("alert_clicks")

    def kpi(value, previous, spark):
        return {"value": value, "previous": previous,
                "change_pct": pct_change(value, previous), "sparkline": spark}

    conv = round(av / vv * 100, 1) if vv else 0
    conv_prev = round(ap / vp * 100, 1) if vp else 0
    pps = round(pv / vv, 2) if vv else 0
    pps_prev = round(pp / vp, 2) if vp else 0
    acr = round(clv / alv * 100, 1) if alv else 0
    acr_prev = round(clp / alp * 100, 1) if alp else 0

    return {
        "unique_visitors": kpi(vv, vp, _sparkline(vd, days)),
        "scans_manual": kpi(sv, sp, _sparkline(cur["scans"]["daily"], days)),
        "accounts_created": kpi(av, ap, _sparkline(ad, days)),
        "conversion_rate": kpi(conv, conv_prev,
                               _ratio_sparkline(ad, vd, days, 100.0)),
        "pageviews_per_session": kpi(pps, pps_prev,
                                     _ratio_sparkline(cur["pageviews"]["daily"], vd, days, 1.0, 2)),
        "alert_click_rate": kpi(acr, acr_prev,
                                _ratio_sparkline(cur["alert_clicks"]["daily"],
                                                 cur["alerts_sent"]["daily"], days, 100.0)),
    }


_FUNNEL_LABELS = [
    ("emails_sent", "Emails enviados"), ("clicks", "Cliques"),
    ("result_viewed", "Resultado visto"), ("scan_started", "Scan iniciado"),
    ("account_created", "Conta criada"), ("payment_created", "PIX gerado"),
    ("payment_completed", "Pagamento confirmado"),
]


def assemble_funnel(raw: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Ordena as etapas + calcula `conversion_from_previous` e marca o gargalo (menor taxa)."""
    stages = []
    prev_total = None
    for name, label in _FUNNEL_LABELS:
        s = raw.get(name) or {"total": 0, "by_campaign": {}}
        conv = None
        if prev_total is not None:
            conv = round(s["total"] / prev_total * 100, 1) if prev_total else 0
        stages.append({"name": name, "label": label, "total": s["total"],
                       "by_campaign": s["by_campaign"], "conversion_from_previous": conv,
                       "bottleneck": False})
        prev_total = s["total"]
    # gargalo = menor conversion_from_previous (ignora a 1ª etapa/None e etapas sem entrada)
    convs = [(i, s["conversion_from_previous"]) for i, s in enumerate(stages)
             if s["conversion_from_previous"] is not None]
    if convs:
        idx = min(convs, key=lambda x: x[1])[0]
        stages[idx]["bottleneck"] = True
    return stages


_PAGE_GROUPS = [
    ("Perfis públicos", "/site/"), ("Páginas de setor", "/setor/"),
    ("Scans", "/scan"), ("Cadastro/Login", "/cadastrar"),
]


def _page_group(path: str) -> str:
    p = path or ""
    for label, prefix in _PAGE_GROUPS:
        if p.startswith(prefix) or p == prefix:
            return label
    if p.startswith("/entrar") or p.startswith("/dashboard"):
        return "Cadastro/Login"
    return "Outras"


def normalize_path(path: str) -> str:
    """/site/<x> → /site/{domain}; /setor/<x> → /setor/{slug}; senão inalterado."""
    p = (path or "").split("?")[0]
    if p.startswith("/site/"):
        return "/site/{domain}"
    if p.startswith("/setor/"):
        return "/setor/{slug}"
    return p or "/"


def assemble_journeys(sessions: List[List[dict]], limit: int = 10) -> List[Dict[str, Any]]:
    """Agrupa sessões por sequência de page_views normalizada (2-4 passos). Sessão com UTM
    alerta que começa em /site/ inicia com 'alerta'; sem conversão → termina com '[saiu]'."""
    from collections import Counter
    seq_counts: Counter = Counter()
    seq_conv: Dict[tuple, int] = {}
    for evs in sessions:
        pages = [normalize_path(e.get("page_url") or "") for e in evs
                 if e.get("event_type") == "page_view" and e.get("page_url")]
        if not pages:
            continue
        camp = next((e.get("utm_campaign") for e in evs if e.get("utm_campaign")), None)
        seq = pages[:4]
        if camp == "alerta" and seq and seq[0] == "/site/{domain}":
            seq = ["alerta"] + seq
        converted = any(e.get("event_type") in ("account_created", "account_created_alert",
                                                "payment_completed") for e in evs)
        if not converted:
            seq = seq + ["[saiu]"]
        key = tuple(seq[:5])
        seq_counts[key] += 1
        seq_conv[key] = seq_conv.get(key, 0) + (1 if converted else 0)
    out = []
    for key, count in seq_counts.most_common(limit):
        conv = seq_conv.get(key, 0)
        out.append({"sequence": list(key), "count": count, "converted": conv,
                    "conversion_rate": round(conv / count * 100, 1) if count else 0})
    return out


def assemble_pages(rows: List[dict], sessions: List[List[dict]],
                   prev_views: Dict[str, int]) -> Dict[str, Any]:
    """Deriva bounce_rate, next_page, conversion e delta_views por página, a partir das
    contagens (rows) + as sequências de sessão (para bounce/next/conversion)."""
    # por sessão: páginas visitadas (ordem), se converteu, e o "próximo passo" por página
    single_page_sessions: Dict[str, int] = {}     # page → nº de sessões onde foi a única pageview
    total_sessions_with: Dict[str, int] = {}       # page → nº de sessões que passaram pela página
    converted_with: Dict[str, int] = {}            # page → nº dessas que converteram
    next_counter: Dict[str, Dict[str, int]] = {}   # page → {next_path → n}
    for evs in sessions:
        pages = [e.get("page_url") for e in evs if e.get("event_type") == "page_view" and e.get("page_url")]
        if not pages:
            continue
        converted = any(e.get("event_type") in ("account_created", "account_created_alert",
                                                "payment_completed") for e in evs)
        uniq = set(pages)
        for i, pg in enumerate(pages):
            total_sessions_with.setdefault(pg, 0)
        for pg in uniq:
            total_sessions_with[pg] = total_sessions_with.get(pg, 0) + 1
            if converted:
                converted_with[pg] = converted_with.get(pg, 0) + 1
        if len(pages) == 1:
            single_page_sessions[pages[0]] = single_page_sessions.get(pages[0], 0) + 1
        for i in range(len(pages) - 1):
            nxt = next_counter.setdefault(pages[i], {})
            nxt[pages[i + 1]] = nxt.get(pages[i + 1], 0) + 1

    pages_out = []
    for r in rows:
        path = r["page_url"]
        sess = int(r.get("sessions") or 0)
        bounce = round(single_page_sessions.get(path, 0) / sess * 100, 1) if sess else 0
        with_total = total_sessions_with.get(path, 0)
        conv = round(converted_with.get(path, 0) / with_total * 100, 1) if with_total else 0
        nxt_map = next_counter.get(path, {})
        next_page = max(nxt_map, key=nxt_map.get) if nxt_map else None
        pages_out.append({
            "path": path, "group": _page_group(path), "views": int(r.get("views") or 0),
            "sessions": sess, "bounce_rate": bounce, "next_page": next_page,
            "conversion": conv, "delta_views": int(r.get("views") or 0) - int(prev_views.get(path, 0)),
        })
    # grupos-resumo
    groups: Dict[str, Dict[str, int]] = {}
    for p in pages_out:
        g = groups.setdefault(p["group"], {"group": p["group"], "total_views": 0, "pages_count": 0})
        g["total_views"] += p["views"]
        g["pages_count"] += 1
    return {"pages": pages_out, "groups": list(groups.values())}


# --------------------------------------------------------------------------- #
# Infra: cache + rate limit (delega a api.main sem import circular)
# --------------------------------------------------------------------------- #

async def _cached(key_suffix: str, period_key: str, builder) -> dict:
    import api.main as _m  # deferido: evita ciclo (main importa este módulo no fim)
    ckey = f"analytics:{key_suffix}:{period_key}"
    cached = await _m._cache_get(ckey)
    if cached is not None:
        return cached
    result = await builder()
    await _m._cache_set(ckey, result, ttl=_CACHE_TTL)
    return result


async def _rate_limit(request: Optional[Request]) -> None:
    if request is None:
        return
    import api.main as _m
    allowed, retry = await _m._redis_allow("admin_analytics", _m._client_ip(request),
                                           _RL_MAX, _RL_WINDOW, _rl_bucket)
    if not allowed:
        raise HTTPException(429, "Muitas requisições. Aguarde um momento.",
                            headers={"Retry-After": str(retry)})


def _period_meta(pr: dict) -> dict:
    return {"start": pr["start"].date().isoformat(),
            "end": (pr["end"] - timedelta(days=1)).date().isoformat(), "days": pr["days"]}


# --------------------------------------------------------------------------- #
# Endpoints (8)
# --------------------------------------------------------------------------- #

@router.get("/metrics")
async def metrics(request: Request, period: str = Query("7d"),
                  start: Optional[str] = None, end: Optional[str] = None) -> dict:
    await _rate_limit(request)
    pr = resolve_period(period, start, end)

    async def build():
        store = get_target_store()
        cur = await store.aa_metrics_raw(pr["start"], pr["end"])
        prev = await store.aa_metrics_raw(pr["prev_start"], pr["prev_end"])
        return {"period": _period_meta(pr),
                "metrics": assemble_metrics(cur, prev, day_list(pr["start"], pr["days"]))}

    return await _cached("metrics", _period_key(period, start, end), build)


@router.get("/trend")
async def trend(request: Request, period: str = Query("30d"),
                metrics: str = Query("visitors,scans,accounts"),
                start: Optional[str] = None, end: Optional[str] = None) -> dict:
    await _rate_limit(request)
    pr = resolve_period(period, start, end)
    wanted = [m.strip() for m in metrics.split(",") if m.strip() in ("visitors", "scans", "accounts")]

    async def build():
        raw = await get_target_store().aa_metrics_raw(pr["start"], pr["end"])
        days = day_list(pr["start"], pr["days"])
        series = {m: _sparkline(raw[m]["daily"], days) for m in wanted}
        return {"dates": days, "series": series}

    return await _cached(f"trend:{','.join(wanted)}", _period_key(period, start, end), build)


@router.get("/funnel")
async def funnel(request: Request, period: str = Query("7d"),
                 start: Optional[str] = None, end: Optional[str] = None) -> dict:
    await _rate_limit(request)
    pr = resolve_period(period, start, end)

    async def build():
        store = get_target_store()
        cur = await store.aa_funnel_raw(pr["start"], pr["end"])
        prev = await store.aa_funnel_raw(pr["prev_start"], pr["prev_end"])
        return {"stages": assemble_funnel(cur),
                "comparison": {"period": "previous", "stages": assemble_funnel(prev)}}

    return await _cached("funnel", _period_key(period, start, end), build)


def _clean_text(s: Optional[str], maxlen: int = 120) -> Optional[str]:
    """Sanitiza input de texto (domain/path/campaign): só chars seguros, sem % SQL."""
    if not s:
        return None
    s = s.strip()[:maxlen]
    return "".join(c for c in s if c.isalnum() or c in "-._/") or None


@router.get("/events")
async def events(request: Request, period: str = Query("7d"), page: int = Query(1, ge=1),
                 limit: int = Query(50, ge=1, le=100), type: Optional[str] = None,
                 domain: Optional[str] = None, campaign: Optional[str] = None,
                 path: Optional[str] = None, start: Optional[str] = None,
                 end: Optional[str] = None) -> dict:
    """Stream de eventos paginado com filtros (AND). NÃO cacheado (paginação/tempo real)."""
    await _rate_limit(request)
    pr = resolve_period(period, start, end)
    types = [t.strip() for t in (type or "").split(",") if t.strip()] or None
    data = await get_target_store().aa_events(
        pr["start"], pr["end"], types, _clean_text(domain), _clean_text(campaign),
        _clean_text(path), (page - 1) * limit, limit)
    total = data["total"]
    return {"events": data["events"], "counters": data["counters"],
            "pagination": {"total": total, "page": page, "limit": limit,
                           "pages": max(1, -(-total // limit))}}


@router.get("/sessions")
async def sessions(request: Request, period: str = Query("7d"), page: int = Query(1, ge=1),
                   limit: int = Query(20, ge=1, le=50), start: Optional[str] = None,
                   end: Optional[str] = None) -> dict:
    """Eventos agrupados por sessão (toggle 'agrupar por sessão'). NÃO cacheado."""
    await _rate_limit(request)
    pr = resolve_period(period, start, end)
    data = await get_target_store().aa_sessions(pr["start"], pr["end"], (page - 1) * limit, limit)
    out = []
    for s in data["sessions"]:
        first, last = s["first_event_at"], s["last_event_at"]
        dur = int((last - first).total_seconds()) if (first and last and hasattr(last, "timestamp")) else 0
        out.append({"session_id": s["session_id"], "event_count": s["event_count"],
                    "duration_seconds": dur, "first_event_at": first, "last_event_at": last,
                    "converted": s["converted"], "campaign": s["campaign"], "events": s["events"]})
    total = data["total"]
    return {"sessions": out, "pagination": {"total": total, "page": page, "limit": limit,
                                            "pages": max(1, -(-total // limit))}}


@router.get("/pages")
async def pages(request: Request, period: str = Query("7d"), sort: str = Query("views"),
                order: str = Query("desc"), search: Optional[str] = None,
                group_by: Optional[str] = None, start: Optional[str] = None,
                end: Optional[str] = None) -> dict:
    await _rate_limit(request)
    pr = resolve_period(period, start, end)

    async def build():
        store = get_target_store()
        rows = await store.aa_pages_raw(pr["start"], pr["end"], _clean_text(search))
        prev_rows = await store.aa_pages_raw(pr["prev_start"], pr["prev_end"], _clean_text(search))
        prev_views = {r["page_url"]: int(r.get("views") or 0) for r in prev_rows}
        sess = await store.aa_journeys_raw(pr["start"], pr["end"])
        result = assemble_pages(rows, sess, prev_views)
        key = sort if sort in ("views", "sessions", "bounce_rate", "conversion", "delta_views") else "views"
        result["pages"].sort(key=lambda p: p.get(key, 0), reverse=(order != "asc"))
        return result

    return await _cached(f"pages:{sort}:{order}:{search or ''}", _period_key(period, start, end), build)


@router.get("/journeys")
async def journeys(request: Request, period: str = Query("7d"),
                   limit: int = Query(10, ge=1, le=30),
                   start: Optional[str] = None, end: Optional[str] = None) -> dict:
    await _rate_limit(request)
    pr = resolve_period(period, start, end)

    async def build():
        sess = await get_target_store().aa_journeys_raw(pr["start"], pr["end"])
        return {"paths": assemble_journeys(sess, limit)}

    return await _cached(f"journeys:{limit}", _period_key(period, start, end), build)


@router.get("/funnel-by-sector")
async def funnel_by_sector(request: Request, period: str = Query("7d"),
                           start: Optional[str] = None, end: Optional[str] = None) -> dict:
    await _rate_limit(request)
    pr = resolve_period(period, start, end)

    async def build():
        rows = await get_target_store().aa_funnel_by_sector(pr["start"], pr["end"])
        sectors = []
        for r in rows:
            clicks, scans, accts = int(r.get("clicks") or 0), int(r.get("scans") or 0), int(r.get("accounts") or 0)
            sectors.append({"sector": r.get("sector") or "outro",
                            "alerts_sent": clicks,  # proxy (cliques de alerta atribuídos ao setor)
                            "clicks": clicks, "scans": scans, "accounts": accts,
                            "click_rate": round(scans / clicks * 100, 1) if clicks else 0})
        return {"sectors": sectors}

    return await _cached("funnel-by-sector", _period_key(period, start, end), build)
