"""KL-83 — Analytics admin redesenhado (Prompt 1). Módulo dedicado: 8 endpoints admin-only
sobre `site_events` (+ users/alert_log/email_log/targets). Não toca no analytics antigo.

Arquitetura testável: as **agregações brutas** (SQL) vivem em `discovery/store.py` (`aa_*`);
a **derivação** (validação de período, %, sparkline, conversão inter-etapa, paginação,
normalização de jornada) vive aqui como funções PURAS, unit-testadas offline. Os endpoints
orquestram store + derivação + cache (5 min) + rate limit (30/min/IP). Auth: prefixo `/admin`
já é protegido pelo middleware admin (JWT). Datas sempre parametrizadas (nunca interpolação).
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from discovery.store import get_target_store

router = APIRouter(prefix="/admin/analytics")

_MAX_DAYS = 90
_CACHE_TTL = 300  # 5 min
_RL_MAX, _RL_WINDOW = 30, 60  # 30 req/min por IP
_rl_bucket: dict = {}
_FIXED_DAYS = {"today": 1, "7d": 7, "30d": 30, "90d": 90}
_EXPORT_LIMIT = 10000  # KL-64: teto do CSV (streaming); acima disso marca truncado


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
    # KL-64 fix: pageviews/sessão usa como denominador as sessões COM page_view (não todas as
    # sessões). Antes `pv/vv` dava < 1 quando havia sessões só-ação (profile_view/scan sem page_view).
    pvs_v, pvs_p, pvs_d = m("pageview_sessions")

    def kpi(value, previous, spark):
        return {"value": value, "previous": previous,
                "change_pct": pct_change(value, previous), "sparkline": spark}

    conv = round(av / vv * 100, 1) if vv else 0
    conv_prev = round(ap / vp * 100, 1) if vp else 0
    pps = round(pv / pvs_v, 2) if pvs_v else 0
    pps_prev = round(pp / pvs_p, 2) if pvs_p else 0
    acr = round(clv / alv * 100, 1) if alv else 0
    acr_prev = round(clp / alp * 100, 1) if alp else 0

    return {
        "unique_visitors": kpi(vv, vp, _sparkline(vd, days)),
        "scans_manual": kpi(sv, sp, _sparkline(cur["scans"]["daily"], days)),
        "accounts_created": kpi(av, ap, _sparkline(ad, days)),
        "conversion_rate": kpi(conv, conv_prev,
                               _ratio_sparkline(ad, vd, days, 100.0)),
        "pageviews_per_session": kpi(pps, pps_prev,
                                     _ratio_sparkline(cur["pageviews"]["daily"], pvs_d, days, 1.0, 2)),
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

async def _cached(key_suffix: str, period_key: str, builder, ttl: int = _CACHE_TTL) -> dict:
    import api.main as _m  # deferido: evita ciclo (main importa este módulo no fim)
    ckey = f"analytics:{key_suffix}:{period_key}"
    cached = await _m._cache_get(ckey)
    if cached is not None:
        return cached
    result = await builder()
    await _m._cache_set(ckey, result, ttl=ttl)
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

# KL-64: sufixo de cache para separar a visão "só humanos" (default) da "com bots" (debug).
def _bots_key(include_bots: bool) -> str:
    return "bots" if include_bots else "h"


@router.get("/metrics")
async def metrics(request: Request, period: str = Query("7d"),
                  start: Optional[str] = None, end: Optional[str] = None,
                  include_bots: bool = Query(False)) -> dict:
    await _rate_limit(request)
    pr = resolve_period(period, start, end)

    async def build():
        store = get_target_store()
        cur = await store.aa_metrics_raw(pr["start"], pr["end"], include_bots)
        prev = await store.aa_metrics_raw(pr["prev_start"], pr["prev_end"], include_bots)
        return {"period": _period_meta(pr),
                "metrics": assemble_metrics(cur, prev, day_list(pr["start"], pr["days"]))}

    return await _cached(f"metrics:{_bots_key(include_bots)}", _period_key(period, start, end), build)


@router.get("/trend")
async def trend(request: Request, period: str = Query("30d"),
                metrics: str = Query("visitors,scans,accounts"),
                start: Optional[str] = None, end: Optional[str] = None,
                include_bots: bool = Query(False)) -> dict:
    await _rate_limit(request)
    pr = resolve_period(period, start, end)
    wanted = [m.strip() for m in metrics.split(",") if m.strip() in ("visitors", "scans", "accounts")]

    async def build():
        raw = await get_target_store().aa_metrics_raw(pr["start"], pr["end"], include_bots)
        days = day_list(pr["start"], pr["days"])
        series = {m: _sparkline(raw[m]["daily"], days) for m in wanted}
        return {"dates": days, "series": series}

    return await _cached(f"trend:{','.join(wanted)}:{_bots_key(include_bots)}",
                         _period_key(period, start, end), build)


@router.get("/funnel")
async def funnel(request: Request, period: str = Query("7d"),
                 start: Optional[str] = None, end: Optional[str] = None,
                 include_bots: bool = Query(False)) -> dict:
    await _rate_limit(request)
    pr = resolve_period(period, start, end)

    async def build():
        store = get_target_store()
        cur = await store.aa_funnel_raw(pr["start"], pr["end"], include_bots)
        prev = await store.aa_funnel_raw(pr["prev_start"], pr["prev_end"], include_bots)
        return {"stages": assemble_funnel(cur),
                "comparison": {"period": "previous", "stages": assemble_funnel(prev)}}

    return await _cached(f"funnel:{_bots_key(include_bots)}", _period_key(period, start, end), build)


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
                 end: Optional[str] = None, include_bots: bool = Query(False)) -> dict:
    """Stream de eventos paginado com filtros (AND). NÃO cacheado (paginação/tempo real)."""
    await _rate_limit(request)
    pr = resolve_period(period, start, end)
    types = [t.strip() for t in (type or "").split(",") if t.strip()] or None
    data = await get_target_store().aa_events(
        pr["start"], pr["end"], types, _clean_text(domain), _clean_text(campaign),
        _clean_text(path), (page - 1) * limit, limit, include_bots)
    total = data["total"]
    return {"events": data["events"], "counters": data["counters"],
            "pagination": {"total": total, "page": page, "limit": limit,
                           "pages": max(1, -(-total // limit))}}


def _export_domain(target_url: Optional[str], page_url: Optional[str]) -> str:
    """Domínio do site para o CSV: de target_url (https://dom) ou do path /site/{dom}."""
    for u in (target_url, page_url):
        if not u:
            continue
        m = re.search(r"https?://([^/]+)", u) or re.search(r"/site/([^/?#]+)", u)
        if m:
            return m.group(1).lower().replace("www.", "")
    return ""


def _csv_safe(v: str) -> str:
    """Anti CSV-injection (Excel/Sheets executam células que começam com = + - @)."""
    s = "" if v is None else str(v)
    return ("'" + s) if s[:1] in ("=", "+", "-", "@") else s


@router.get("/events/export")
async def events_export(request: Request, period: str = Query("7d"), type: Optional[str] = None,
                        domain: Optional[str] = None, campaign: Optional[str] = None,
                        path: Optional[str] = None, start: Optional[str] = None,
                        end: Optional[str] = None, include_bots: bool = Query(False)
                        ) -> StreamingResponse:
    """KL-64 — export CSV dos eventos (server-side, streaming). Mesmos filtros da aba Eventos +
    `is_human` (default só humanos). Teto de 10.000 registros (header `X-Truncated: true` +
    linha final de aviso). Admin-only (prefixo `/admin` → middleware JWT)."""
    await _rate_limit(request)
    pr = resolve_period(period, start, end)
    types = [t.strip() for t in (type or "").split(",") if t.strip()] or None
    rows = await get_target_store().aa_events_export(
        pr["start"], pr["end"], types, _clean_text(domain), _clean_text(campaign),
        _clean_text(path), include_bots, limit=_EXPORT_LIMIT)
    truncated = len(rows) > _EXPORT_LIMIT
    rows = rows[:_EXPORT_LIMIT]

    def _generate():
        buf = io.StringIO()
        writer = csv.writer(buf)

        def _flush() -> str:
            v = buf.getvalue()
            buf.seek(0)
            buf.truncate(0)
            return v

        writer.writerow(["timestamp", "event_type", "page", "domain", "campaign",
                         "session_id", "is_human", "referrer"])
        yield _flush()
        for r in rows:
            created_at, etype, page_url, target_url, camp, sid, is_human, referrer = r
            writer.writerow([
                created_at.isoformat() if created_at else "",
                _csv_safe(etype), _csv_safe(page_url), _export_domain(target_url, page_url),
                _csv_safe(camp), _csv_safe(sid),
                "" if is_human is None else ("true" if is_human else "false"),
                _csv_safe(referrer)])
            yield _flush()
        if truncated:
            yield f"# Exportacao limitada a {_EXPORT_LIMIT} registros. Refine os filtros.\n"

    fname = f"klarim-events-{_now().date().isoformat()}.csv"
    headers = {"Content-Disposition": f'attachment; filename="{fname}"'}
    if truncated:
        headers["X-Truncated"] = "true"
    return StreamingResponse(_generate(), media_type="text/csv; charset=utf-8", headers=headers)


@router.get("/sessions")
async def sessions(request: Request, period: str = Query("7d"), page: int = Query(1, ge=1),
                   limit: int = Query(20, ge=1, le=50), start: Optional[str] = None,
                   end: Optional[str] = None, include_bots: bool = Query(False)) -> dict:
    """Eventos agrupados por sessão (toggle 'agrupar por sessão'). NÃO cacheado."""
    await _rate_limit(request)
    pr = resolve_period(period, start, end)
    data = await get_target_store().aa_sessions(pr["start"], pr["end"], (page - 1) * limit, limit,
                                                include_bots)
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
                end: Optional[str] = None, include_bots: bool = Query(False)) -> dict:
    await _rate_limit(request)
    pr = resolve_period(period, start, end)

    async def build():
        store = get_target_store()
        rows = await store.aa_pages_raw(pr["start"], pr["end"], _clean_text(search), include_bots=include_bots)
        prev_rows = await store.aa_pages_raw(pr["prev_start"], pr["prev_end"], _clean_text(search), include_bots=include_bots)
        prev_views = {r["page_url"]: int(r.get("views") or 0) for r in prev_rows}
        sess = await store.aa_journeys_raw(pr["start"], pr["end"], include_bots=include_bots)
        result = assemble_pages(rows, sess, prev_views)
        key = sort if sort in ("views", "sessions", "bounce_rate", "conversion", "delta_views") else "views"
        result["pages"].sort(key=lambda p: p.get(key, 0), reverse=(order != "asc"))
        return result

    return await _cached(f"pages:{sort}:{order}:{search or ''}:{_bots_key(include_bots)}",
                         _period_key(period, start, end), build)


@router.get("/journeys")
async def journeys(request: Request, period: str = Query("7d"),
                   limit: int = Query(10, ge=1, le=30),
                   start: Optional[str] = None, end: Optional[str] = None,
                   include_bots: bool = Query(False)) -> dict:
    await _rate_limit(request)
    pr = resolve_period(period, start, end)

    async def build():
        sess = await get_target_store().aa_journeys_raw(pr["start"], pr["end"], include_bots=include_bots)
        return {"paths": assemble_journeys(sess, limit)}

    return await _cached(f"journeys:{limit}:{_bots_key(include_bots)}",
                         _period_key(period, start, end), build)


@router.get("/alert-quality")
async def alert_quality(request: Request, period: str = Query("7d"),
                        start: Optional[str] = None, end: Optional[str] = None) -> dict:
    """KL-85 — qualidade do lead scoring: distribuição do score, quanto seria filtrado,
    médias, alertas enviados no período. `click_rate` por faixa e `top_disqualify_reasons`
    exigem log por-envio (não no modelo da Parte 1) → omitidos/nulos por honestidade."""
    await _rate_limit(request)
    pr = resolve_period(period, start, end)

    async def build():
        store = get_target_store()
        dist = await store.alert_quality_stats()
        sent = await store.alert_quality_sent_stats(pr["start"], pr["end"])
        d = dist["distribution"]
        high = d["[40,60)"] + d["[60,80)"] + d["[80,200)"]
        filtered = dist["low"] + dist["disqualified"]
        total_eval = dist["total_scored"]
        return {
            "period": _period_meta(pr),
            "total_evaluated": total_eval,
            "total_sent": sent["total_sent"],
            "total_filtered": filtered,
            "filter_rate": round(filtered / total_eval * 100, 1) if total_eval else 0,
            "avg_score_sent": sent["avg_score_sent"],
            "by_score_range": {
                "high_quality": {"range": ">=40", "count": high, "click_rate": None},
                "medium_quality": {"range": "20-39", "count": d["[20,40)"], "click_rate": None},
                "filtered": {"range": "<20", "count": filtered, "click_rate": None},
            },
            "distribution": d, "qualified": dist["qualified"],
            "disqualified": dist["disqualified"], "avg_score_all": dist["avg_score"],
        }

    return await _cached("alert-quality", _period_key(period, start, end), build)


@router.get("/funnel-by-sector")
async def funnel_by_sector(request: Request, period: str = Query("7d"),
                           start: Optional[str] = None, end: Optional[str] = None,
                           include_bots: bool = Query(False)) -> dict:
    await _rate_limit(request)
    pr = resolve_period(period, start, end)

    async def build():
        rows = await get_target_store().aa_funnel_by_sector(pr["start"], pr["end"], include_bots=include_bots)
        sectors = []
        for r in rows:
            clicks, scans, accts = int(r.get("clicks") or 0), int(r.get("scans") or 0), int(r.get("accounts") or 0)
            sectors.append({"sector": r.get("sector") or "outro",
                            "alerts_sent": clicks,  # proxy (cliques de alerta atribuídos ao setor)
                            "clicks": clicks, "scans": scans, "accounts": accts,
                            "click_rate": round(scans / clicks * 100, 1) if clicks else 0})
        return {"sectors": sectors}

    return await _cached(f"funnel-by-sector:{_bots_key(include_bots)}",
                         _period_key(period, start, end), build)


# --------------------------------------------------------------------------- #
# KL-92 — analytics server-side (access_log): server-metrics / ip-behavior / ip-detail.
# As agregações brutas vivem no store (`al_*`); a derivação (hourly, mascaramento LGPD)
# é PURA aqui. Auth: prefixo /admin → middleware JWT. IP mascarado em TODO response da API.
# --------------------------------------------------------------------------- #

def assemble_server_metrics(raw: Dict[str, Any], meta: Dict[str, Any]) -> Dict[str, Any]:
    """Monta o payload de server-metrics: expande `hourly` (dict h→n) em uma lista densa
    de 24 horas (0..23) e repassa os agregados. Puro."""
    hourly = raw.get("hourly") or {}
    hourly_distribution = [{"hour": h, "count": int(hourly.get(h, hourly.get(str(h), 0)) or 0)}
                           for h in range(24)]
    return {
        "period": meta,
        "visitors_br": raw.get("visitors_br", 0),
        "visitors_total": raw.get("visitors_total", 0),
        "bots_filtered": raw.get("bots_filtered", 0),
        "scans": raw.get("scans", 0),
        "accounts": raw.get("accounts", 0),
        "pdfs": raw.get("pdfs", 0),
        "alert_clicks_br": raw.get("alert_clicks_br", 0),
        "profiles_viewed_br": raw.get("profiles_viewed_br", 0),
        "unique_domains_queried": raw.get("unique_domains_queried", 0),
        "top_countries": raw.get("top_countries", []),
        "top_endpoints": raw.get("top_endpoints", []),
        "hourly_distribution": hourly_distribution,
    }


def assemble_ip_behavior(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Mascara os IPs (LGPD: 1º octeto) dos tops de multi-site/recorrentes. O IP completo
    NUNCA sai da API por aqui. Puro (recebe `mask` injetável para testar sem I/O)."""
    from api.access_log_middleware import mask_ip
    multi = [{"ip_masked": mask_ip(r.get("ip"), 1), "country": r.get("country"),
              "sites": r.get("sites", 0), "domains": r.get("domains", [])}
             for r in raw.get("top_multi_site_ips", [])]
    ret = [{"ip_masked": mask_ip(r.get("ip"), 1), "country": r.get("country"),
            "days_active": r.get("days_active", 0),
            "total_requests": r.get("total_requests", 0)}
           for r in raw.get("top_returning_ips", [])]
    return {
        "multi_site_visitors": raw.get("multi_site_visitors", 0),
        "returning_visitors": raw.get("returning_visitors", 0),
        "avg_sites_per_visitor": raw.get("avg_sites_per_visitor", 0.0),
        "top_multi_site_ips": multi,
        "top_returning_ips": ret,
    }


# --------------------------------------------------------------------------- #
# KL-92 Prompt 2 — derivações puras de comportamento (funil/série/jornada/retenção/heatmap)
# --------------------------------------------------------------------------- #

def _rate(num: int, den: int) -> Optional[float]:
    """Taxa de conversão inter-etapa em %. None se o denominador é 0 (sem base)."""
    return round(num / den * 100, 1) if den else None


def assemble_server_funnel(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Funil server-side + taxas de conversão inter-etapa (puro). Divisão por zero → None."""
    vb = raw.get("visitors_br", 0)
    vp = raw.get("viewed_profile", 0)
    ss = raw.get("started_scan", 0)
    cs = raw.get("completed_scan", 0)
    ca = raw.get("created_account", 0)
    pdf = raw.get("downloaded_pdf", 0)
    return {
        "visitors_br": vb, "viewed_profile": vp, "started_scan": ss,
        "completed_scan": cs, "created_account": ca, "downloaded_pdf": pdf,
        "conversion_rates": {
            "visit_to_profile": _rate(vp, vb),
            "profile_to_scan": _rate(ss, vp),
            "scan_to_account": _rate(ca, cs),
            "account_to_pdf": _rate(pdf, ca),
            "overall": _rate(ca, vb),
        },
    }


def assemble_daily_series(rows: List[dict], days: List[str]) -> Dict[str, Any]:
    """Densifica a série diária (dias sem dado → 0) sobre a lista de datas do período. Puro."""
    by_day = {r["day"]: r for r in rows}
    zero = {"visitors_br": 0, "scans": 0, "accounts": 0}
    picked = [by_day.get(d, zero) for d in days]
    return {
        "dates": list(days),
        "visitors_br": [int(p.get("visitors_br", 0)) for p in picked],
        "scans": [int(p.get("scans", 0)) for p in picked],
        "accounts": [int(p.get("accounts", 0)) for p in picked],
    }


def assemble_retention(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Retenção D1/D3/D7: {returned, total, pct} por janela (puro)."""
    total = int(raw.get("total", 0) or 0)

    def one(k):
        got = int(raw.get(k, 0) or 0)
        return {"returned": got, "total": total,
                "pct": round(got / total * 100, 1) if total else 0.0}

    return {"day_1": one("day_1"), "day_3": one("day_3"), "day_7": one("day_7")}


_JOURNEY_MAX = 20              # nº de jornadas detalhadas no payload
_RETURN_MINUTES = 60 * 24      # atividade > 1 dia após o signup = "voltou"


def _is_via_alert(steps: List[dict]) -> bool:
    """A jornada veio de um alerta se tocou o link do alerta (endpoint /alert-access) ou o
    referrer/UTM carrega 'alerta'."""
    for s in steps:
        ep = s.get("endpoint") or ""
        ref = s.get("referrer") or ""
        if "/alert-access" in ep or "alerta" in ref.lower():
            return True
    return False


def assemble_pre_signup_journeys(rows: List[dict], limit: int = _JOURNEY_MAX) -> Dict[str, Any]:
    """Agrupa a atividade por IP em jornadas pré/pós signup + calcula a jornada típica. Puro.
    `rows` já vêm ordenados por (ip, created_at) do store."""
    by_ip: Dict[str, list] = {}
    for r in rows:
        by_ip.setdefault(r["ip_address"], []).append(r)

    journeys = []
    for ip, steps in by_ip.items():
        uid = next((s.get("user_id") for s in steps if s.get("user_id")), None)
        before = [{"endpoint": s["endpoint"], "minutes_before": int(round(s["minutes_relative"]))}
                  for s in steps if s["minutes_relative"] <= 0]
        after = [{"endpoint": s["endpoint"], "minutes_after": int(round(s["minutes_relative"]))}
                 for s in steps if s["minutes_relative"] > 0]
        returned = any(s["minutes_relative"] > _RETURN_MINUTES for s in steps)
        journeys.append({
            "user_id": uid, "steps_before": before[:10], "steps_after": after[:5],
            "returned_within_7d": returned, "via_alert": _is_via_alert(steps),
        })

    typical = _typical_journey(journeys)
    return {"pre_signup_journey": journeys[:limit], "typical_journey": typical}


def _typical_journey(journeys: List[dict]) -> Dict[str, Any]:
    """Jornada típica agregada: 1ª ação mais comum, média de passos, tempo médio até o
    signup, % via alerta vs orgânico. Puro."""
    from collections import Counter
    if not journeys:
        return {"most_common_first_action": None, "avg_steps_before_signup": 0.0,
                "avg_minutes_to_signup": 0.0, "pct_via_alert": 0.0, "pct_via_organic": 0.0}
    firsts = Counter()
    steps_counts, times = [], []
    via_alert = 0
    for j in journeys:
        before = j["steps_before"]
        # exclui o próprio /account/signup (minute 0) da contagem de passos "antes"
        pre = [s for s in before if s["minutes_before"] < 0]
        steps_counts.append(len(pre))
        if pre:
            firsts[pre[0]["endpoint"]] += 1
            times.append(abs(pre[0]["minutes_before"]))
        if j.get("via_alert"):
            via_alert += 1
    n = len(journeys)
    pct_alert = round(via_alert / n * 100, 1)
    return {
        "most_common_first_action": firsts.most_common(1)[0][0] if firsts else None,
        "avg_steps_before_signup": round(sum(steps_counts) / n, 1) if n else 0.0,
        "avg_minutes_to_signup": round(sum(times) / len(times), 1) if times else 0.0,
        "pct_via_alert": pct_alert,
        "pct_via_organic": round(100 - pct_alert, 1),
    }


def assemble_hourly_heatmap(rows: List[dict]) -> Dict[str, Any]:
    """Grade densa 7×24 (dia-da-semana × hora) + o máximo (para escala de cor). Puro.
    `dow` 0=domingo..6=sábado (padrão Postgres EXTRACT(DOW))."""
    grid = [[0] * 24 for _ in range(7)]
    for r in rows:
        dow, hour = int(r.get("dow", 0)), int(r.get("hour", 0))
        if 0 <= dow < 7 and 0 <= hour < 24:
            grid[dow][hour] = int(r.get("count", 0) or 0)
    mx = max((max(row) for row in grid), default=0)
    return {"grid": grid, "max": mx}


def _valid_ip(ip: Optional[str]) -> Optional[str]:
    """Valida/normaliza um IP (v4/v6). None se inválido — evita SQL com lixo."""
    import ipaddress
    if not ip:
        return None
    try:
        return str(ipaddress.ip_address(ip.strip()))
    except ValueError:
        return None


@router.get("/server-metrics")
async def server_metrics(request: Request, period: str = Query("7d"),
                         start: Optional[str] = None, end: Optional[str] = None) -> dict:
    """KL-92 — métricas server-side derivadas do access_log (fonte de verdade vs. tracker
    inflado): visitantes BR/total (IPs únicos, is_bot=false), bots filtrados, scans, contas,
    PDFs, cliques de alerta e perfis vistos (BR), domínios únicos consultados, top países/
    endpoints, distribuição horária. **P2:** + `server_funnel` (funil server-side +
    conversões), `top_domains`, `daily_series` (tendência) e `hourly_heatmap` (7×24).
    Períodos: today|7d|30d|90d. Cache 5 min."""
    await _rate_limit(request)
    pr = resolve_period(period, start, end)

    async def build():
        store = get_target_store()
        raw = await store.al_server_metrics(pr["start"], pr["end"])
        funnel = await store.al_server_funnel(pr["start"], pr["end"])
        top_domains = await store.al_top_domains(pr["start"], pr["end"])
        daily = await store.al_daily_series(pr["start"], pr["end"])
        heatmap = await store.al_hourly_heatmap(pr["start"], pr["end"])
        result = assemble_server_metrics(raw, _period_meta(pr))
        result["server_funnel"] = assemble_server_funnel(funnel)
        result["top_domains"] = top_domains
        result["daily_series"] = assemble_daily_series(daily, day_list(pr["start"], pr["days"]))
        result["hourly_heatmap"] = assemble_hourly_heatmap(heatmap)
        return result

    return await _cached("server-metrics", _period_key(period, start, end), build)


@router.get("/ip-behavior")
async def ip_behavior(request: Request, period: str = Query("7d"),
                      start: Optional[str] = None, end: Optional[str] = None) -> dict:
    """KL-92 — comportamento por IP (só humanos): visitantes multi-site (consultaram >1
    domínio), recorrentes (ativos em >1 dia), média de sites/visitante e os tops. **P2:** +
    `pre_signup_journey` (jornada de -24h a +7d por IP que criou conta), `typical_journey` e
    `post_signup_retention` (D1/D3/D7). IPs MASCARADOS (1º octeto, LGPD). Períodos:
    today|7d|30d|90d. Cache **10 min** (queries de comportamento são mais pesadas)."""
    await _rate_limit(request)
    pr = resolve_period(period, start, end)

    async def build():
        store = get_target_store()
        raw = await store.al_ip_behavior(pr["start"], pr["end"])
        journeys = await store.al_pre_signup_journeys(pr["start"], pr["end"])
        retention = await store.al_retention(pr["start"], pr["end"])
        result = assemble_ip_behavior(raw)
        jr = assemble_pre_signup_journeys(journeys)
        result["pre_signup_journey"] = jr["pre_signup_journey"]
        result["typical_journey"] = jr["typical_journey"]
        result["post_signup_retention"] = assemble_retention(retention)
        return result

    return await _cached("ip-behavior", _period_key(period, start, end), build, ttl=600)


@router.get("/ip-detail")
async def ip_detail(request: Request, ip: str = Query(...)) -> dict:
    """KL-92 — dossiê de UM IP (admin-only): first/last seen, dias ativos, domínios
    consultados, ações, user_id, is_bot e a timeline recente. Aceita o IP COMPLETO como
    parâmetro (nunca exposto publicamente); o IP no response volta MASCARADO (2 octetos)."""
    await _rate_limit(request)
    from api.access_log_middleware import mask_ip
    clean = _valid_ip(ip)
    if not clean:
        raise HTTPException(422, "IP inválido.")

    def _iso(v):
        return v.isoformat() if hasattr(v, "isoformat") else v

    async def build():
        data = await get_target_store().al_ip_detail(clean)
        if not data:
            return {"ip": mask_ip(clean, 2), "found": False, "timeline": []}
        data["ip"] = mask_ip(clean, 2)   # LGPD: mascara o IP no response
        data["found"] = True
        data["first_seen"] = _iso(data.get("first_seen"))
        data["last_seen"] = _iso(data.get("last_seen"))
        for ev in data.get("timeline", []):
            ev["at"] = _iso(ev.get("at"))
        return data

    return await _cached("ip-detail", _period_key("ip", clean, None), build)
