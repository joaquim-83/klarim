"""KL-44 P2 — Lógica das 5 vigílias core (monitoramento silencioso contínuo).

Cada `check_*` recebe o `store`, o domínio e o `last_data` (estado do último check,
para anti-spam) e devolve um dicionário homogêneo:

    {
        "status": "ok" | "warning" | "critical" | "error",  # saúde da vigília
        "should_alert": bool,                                # gerar alerta agora?
        "severity": "info" | "warning" | "critical",         # gravidade do alerta
        "subject": str,                                      # assunto do e-mail
        "title": str,                                        # título do alerta
        "message": str,                                      # descrição acessível
        "action_text": str | None,                           # texto p/ o técnico (copiável)
        "data": dict,                                        # persistido em last_data
    }

**100% passivo:** SSL/score/email/reputação leem o último scan (o scanner já fez o
trabalho); domínio faz um lookup RDAP público (leitura). Nenhuma função levanta — na
dúvida devolve `status="error"` / `should_alert=False`. O worker (P3) é quem envia o
e-mail e reagenda.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx

VIGILIA_TYPES = ["ssl", "domain", "score", "email", "reputation"]

_SITE_BASE = "https://klarim.net"

# Thresholds (dias) → severidade do alerta. Ordenados do mais para o menos urgente.
_SSL_THRESHOLDS: List[Tuple[int, str]] = [(1, "critical"), (7, "warning"),
                                          (14, "warning"), (30, "info")]
_DOMAIN_THRESHOLDS: List[Tuple[int, str]] = [(7, "critical"), (14, "warning"),
                                             (30, "warning"), (60, "info")]

_RDAP_TIMEOUT = 10.0
_RDAP_CACHE_TTL = 86400  # 24h


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _extract_check(checks_json: Any, check_id: str) -> Optional[Dict[str, Any]]:
    """Acha um check pelo `check_id` dentro do `checks_json` de um scan (ScanReport)."""
    if not isinstance(checks_json, dict):
        return None
    for r in checks_json.get("results", []) or []:
        if isinstance(r, dict) and r.get("check_id") == check_id:
            return r
    return None


def _error(msg: str) -> Dict[str, Any]:
    return {"status": "error", "should_alert": False, "severity": "info",
            "subject": "", "title": "", "message": "", "action_text": None,
            "data": {"error": msg}}


def _ok(data: Optional[dict] = None) -> Dict[str, Any]:
    return {"status": "ok", "should_alert": False, "severity": "info", "subject": "",
            "title": "", "message": "", "action_text": None, "data": data or {}}


def _threshold_alert(days: int, thresholds: List[Tuple[int, str]],
                     alerted: List[int]) -> Tuple[Optional[Tuple[int, str]], List[int]]:
    """Anti-spam por threshold. Devolve `((threshold, severity) | None, alerted_atualizado)`.

    `crossed` = thresholds já ultrapassados (days <= t). Dispara para o **mais urgente**
    ainda não alertado. Renovação (days sobe acima de um threshold) **limpa** aquele
    threshold da lista, para um novo mergulho voltar a alertar."""
    sev_by_thr = dict(thresholds)
    alerted_set = set(int(a) for a in (alerted or []))
    crossed = [t for (t, _) in thresholds if days <= t]
    still_alerted = sorted({t for t in (alerted_set | set(crossed)) if days <= t},
                           reverse=True)
    new = [t for t in crossed if t not in alerted_set]
    if not new:
        return None, still_alerted
    trigger = min(crossed)  # o mais urgente ultrapassado
    return (trigger, sev_by_thr[trigger]), still_alerted


def _parse_rdap_date(value: Any) -> Optional[datetime]:
    if not value or not isinstance(value, str):
        return None
    v = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(v)
    except ValueError:
        # RDAP às vezes traz só a data
        try:
            dt = datetime.strptime(value.strip()[:10], "%Y-%m-%d")
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


# --------------------------------------------------------------------------- #
# 2.1 — Vigília SSL (certificado expirando)
# --------------------------------------------------------------------------- #

async def check_ssl(store: Any, domain: str, last_data: Dict[str, Any],
                    **_: Any) -> Dict[str, Any]:
    """Lê `days_left` do check 03 (SSL) do scan mais recente e alerta por threshold."""
    target = await store.get_target_by_domain(domain)
    if not target:
        return _error("alvo não encontrado")
    scans = await store.get_recent_scans_with_checks(target["id"], limit=1)
    if not scans:
        return _error("sem scan recente")
    chk = _extract_check(scans[0].get("checks_json"), "check_03_ssl")
    details = (chk or {}).get("details") or {}
    days = details.get("days_left")
    not_after = details.get("not_after")
    if days is None:
        return _error("SSL sem days_left no último scan")
    days = int(days)
    alerted = (last_data or {}).get("alerted_thresholds", [])
    hit, still = _threshold_alert(days, _SSL_THRESHOLDS, alerted)
    data = {"days_left": days, "expiry_date": not_after, "alerted_thresholds": still}
    status = "critical" if days <= 1 else ("warning" if days <= 30 else "ok")
    if not hit:
        return {"status": status, "should_alert": False, "severity": "info",
                "subject": "", "title": "", "message": "", "action_text": None, "data": data}
    _, severity = hit
    exp = (not_after or "")[:10]
    return {
        "status": status, "should_alert": True, "severity": severity,
        "subject": f"⚠️ Certificado SSL do site {domain} expira em {days} dias",
        "title": f"Certificado SSL expira em {days} dias",
        "message": (f"O certificado SSL do site {domain} expira em {days} dias"
                    f"{f' ({exp})' if exp else ''}. Se não for renovado, seus visitantes "
                    "verão um aviso de \"site inseguro\" no navegador e podem abandonar o site."),
        "action_text": (f"O certificado SSL de {domain} expira em {exp or 'breve'}. "
                        "Renove via painel da hospedagem ou reconfigure o Let's Encrypt/"
                        "Certbot. Verifique: certbot renew --dry-run"),
        "data": data,
    }


# --------------------------------------------------------------------------- #
# 2.2 — Vigília de domínio (registro expirando) — RDAP
# --------------------------------------------------------------------------- #

async def _rdap_expiry(domain: str) -> Optional[datetime]:
    """Data de expiração do registro via RDAP (RFC 7480). `.br` → registro.br;
    fallback rdap.org. Best-effort — None em qualquer falha."""
    d = domain.lower().strip()
    urls = []
    if d.endswith(".br"):
        urls.append(f"https://rdap.registro.br/domain/{d}")
    urls.append(f"https://rdap.org/domain/{d}")
    headers = {"Accept": "application/rdap+json", "User-Agent": "Klarim-Vigilia/1.0"}
    async with httpx.AsyncClient(timeout=_RDAP_TIMEOUT, follow_redirects=True) as client:
        for url in urls:
            try:
                resp = await client.get(url, headers=headers)
            except Exception:  # noqa: BLE001 - rede instável, tenta o próximo
                continue
            if resp.status_code != 200:
                continue
            try:
                payload = resp.json()
            except Exception:  # noqa: BLE001
                continue
            for ev in payload.get("events", []) or []:
                if str(ev.get("eventAction", "")).lower() in ("expiration", "expiry"):
                    dt = _parse_rdap_date(ev.get("eventDate"))
                    if dt:
                        return dt
    return None


async def check_domain_expiry(store: Any, domain: str, last_data: Dict[str, Any],
                              redis: Any = None, **_: Any) -> Dict[str, Any]:
    """Alerta quando o registro do domínio está perto de expirar (perder o domínio =
    perder o site). Cacheia a resposta RDAP por 24h no Redis."""
    expiry: Optional[datetime] = None
    cache_key = f"vigilia:rdap:{domain.lower().strip()}"
    if redis is not None:
        try:
            cached = await redis.get(cache_key)
            if cached:
                expiry = _parse_rdap_date(cached.decode() if isinstance(cached, bytes) else cached)
        except Exception:  # noqa: BLE001
            expiry = None
    if expiry is None:
        expiry = await _rdap_expiry(domain)
        if expiry is not None and redis is not None:
            try:
                await redis.set(cache_key, expiry.isoformat(), ex=_RDAP_CACHE_TTL)
            except Exception:  # noqa: BLE001
                pass
    if expiry is None:
        return _error("RDAP indisponível")
    now = datetime.now(timezone.utc)
    days = (expiry - now).days
    alerted = (last_data or {}).get("alerted_thresholds", [])
    hit, still = _threshold_alert(days, _DOMAIN_THRESHOLDS, alerted)
    data = {"days_left": days, "expiry_date": expiry.date().isoformat(),
            "alerted_thresholds": still}
    status = "critical" if days <= 7 else ("warning" if days <= 60 else "ok")
    if not hit:
        return {"status": status, "should_alert": False, "severity": "info",
                "subject": "", "title": "", "message": "", "action_text": None, "data": data}
    _, severity = hit
    exp = expiry.date().isoformat()
    return {
        "status": status, "should_alert": True, "severity": severity,
        "subject": f"⚠️ O domínio {domain} expira em {days} dias",
        "title": f"O domínio {domain} expira em {days} dias",
        "message": (f"O registro do domínio {domain} expira em {days} dias ({exp}). "
                    "Se não for renovado, o site sai do ar e o domínio pode ser "
                    "registrado por outra pessoa — você perde o endereço do seu site."),
        "action_text": (f"O domínio {domain} expira em {exp}. Renove o registro no "
                        "registrador (Registro.br, GoDaddy, etc.) antes dessa data. "
                        "Considere ativar a renovação automática."),
        "data": data,
    }


# --------------------------------------------------------------------------- #
# 2.3 — Vigília de score (queda de segurança)
# --------------------------------------------------------------------------- #

async def check_score_change(store: Any, domain: str, last_data: Dict[str, Any],
                             **_: Any) -> Dict[str, Any]:
    """Compara o score dos 2 scans mais recentes. Alerta em queda > 5 pontos ou ao sair
    do verde (≥90 → <90). Anti-spam: 1 alerta por par de scans (scan mais recente)."""
    target = await store.get_target_by_domain(domain)
    if not target:
        return _error("alvo não encontrado")
    scans = await store.get_recent_scans_with_checks(target["id"], limit=2)
    if len(scans) < 2:
        return _ok({"note": "primeiro scan (sem comparação)"})
    cur, prev = scans[0].get("score"), scans[1].get("score")
    if cur is None or prev is None:
        return _ok({"note": "score ausente"})
    cur, prev = int(cur), int(prev)
    delta = cur - prev
    scan_id = scans[0].get("id")
    already = (last_data or {}).get("last_alerted_scan_id")
    should = (cur < prev - 5) or (prev >= 90 and cur < 90)
    data = {"previous_score": prev, "current_score": cur, "delta": delta,
            "last_alerted_scan_id": already}
    if not should:
        return {"status": "ok", "should_alert": False, "severity": "info", "subject": "",
                "title": "", "message": "", "action_text": None, "data": data}
    if already == scan_id:  # já alertou por este scan
        return {"status": "warning", "should_alert": False, "severity": "warning",
                "subject": "", "title": "", "message": "", "action_text": None, "data": data}
    data["last_alerted_scan_id"] = scan_id
    severity = "critical" if cur < 50 else "warning"
    return {
        "status": "warning", "should_alert": True, "severity": severity,
        "subject": f"⚠️ Score de segurança do site {domain} caiu de {prev} para {cur}",
        "title": f"Score de segurança caiu de {prev} para {cur}",
        "message": (f"A nota de segurança do site {domain} caiu {abs(delta)} pontos "
                    f"(de {prev} para {cur}). Pode ter havido mudança na configuração do "
                    "servidor, remoção de um certificado/header de segurança, ou uma "
                    "atualização que introduziu uma vulnerabilidade."),
        "action_text": (f"O score de segurança de {domain} caiu de {prev} para {cur}. "
                        "Revise as últimas mudanças de servidor/site e os headers de "
                        "segurança (HSTS, CSP, etc.)."),
        "data": data,
    }


# --------------------------------------------------------------------------- #
# 2.4 — Vigília de email (SPF/DKIM/DMARC quebrados)
# --------------------------------------------------------------------------- #

_EMAIL_CHECKS = [("check_21_spf", "SPF"), ("check_22_dkim", "DKIM"),
                 ("check_23_dmarc", "DMARC")]


async def check_email_security(store: Any, domain: str, last_data: Dict[str, Any],
                               **_: Any) -> Dict[str, Any]:
    """Alerta quando SPF/DKIM/DMARC passou de PASS para FAIL entre os 2 últimos scans."""
    target = await store.get_target_by_domain(domain)
    if not target:
        return _error("alvo não encontrado")
    scans = await store.get_recent_scans_with_checks(target["id"], limit=2)
    if len(scans) < 2:
        return _ok({"note": "primeiro scan (sem comparação)"})
    changed: List[str] = []
    for cid, label in _EMAIL_CHECKS:
        cur = _extract_check(scans[0].get("checks_json"), cid)
        prev = _extract_check(scans[1].get("checks_json"), cid)
        if cur and prev and prev.get("status") == "PASS" and cur.get("status") == "FAIL":
            changed.append(label)
    scan_id = scans[0].get("id")
    already = (last_data or {}).get("last_alerted_scan_id")
    data = {"changed_checks": changed, "last_alerted_scan_id": already}
    if not changed:
        return {"status": "ok", "should_alert": False, "severity": "info", "subject": "",
                "title": "", "message": "", "action_text": None, "data": data}
    if already == scan_id:
        return {"status": "warning", "should_alert": False, "severity": "warning",
                "subject": "", "title": "", "message": "", "action_text": None, "data": data}
    data["last_alerted_scan_id"] = scan_id
    joined = ", ".join(changed)
    return {
        "status": "warning", "should_alert": True, "severity": "warning",
        "subject": f"⚠️ Proteção de email do site {domain} foi comprometida",
        "title": f"Proteção de email enfraquecida ({joined})",
        "message": (f"As proteções de e-mail do domínio {domain} mudaram: {joined} "
                    "deixou de estar ativo. Isso pode permitir que golpistas enviem "
                    "e-mails se passando pelo seu domínio (phishing), enganando clientes."),
        "action_text": (f"Os registros DNS de e-mail de {domain} regrediram ({joined}). "
                        "Verifique os registros SPF/DKIM/DMARC no DNS do domínio — "
                        "provavelmente foram removidos ou alterados por engano."),
        "data": data,
    }


# --------------------------------------------------------------------------- #
# 2.5 — Vigília de reputação (blacklist)
# --------------------------------------------------------------------------- #

_REPUTATION_CHECKS = [("check_28_hibp", "Vazamento de dados (HIBP)"),
                      ("check_29_safe_browsing", "Google Safe Browsing")]


async def check_reputation(store: Any, domain: str, last_data: Dict[str, Any],
                           **_: Any) -> Dict[str, Any]:
    """Alerta (crítico) se o site aparece em blacklist (HIBP/Safe Browsing = FAIL). Só
    alerta quando é NOVO (não repete para a mesma blacklist já sinalizada)."""
    target = await store.get_target_by_domain(domain)
    if not target:
        return _error("alvo não encontrado")
    scans = await store.get_recent_scans_with_checks(target["id"], limit=1)
    if not scans:
        return _error("sem scan recente")
    failing: List[str] = []
    for cid, label in _REPUTATION_CHECKS:
        chk = _extract_check(scans[0].get("checks_json"), cid)
        if chk and chk.get("status") == "FAIL":
            failing.append(label)
    previous = set((last_data or {}).get("blacklisted", []))
    new = [f for f in failing if f not in previous]
    data = {"blacklisted": failing}
    if not failing:
        return {"status": "ok", "should_alert": False, "severity": "info", "subject": "",
                "title": "", "message": "", "action_text": None, "data": data}
    if not new:  # já sinalizado antes — não repete
        return {"status": "critical", "should_alert": False, "severity": "critical",
                "subject": "", "title": "", "message": "", "action_text": None, "data": data}
    joined = ", ".join(failing)
    return {
        "status": "critical", "should_alert": True, "severity": "critical",
        "subject": f"🔴 URGENTE: {domain} marcado como site perigoso",
        "title": f"{domain} entrou em uma blacklist de segurança",
        "message": (f"ATENÇÃO: o site {domain} foi sinalizado como perigoso "
                    f"({joined}). Navegadores como o Google Chrome podem exibir uma "
                    "tela vermelha de aviso e bloquear o acesso — seus clientes não "
                    "conseguirão abrir o site. Aja imediatamente."),
        "action_text": (f"{domain} está sinalizado em: {joined}. Verifique se o site foi "
                        "comprometido (malware/phishing), limpe o conteúdo malicioso e "
                        "solicite a re-análise no Google Search Console (Safe Browsing)."),
        "data": data,
    }


# --------------------------------------------------------------------------- #
# Dispatcher
# --------------------------------------------------------------------------- #

_CHECKERS = {
    "ssl": check_ssl,
    "domain": check_domain_expiry,
    "score": check_score_change,
    "email": check_email_security,
    "reputation": check_reputation,
}


async def run_vigilia_check(store: Any, vigilia: Dict[str, Any], redis: Any = None
                            ) -> Dict[str, Any]:
    """Roda o check do `tipo` da vigília. Nunca levanta — tipo inválido/erro → status
    de erro."""
    tipo = vigilia.get("tipo")
    fn = _CHECKERS.get(tipo)
    if fn is None:
        return _error(f"tipo de vigília inválido: {tipo}")
    domain = vigilia.get("site_domain") or ""
    last_data = vigilia.get("last_data") or {}
    try:
        return await fn(store, domain, last_data, redis=redis)
    except Exception as exc:  # noqa: BLE001 - best-effort, o worker segue
        return _error(f"{type(exc).__name__}: {exc}")
