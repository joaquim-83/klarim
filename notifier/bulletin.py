"""Builders de TEXTO PURO dos boletins de segurança (KL-44 P3).

Puros/testáveis (sem I/O). O worker monta o `data` (score, vigílias, ação prioritária,
técnico) e estas funções formatam o corpo. Owner → `alerta@klarimscan.com` (proativo);
técnico/convite → `seguranca@klarim.net` (transacional). Sem HTML, sem preço.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

SITE_BASE = "https://klarim.net"
_EMOJI = {"verde": "🟢", "amarelo": "🟡", "vermelho": "🔴"}
_VIGILIA_LABEL = {"ssl": "SSL", "domain": "Domínio", "score": "Score",
                  "email": "E-mail", "reputation": "Reputação"}
_VIGILIA_STATUS = {"ok": "OK", "warning": "⚠️ atenção", "error": "🔴 erro", "—": "—"}


def _months_pt(month: int) -> str:
    return ["", "Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out",
            "Nov", "Dez"][month]


def bulletin_period_label(month: int, year: int) -> str:
    return f"{_months_pt(month)}/{year}"


def trend_text(trend: str, delta: int) -> str:
    if trend == "up":
        return f"Subiu {abs(delta)} ponto(s) desde o último boletim ↑"
    if trend == "down":
        return f"Caiu {abs(delta)} ponto(s) desde o último boletim ↓"
    return "Estável desde o último boletim"


def owner_subject(domain: str, period: str) -> str:
    return f"{domain} — Boletim de segurança {period}"


def technician_subject(domain: str, score: Any) -> str:
    return f"Laudo técnico — {domain} ({score}/100)"


def invite_subject(owner: str, domain: str) -> str:
    return f"{owner} convidou você como técnico — {domain}"


def build_owner_bulletin(data: Dict[str, Any]) -> str:
    """Corpo do boletim do DONO (plain text, proativo)."""
    dom = data["domain"]
    emoji = _EMOJI.get(data.get("semaphore"), "🟡")
    lines = [
        f"Boletim de segurança — {dom}",
        "",
        f"Score: {data.get('score')}/100 {emoji}  {trend_text(data.get('trend', 'stable'), data.get('delta', 0))}",
        "",
        "Vigílias:",
    ]
    vig = data.get("vigilias") or {}
    for key in ("ssl", "domain", "score", "email", "reputation"):
        st = vig.get(key, "—")
        lines.append(f"  {_VIGILIA_LABEL[key]}: {_VIGILIA_STATUS.get(st, st)}")
    for a in (data.get("vigilia_alerts") or []):
        lines += ["", f"⚠️  {a}"]

    top = data.get("top_action")
    if top:
        lines += ["", "--- Ação prioritária ---", "",
                  f"{top.get('name')}: {top.get('evidence', '')}".strip(), "",
                  "O que fazer:", top.get("fix", "—")]
        if top.get("technical"):
            lines += ["", "Texto para enviar ao técnico:", f'"{top["technical"]}"']

    # KL-44 P5: benchmark do setor (anônimo) + indicadores técnicos de privacidade.
    bench = data.get("benchmark")
    if bench and bench.get("avg_score") is not None:
        score = data.get("score") or 0
        avg = bench.get("avg_score") or 0
        pos = "Acima da média ↑" if score > avg else ("Na média" if score == avg else "Abaixo da média ↓")
        label = bench.get("sector_label") or bench.get("sector") or "geral"
        lines += ["", f"--- Benchmark do setor ({label}) ---", "",
                  f"Seu score: {score}/100", f"Média do setor: {avg}/100", pos]
    priv = data.get("privacy")
    if priv and priv.get("total"):
        lines += ["", f"--- Indicadores de privacidade: {priv.get('score')}/{priv.get('total')} ---", ""]
        for c in (priv.get("checks") or []):
            lines.append(f"  {'✓' if c.get('status') == 'PASS' else '✗'} {c.get('name')} ({c.get('lgpd_ref')})")
        lines += ["", ("⚖️ " + (priv.get("disclaimer") or ""))]

    lines += ["", "--- Seu técnico ---", ""]
    if data.get("technician_masked"):
        lines.append(f"Este boletim também foi enviado para {data['technician_masked']} "
                     "(seu técnico vinculado).")
    else:
        code = data.get("code")
        lines += ["Precisa de ajuda? Encaminhe para seu técnico:",
                  f"{SITE_BASE}/laudo/{code}", "",
                  f"Código do laudo: {code} (válido 30 dias)"]
        if data.get("whatsapp_url"):
            lines += ["", "Ou compartilhe pelo WhatsApp:", data["whatsapp_url"]]

    lines += ["", "---", "", "Ver laudo completo:",
              f"{SITE_BASE}/dashboard?utm_source=klarim&utm_medium=email&utm_campaign=boletim",
              "", "Klarim · Segurança web para o Brasil", SITE_BASE]
    return "\n".join(lines)


def build_technician_bulletin(data: Dict[str, Any]) -> str:
    """Corpo do laudo técnico ao TÉCNICO vinculado (plain text, transacional)."""
    dom = data["domain"]
    emoji = _EMOJI.get(data.get("semaphore"), "🟡")
    fails: List[Dict[str, Any]] = data.get("fails") or []
    lines = [
        f"Laudo técnico — {dom}",
        "",
        f"Você é o técnico vinculado a este site por {data.get('owner_masked', 'um cliente')}.",
        "",
        f"Score: {data.get('score')}/100 {emoji}  {trend_text(data.get('trend', 'stable'), data.get('delta', 0))}",
        "",
        f"--- Checks com falha ({len(fails)}) ---",
        "",
    ]
    for f in fails:
        ref = " / ".join([x for x in (f.get("owasp"), f.get("cwe")) if x]) or "—"
        lines += [f"[{f.get('severity', '')}] {f.get('name')}",
                  f"  Evidência: {f.get('evidence', '—')}",
                  f"  Referência: {ref}",
                  f"  Correção: {f.get('fix', '—')}",
                  ""]
    lines += [f"--- Checks OK ({data.get('pass_count', 0)}) ---", "",
              "Ver detalhes completos:", f"{SITE_BASE}/laudo/{data.get('code')}",
              "", "---", "",
              "Você é profissional de TI e atende outros clientes?",
              f"Gerencie todos num só painel: {SITE_BASE}/cadastrar?role=technician",
              "", "Klarim · Segurança web para o Brasil", SITE_BASE]
    return "\n".join(lines)


def build_technician_invite(data: Dict[str, Any]) -> str:
    """Corpo do convite ao técnico (plain text, transacional)."""
    dom = data["domain"]
    emoji = _EMOJI.get(data.get("semaphore"), "🟡")
    code, invite = data.get("code"), data.get("invite_code")
    # KL-71 Bug 4: com laudo → link do laudo; sem laudo (site sem scan) → perfil público.
    laudo_line = (f"{SITE_BASE}/laudo/{code}" if code else f"{SITE_BASE}/site/{dom}")
    return "\n".join([
        "Olá,",
        "",
        f"{data.get('owner_masked', 'Um cliente')} vinculou você como técnico responsável",
        f"pelo site {dom} na plataforma Klarim.",
        "",
        f"Score atual: {data.get('score')}/100 {emoji}",
        "",
        "Acesse o laudo técnico completo:",
        laudo_line,
        "",
        "Se você já tem conta na Klarim, vincule com o código:",
        f"{invite}",
        "",
        "Se ainda não tem conta, crie seu perfil de profissional:",
        f"{SITE_BASE}/cadastrar?role=technician&invite={invite}",
        "",
        "Klarim · Segurança web para o Brasil",
        SITE_BASE,
    ])
