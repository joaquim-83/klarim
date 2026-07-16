"""KL-44 P3 — enriquecimento de FALHAS de um `checks_json` (evidência + impacto +
correção + OWASP/CWE/LGPD), ordenadas por severidade. Compartilhado pelo laudo público
(`api.main`) e pelo bulletin worker. Import do reporter é **guardado** (WeasyPrint pode
faltar no container → degrada sem impacto/correção, nunca levanta)."""

from __future__ import annotations

from typing import List, Optional

_SEVERITY_ORDER = {"CRITICA": 0, "ALTA": 1, "MEDIA": 2, "BAIXA": 3}


def _technical() -> dict:
    try:
        from reporter.generator import TECHNICAL
        return TECHNICAL
    except Exception:  # noqa: BLE001 - sem libs nativas → sem impacto/correção
        return {}


def enrich_fails(checks_json: Optional[list]) -> List[dict]:
    """FALHAS enriquecidas e ordenadas por severidade (CRÍTICA→BAIXA). Os `checks_json`
    já carregam `evidence`/`owasp`/`cwe`/`lgpd` (carimbados pelo runner, KL-34/35);
    `impact`/`fix` vêm do TECHNICAL do reporter."""
    from scanner.checks.classifications import classify
    tech = _technical()
    fails = []
    for c in (checks_json or []):
        if c.get("status") != "FAIL":
            continue
        cid = c.get("check_id")
        t = tech.get(cid, {})
        cc = classify(cid)
        fails.append({
            "check_id": cid, "name": c.get("name"), "severity": c.get("severity"),
            "evidence": c.get("evidence"), "impact": t.get("impact"), "fix": t.get("fix"),
            "fix_code": t.get("fix_code"),
            "owasp": c.get("owasp") or cc.owasp, "cwe": c.get("cwe") or cc.cwe,
            "lgpd": c.get("lgpd") or cc.lgpd,
        })
    fails.sort(key=lambda f: _SEVERITY_ORDER.get((f.get("severity") or "").upper(), 9))
    return fails
