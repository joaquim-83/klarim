"""KL-123 — detalhes contextuais de cada vigília para o card expansível do dashboard.

O endpoint `GET /account/sites/{id}/vigilias/{tipo}/details` monta um payload rico e
**acessível** (SEM jargão OWASP/CWE/header raw — o dono é PME) para cada tipo de vigília.
A maioria dos dados já vive em `vigilias.last_data` (JSONB) ou em tabelas existentes
(`typosquat_alerts`, `scans.checks_json`), então são só leituras.

Arquitetura (padrão do projeto): as **queries** vivem no `store` e no orquestrador em
`api/main.py`; a **derivação é PURA e testável** — cada `build_<tipo>` abaixo recebe os
dados brutos e devolve o miolo do payload:

    {
        "status": "ok" | "warning" | "critical" | "error" | "unknown",
        "summary": str,          # 1 linha, linguagem de negócio
        "data": dict,            # específico ao tipo
        "guidance": str,         # orientação prática em PT-BR
        "actions": [ ... ],      # {type: link|api, ...}
        "pending_count": int,    # itens pendentes (badge)
    }

O caller acrescenta `tipo` + `history` (alertas recentes daquela vigília). Nenhuma função
aqui faz I/O nem levanta — dados ausentes viram um payload gracioso (`unknown`).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from api.dashboard import check_num, norm_status
from reporter.risk_messages import RISK_MESSAGES

# Rótulos acessíveis por tipo (nada de "check_03_ssl" na UI do dono).
VIGILIA_LABELS: Dict[str, str] = {
    "ssl": "Certificado SSL",
    "domain": "Registro do domínio",
    "score": "Nota de segurança",
    "email": "Proteção de e-mail",
    "reputation": "Reputação online",
    "uptime": "Disponibilidade do site",
    "changes": "Alterações no site",
    "phishing": "Typosquat / phishing",
}

# Links externos úteis (nova aba). Não expõem PII nem dependem de estado.
_GOOGLE_PHISH_REPORT = "https://safebrowsing.google.com/safebrowsing/report_phish/"
_SEARCH_CONSOLE = "https://search.google.com/search-console"

# Nomes acessíveis dos checks de e-mail e reputação (evitam "check_21_spf" na UI).
_SPF, _DKIM, _DMARC = "check_21_spf", "check_22_dkim", "check_23_dmarc"
_HIBP, _SAFE_BROWSING = "check_28_hibp", "check_29_safe_browsing"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _checks_of(scan: Optional[dict]) -> List[dict]:
    """A lista de checks de um scan (`checks_json['results']` ou `['checks']`)."""
    cj = (scan or {}).get("checks_json") or {}
    if not isinstance(cj, dict):
        return []
    return cj.get("results") or cj.get("checks") or []


def _find_check(checks: List[dict], check_id: str) -> Optional[dict]:
    n = check_num(check_id)
    for c in checks:
        if check_num(c.get("check_id")) == n:
            return c
    return None


def _risk(check_id: str) -> str:
    """Explicação acessível (linguagem de negócio) de por que um check importa."""
    return (RISK_MESSAGES.get(check_id) or {}).get("risk") or ""


def _ack_action(status: str) -> List[Dict[str, Any]]:
    """Botão "marcar como resolvido" — só faz sentido quando há algo a resolver."""
    if status in ("warning", "critical", "error"):
        return [{"type": "api", "label": "Marcar como resolvido", "endpoint": "acknowledge"}]
    return []


def _is_acknowledged(last_data: dict) -> bool:
    return bool((last_data or {}).get("acknowledged_at"))


def unknown_payload(tipo: str) -> Dict[str, Any]:
    """Vigília sem dados ainda (nunca rodou) — payload gracioso, sem crash."""
    return {
        "status": "unknown",
        "summary": "Aguardando a primeira verificação.",
        "data": {},
        "guidance": ("Esta vigília ainda não foi executada. Assim que a primeira verificação "
                     "rodar, os detalhes aparecem aqui."),
        "actions": [],
        "pending_count": 0,
    }


def format_history(alerts: List[dict], limit: int = 10) -> List[Dict[str, Any]]:
    """Histórico genérico a partir dos alertas de vigília (mais recente 1º)."""
    out = []
    for a in (alerts or [])[:limit]:
        out.append({
            "title": a.get("title") or "",
            "severity": a.get("severity") or "info",
            "created_at": a.get("created_at"),
        })
    return out


# --------------------------------------------------------------------------- #
# SSL — certificado expirando
# --------------------------------------------------------------------------- #

def _ssl_status(days: Optional[int]) -> str:
    if days is None:
        return "unknown"
    if days < 0:
        return "critical"
    if days <= 1:
        return "critical"
    if days <= 30:
        return "warning"
    return "ok"


def build_ssl(last_data: dict, ssl_check: Optional[dict], cert_authority: Optional[str],
              domain: str) -> Dict[str, Any]:
    """Certificado SSL: dias restantes + emissor + validade. Enriquece o `last_data` da
    vigília com os detalhes do check 03 (subject/not_before) e a CA do perfil."""
    ld = last_data or {}
    details = (ssl_check or {}).get("details") or {}
    days = ld.get("days_left")
    if days is None:
        days = details.get("days_left")
    expiry = ld.get("expiry_date") or details.get("not_after")
    status = _ssl_status(days if days is None else int(days))
    data = {
        "issuer": cert_authority or None,
        "subject_cn": details.get("subject_cn"),
        "valid_from": details.get("not_before"),
        "valid_until": expiry,
        "days_left": None if days is None else int(days),
    }
    if days is None:
        return {**unknown_payload("ssl"), "data": data}
    days = int(days)
    if days < 0:
        summary = "Certificado SSL expirado."
        guidance = ("🚨 Seu certificado SSL está EXPIRADO. Os visitantes veem um aviso de "
                    "\"site inseguro\" e podem abandonar o site. Renove imediatamente no painel "
                    "da sua hospedagem.")
    elif days <= 14:
        summary = f"Certificado SSL expira em {days} dia(s) — renove com urgência."
        guidance = (f"🚨 Seu certificado SSL expira em {days} dia(s). Renove IMEDIATAMENTE para "
                    "evitar que o site fique inacessível ou marcado como inseguro.")
    elif days <= 30:
        summary = f"Certificado SSL expira em {days} dias — atenção."
        guidance = (f"⚠️ Seu certificado SSL expira em {days} dias. Verifique se a renovação "
                    "automática está ativa no seu provedor de hospedagem.")
    else:
        summary = f"Certificado válido por mais {days} dias."
        guidance = (f"✅ Seu certificado SSL está válido por mais {days} dias. Sem urgência — a "
                    "renovação automática deve ocorrer antes do vencimento.")
    return {
        "status": status,
        "summary": summary,
        "data": data,
        "guidance": guidance,
        "actions": _ack_action(status),
        "pending_count": 0 if (status == "ok" or _is_acknowledged(ld)) else 1,
    }


# --------------------------------------------------------------------------- #
# Domínio — registro expirando
# --------------------------------------------------------------------------- #

def _domain_status(days: Optional[int]) -> str:
    if days is None:
        return "unknown"
    if days <= 7:
        return "critical"
    if days <= 60:
        return "warning"
    return "ok"


def build_domain(last_data: dict, domain: str) -> Dict[str, Any]:
    """Registro do domínio: dias até expirar (via RDAP, já em `last_data`)."""
    ld = last_data or {}
    days = ld.get("days_left")
    expiry = ld.get("expiry_date")
    data = {"expiry_date": expiry, "days_left": None if days is None else int(days),
            "registrar": ld.get("registrar")}
    if days is None:
        return {**unknown_payload("domain"), "data": data}
    days = int(days)
    status = _domain_status(days)
    exp = f" ({expiry})" if expiry else ""
    if days <= 30:
        summary = f"Domínio expira em {days} dia(s) — renove já."
        guidance = (f"🚨 Seu domínio expira em {days} dia(s){exp}. RENOVE IMEDIATAMENTE. Domínios "
                    "expirados podem ser registrados por terceiros — você perde o endereço do seu site.")
    elif days <= 60:
        summary = f"Domínio expira em {days} dias — renove em breve."
        guidance = (f"⚠️ Seu domínio expira em {days} dias{exp}. Renove em breve para não perder o "
                    "registro. Considere ativar a renovação automática no registrador.")
    else:
        summary = f"Domínio válido por mais {days} dias."
        guidance = (f"✅ Seu domínio está registrado por mais {days} dias{exp}. Sem urgência.")
    return {
        "status": status,
        "summary": summary,
        "data": data,
        "guidance": guidance,
        "actions": _ack_action(status),
        "pending_count": 0 if (status == "ok" or _is_acknowledged(ld)) else 1,
    }


# --------------------------------------------------------------------------- #
# Score — queda de nota
# --------------------------------------------------------------------------- #

_SEMA_PT = {"verde": "verde", "amarelo": "amarelo", "vermelho": "vermelho"}


def _semaphore(score: Optional[int]) -> str:
    if score is None:
        return "vermelho"
    if score >= 90:
        return "verde"
    if score >= 50:
        return "amarelo"
    return "vermelho"


def build_score(scans: List[dict], last_data: dict, domain: str) -> Dict[str, Any]:
    """Nota de segurança: valor atual, variação e quais checks mudaram entre os 2 scans
    mais recentes (em linguagem acessível)."""
    ld = last_data or {}
    scans = scans or []
    if not scans:
        cur = ld.get("current_score")
        if cur is None:
            return unknown_payload("score")
        cur = int(cur)
        return {
            "status": "ok" if cur >= 50 else "critical",
            "summary": f"Nota de segurança atual: {cur}/100.",
            "data": {"current_score": cur, "previous_score": ld.get("previous_score"),
                     "delta": ld.get("delta"), "semaphore": _semaphore(cur), "checks_changed": []},
            "guidance": _score_guidance([]),
            "actions": [],
            "pending_count": 0,
        }
    cur = scans[0].get("score")
    cur = int(cur) if cur is not None else None
    prev = None
    checks_changed: List[Dict[str, Any]] = []
    if len(scans) >= 2 and scans[1].get("score") is not None:
        prev = int(scans[1]["score"])
        checks_changed = _diff_checks(_checks_of(scans[0]), _checks_of(scans[1]))
    delta = (cur - prev) if (cur is not None and prev is not None) else None
    status = "ok"
    if cur is not None and cur < 50:
        status = "critical"
    elif delta is not None and delta < 0:
        status = "warning"
    if delta is None:
        summary = f"Nota de segurança atual: {cur}/100." if cur is not None else "Nota indisponível."
    elif delta > 0:
        summary = f"Nota subiu {delta} ponto(s): {prev} → {cur}."
    elif delta < 0:
        summary = f"Nota caiu {abs(delta)} ponto(s): {prev} → {cur}."
    else:
        summary = f"Nota estável em {cur}/100."
    data = {
        "current_score": cur,
        "previous_score": prev,
        "delta": delta,
        "semaphore": _semaphore(cur),
        "checks_changed": checks_changed,
        "score_history": _score_history(scans),
    }
    worsened = [c for c in checks_changed if c["to"] == "FAIL"]
    return {
        "status": status,
        "summary": summary,
        "data": data,
        "guidance": _score_guidance(worsened),
        "actions": _ack_action(status),
        "pending_count": 0 if (status != "warning" or _is_acknowledged(ld)) else 1,
    }


def _score_history(scans: List[dict], limit: int = 5) -> List[Dict[str, Any]]:
    """Últimas variações de score (do mais antigo ao mais recente) para o mini-gráfico."""
    pts = []
    for s in scans:
        if s.get("score") is None:
            continue
        dt = s.get("scanned_at")
        date = dt.date().isoformat() if hasattr(dt, "date") else (str(dt)[:10] if dt else None)
        pts.append({"date": date, "score": int(s["score"]),
                    "semaphore": s.get("semaphore") or _semaphore(s.get("score"))})
    pts.reverse()   # scans vêm do mais novo ao mais antigo
    return pts[-limit:]


def _diff_checks(cur_checks: List[dict], prev_checks: List[dict]) -> List[Dict[str, Any]]:
    """Checks que passaram de PASS↔FAIL entre 2 scans, com nome acessível + orientação."""
    prev_by = {check_num(c.get("check_id")): c for c in prev_checks}
    changed: List[Dict[str, Any]] = []
    for c in cur_checks:
        n = check_num(c.get("check_id"))
        p = prev_by.get(n)
        if not p:
            continue
        cur_st = norm_status(c.get("status")).upper()
        prev_st = norm_status(p.get("status")).upper()
        if cur_st == prev_st or cur_st not in ("PASS", "FAIL") or prev_st not in ("PASS", "FAIL"):
            continue
        cid = c.get("check_id") or ""
        changed.append({
            "name": c.get("name") or cid,
            "from": prev_st,
            "to": cur_st,
            "severity": (c.get("severity") or "media").strip().lower(),
            "guidance": _risk(cid),
        })
    # Piora (→FAIL) primeiro, depois melhora.
    changed.sort(key=lambda x: 0 if x["to"] == "FAIL" else 1)
    return changed


def _score_guidance(worsened: List[Dict[str, Any]]) -> str:
    if not worsened:
        return ("✅ Nenhuma proteção deixou de funcionar. Continue acompanhando — avisamos "
                "sempre que a nota cair.")
    first = worsened[0]
    extra = first.get("guidance") or ""
    tail = f" {extra}" if extra else ""
    return (f"⚠️ A proteção \"{first['name']}\" deixou de funcionar, o que baixou a sua nota.{tail} "
            "Revise as últimas mudanças no seu servidor ou site.")


# --------------------------------------------------------------------------- #
# E-mail — SPF / DKIM / DMARC
# --------------------------------------------------------------------------- #

def _email_state(check: Optional[dict]) -> str:
    if not check:
        return "unknown"
    st = norm_status(check.get("status")).upper()
    if st == "PASS":
        return "pass"
    if st == "FAIL":
        return "absent"
    return "unknown"


def build_email(latest_checks: List[dict], domain: str) -> Dict[str, Any]:
    """Proteção de e-mail: estado de SPF/DKIM/DMARC no último scan (linguagem acessível)."""
    checks = latest_checks or []
    if not checks:
        return unknown_payload("email")
    spf = _find_check(checks, _SPF)
    dkim = _find_check(checks, _DKIM)
    dmarc = _find_check(checks, _DMARC)
    data = {
        "spf": {"status": _email_state(spf)},
        "dkim": {"status": _email_state(dkim)},
        "dmarc": {"status": _email_state(dmarc)},
    }
    dmarc_ok = data["dmarc"]["status"] == "pass"
    spf_ok = data["spf"]["status"] == "pass"
    missing = [n for n, ok in (("SPF", spf_ok), ("DMARC", dmarc_ok)) if not ok]
    if not missing:
        status = "ok"
        summary = "Proteção de e-mail configurada (SPF e DMARC ativos)."
        guidance = ("✅ Seu domínio está protegido contra falsificação de e-mail. Golpistas não "
                    "conseguem enviar e-mails fingindo ser da sua empresa com facilidade.")
    else:
        status = "warning"
        summary = f"Proteção de e-mail incompleta ({', '.join(missing)} ausente)."
        if not dmarc_ok:
            guidance = (f"⚠️ Sem DMARC, qualquer pessoa pode enviar e-mails fingindo ser "
                        f"@{domain} e enganar seus clientes. Configure SPF, DKIM e DMARC no painel "
                        "de DNS do seu domínio.")
        else:
            guidance = ("⚠️ Falta o registro SPF no seu DNS. Sem ele, provedores como o Gmail "
                        "podem marcar seus e-mails como suspeitos. Configure no painel de DNS.")
    return {
        "status": status,
        "summary": summary,
        "data": data,
        "guidance": guidance,
        "actions": _ack_action(status),
        "pending_count": 0,
    }


# --------------------------------------------------------------------------- #
# Reputação — blacklist / Safe Browsing
# --------------------------------------------------------------------------- #

def build_reputation(last_data: dict, latest_checks: List[dict], domain: str) -> Dict[str, Any]:
    """Reputação: aparece em alguma blacklist (HIBP / Google Safe Browsing)?"""
    ld = last_data or {}
    blacklisted = list(ld.get("blacklisted") or [])
    sb = _find_check(latest_checks or [], _SAFE_BROWSING)
    sb_state = "clean"
    if sb and norm_status(sb.get("status")).upper() == "FAIL":
        sb_state = "listed"
    elif not sb:
        sb_state = "unknown"
    listed = bool(blacklisted)
    data = {
        "blacklisted": blacklisted,
        "google_safe_browsing": sb_state,
        "status": "listed" if listed else "clean",
    }
    if listed:
        status = "critical"
        summary = f"Listado em {len(blacklisted)} blacklist(s)."
        guidance = (f"🚨 Seu domínio aparece em: {', '.join(blacklisted)}. Isso faz navegadores "
                    "bloquearem o acesso e prejudica a entrega dos seus e-mails. Verifique se o site "
                    "foi comprometido, limpe o conteúdo malicioso e peça a re-análise no Google "
                    "Search Console.")
    else:
        status = "ok"
        summary = "Nenhuma blacklist detectada."
        guidance = ("✅ Seu domínio não aparece em nenhuma blacklist conhecida. Sua reputação está "
                    "limpa — continuamos monitorando.")
    actions: List[Dict[str, Any]] = []
    if listed:
        actions.append({"type": "link", "label": "Abrir o Search Console ↗",
                        "url": _SEARCH_CONSOLE, "target": "_blank"})
        actions += _ack_action(status)
    return {
        "status": status,
        "summary": summary,
        "data": data,
        "guidance": guidance,
        "actions": actions,
        "pending_count": 0 if (not listed or _is_acknowledged(ld)) else 1,
    }


# --------------------------------------------------------------------------- #
# Uptime — disponibilidade
# --------------------------------------------------------------------------- #

def build_uptime(last_data: dict, domain: str) -> Dict[str, Any]:
    """Disponibilidade: site online/offline + tempo de resposta (de `last_data`)."""
    ld = last_data or {}
    if not ld:
        return unknown_payload("uptime")
    fails = int(ld.get("consecutive_failures", 0) or 0)
    code = ld.get("last_response_code")
    ms = ld.get("last_response_time_ms")
    online = fails == 0
    data = {
        "last_response_code": code,
        "last_response_time_ms": ms,
        "consecutive_failures": fails,
        "status": "online" if online else "offline",
    }
    if online:
        status = "ok"
        ms_txt = f" ({ms}ms)" if ms else ""
        summary = f"Site respondendo normalmente{ms_txt}."
        guidance = (f"✅ Seu site {domain} está no ar e respondendo normalmente{ms_txt}. "
                    "Monitoramos a disponibilidade continuamente.")
    else:
        status = "critical"
        summary = f"Site fora do ar ({fails} falha(s) seguida(s))."
        guidance = (f"🚨 Seu site {domain} não respondeu nas últimas {fails} verificações "
                    f"(último código HTTP: {code if code is not None else '—'}). Verifique com seu "
                    "provedor de hospedagem se o servidor está no ar.")
    return {
        "status": status,
        "summary": summary,
        "data": data,
        "guidance": guidance,
        "actions": _ack_action(status),
        "pending_count": 0 if (online or _is_acknowledged(ld)) else 1,
    }


# --------------------------------------------------------------------------- #
# Changes — alterações no site
# --------------------------------------------------------------------------- #

def build_changes(last_data: dict, last_status: Optional[str], domain: str) -> Dict[str, Any]:
    """Alterações no site: mostra o snapshot atual. O alerta em si vem pelo histórico
    (não guardamos um diff persistente — o worker compara ciclo a ciclo)."""
    ld = last_data or {}
    snap = ld.get("snapshot") or {}
    if not snap:
        return {**unknown_payload("changes"),
                "summary": "Aguardando a primeira leitura do site."}
    data = {
        "changes": [],   # o diff é efêmero (worker); o alerta aparece no histórico
        "last_snapshot": {
            "title": snap.get("title"),
            "scripts_count": snap.get("scripts_count"),
            "forms_count": snap.get("forms_count"),
            "taken_at": snap.get("taken_at"),
        },
    }
    status = last_status if last_status in ("ok", "warning", "critical", "error") else "ok"
    if status in ("warning", "critical"):
        summary = "Alterações detectadas no site."
        guidance = ("⚠️ Detectamos mudanças no seu site. Podem ser normais (atualização que você "
                    "fez) ou sinal de invasão. Acesse o site e confira se está tudo como esperado. "
                    "Se você não fez mudanças, investigue com seu desenvolvedor.")
    else:
        summary = "Nenhuma alteração significativa detectada."
        guidance = ("✅ O conteúdo do seu site está estável desde a última verificação. Avisamos se "
                    "houver mudança relevante (possível invasão ou injeção de código).")
    return {
        "status": status,
        "summary": summary,
        "data": data,
        "guidance": guidance,
        "actions": _ack_action(status),
        "pending_count": 0 if (status not in ("warning", "critical") or _is_acknowledged(ld)) else 1,
    }


# --------------------------------------------------------------------------- #
# Phishing — typosquat
# --------------------------------------------------------------------------- #

_SIMILARITY_PT = {
    "levenshtein": "letras trocadas",
    "homoglyph": "caracteres parecidos",
    "tld_variant": "outra extensão (.com/.net…)",
}


def build_phishing(alerts: List[dict], domain: str) -> Dict[str, Any]:
    """Typosquat/phishing: domínios suspeitos que imitam o do dono (typosquat_alerts).
    `pending_count` = suspeitos ainda não descartados (o badge do card)."""
    alerts = alerts or []
    items = []
    for a in alerts:
        items.append({
            "id": a.get("id"),
            "suspicious_domain": a.get("suspicious_domain"),
            "similarity_type": a.get("similarity_type"),
            "similarity_label": _SIMILARITY_PT.get(a.get("similarity_type"), a.get("similarity_type")),
            "distance": a.get("distance"),
            "detected_at": a.get("detected_at"),
            "dismissed": bool(a.get("dismissed")),
        })
    pending = [i for i in items if not i["dismissed"]]
    data = {"alerts": items}
    if pending:
        status = "critical"
        summary = f"{len(pending)} domínio(s) suspeito(s) para revisar."
        guidance = ("1. Acesse cada domínio e verifique se ele imita o seu site.\n"
                    "2. Se confirmar que é phishing, denuncie no Google Safe Browsing e no "
                    "registrador (Registro.br).\n"
                    "3. Considere registrar variações do seu domínio para se proteger.")
        actions = [{"type": "link", "label": "Denunciar no Google ↗",
                    "url": _GOOGLE_PHISH_REPORT, "target": "_blank"}]
    else:
        status = "ok"
        summary = "Nenhum domínio suspeito detectado."
        guidance = ("✅ Não encontramos domínios tentando se passar pelo seu. Continuamos vigiando "
                    "os registros de novos domínios parecidos com o seu.")
        actions = []
    return {
        "status": status,
        "summary": summary,
        "data": data,
        "guidance": guidance,
        "actions": actions,
        "pending_count": len(pending),
    }
