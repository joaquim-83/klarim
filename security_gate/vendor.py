"""KL-152 P3 — lógica PURA da avaliação de fornecedores (Enterprise due diligence).

Sem FastAPI/DB aqui (importável pelo endpoint E pelo worker de monitoramento). Transforma um
`GateReport` num payload de vendor: status (aprovado/atenção/reprovado), contagens amigáveis
(sem paths/credenciais — redação do KL-151 P4) e categorias com status.
"""
from __future__ import annotations

import copy
from typing import Dict, List

from security_gate.models import Status

# --- status do fornecedor --------------------------------------------------- #

STATUS_APPROVED = "approved"
STATUS_ATTENTION = "attention"
STATUS_REJECTED = "rejected"


def calculate_vendor_status(score, critical_count: int, threshold: int, max_critical: int) -> str:
    """Deriva o status de um fornecedor a partir do score e do nº de findings críticos.

    - críticos acima do permitido  → reprovado (independe do score)
    - score >= threshold           → aprovado
    - score >= threshold - 20      → atenção
    - senão                        → reprovado
    """
    s = int(score or 0)
    if int(critical_count or 0) > int(max_critical or 0):
        return STATUS_REJECTED
    if s >= int(threshold):
        return STATUS_APPROVED
    if s >= int(threshold) - 20:
        return STATUS_ATTENTION
    return STATUS_REJECTED


STATUS_LABEL = {
    STATUS_APPROVED: "Aprovado",
    STATUS_ATTENTION: "Atenção",
    STATUS_REJECTED: "Reprovado",
    "pending": "Pendente",
}
SEMAPHORE = {STATUS_APPROVED: "🟢", STATUS_ATTENTION: "🟡", STATUS_REJECTED: "🔴", "pending": "⚪"}


# --- serialização + redação ------------------------------------------------- #

def serialize_result(r) -> dict:
    return {"check": r.check, "category": r.category, "path": r.path,
            "status": r.status.value, "severity": r.severity.value,
            "detail": r.detail, "http_status": r.http_status}


def redact_third_party(results: List[dict]) -> None:
    """Remove path/valor de credencial e caminho de recurso exposto (mutação in-place). Só a
    CATEGORIA + severidade do risco sobrevive — nunca o segredo/caminho do terceiro."""
    for r in results:
        if r.get("category") == "credentials":
            r["detail"] = "Credencial detectada (redigido)"
            r["path"] = "[redacted]"
        elif r.get("category") == "exposure" and r.get("status") == Status.FAIL.value:
            r["detail"] = f"Recurso exposto detectado ({r.get('severity')})"
            r["path"] = "[redacted]"
        elif r.get("category") == "api" and r.get("status") == Status.FAIL.value:
            r["detail"] = "Endpoint sem autenticação detectado (redigido)"
            r["path"] = "[redacted]"


# --- contagens amigáveis + categorias --------------------------------------- #

def _fails(results, category) -> int:
    return sum(1 for r in results if r.get("category") == category and r.get("status") == Status.FAIL.value)


def vendor_summary(raw_results: List[dict]) -> Dict[str, int]:
    """Contagens legíveis a partir dos results BRUTOS (antes da redação) — quantos, nunca quais."""
    return {
        "exposed_files": _fails(raw_results, "exposure"),
        "credentials": _fails(raw_results, "credentials"),
        "unauth_endpoints": _fails(raw_results, "api"),
    }


_CATEGORY_LABEL = {
    "headers": "Cabeçalhos de segurança", "ssl": "SSL/TLS", "exposure": "Arquivos expostos",
    "credentials": "Credenciais", "api": "Endpoints/API", "cors": "CORS", "cookies": "Cookies",
    "https_redirect": "Redirect HTTPS", "open_redirect": "Open redirect", "jwt": "JWT",
    "error_disclosure": "Vazamento de erro", "form_security": "Formulários", "dns": "DNS",
    "dependencies": "Dependências", "tls_ciphers": "Cifras TLS", "subdomain": "Subdomínios",
    "infrastructure": "Infraestrutura", "rate_limit": "Rate limit",
}


def vendor_categories(results: List[dict]) -> List[dict]:
    """Agrupa os checks por categoria com um status agregado (fail > warning > pass). Sem paths."""
    order, agg = [], {}
    for r in results:
        cat = r.get("category") or "outros"
        if cat not in agg:
            agg[cat] = {"category": cat, "label": _CATEGORY_LABEL.get(cat, cat), "status": "pass"}
            order.append(cat)
        st = r.get("status")
        cur = agg[cat]["status"]
        if st == Status.FAIL.value:
            agg[cat]["status"] = "fail"
        elif st == Status.ERROR.value and cur != "fail":
            agg[cat]["status"] = "warning"
    return [agg[c] for c in order]


def build_vendor_scan_payload(report, approval_threshold: int, critical_threshold: int) -> dict:
    """`GateReport` → payload do vendor (status + contagens + categorias + results REDIGIDOS)."""
    raw = [serialize_result(r) for r in report.results]
    summary = vendor_summary(raw)
    categories = vendor_categories(raw)
    redacted = copy.deepcopy(raw)
    redact_third_party(redacted)
    status = calculate_vendor_status(report.score, report.critical_count,
                                     approval_threshold, critical_threshold)
    return {
        "score": report.score, "status": status, "passed": status == STATUS_APPROVED,
        "critical": report.critical_count, "high": report.high_count, "medium": report.medium_count,
        "duration_ms": report.duration_ms, "results": redacted, "summary": summary,
        "categories": categories,
    }
