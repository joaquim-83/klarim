"""KL-152 P3 — PDF de avaliação de fornecedores (Enterprise) via WeasyPrint.

`build_vendor_report_html` é PURO/testável (Jinja → string); `generate_vendor_report_pdf` renderiza
(WeasyPrint em thread). Serve o comparativo (N fornecedores) e o run individual (1 site). O conteúdo
respeita a redação do KL-151 P4: contagens, nunca paths/credenciais de terceiros.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict

from jinja2 import Template

_STATUS_LABEL = {"approved": "Aprovado", "attention": "Atenção", "rejected": "Reprovado",
                 "pending": "Pendente"}
_SEMA = {"approved": "🟢", "attention": "🟡", "rejected": "🔴", "pending": "⚪"}


def _sema_for_score(score) -> str:
    s = int(score or 0)
    return "🟢" if s >= 90 else "🟡" if s >= 50 else "🔴"


_TMPL = Template("""<!DOCTYPE html>
<html lang="pt-BR"><head><meta charset="utf-8"><style>
  @page { size: A4; margin: 1.8cm 1.6cm; }
  body { font-family: 'Helvetica Neue', Arial, sans-serif; color: #1e293b; font-size: 11px; line-height: 1.5; }
  h1 { font-size: 20px; margin: 0 0 2px; color: #0f172a; }
  h2 { font-size: 14px; margin: 22px 0 8px; color: #0f172a; border-bottom: 2px solid #ff6b35; padding-bottom: 3px; }
  h3 { font-size: 12px; margin: 14px 0 4px; color: #334155; }
  .brand { color: #ff6b35; font-weight: 700; letter-spacing: .5px; }
  .meta { color: #64748b; font-size: 10px; margin-bottom: 2px; }
  table { width: 100%; border-collapse: collapse; margin: 8px 0 4px; }
  th, td { border: 1px solid #cbd5e1; padding: 6px 8px; text-align: left; }
  th { background: #f1f5f9; font-weight: 700; }
  .num { text-align: center; }
  .disclaimer { margin-top: 26px; padding-top: 8px; border-top: 1px solid #cbd5e1; color: #64748b; font-size: 9px; }
  .cat { display: inline-block; margin: 2px 6px 2px 0; font-size: 10px; }
  .rec { background: #f8fafc; border-left: 3px solid #ff6b35; padding: 6px 10px; margin: 6px 0; }
  .summary-line { background: #f8fafc; padding: 8px 10px; border-radius: 4px; }
</style></head><body>
  <div class="brand">🔒 KLARIM SECURITY GATE</div>
  <h1>{{ title }}</h1>
  <p class="meta">Gerado em: {{ generated_at }}</p>
  {% if enterprise_name %}<p class="meta">Empresa: {{ enterprise_name }}{% if cnpj %} (CNPJ: {{ cnpj }}){% endif %}</p>{% endif %}

  {% if vendors|length > 1 %}
  <h2>Resumo Executivo</h2>
  <p class="summary-line">{{ vendors|length }} fornecedores avaliados —
    {{ counts.approved }} aprovado(s), {{ counts.attention }} em atenção, {{ counts.rejected }} reprovado(s).</p>
  <table>
    <thead><tr><th>Fornecedor</th><th class="num">Score</th><th class="num">Críticos</th><th class="num">Altos</th><th>Status</th></tr></thead>
    <tbody>
    {% for v in vendors %}
      <tr><td>{{ v.name }}</td><td class="num">{{ v.score }} {{ v.semaphore }}</td>
        <td class="num">{{ v.critical }}</td><td class="num">{{ v.high }}</td>
        <td>{{ v.status_label }}</td></tr>
    {% endfor %}
    </tbody>
  </table>
  {% if recommendation %}<div class="rec">{{ recommendation }}</div>{% endif %}
  {% endif %}

  {% for v in vendors %}
  <h2>{{ v.name }} — {{ v.domain }}</h2>
  <p><strong>Score:</strong> {{ v.score }}/100 {{ v.semaphore }} &nbsp;·&nbsp;
     <strong>Status:</strong> {{ v.status_label }} &nbsp;·&nbsp;
     Críticos: {{ v.critical }} · Altos: {{ v.high }} · Médios: {{ v.medium }}</p>
  {% if v.summary %}
  <p class="summary-line">
    {{ v.summary.exposed_files }} arquivo(s) de configuração exposto(s) ·
    {{ v.summary.credentials }} credencial(is) detectada(s) ·
    {{ v.summary.unauth_endpoints }} endpoint(s) sem autenticação
  </p>
  {% endif %}
  <h3>Categorias</h3>
  <div>
    {% for c in v.categories %}
      <span class="cat">{{ '✅' if c.status == 'pass' else '⚠️' if c.status == 'warning' else '❌' }} {{ c.label }}</span>
    {% endfor %}
  </div>
  <div class="rec">{{ v.recommendation }}</div>
  {% endfor %}

  <div class="disclaimer">
    Avaliação passiva automatizada. Não constitui pentest nem auditoria formal de segurança.
    Os detalhes de terceiros são apresentados de forma redigida (sem caminhos ou credenciais).<br>
    Klarim Security Gate — klarim.net/security-gate
  </div>
</body></html>""")


def _recommendation(status: str) -> str:
    if status == "rejected":
        return "Recomendação: não contratar/renovar até a correção dos findings críticos."
    if status == "attention":
        return "Recomendação: prosseguir com atenção — solicitar plano de correção ao fornecedor."
    return "Recomendação: aprovado. Postura de segurança dentro do threshold definido."


def build_vendor_context(vendors_data, *, title: str, enterprise_name=None, cnpj=None,
                         generated_at: str) -> Dict[str, Any]:
    """Monta o contexto do template a partir da lista de vendors (cada um já com score/status/
    contagens/categorias). PURO — a data é injetada (scripts não têm relógio)."""
    vendors = []
    counts = {"approved": 0, "attention": 0, "rejected": 0}
    for v in vendors_data:
        status = v.get("status") or "pending"
        counts[status] = counts.get(status, 0) + 1
        vendors.append({
            "name": v.get("name") or v.get("domain"), "domain": v.get("domain"),
            "score": v.get("score") if v.get("score") is not None else "—",
            "semaphore": _SEMA.get(status, _sema_for_score(v.get("score"))),
            "status_label": _STATUS_LABEL.get(status, status),
            "critical": v.get("critical", 0), "high": v.get("high", 0), "medium": v.get("medium", 0),
            "summary": v.get("summary"), "categories": v.get("categories") or [],
            "recommendation": _recommendation(status),
        })
    rejected_names = [v["name"] for v in vendors if _STATUS_LABEL.get(
        next((d.get("status") for d in vendors_data if (d.get("name") or d.get("domain")) == v["name"]), "")) == "Reprovado"]
    recommendation = ""
    if len(vendors) > 1 and rejected_names:
        recommendation = "Recomendação: não contratar " + ", ".join(rejected_names) + \
                         " até a correção dos findings críticos."
    return {"title": title, "enterprise_name": enterprise_name, "cnpj": cnpj,
            "generated_at": generated_at, "vendors": vendors, "counts": counts,
            "recommendation": recommendation}


def build_vendor_report_html(context: Dict[str, Any]) -> str:
    return _TMPL.render(**context)


def _render_pdf(html_str: str) -> bytes:
    from weasyprint import HTML
    return HTML(string=html_str).write_pdf()


async def generate_vendor_report_pdf(context: Dict[str, Any]) -> bytes:
    return await asyncio.to_thread(_render_pdf, build_vendor_report_html(context))
