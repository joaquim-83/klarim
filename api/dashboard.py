"""KL-90 — agregador do Dashboard v2 (`GET /account/dashboard-summary`).

Uma única chamada devolve TUDO que o dashboard precisa: sites do usuário, o site
selecionado (`?site_id=`, senão o primário), benchmark setorial, riscos em linguagem
de negócio (KL-20) com fix por plataforma, os 48 checks agrupados em 6 categorias,
histórico de score, checklist de ações, plano, monitoramento (vigílias/boletim/selo/
técnico) e perfil comercial.

**Substitui** o payload do KL-86 (o front v2 é reescrito nos próximos prompts do KL-90).

Arquitetura (padrão do projeto): as **agregações brutas** vêm do `store` (SQL); a
**derivação é PURA e testável** (as funções `build_*` abaixo, sem I/O). O orquestrador
`build_dashboard_summary` faz as queries em paralelo (`asyncio.gather`) → < 500ms.

Regra de ouro: `contact_email`/`cnpj`/`whatsapp` NUNCA entram no payload (só lemos os
campos explicitamente listados de `target`/`profile`).
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException

from reporter.risk_messages import RISK_MESSAGES

# --------------------------------------------------------------------------- #
# Constantes
# --------------------------------------------------------------------------- #

# 6 categorias × números de check (cobrem os 48). Ordem fixa de exibição.
CATEGORIES: List[Tuple[str, str, Tuple[int, ...]]] = [
    ("tls", "Transporte & TLS", (1, 2, 3, 4, 41, 42, 43, 44)),
    ("headers", "Headers de segurança", (5, 6, 7, 8, 17, 18, 31, 32, 33, 34, 35, 36)),
    ("supply", "Supply chain", (9, 10, 11, 13, 14, 15, 16, 30)),
    ("dns", "DNS & E-mail", (21, 22, 23, 37, 38, 39, 40)),
    ("content", "Conteúdo", (12, 19, 20, 24, 25, 45, 46, 47, 48)),
    ("osint", "OSINT & Reputação", (26, 27, 28, 29)),
]
_NUM_TO_SLUG = {n: slug for slug, _, nums in CATEGORIES for n in nums}

# Rótulo v2 das features por plano (linguagem do dashboard, não a do KL-44).
PLAN_FEATURES: Dict[str, List[str]] = {
    "free": ["15 checks", "Score público"],
    "pro": ["48 checks", "Relatório PDF", "Vigílias", "Boletim mensal", "Selo"],
    "agency": ["48 checks", "Relatório PDF", "Vigílias avançadas", "Boletim diário",
               "Multi-cliente", "API"],
}

_BULLETIN_PT = {"none": "nenhum", "monthly": "mensal", "weekly": "semanal", "daily": "diário"}
_SCAN_INTERVAL_DAYS = {"free": 30, "pro": 7, "agency": 1}

_SEV_RANK = {"critica": 0, "alta": 1, "media": 2, "baixa": 3}
_STATUS_MAP = {"PASS": "pass", "FAIL": "fail", "INCONCLUSO": "inconclusive"}
_SSL_DAYS_RE = re.compile(r"(\d+)\s*dias?")
_NUM_RE = re.compile(r"(\d+)")

# Fix por plataforma (WordPress/Nginx/Apache) — mapa CANÔNICO por número de check.
# Serve para produção (não depende do seed). Checks fora do mapa → fix_inline = None.
# Para itens de DNS (SPF/DKIM/DMARC/DNSSEC/MTA-STS) a coluna "wordpress" explica que a
# correção é no DNS, não no CMS.
FIX_INLINE: Dict[int, Dict[str, str]] = {
    1: {  # HTTPS
        "wordpress": "Ative o SSL (plugin 'Really Simple SSL') e force HTTPS nas Configurações.",
        "nginx": "server { listen 80; return 301 https://$host$request_uri; }",
        "apache": "RewriteEngine On\nRewriteCond %{HTTPS} off\nRewriteRule ^ https://%{HTTP_HOST}%{REQUEST_URI} [R=301,L]",
    },
    2: {  # HSTS
        "wordpress": "Instale o plugin 'Really Simple SSL' e ative o HSTS nas opções de segurança.",
        "nginx": 'add_header Strict-Transport-Security "max-age=31536000; includeSubDomains; preload" always;',
        "apache": 'Header always set Strict-Transport-Security "max-age=31536000; includeSubDomains; preload"',
    },
    3: {  # SSL
        "wordpress": "Renove o certificado no painel da hospedagem ou peça suporte para reemitir.",
        "nginx": "certbot --nginx -d seudominio.com.br -d www.seudominio.com.br",
        "apache": "certbot --apache -d seudominio.com.br -d www.seudominio.com.br",
    },
    5: {  # CSP
        "wordpress": "Instale o plugin 'Headers Security Advanced' e defina uma Content-Security-Policy.",
        "nginx": "add_header Content-Security-Policy \"default-src 'self'\" always;",
        "apache": "Header always set Content-Security-Policy \"default-src 'self'\"",
    },
    6: {  # X-Frame-Options
        "wordpress": "Instale um plugin de headers de segurança e ative o X-Frame-Options.",
        "nginx": 'add_header X-Frame-Options "SAMEORIGIN" always;',
        "apache": 'Header always set X-Frame-Options "SAMEORIGIN"',
    },
    7: {  # X-Content-Type-Options
        "wordpress": "Ative o 'X-Content-Type-Options: nosniff' via plugin de headers.",
        "nginx": 'add_header X-Content-Type-Options "nosniff" always;',
        "apache": 'Header always set X-Content-Type-Options "nosniff"',
    },
    8: {  # Server header exposto
        "wordpress": "Peça à hospedagem para ocultar a versão do servidor.",
        "nginx": "server_tokens off;",
        "apache": "ServerTokens Prod\nServerSignature Off",
    },
    10: {  # Arquivos sensíveis
        "wordpress": "Bloqueie o acesso a .env, wp-config.php.bak e backups no .htaccess.",
        "nginx": "location ~ /\\.(env|git) { deny all; return 404; }",
        "apache": '<FilesMatch "^\\.(env|git)">\n  Require all denied\n</FilesMatch>',
    },
    11: {  # Directory listing
        "wordpress": "Adicione 'Options -Indexes' no .htaccess da raiz.",
        "nginx": "autoindex off;",
        "apache": "Options -Indexes",
    },
    17: {  # Cookies sem flags
        "wordpress": "Force cookies seguros (plugin de segurança ou HTTPS em todo o site).",
        "nginx": "proxy_cookie_flags ~ secure httponly samesite=lax;",
        "apache": "Header edit Set-Cookie ^(.*)$ $1;HttpOnly;Secure;SameSite=Lax",
    },
    18: {  # CORS permissivo
        "wordpress": "Remova 'Access-Control-Allow-Origin: *' do tema/plugin que o adiciona.",
        "nginx": "add_header Access-Control-Allow-Origin https://seudominio.com.br always;",
        "apache": 'Header always set Access-Control-Allow-Origin "https://seudominio.com.br"',
    },
    21: {  # SPF (DNS)
        "wordpress": "SPF é no DNS, não no WordPress: publique um registro TXT no seu provedor de DNS.",
        "nginx": "SPF é DNS (não Nginx). Publique um TXT: v=spf1 include:_spf.seuprovedor.com ~all",
        "apache": "SPF é DNS (não Apache). Publique um TXT: v=spf1 include:_spf.seuprovedor.com ~all",
    },
    22: {  # DKIM (DNS)
        "wordpress": "DKIM é no provedor de e-mail: ative a assinatura e publique a chave no DNS.",
        "nginx": "DKIM é DNS/e-mail. Gere a chave no provedor e publique o TXT (selector._domainkey).",
        "apache": "DKIM é DNS/e-mail. Gere a chave no provedor e publique o TXT (selector._domainkey).",
    },
    23: {  # DMARC (DNS)
        "wordpress": "DMARC é no DNS: publique um TXT em _dmarc com a política.",
        "nginx": "DMARC é DNS. Publique um TXT em _dmarc: v=DMARC1; p=quarantine; rua=mailto:dmarc@seudominio",
        "apache": "DMARC é DNS. Publique um TXT em _dmarc: v=DMARC1; p=quarantine; rua=mailto:dmarc@seudominio",
    },
    24: {  # Mixed content
        "wordpress": "Instale 'Really Simple SSL' (corrige links http:// para https://).",
        "nginx": "Troque todas as URLs de recursos para https:// no conteúdo do site.",
        "apache": "Troque todas as URLs de recursos para https:// no conteúdo do site.",
    },
    31: {  # Permissions-Policy
        "wordpress": "Adicione o header Permissions-Policy via plugin de headers.",
        "nginx": 'add_header Permissions-Policy "geolocation=(), microphone=(), camera=()" always;',
        "apache": 'Header always set Permissions-Policy "geolocation=(), microphone=(), camera=()"',
    },
    32: {  # COOP
        "wordpress": "Adicione o header Cross-Origin-Opener-Policy via plugin de headers.",
        "nginx": 'add_header Cross-Origin-Opener-Policy "same-origin" always;',
        "apache": 'Header always set Cross-Origin-Opener-Policy "same-origin"',
    },
    33: {  # COEP
        "wordpress": "Adicione o header Cross-Origin-Embedder-Policy via plugin de headers.",
        "nginx": 'add_header Cross-Origin-Embedder-Policy "require-corp" always;',
        "apache": 'Header always set Cross-Origin-Embedder-Policy "require-corp"',
    },
    34: {  # CORP
        "wordpress": "Adicione o header Cross-Origin-Resource-Policy via plugin de headers.",
        "nginx": 'add_header Cross-Origin-Resource-Policy "same-origin" always;',
        "apache": 'Header always set Cross-Origin-Resource-Policy "same-origin"',
    },
    35: {  # Referrer-Policy
        "wordpress": "Adicione o header Referrer-Policy via plugin de headers.",
        "nginx": 'add_header Referrer-Policy "strict-origin-when-cross-origin" always;',
        "apache": 'Header always set Referrer-Policy "strict-origin-when-cross-origin"',
    },
    36: {  # Cache-Control em páginas sensíveis
        "wordpress": "Defina no-store nas páginas de login/checkout via plugin de headers.",
        "nginx": 'location /minha-conta { add_header Cache-Control "no-store" always; }',
        "apache": '<Location "/minha-conta">\n  Header always set Cache-Control "no-store"\n</Location>',
    },
    37: {  # DNSSEC (DNS/registrar)
        "wordpress": "DNSSEC é no registrador/DNS: ative o DNSSEC no painel do seu domínio.",
        "nginx": "DNSSEC é DNS/registrador. Ative no painel do provedor de DNS.",
        "apache": "DNSSEC é DNS/registrador. Ative no painel do provedor de DNS.",
    },
    39: {  # MTA-STS (DNS)
        "wordpress": "MTA-STS é no DNS + um arquivo de política; peça ao provedor de e-mail.",
        "nginx": "Publique o TXT _mta-sts e sirva /.well-known/mta-sts.txt no subdomínio mta-sts.",
        "apache": "Publique o TXT _mta-sts e sirva /.well-known/mta-sts.txt no subdomínio mta-sts.",
    },
    46: {  # Debug mode
        "wordpress": "Defina WP_DEBUG como false no wp-config.php.",
        "nginx": "Desative o modo debug/stack trace da aplicação em produção.",
        "apache": "Desative o modo debug/stack trace da aplicação em produção.",
    },
    48: {  # Password fields sem proteção
        "wordpress": "Sirva o formulário de login por HTTPS (ative o SSL no site inteiro).",
        "nginx": "Sirva as páginas com campo de senha exclusivamente por HTTPS.",
        "apache": "Sirva as páginas com campo de senha exclusivamente por HTTPS.",
    },
}


# --------------------------------------------------------------------------- #
# Helpers puros
# --------------------------------------------------------------------------- #

def _iso(dt: Any) -> Optional[str]:
    return dt.isoformat() if hasattr(dt, "isoformat") else (dt if isinstance(dt, str) else None)


def check_num(check_id: Optional[str]) -> int:
    m = _NUM_RE.search(check_id or "")
    return int(m.group(1)) if m else 0


def short_id(check_id: Optional[str]) -> str:
    return f"check_{check_num(check_id):02d}"


def norm_severity(sev: Optional[str]) -> str:
    return (sev or "").strip().lower() or "baixa"


def norm_status(status: Optional[str]) -> str:
    s = (status or "").strip().upper()
    return _STATUS_MAP.get(s, s.lower())


def _headline(full_id: str, fallback: str = "") -> str:
    return (RISK_MESSAGES.get(full_id) or {}).get("headline") or fallback


def _risk_text(full_id: str) -> Optional[str]:
    return (RISK_MESSAGES.get(full_id) or {}).get("risk")


def _extract_checks(scan: Optional[dict]) -> list:
    """A lista de checks de um scan (checks_json['checks'] ou ['results'])."""
    cj = (scan or {}).get("checks_json") or {}
    if not isinstance(cj, dict):
        return []
    return cj.get("checks") or cj.get("results") or []


def build_categories(checks: list) -> list:
    """As 6 categorias (ordem fixa) com passed/total/status + os checks agrupados.

    status da categoria: 0 falhas → ok · 1-2 → warning · ≥3 → critical.
    """
    buckets: Dict[str, list] = {slug: [] for slug, _, _ in CATEGORIES}
    for c in checks:
        slug = _NUM_TO_SLUG.get(check_num(c.get("check_id")))
        if slug is None:
            continue
        full = c.get("check_id") or ""
        st = norm_status(c.get("status"))
        is_fail = st == "fail"
        buckets[slug].append({
            "id": short_id(full),
            "name": c.get("name") or short_id(full),
            "severity": norm_severity(c.get("severity")),
            "status": st,
            "evidence": (c.get("evidence") or None),
            "risk_message": _risk_text(full) if is_fail else None,
            "fix_inline": FIX_INLINE.get(check_num(full)) if is_fail else None,
        })
    out = []
    for slug, name, _ in CATEGORIES:
        items = buckets[slug]
        passed = sum(1 for c in items if c["status"] == "pass")
        fails = sum(1 for c in items if c["status"] == "fail")
        status = "ok" if fails == 0 else ("warning" if fails <= 2 else "critical")
        out.append({"name": name, "slug": slug, "passed": passed, "total": len(items),
                    "status": status, "checks": items})
    return out


def build_risks(checks: list, limit: int = 12) -> list:
    """Riscos em linguagem de negócio (KL-20) dos checks FAIL, ordenados por severidade
    (crítica → alta → média → baixa), com fix por plataforma. Máx `limit`."""
    fails = [c for c in checks if norm_status(c.get("status")) == "fail"]
    fails.sort(key=lambda c: (_SEV_RANK.get(norm_severity(c.get("severity")), 9),
                              check_num(c.get("check_id"))))
    out = []
    for c in fails[:limit]:
        full = c.get("check_id") or ""
        out.append({
            "check_id": short_id(full),
            "severity": norm_severity(c.get("severity")),
            "title": _headline(full, c.get("name") or short_id(full)),
            "description": _risk_text(full) or (c.get("evidence") or ""),
            "fix_inline": FIX_INLINE.get(check_num(full)),
        })
    return out


def build_score_history(scans: list, limit: int = 12) -> list:
    """[{date: 'YYYY-MM-DD', score}] do mais antigo ao mais recente (só scans com score)."""
    pts = []
    for s in scans:
        if s.get("score") is None:
            continue
        dt = s.get("scanned_at")
        date = dt.date().isoformat() if hasattr(dt, "date") else (str(dt)[:10] if dt else None)
        pts.append({"date": date, "score": s.get("score")})
    pts.reverse()  # list_scans vem do mais novo ao mais antigo
    return pts[-limit:]


def build_trend(history: list) -> Tuple[str, int]:
    """Tendência a partir dos 2 últimos pontos: subindo/caindo/estavel/primeiro."""
    if len(history) < 2:
        return ("primeiro", 0)
    delta = (history[-1]["score"] or 0) - (history[-2]["score"] or 0)
    if delta > 0:
        return ("subindo", delta)
    if delta < 0:
        return ("caindo", delta)
    return ("estavel", 0)


def ssl_days_from_checks(checks: list) -> Optional[int]:
    """Dias até expirar o certificado, lidos da evidência do check de cert/SSL."""
    for c in checks:
        cid = (c.get("check_id") or "").lower()
        if "cert" in cid or "ssl" in cid:
            m = _SSL_DAYS_RE.search(c.get("evidence") or "")
            if m:
                try:
                    return int(m.group(1))
                except ValueError:
                    pass
    return None


def build_checklist(checks: list, profile: Optional[dict], score: Optional[int],
                    user: dict, seal_enabled: bool) -> list:
    """Checklist priorizado (máx 5): confirmar e-mail, corrigir FAIL alta/crítica,
    completar perfil, compartilhar score (≥80), ativar selo."""
    items: List[dict] = []
    if user.get("email_confirmed") is False:
        items.append({"id": "confirm_email", "label": "Confirme seu e-mail",
                      "completed": False, "action": "confirm", "_p": 0})

    fails = [c for c in checks if norm_status(c.get("status")) == "fail"
             and norm_severity(c.get("severity")) in ("critica", "alta")]
    fails.sort(key=lambda c: (_SEV_RANK.get(norm_severity(c.get("severity")), 9),
                              check_num(c.get("check_id"))))
    for c in fails:
        full = c.get("check_id") or ""
        head = _headline(full, c.get("name") or short_id(full))
        p = 1 if norm_severity(c.get("severity")) == "critica" else 2
        items.append({"id": f"fix_{short_id(full)}", "label": f"Corrija: {head}",
                      "completed": False, "check_id": short_id(full), "_p": p})

    company = (profile or {}).get("company_name")
    items.append({"id": "complete_profile", "label": "Complete o perfil",
                  "completed": bool(company), "action": "profile", "_p": 3})
    if score is not None and score >= 80:
        items.append({"id": "share_score", "label": "Compartilhe seu score",
                      "completed": False, "action": "share", "_p": 4})
    if not seal_enabled:
        items.append({"id": "activate_seal", "label": "Ative o selo Klarim",
                      "completed": False, "action": "seal", "_p": 5})

    items.sort(key=lambda x: x["_p"])
    for it in items:
        it.pop("_p", None)
    return items[:5]


def new_user_checklist(user: dict) -> list:
    """Checklist do usuário SEM site (dados reduzidos)."""
    return [
        {"id": "add_site", "label": "Adicione seu site", "completed": False, "action": "add_site"},
        {"id": "confirm_email", "label": "Confirme seu e-mail",
         "completed": user.get("email_confirmed") is True, "action": "confirm"},
    ]


def build_plan(subscription: dict) -> dict:
    """Bloco de plano (nome, status, expiração, dias restantes, features)."""
    plan_id = subscription.get("plan_id") or "free"
    status = subscription.get("status") or "active"
    trial_ends = subscription.get("trial_ends_at")
    expires = trial_ends if status == "trial" else subscription.get("expires_at")
    days = subscription.get("trial_days_left")
    if days is None and expires is not None and hasattr(expires, "date"):
        days = max(0, (expires.date() - datetime.now(timezone.utc).date()).days)
    return {
        "name": subscription.get("plan_name") or "Free",
        "status": status,
        "expires_at": _iso(expires),
        "days_remaining": days,
        "features": PLAN_FEATURES.get(plan_id, PLAN_FEATURES["free"]),
    }


def build_monitoring(vigilias: list, domain: Optional[str], subscription: dict,
                     seal_enabled: bool, technician_linked: bool) -> dict:
    """Resumo do monitoramento: contagem de vigílias por status + boletim/selo/técnico."""
    rel = [v for v in vigilias if (not domain or v.get("site_domain") == domain)]
    plan = subscription.get("plan") or {}
    freq = _BULLETIN_PT.get(plan.get("bulletin_frequency") or "none", "nenhum")
    return {
        "vigilias_active": sum(1 for v in rel if v.get("enabled")),
        "vigilias_ok": sum(1 for v in rel if v.get("last_status") == "ok"),
        "vigilias_warning": sum(1 for v in rel if v.get("last_status") in ("warning", "alert")),
        "vigilias_critical": sum(1 for v in rel if v.get("last_status") in ("critical", "error")),
        "bulletin_frequency": freq,
        "seal_enabled": bool(seal_enabled),
        "technician_linked": bool(technician_linked),
    }


def _sector_label(sector: Optional[str]) -> Optional[str]:
    if not sector:
        return None
    try:
        from discovery.sector_taxonomy import get_label
        return get_label(sector)
    except Exception:  # noqa: BLE001
        return sector


def build_benchmark(sector: Optional[str], score: Optional[int],
                    bench: Optional[dict], position: Optional[dict],
                    global_avg: Optional[dict]) -> dict:
    """Benchmark setorial: rank + média do setor (fallback: média global)."""
    avg = None
    if bench and bench.get("avg_score") is not None:
        avg = bench.get("avg_score")
    elif global_avg and global_avg.get("avg_score") is not None:
        avg = global_avg.get("avg_score")
    return {
        "sector": sector,
        "sector_label": _sector_label(sector),
        "rank_position": (position or {}).get("position"),
        "rank_total": (position or {}).get("total"),
        "sector_avg": avg,
        "above_average": (score is not None and avg is not None and score > avg),
    }


def next_scan_estimate(last_scan_at: Any, plan_id: Optional[str]) -> Optional[str]:
    days = _SCAN_INTERVAL_DAYS.get(plan_id or "free", 30)
    if last_scan_at is None or not hasattr(last_scan_at, "isoformat"):
        return None
    return (last_scan_at + timedelta(days=days)).isoformat()


def _pick_site(sites: list, site_id: Optional[int]) -> dict:
    """Site selecionado: `site_id` (deve ser do usuário → senão 404) ou o primário (1º)."""
    if site_id is None:
        return sites[0]
    for s in sites:
        if int(s.get("target_id")) == int(site_id):
            return s
    raise HTTPException(status_code=404, detail="Site não encontrado.")


# --------------------------------------------------------------------------- #
# Orquestrador (I/O em paralelo)
# --------------------------------------------------------------------------- #

async def _safe(coro):
    """Executa uma coroutine e engole erro (fail-open) → None. Uma query opcional que
    falha (ex.: benchmark) nunca derruba o dashboard inteiro."""
    try:
        return await coro
    except Exception:  # noqa: BLE001
        return None


def _mask_email(email: str) -> str:
    """Mascara o e-mail do dono (regra inviolável: nunca expor cru ao técnico). c***i@x.com."""
    e = (email or "").strip()
    if "@" not in e:
        return e
    local, _, domain = e.partition("@")
    masked = (local[:1] + "***") if len(local) <= 2 else (local[0] + "***" + local[-1])
    return f"{masked}@{domain}"


async def build_dashboard_summary(store, user: dict, site_id: Optional[int] = None) -> dict:
    from api import plans  # import tardio: evita ciclo e respeita monkeypatch nos testes

    uid = user["id"]
    sites = await store.list_user_sites(uid)
    owned = {int(s["target_id"]) for s in sites}

    # KL-90 — MODO TÉCNICO: `site_id` é um site VINCULADO (não próprio). O técnico vê o
    # dashboard TÉCNICO do cliente (checks + evidência + fix), nunca dados da conta do dono.
    # Segurança: só entra aqui se houver um technician_link ATIVO deste técnico p/ o alvo.
    if site_id is not None and int(site_id) not in owned:
        try:
            clients = await store.get_technician_clients(uid) or []
        except Exception:  # noqa: BLE001 - sem vínculo/método → 404 (nunca 500)
            clients = []
        link = next((c for c in clients if int(c.get("target_id")) == int(site_id)), None)
        if not link:
            raise HTTPException(status_code=404, detail="Site não encontrado.")
        return await _build_technician_view(store, user, int(site_id), link)

    subscription = await plans.get_subscription(uid)
    if not sites:
        return {
            "has_site": False, "sites": [], "selected_site_id": None,
            "plan": build_plan(subscription), "checklist": new_user_checklist(user),
        }

    selected = _pick_site(sites, site_id)   # 404 se site_id não for do usuário
    tid = selected["target_id"]

    target, scans, profile, vigilias, technician = await asyncio.gather(
        _safe(store.get_target(tid)),
        _safe(store.list_scans(target_id=tid, limit=30)),
        _safe(store.get_site_profile(tid)),
        _safe(store.get_user_vigilias(uid)),
        _safe(store.get_active_technician_for_target(uid, tid)),
    )
    target = target or {}
    scans = scans or []
    vigilias = vigilias or []

    latest = scans[0] if scans else None
    checks: list = []
    scan_status = "ok"
    if latest:
        full_scan = await _safe(store.get_scan(latest["id"]))
        checks = _extract_checks(full_scan)
        cj = (full_scan or {}).get("checks_json") or {}
        if isinstance(cj, dict):
            scan_status = cj.get("status") or "ok"

    sector = target.get("sector")
    sector_valid = bool(sector) and sector != "outro"
    bench, position, global_avg = await asyncio.gather(
        _safe(store.sector_benchmark(sector)) if sector_valid else _noop(),
        _safe(store.get_sector_position(sector, tid)) if sector_valid else _noop(),
        _safe(store.global_avg_score()),
    )

    history = build_score_history(scans)
    trend, trend_delta = build_trend(history)
    score = (latest or {}).get("score", target.get("last_scan_score"))
    semaphore = (latest or {}).get("semaphore")
    plan_id = subscription.get("plan_id")

    # SSL: preferir o dado da vigília ssl (last_data), senão a evidência do check.
    ssl_days = _ssl_from_vigilias(vigilias, selected.get("domain"))
    if ssl_days is None:
        ssl_days = ssl_days_from_checks(checks)

    platform = target.get("platform")
    site_type = platform if platform and platform != "unknown" else target.get("site_type")

    seal_enabled = False  # sem flag por-site persistido hoje (KL-44 P5 é por dono verificado)

    return {
        "has_site": True,
        "sites": [{"id": s["target_id"], "domain": s.get("domain"),
                   "score": s.get("last_scan_score"), "semaphore": s.get("last_semaphore")}
                  for s in sites],
        "selected_site_id": tid,
        "site": {
            "domain": target.get("domain") or selected.get("domain"),
            "score": score,
            "semaphore": semaphore,
            "trend": trend,
            "trend_delta": trend_delta,
            "last_scan_at": _iso(target.get("last_scan_at")) or (_iso((latest or {}).get("scanned_at"))),
            "next_scan_estimate": next_scan_estimate(target.get("last_scan_at"), plan_id),
            "is_online": scan_status == "ok",
            "site_type": site_type,
            "ssl_days_remaining": ssl_days,
        },
        "benchmark": build_benchmark(sector, score, bench, position, global_avg),
        "risks": build_risks(checks),
        "categories": build_categories(checks),
        "score_history": history,
        "checklist": build_checklist(checks, profile, score, user, seal_enabled),
        "plan": build_plan(subscription),
        "monitoring": build_monitoring(vigilias, selected.get("domain"), subscription,
                                       seal_enabled, bool(technician)),
        "profile": {
            "company_name": (profile or {}).get("company_name"),
            "phone": (profile or {}).get("phone"),
            "sector": sector,
            "confirmed": bool((profile or {}).get("edited_by_admin")),
        },
    }


async def _build_technician_view(store, user: dict, tid: int, link: dict) -> dict:
    """Dashboard TÉCNICO de um site de cliente (o técnico vê os dados técnicos completos —
    checks + evidência + fix por plataforma + PDF técnico — mas NUNCA a conta do dono).
    `link` = a linha de get_technician_clients (vínculo ativo, já validado pelo chamador)."""
    domain = link.get("domain")
    owner_uid = link.get("owner_user_id")

    target, scans, profile, owner_vigilias = await asyncio.gather(
        _safe(store.get_target(tid)),
        _safe(store.list_scans(target_id=tid, limit=30)),
        _safe(store.get_site_profile(tid)),
        _safe(store.get_user_vigilias(owner_uid)) if owner_uid else _noop(),
    )
    target = target or {}
    scans = scans or []
    owner_vigilias = owner_vigilias or []

    latest = scans[0] if scans else None
    checks: list = []
    scan_status = "ok"
    if latest:
        full_scan = await _safe(store.get_scan(latest["id"]))
        checks = _extract_checks(full_scan)
        cj = (full_scan or {}).get("checks_json") or {}
        if isinstance(cj, dict):
            scan_status = cj.get("status") or "ok"

    sector = target.get("sector")
    sector_valid = bool(sector) and sector != "outro"
    bench, position, global_avg = await asyncio.gather(
        _safe(store.sector_benchmark(sector)) if sector_valid else _noop(),
        _safe(store.get_sector_position(sector, tid)) if sector_valid else _noop(),
        _safe(store.global_avg_score()),
    )

    history = build_score_history(scans)
    trend, trend_delta = build_trend(history)
    score = (latest or {}).get("score", target.get("last_scan_score"))
    semaphore = (latest or {}).get("semaphore")
    ssl_days = _ssl_from_vigilias(owner_vigilias, domain)
    if ssl_days is None:
        ssl_days = ssl_days_from_checks(checks)
    platform = target.get("platform")
    site_type = platform if platform and platform != "unknown" else target.get("site_type")

    return {
        "has_site": True,
        "technician_mode": True,                 # o front entra em modo técnico
        "owner_email": _mask_email(link.get("owner_email") or ""),   # SEMPRE mascarado
        "can_receive_alerts": bool(link.get("receive_alerts", True)),  # item 4 (toggle)
        "sites": [],                             # sem seletor de sites próprios no modo técnico
        "selected_site_id": tid,
        "site": {
            "domain": target.get("domain") or domain,
            "score": score, "semaphore": semaphore,
            "trend": trend, "trend_delta": trend_delta,
            "last_scan_at": _iso(target.get("last_scan_at")) or _iso((latest or {}).get("scanned_at")),
            "next_scan_estimate": next_scan_estimate(target.get("last_scan_at"), None),
            "is_online": scan_status == "ok",
            "site_type": site_type, "ssl_days_remaining": ssl_days,
        },
        "benchmark": build_benchmark(sector, score, bench, position, global_avg),
        "risks": build_risks(checks),
        "categories": build_categories(checks),   # já inclui evidence + fix_inline por check
        "score_history": history,
        # vigílias do DONO (read-only para o técnico acompanhar).
        "monitoring": build_monitoring(owner_vigilias, domain, {}, False, True),
        "profile": {
            "company_name": (profile or {}).get("company_name"),
            "sector": sector,
            "confirmed": bool((profile or {}).get("edited_by_admin")),
        },
        # SEM plan, SEM checklist, SEM dados de conta do dono.
    }


async def _noop():
    return None


def _ssl_from_vigilias(vigilias: list, domain: Optional[str]) -> Optional[int]:
    for v in vigilias:
        if v.get("tipo") == "ssl" and (not domain or v.get("site_domain") == domain):
            data = v.get("last_data") or {}
            if isinstance(data, dict) and data.get("ssl_days_remaining") is not None:
                try:
                    return int(data["ssl_days_remaining"])
                except (ValueError, TypeError):
                    return None
    return None
