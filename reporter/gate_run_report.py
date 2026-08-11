"""KL-163 (Prompt 1/2) — PDF de UM run do Security Gate (o dev exporta o resultado do scan).

Diferente do `gate_report.py` (avaliação de FORNECEDORES, Enterprise, redigido): aqui o dono do
run vê o relatório COMPLETO e denso — cabeçalho + todas as categorias com cada check (nome,
severidade, status e, se FALHOU, a orientação de correção) + resumo + rodapé com paginação.

Arquitetura testável (padrão do repo): `build_gate_run_context` é PURO (dict `run` → contexto,
sem I/O nem relógio — a data já vem formatada); `build_gate_run_report_html` é PURO (Jinja →
string); `generate_gate_run_report_pdf` renderiza (WeasyPrint em thread, CPU-bound).

Regra do card: o CPF do desenvolvedor SEMPRE entra mascarado (`api.validators.mask_cpf`) — o valor
completo nunca vai para um documento compartilhável (só para o audit log). A engine de scan NÃO é
alterada; o módulo só consome os `results` já persistidos em `gate_runs`.
"""
from __future__ import annotations

import asyncio
import html
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from jinja2 import Template

# Reusa a fonte ÚNICA dos rótulos de categoria (o mesmo do formatter de terminal) — quando o KL
# adiciona um check/categoria nova, o PDF acompanha sem drift.
from security_gate.formatters.terminal import _CATEGORY_LABELS

# Fuso de Brasília (BRT, UTC-3 fixo, sem horário de verão) — o painel/dev é operado do Brasil.
_BRT = timezone(timedelta(hours=-3))

_CAT_LABEL: Dict[str, str] = dict(_CATEGORY_LABELS)
_CAT_ORDER: Dict[str, int] = {cat: i for i, (cat, _l) in enumerate(_CATEGORY_LABELS)}

_ICON = {"pass": "✅", "fail": "❌", "error": "⚠️", "skip": "⏭️"}
_STATUS_LABEL = {"pass": "PASS", "fail": "FAIL", "error": "ERRO", "skip": "PULADO"}
_STATUS_COLOR = {"pass": "#16a34a", "fail": "#dc2626", "error": "#d97706", "skip": "#94a3b8"}

_SEVERITY_LABEL = {"critical": "Crítica", "high": "Alta", "medium": "Média",
                   "low": "Baixa", "info": "Info"}
_SEVERITY_COLOR = {"critical": "#dc2626", "high": "#f97316", "medium": "#eab308",
                   "low": "#94a3b8", "info": "#94a3b8"}


def _semaphore(score: int) -> str:
    return "🟢" if score >= 90 else "🟡" if score >= 50 else "🔴"


def _score_color(score: int) -> str:
    # Verde ≥90, amarelo 50-89, vermelho <50 (mesma faixa do scoring do produto).
    return "#16a34a" if score >= 90 else "#eab308" if score >= 50 else "#dc2626"


def _domain(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return ""
    if "://" not in u:
        u = "https://" + u
    host = (urlparse(u).hostname or "").lower().strip()
    return host[4:] if host.startswith("www.") else host


def fmt_scan_date(value: Any) -> str:
    """`created_at` (datetime aware/naive-UTC ou string ISO) → `'10/08/2026 às 14:32'` (Brasília).

    Determinístico (o input carrega o instante) — mantém `build_gate_run_context` puro/testável."""
    dt = _coerce_dt(value)
    if dt is None:
        return str(value or "")
    return dt.astimezone(_BRT).strftime("%d/%m/%Y às %H:%M")


def date_for_filename(value: Any) -> str:
    dt = _coerce_dt(value)
    if dt is None:
        return "sem-data"
    return dt.astimezone(_BRT).strftime("%Y-%m-%d")


def _coerce_dt(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value.strip():
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    # naive → assume UTC (as colunas do Postgres são naive-UTC).
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _cat_label(cat: str) -> str:
    return _CAT_LABEL.get(cat) or (cat or "outros").replace("_", " ").title()


def _check_name(check: str) -> str:
    """`content_security_policy` → `Content Security Policy` (mesmo humanize do terminal)."""
    return (check or "").replace("header_", "").replace("_", " ").title() or "—"


# --------------------------------------------------------------------------- #
# Contexto (PURO)
# --------------------------------------------------------------------------- #

def build_gate_run_context(run: Dict[str, Any], *, cpf_masked: Optional[str] = None,
                           plan_name: Optional[str] = None,
                           generated_at: str, city_state: Optional[str] = None) -> Dict[str, Any]:
    """Monta o contexto do template a partir de um `run` (dict de `gate_runs`, com `results`).

    PURO: nenhuma I/O nem relógio — `generated_at`, o CPF mascarado e `city_state` são injetados pelo
    chamador. `cpf_masked` já vem mascarado (`api.validators.mask_cpf`); este módulo nunca recebe o
    CPF cru. `city_state` (KL-163 P2) = `'Cidade/UF'` do endereço estruturado — NUNCA rua/número.
    """
    results: List[Dict[str, Any]] = list(run.get("results") or [])
    score = int(run.get("score") or 0)
    passed = bool(run.get("passed"))
    fail_on = (run.get("fail_on") or "critical").strip().lower()

    # Agrupa por categoria, na ordem canônica dos checks (desconhecidas ao fim).
    by_cat: Dict[str, List[Dict[str, Any]]] = {}
    for r in results:
        by_cat.setdefault(r.get("category") or "outros", []).append(r)
    ordered = sorted(by_cat, key=lambda c: _CAT_ORDER.get(c, len(_CATEGORY_LABELS)))

    categories: List[Dict[str, Any]] = []
    sev_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    counts = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}
    critical_fails: List[Dict[str, str]] = []

    for cat in ordered:
        rows: List[Dict[str, Any]] = []
        passed_n = 0
        for r in by_cat[cat]:
            status = (r.get("status") or "").lower()
            sev = (r.get("severity") or "info").lower()
            is_fail = status == "fail"
            if status == "pass":
                counts["passed"] += 1
                passed_n += 1
            elif status == "fail":
                counts["failed"] += 1
                if sev in sev_counts:
                    sev_counts[sev] += 1
            elif status == "error":
                counts["errors"] += 1
            elif status == "skip":
                counts["skipped"] += 1
            name = _check_name(r.get("check") or "")
            detail = html.escape(str(r.get("detail") or ""))
            row = {
                "icon": _ICON.get(status, "•"),
                "name": html.escape(name),
                "severity_label": _SEVERITY_LABEL.get(sev, sev),
                "severity_color": _SEVERITY_COLOR.get(sev, "#94a3b8"),
                "status": status,
                "status_label": _STATUS_LABEL.get(status, status.upper()),
                "status_color": _STATUS_COLOR.get(status, "#94a3b8"),
                "is_fail": is_fail,
                "detail": detail,
            }
            rows.append(row)
            # Destaque no resumo: falhas críticas e altas.
            if is_fail and sev in ("critical", "high"):
                critical_fails.append({"name": row["name"], "severity_label": row["severity_label"],
                                       "severity_color": row["severity_color"], "detail": detail})
        categories.append({
            "label": _cat_label(cat),
            "passed": passed_n,
            "total": len(by_cat[cat]),
            "status": "fail" if any(x["is_fail"] for x in rows) else "pass",
            "checks": rows,
        })

    total = len(results)
    no_findings = counts["failed"] == 0
    return {
        "domain": html.escape(_domain(run.get("url") or "")),
        "scan_url": html.escape(str(run.get("url") or "")),
        "scan_date": html.escape(fmt_scan_date(run.get("created_at"))),
        "score": score,
        "semaphore": _semaphore(score),
        "score_color": _score_color(score),
        "passed": passed,
        "passed_label": "Passou" if passed else "Reprovou",
        "fail_on_label": _SEVERITY_LABEL.get(fail_on, fail_on),
        "plan_name": html.escape(plan_name or "—"),
        "cpf_masked": html.escape(cpf_masked) if cpf_masked else None,
        "city_state": html.escape(city_state) if city_state else None,
        "counts": counts,
        "total_checks": total,
        "sev_counts": sev_counts,
        "critical_fails": critical_fails,
        "no_findings": no_findings,
        "categories": categories,
        "generated_at": html.escape(generated_at),
    }


# --------------------------------------------------------------------------- #
# Template (PT-BR, A4, print-friendly). Bare Template + escape no builder.
# --------------------------------------------------------------------------- #

_TMPL = Template("""<!DOCTYPE html>
<html lang="pt-BR"><head><meta charset="utf-8"><style>
  @page {
    size: A4; margin: 1.7cm 1.5cm 2cm;
    @bottom-center {
      content: "Relatório gerado pelo Klarim Security Gate — scanner 100% passivo · klarim.net/security-gate · {{ generated_at }} · Página " counter(page) " de " counter(pages);
      font-size: 7.5px; color: #94a3b8;
    }
  }
  body { font-family: 'Helvetica Neue', Arial, sans-serif; color: #1e293b; font-size: 10.5px; line-height: 1.5; }
  .brand { color: #ff6b35; font-weight: 700; letter-spacing: .5px; font-size: 12px; }
  h1 { font-size: 19px; margin: 2px 0 10px; color: #0f172a; }
  h2 { font-size: 13px; margin: 20px 0 6px; color: #0f172a; border-bottom: 2px solid #ff6b35; padding-bottom: 3px; }
  .header { border: 1px solid #cbd5e1; border-radius: 6px; padding: 12px 14px; margin-bottom: 6px; background: #f8fafc; }
  .header .row { margin: 2px 0; }
  .header .label { color: #64748b; display: inline-block; min-width: 100px; }
  .score { font-size: 26px; font-weight: 800; color: {{ score_color }}; }
  .verdict { font-weight: 700; }
  .verdict.ok { color: #16a34a; }
  .verdict.fail { color: #dc2626; }
  table { width: 100%; border-collapse: collapse; margin: 4px 0 2px; }
  th, td { border: 1px solid #e2e8f0; padding: 4px 7px; text-align: left; vertical-align: top; }
  th { background: #f1f5f9; font-weight: 700; font-size: 9.5px; }
  .cat-head { background: #0f172a; color: #fff; padding: 5px 8px; border-radius: 4px 4px 0 0;
              font-weight: 700; font-size: 11px; margin-top: 12px; display: flex; justify-content: space-between; }
  .cat-count { font-weight: 600; }
  .cat-count.ok { color: #4ade80; }
  .cat-count.fail { color: #f87171; }
  .sev { font-weight: 700; font-size: 9px; }
  .st { font-weight: 700; font-size: 9px; }
  .fix { color: #b45309; font-style: italic; font-size: 9.5px; }
  .fixrow td { background: #fffbeb; border-top: none; }
  .summary-box { background: #f8fafc; border-left: 3px solid #ff6b35; padding: 8px 12px; margin: 6px 0; border-radius: 3px; }
  .ok-box { background: #f0fdf4; border-left: 3px solid #16a34a; padding: 10px 12px; border-radius: 3px; color: #166534; font-weight: 600; }
  .sev-tag { display: inline-block; padding: 0 5px; border-radius: 3px; color: #fff; font-size: 9px; font-weight: 700; }
  ul.fails { margin: 6px 0 0; padding-left: 18px; }
  ul.fails li { margin: 2px 0; }
  .num { text-align: center; white-space: nowrap; }
  .disclaimer { margin-top: 22px; padding-top: 8px; border-top: 1px solid #cbd5e1; color: #64748b; font-size: 8.5px; }
</style></head><body>
  <div class="brand">🔒 KLARIM SECURITY GATE</div>
  <h1>Relatório de Segurança</h1>

  <div class="header">
    <div class="row"><span class="label">Domínio:</span> <strong>{{ domain }}</strong></div>
    <div class="row"><span class="label">Data do scan:</span> {{ scan_date }} (Brasília)</div>
    <div class="row"><span class="label">Score:</span> <span class="score">{{ score }}/100</span> {{ semaphore }}</div>
    <div class="row"><span class="label">Resultado:</span>
      <span class="verdict {{ 'ok' if passed else 'fail' }}">{{ passed_label }} {{ '✅' if passed else '❌' }}</span>
      &nbsp;·&nbsp; reprova em falha <strong>{{ fail_on_label }}</strong> ou pior</div>
    <div class="row"><span class="label">Plano:</span> {{ plan_name }}</div>
    {% if cpf_masked %}<div class="row"><span class="label">Desenvolvedor:</span> CPF {{ cpf_masked }}</div>{% endif %}
    {% if city_state %}<div class="row"><span class="label"></span> {{ city_state }}</div>{% endif %}
  </div>

  <h2>Resumo</h2>
  {% if no_findings %}
  <div class="ok-box">Nenhum problema encontrado. Todas as {{ total_checks }} verificações passaram — score {{ score }}/100 ✅</div>
  {% else %}
  <div class="summary-box">
    Total de {{ total_checks }} verificações: <strong>{{ counts.passed }} passaram</strong>,
    <strong>{{ counts.failed }} falharam</strong>{% if counts.errors %}, {{ counts.errors }} inconclusiva(s){% endif %}.
    <span style="margin-left:6px;">
      {% if sev_counts.critical %}<span class="sev-tag" style="background:#dc2626;">{{ sev_counts.critical }} crítica(s)</span>{% endif %}
      {% if sev_counts.high %}<span class="sev-tag" style="background:#f97316;">{{ sev_counts.high }} alta(s)</span>{% endif %}
      {% if sev_counts.medium %}<span class="sev-tag" style="background:#eab308;">{{ sev_counts.medium }} média(s)</span>{% endif %}
      {% if sev_counts.low %}<span class="sev-tag" style="background:#94a3b8;">{{ sev_counts.low }} baixa(s)</span>{% endif %}
    </span>
  </div>
  {% if critical_fails %}
  <p style="margin:8px 0 2px;"><strong>Falhas prioritárias (críticas/altas):</strong></p>
  <ul class="fails">
    {% for f in critical_fails %}
    <li><span class="sev-tag" style="background:{{ f.severity_color }};">{{ f.severity_label }}</span>
        <strong>{{ f.name }}</strong> — {{ f.detail }}</li>
    {% endfor %}
  </ul>
  {% endif %}
  {% endif %}

  <h2>Verificações por categoria</h2>
  {% for cat in categories %}
  <div class="cat-head">
    <span>{{ cat.label }}</span>
    <span class="cat-count {{ 'fail' if cat.status == 'fail' else 'ok' }}">{{ cat.passed }}/{{ cat.total }} {{ '❌' if cat.status == 'fail' else '✅' }}</span>
  </div>
  <table>
    <thead><tr><th style="width:48%;">Verificação</th><th style="width:16%;">Severidade</th><th style="width:12%;">Status</th></tr></thead>
    <tbody>
    {% for c in cat.checks %}
      <tr>
        <td>{{ c.icon }} {{ c.name }}</td>
        <td><span class="sev" style="color:{{ c.severity_color }};">{{ c.severity_label }}</span></td>
        <td><span class="st" style="color:{{ c.status_color }};">{{ c.status_label }}</span></td>
      </tr>
      {% if c.is_fail and c.detail %}
      <tr class="fixrow"><td colspan="3"><span class="fix">↳ {{ c.detail }}</span></td></tr>
      {% endif %}
    {% endfor %}
    </tbody>
  </table>
  {% endfor %}

  <div class="disclaimer">
    Avaliação passiva automatizada (GET/HEAD/DNS/handshake TLS — sem payload de ataque). Não constitui
    pentest nem auditoria formal de segurança. O CPF é exibido parcialmente mascarado.<br>
    Klarim Security Gate — klarim.net/security-gate
  </div>
</body></html>""")


def build_gate_run_report_html(context: Dict[str, Any]) -> str:
    return _TMPL.render(**context)


def _render_pdf(html_str: str) -> bytes:
    from weasyprint import HTML
    return HTML(string=html_str).write_pdf()


async def generate_gate_run_report_pdf(context: Dict[str, Any]) -> bytes:
    return await asyncio.to_thread(_render_pdf, build_gate_run_report_html(context))


def report_filename(domain: str, created_at: Any) -> str:
    """Nome do arquivo: `klarim-gate-<domínio>-<AAAA-MM-DD>.pdf`."""
    safe = (domain or "site").replace("/", "-")
    return f"klarim-gate-{safe}-{date_for_filename(created_at)}.pdf"
