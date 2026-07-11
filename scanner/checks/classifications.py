"""Classificação OWASP Top 10 2025 + CWE + LGPD de cada check (KL-34/35).

Isto é **metadata** sobre os checks existentes — não muda a lógica de scan nem o
score. Dá peso institucional ao relatório técnico: um auditor, advogado ou
seguradora reconhece OWASP/CWE/LGPD.

**Fonte da verdade única.** A tabela abaixo (``CLASSIFICATIONS``, keyed por
``check_id``) é a definição canônica. O ``runner`` carimba ``owasp``/``cwe``/``lgpd``
em cada :class:`CheckResult` pelo ``check_id`` — assim não é preciso editar as ~100
``return CheckResult(...)`` espalhadas pelos 29 checks (cada um tem 2-6 returns), e
até o resultado de fallback (quando um check levanta exceção) fica classificado.

**Identidade dual preservada:** o relatório técnico (PDF + resultado web completo) e a
API expõem estes campos; o relatório executivo **nunca** menciona OWASP/CWE/LGPD.
"""

from __future__ import annotations

from typing import Dict, List, NamedTuple, Optional

from .base import Status


class Classification(NamedTuple):
    """OWASP Top 10 2025 (rótulo completo), CWE (código) e LGPD (artigo(s))."""

    owasp: Optional[str]
    cwe: Optional[str]
    lgpd: Optional[str]


# --------------------------------------------------------------------------- #
# Rótulos OWASP Top 10 2025 (reusados entre os checks)
# --------------------------------------------------------------------------- #

_A01 = "A01:2025 Broken Access Control"
_A02 = "A02:2025 Cryptographic Failures"
_A05 = "A05:2025 Security Misconfiguration"
_A07 = "A07:2025 Identification and Authentication Failures"
_A08 = "A08:2025 Software and Data Integrity Failures"
_A09 = "A09:2025 Security Logging and Monitoring Failures"


# --------------------------------------------------------------------------- #
# Mapeamento definitivo dos 29 checks (copiado do card KL-34/35)
# --------------------------------------------------------------------------- #

CLASSIFICATIONS: Dict[str, Classification] = {
    # Transporte (01-04)
    "check_01_https":            Classification(_A02, "CWE-319", "Art. 46"),
    "check_02_hsts":             Classification(_A05, "CWE-523", "Art. 46"),
    "check_03_ssl":              Classification(_A02, "CWE-295", "Art. 46"),
    "check_04_tls":              Classification(_A02, "CWE-326", "Art. 46"),
    # Headers (05-08)
    "check_05_csp":              Classification(_A05, "CWE-693", "Art. 46"),
    "check_06_xfo":              Classification(_A05, "CWE-1021", "Art. 46"),
    "check_07_xcto":             Classification(_A05, "CWE-16", "Art. 46"),
    "check_08_server":           Classification(_A05, "CWE-200", "Art. 46"),
    # Supply chain (09-15)
    "check_09_sourcemaps":       Classification(_A05, "CWE-540", "Art. 46"),
    "check_10_sensitive":        Classification(_A01, "CWE-538", "Art. 46, Art. 48"),
    "check_11_dirlist":          Classification(_A01, "CWE-548", "Art. 46"),
    "check_12_metatags":         Classification(_A05, "CWE-200", None),
    "check_13_sri":              Classification(_A08, "CWE-353", "Art. 46"),
    "check_14_risky_sources":    Classification(_A08, "CWE-829", "Art. 46"),
    "check_15_external_domains": Classification(_A08, "CWE-829", "Art. 46"),
    # Web (16-20)
    "check_16_api_docs":         Classification(_A01, "CWE-538", "Art. 46"),
    "check_17_cookies":          Classification(_A05, "CWE-614", "Art. 46"),
    "check_18_cors":             Classification(_A05, "CWE-942", "Art. 46"),
    "check_19_redirect_domain":  Classification(_A01, "CWE-601", "Art. 46"),
    "check_20_info_disclosure":  Classification(_A05, "CWE-200", None),
    # DNS/Email (21-23)
    "check_21_spf":              Classification(_A07, "CWE-290", "Art. 46"),
    "check_22_dkim":             Classification(_A07, "CWE-290", "Art. 46"),
    "check_23_dmarc":            Classification(_A07, "CWE-290", "Art. 46"),
    # Conteúdo + Infra + OSINT (24-29)
    "check_24_mixed_content":    Classification(_A02, "CWE-319", "Art. 46"),
    "check_25_form_security":    Classification(_A02, "CWE-319", "Art. 46, Art. 11"),
    "check_26_subdomains":       Classification(_A05, "CWE-200", None),
    "check_27_dangling_cname":   Classification(_A05, "CWE-672", "Art. 46"),
    "check_28_hibp":             Classification(_A07, "CWE-521", "Art. 46, Art. 48"),
    "check_29_safe_browsing":    Classification(_A09, "CWE-693", "Art. 46, Art. 48"),
}

_EMPTY = Classification(None, None, None)


# Rótulo humano de cada artigo da LGPD (para o relatório; a API guarda só "Art. 46").
LGPD_LABELS: Dict[str, str] = {
    "Art. 11": "Art. 11 (dados pessoais sensíveis)",
    "Art. 46": "Art. 46 (medidas de segurança)",
    "Art. 48": "Art. 48 (comunicação de incidente)",
}

# Disclaimer obrigatório no sumário de conformidade (o relatório não é auditoria).
COMPLIANCE_DISCLAIMER = (
    "Este relatório não constitui auditoria de segurança nem avaliação de "
    "conformidade legal. Consulte um profissional especializado para avaliação "
    "formal."
)


def classify(check_id: str) -> Classification:
    """Retorna a classificação de um ``check_id`` (tri-``None`` se desconhecido)."""
    return CLASSIFICATIONS.get(check_id, _EMPTY)


def owasp_parts(label: str) -> tuple[str, str]:
    """``"A02:2025 Cryptographic Failures"`` -> ``("A02", "Cryptographic Failures")``."""
    code, _, rest = (label or "").partition(":")
    _year, _, name = rest.partition(" ")
    return code.strip(), name.strip()


def lgpd_articles(value: Optional[str]) -> List[str]:
    """Divide ``"Art. 46, Art. 48"`` em ``["Art. 46", "Art. 48"]`` (vazio se ``None``)."""
    if not value:
        return []
    return [a.strip() for a in value.split(",") if a.strip()]


def _cid_of(item) -> str:
    if isinstance(item, dict):
        return item.get("check_id", "") or ""
    return getattr(item, "check_id", "") or ""


def _status_of(item) -> str:
    if isinstance(item, dict):
        return item.get("status", "") or ""
    return getattr(item, "status", "") or ""


def compliance_summary(results) -> Dict[str, object]:
    """Sumário de conformidade a partir das **FALHAS** de um scan.

    Conta os findings (checks com status FAIL) por categoria OWASP e por artigo da
    LGPD. Aceita ``CheckResult`` ou dicts (``results`` do ``to_dict``). A fonte da
    contagem é sempre o mapa ``CLASSIFICATIONS`` (pelo ``check_id``), robusto mesmo
    para scans antigos cujo JSON não tinha os campos.
    """
    owasp_counts: Dict[str, int] = {}
    lgpd_counts: Dict[str, int] = {}

    for r in results or []:
        if _status_of(r) != Status.FAIL:
            continue
        c = classify(_cid_of(r))
        if c.owasp:
            owasp_counts[c.owasp] = owasp_counts.get(c.owasp, 0) + 1
        for art in lgpd_articles(c.lgpd):
            lgpd_counts[art] = lgpd_counts.get(art, 0) + 1

    owasp_rows = [
        {"code": owasp_parts(label)[0], "name": owasp_parts(label)[1],
         "label": label, "count": n}
        for label, n in owasp_counts.items()
    ]
    owasp_rows.sort(key=lambda x: x["code"])

    lgpd_rows = [
        {"article": art, "label": LGPD_LABELS.get(art, art), "count": n}
        for art, n in lgpd_counts.items()
    ]
    lgpd_rows.sort(key=lambda x: x["article"])

    return {
        "owasp": owasp_rows,
        "lgpd": lgpd_rows,
        "has_data": bool(owasp_rows or lgpd_rows),
        "disclaimer": COMPLIANCE_DISCLAIMER,
    }
