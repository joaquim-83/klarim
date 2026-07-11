"""Testes da classificação OWASP/CWE/LGPD (KL-34/35). Offline, sem rede."""

from __future__ import annotations

import asyncio

from scanner.checks import ALL_CHECKS, CHECK_META
from scanner.checks.base import CheckResult, Status, Severity
from scanner.checks.classifications import (
    CLASSIFICATIONS, classify, compliance_summary, owasp_parts, lgpd_articles,
    COMPLIANCE_DISCLAIMER,
)


# --- cobertura do mapa ----------------------------------------------------- #

def test_every_check_is_mapped():
    suite = {m["check_id"] for m in CHECK_META}
    mapped = set(CLASSIFICATIONS)
    assert suite == mapped, f"faltando={suite - mapped} extra={mapped - suite}"
    assert len(mapped) == 44


def test_no_orphan_classifications():
    # Toda classificação corresponde a um check real da suíte.
    suite = {cid for cid, _fn in ALL_CHECKS}
    assert set(CLASSIFICATIONS) <= suite


def test_specific_mappings():
    assert classify("check_05_csp") == ("A05:2025 Security Misconfiguration", "CWE-693", "Art. 46")
    assert classify("check_01_https").owasp == "A02:2025 Cryptographic Failures"
    assert classify("check_28_hibp").lgpd == "Art. 46, Art. 48"
    assert classify("check_29_safe_browsing").owasp == "A09:2025 Security Logging and Monitoring Failures"
    # Checks sem LGPD aplicável (12, 20, 26) => None.
    assert classify("check_12_metatags").lgpd is None
    assert classify("check_20_info_disclosure").lgpd is None
    assert classify("check_26_subdomains").lgpd is None
    # check_id desconhecido => tudo None.
    assert classify("check_zzz") == (None, None, None)


# --- CheckResult: campos opcionais + retrocompatibilidade ------------------ #

def test_checkresult_fields_default_none():
    r = CheckResult("x", Status.PASS, Severity.BAIXA)
    assert r.owasp is None and r.cwe is None and r.lgpd is None


def test_checkresult_roundtrip_preserves_classification():
    r = CheckResult("CSP", Status.FAIL, Severity.ALTA, "no csp",
                    check_id="check_05_csp", owasp="A05:2025 Security Misconfiguration",
                    cwe="CWE-693", lgpd="Art. 46")
    d = r.to_dict()
    assert d["owasp"] == "A05:2025 Security Misconfiguration" and d["cwe"] == "CWE-693"
    r2 = CheckResult.from_dict(d)
    assert (r2.owasp, r2.cwe, r2.lgpd) == (r.owasp, r.cwe, r.lgpd)


def test_from_dict_without_new_fields_is_backward_compatible():
    # JSON de um scan antigo (sem os campos) não quebra e vira None.
    r = CheckResult.from_dict({"name": "x", "status": "PASS", "severity": "BAIXA"})
    assert r.owasp is None and r.cwe is None and r.lgpd is None


# --- helpers --------------------------------------------------------------- #

def test_owasp_parts_and_articles():
    assert owasp_parts("A05:2025 Security Misconfiguration") == ("A05", "Security Misconfiguration")
    assert lgpd_articles("Art. 46, Art. 48") == ["Art. 46", "Art. 48"]
    assert lgpd_articles(None) == []


# --- sumário de conformidade ----------------------------------------------- #

def test_compliance_summary_counts_only_fails():
    results = [
        {"check_id": "check_05_csp", "status": "FAIL"},          # A05 / Art.46
        {"check_id": "check_06_xfo", "status": "FAIL"},          # A05 / Art.46
        {"check_id": "check_10_sensitive", "status": "FAIL"},    # A01 / Art.46+48
        {"check_id": "check_01_https", "status": "PASS"},        # ignorado
        {"check_id": "check_12_metatags", "status": "FAIL"},     # A05 / sem LGPD
    ]
    c = compliance_summary(results)
    owasp = {row["code"]: row["count"] for row in c["owasp"]}
    assert owasp == {"A01": 1, "A05": 3}
    lgpd = {row["article"]: row["count"] for row in c["lgpd"]}
    assert lgpd == {"Art. 46": 3, "Art. 48": 1}
    assert c["has_data"] is True
    assert c["disclaimer"] == COMPLIANCE_DISCLAIMER
    assert "não constitui auditoria" in c["disclaimer"]


def test_compliance_summary_empty_when_no_fails():
    c = compliance_summary([{"check_id": "check_01_https", "status": "PASS"}])
    assert c["owasp"] == [] and c["lgpd"] == [] and c["has_data"] is False


# --- integração: o runner carimba a classificação -------------------------- #

def test_runner_stamps_classification(monkeypatch):
    import scanner.runner as runner

    async def fake_csp(url):  # não faz rede
        return CheckResult("CSP", Status.FAIL, Severity.ALTA, "sem CSP")

    monkeypatch.setattr(runner, "ALL_CHECKS", [("check_05_csp", fake_csp)])
    report = asyncio.run(runner.run_scan("https://x.com.br", full=True))
    r = report.results[0]
    assert r.check_id == "check_05_csp"
    assert r.owasp == "A05:2025 Security Misconfiguration"
    assert r.cwe == "CWE-693" and r.lgpd == "Art. 46"
    # serializado (o que vai para o cache/banco/API)
    assert report.to_dict()["results"][0]["owasp"] == r.owasp
