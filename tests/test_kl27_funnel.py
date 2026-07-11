"""Testes do funil KL-27 (offline): tiering free/full, cache por tier, preço único,
payload do resultado gratuito, token de re-verificação e rótulo de evolução."""

from __future__ import annotations

import asyncio

import pytest

import scanner
from scanner.checks import discover_checks, FREE_CHECK_MAX_ORDER
from scanner.cache import ScanCache
from scanner.runner import ScanReport
from scanner.scoring import compute_score
from scanner.checks.base import CheckResult, Status, Severity


# --- tiering --------------------------------------------------------------- #

def test_tier_split_counts():
    # 30 checks após o KL-33 (check_30); 15 gratuitos, 15 pagos (ORDER>15).
    assert len(scanner.ALL_CHECKS) == 30
    assert len(scanner.FREE_CHECKS) == 15
    assert FREE_CHECK_MAX_ORDER == 15
    free_ids = {cid for cid, _ in scanner.FREE_CHECKS}
    all_ids = {cid for cid, _ in scanner.ALL_CHECKS}
    assert free_ids <= all_ids                     # gratuito é subconjunto
    assert len(all_ids - free_ids) == 15           # os pagos


def test_discover_checks_free_filters_by_order():
    assert len(discover_checks(full=False)) == 15
    assert len(discover_checks(full=True)) == 30


def test_check_meta_marks_paid():
    meta = scanner.CHECK_META
    assert len(meta) == 30
    assert sum(1 for m in meta if m["paid"]) == 15
    assert sum(1 for m in meta if not m["paid"]) == 15
    # os 15 primeiros (ORDER<=15) não são pagos; os demais são
    for m in meta:
        assert m["paid"] == (m["order"] > 15)


# --- cache por tier -------------------------------------------------------- #

def test_cache_keys_are_tier_namespaced():
    c = ScanCache(redis_client=None)
    kf = c._key("https://x.com.br", full=False)
    kp = c._key("https://x.com.br", full=True)
    assert kf != kp
    assert kf.startswith("scan:free:") and kp.startswith("scan:full:")
    # ambos casam o padrão de flush operacional na VM
    assert kf.startswith("scan:") and kp.startswith("scan:")


# --- preço único ----------------------------------------------------------- #

def test_single_price():
    from payments import PRICE_AMOUNT, PRICE_DISPLAY
    assert PRICE_AMOUNT == 1900
    assert PRICE_DISPLAY == "R$ 19"


# --- payload do resultado gratuito ----------------------------------------- #

def _free_report(fails=("check_02_hsts",)):
    results = []
    for cid, _fn in scanner.FREE_CHECKS:
        st = Status.FAIL if cid in fails else Status.PASS
        sv = Severity.ALTA if st == Status.FAIL else Severity.MEDIA
        r = CheckResult(name=cid, status=st, severity=sv, evidence="x")
        r.check_id = cid
        results.append(r)
    return ScanReport(url="https://www.example.com", started_at="t", finished_at="t",
                      duration_s=1.0, results=results, score=compute_score(results))


def test_summary_payload_free_locks_paid_and_hides_detail():
    import api.main as m
    p = m._summary_payload(_free_report(), full=False)
    assert len(p["free_checks"]) == 15 and len(p["paid_checks"]) == 15
    assert all(c["status"] == "locked" for c in p["paid_checks"])
    assert {c["status"] for c in p["free_checks"]} <= {"PASS", "FAIL", "INCONCLUSO"}
    # sem detalhes de risco no gratuito
    assert "risk_messages" not in p
    for c in p["free_checks"] + p["paid_checks"]:
        assert set(c.keys()) == {"check_id", "name", "status"}
    # preço único
    assert p["price"] == 1900 and p["price_display"] == "R$ 19"
    assert p["fail_count"] == 1 and p["is_full"] is False


def test_summary_payload_full_reveals_paid_status():
    import api.main as m
    # relatório completo (29) → admin vê status real dos pagos
    results = []
    for cid, _fn in scanner.ALL_CHECKS:
        r = CheckResult(name=cid, status=Status.PASS, severity=Severity.MEDIA, evidence="ok")
        r.check_id = cid
        results.append(r)
    rep = ScanReport(url="https://x", started_at="t", finished_at="t", duration_s=1.0,
                     results=results, score=compute_score(results))
    p = m._summary_payload(rep, full=True)
    assert all(c["status"] == "PASS" for c in p["paid_checks"])
    assert p["is_full"] is True


# --- token de re-verificação (full) ---------------------------------------- #

def test_scan_token_carries_full_claim(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-secret-kl27")
    import api.main as m
    tok_free = m._make_scan_token("a@b.com", "https://x.com.br", full=False)
    tok_full = m._make_scan_token("a@b.com", "https://x.com.br", full=True)
    assert m._verify_scan_token(tok_free)["full"] is False
    assert m._verify_scan_token(tok_full)["full"] is True
    # bypass de PDF só com token full válido para a URL
    assert m._has_full_scan_token("https://x.com.br", tok_full) is True
    assert m._has_full_scan_token("https://x.com.br", tok_free) is False
    assert m._has_full_scan_token("https://outra.com.br", tok_full) is False


def test_evolution_label():
    import api.main as m
    assert m._evolution_label(70, 90) == "improved"
    assert m._evolution_label(90, 70) == "worsened"
    assert m._evolution_label(80, 80) == "unchanged"
    assert m._evolution_label(None, 90) == "first_rescan"


# --- Fix pós-KL-27: detalhe no resultado completo + modo demo -------------- #

def _full_report(fail_ids=("check_23_dmarc",)):
    results = []
    for cid, _fn in scanner.ALL_CHECKS:
        st = Status.FAIL if cid in fail_ids else Status.PASS
        sv = Severity.ALTA if st == Status.FAIL else Severity.MEDIA
        r = CheckResult(name=cid, status=st, severity=sv,
                        evidence=("falhou" if st == Status.FAIL else "ok"))
        r.check_id = cid
        results.append(r)
    return ScanReport(url="https://x", started_at="t", finished_at="t", duration_s=1.0,
                      results=results, score=compute_score(results))


def test_full_payload_reveals_details_on_fails():
    import api.main as m
    p = m._summary_payload(_full_report(), full=True)
    assert p["is_full"] is True
    # os 14 pagos vêm com status real (não "locked")
    assert all(c["status"] in {"PASS", "FAIL", "INCONCLUSO"} for c in p["paid_checks"])
    dmarc = next(c for c in p["paid_checks"] if c["check_id"] == "check_23_dmarc")
    assert dmarc["status"] == "FAIL"
    assert dmarc.get("evidence") and dmarc.get("impact") and dmarc.get("fix")
    # PASS não expande detalhe
    a_pass = next(c for c in p["free_checks"] if c["status"] == "PASS")
    assert "evidence" not in a_pass and "impact" not in a_pass


def test_full_payload_free_still_locks_paid():
    import api.main as m
    p = m._summary_payload(_full_report(), full=False)  # mesmo com report de 29
    assert all(c["status"] == "locked" for c in p["paid_checks"])
    assert all("evidence" not in c for c in p["free_checks"])  # sem detalhe no grátis


# --- KL-34/35: classificação OWASP/CWE/LGPD no payload completo ------------- #

def test_full_payload_includes_compliance_classification():
    import api.main as m
    p = m._summary_payload(_full_report(fail_ids=("check_23_dmarc", "check_05_csp")), full=True)
    dmarc = next(c for c in p["paid_checks"] if c["check_id"] == "check_23_dmarc")
    assert dmarc["owasp"] == "A07:2025 Identification and Authentication Failures"
    assert dmarc["cwe"] == "CWE-290" and dmarc["lgpd"] == "Art. 46"
    # FAIL em check gratuito (CSP) também traz a classificação no modo completo.
    csp = next(c for c in p["free_checks"] if c["check_id"] == "check_05_csp")
    assert csp["owasp"] == "A05:2025 Security Misconfiguration" and csp["cwe"] == "CWE-693"


def test_free_payload_omits_compliance():
    import api.main as m
    p = m._summary_payload(_full_report(fail_ids=("check_05_csp",)), full=False)
    for c in p["free_checks"] + p["paid_checks"]:
        assert "owasp" not in c and "cwe" not in c and "lgpd" not in c


def test_is_demo(monkeypatch):
    import api.main as m
    monkeypatch.setenv("DEMO_EMAIL", "demo@klarim.net")
    monkeypatch.setenv("DEMO_URL", "https://demo.klarim.net")
    assert m._is_demo(email="demo@klarim.net") is True
    assert m._is_demo(email="DEMO@Klarim.net") is True         # case-insensitive
    assert m._is_demo(url="https://demo.klarim.net/x") is True
    assert m._is_demo(email="real@cliente.com.br", url="https://cliente.com.br") is False
    monkeypatch.delenv("DEMO_EMAIL", raising=False)
    monkeypatch.delenv("DEMO_URL", raising=False)
    assert m._is_demo(email="demo@klarim.net") is False        # desligado por padrão


def test_payment_stats_excludes_demo_charges():
    from payments.store import MemoryStore
    from payments.models import Charge, PaymentStatus
    store = MemoryStore()
    asyncio.run(store.save(Charge("real_1", "https://a.com", 1900, status=PaymentStatus.PAID)))
    asyncio.run(store.save(Charge("demo_abc", "https://demo.klarim.net", 1900,
                                  status=PaymentStatus.PAID)))
    stats = asyncio.run(store.payment_stats())
    assert stats["paid_count"] == 1 and stats["revenue_cents"] == 1900  # demo não conta
    assert stats["total"] == 1
