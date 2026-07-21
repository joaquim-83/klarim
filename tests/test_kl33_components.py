"""Testes do check_30 — componentes vulneráveis + base de CVE (KL-33). Offline."""

from __future__ import annotations

import asyncio
import os
import time

import httpx
import pytest

import scanner.cve_db as cve_db
from scanner.cve_db import CVEDatabase, severity_from_cves, max_cvss, get_cve_db
from scanner.checks import check_30_vulnerable_components as chk
from scanner.checks.base import Status, Severity
from scanner.checks.classifications import classify


# Base Retire.js sintética (formato v1: below/atOrAbove/identifiers.CVE).
MOCK_RETIRE = {
    "jquery": {
        "bowername": "jquery", "npmname": "jquery",
        "vulnerabilities": [
            {"below": "3.5.0", "severity": "medium",
             "identifiers": {"summary": "XSS via HTML injection",
                             "CVE": ["CVE-2020-11022", "CVE-2020-11023"]}},
            {"atOrAbove": "1.2.1", "below": "1.9.0", "severity": "medium",
             "identifiers": {"summary": "XSS load()", "CVE": ["CVE-2020-7656"]}},
        ],
    },
    "bootstrap": {
        "vulnerabilities": [
            {"atOrAbove": "3.0.0", "below": "3.4.0", "severity": "medium",
             "identifiers": {"summary": "XSS in data-target", "CVE": ["CVE-2018-14040"]}},
        ],
    },
}


# Padding inerte (>100 chars, sem strings de versão de biblioteca) para que o
# ``content_guard`` (KL-94) não trate estes fixtures curtos como "resposta
# vazia/mínima". Não introduz nenhuma detecção de versão nem muda a lógica.
_BODY_PAD = (
    "<p>Conteudo institucional de exemplo para uma pagina real com texto "
    "suficiente para representar um site legitimo em producao com varios "
    "paragrafos sobre a empresa.</p>"
)


def _resp(html: str, headers=None, url="https://x.com.br/") -> httpx.Response:
    return httpx.Response(200, headers=headers or {}, text=html + _BODY_PAD,
                          request=httpx.Request("GET", url))


def _fake_fetch(html, headers=None):
    async def _f(url, method="GET", **kw):
        return _resp(html, headers=headers, url=url)
    return _f


def _mock_db() -> CVEDatabase:
    db = get_cve_db()
    db.load_from_dict(MOCK_RETIRE)  # evita rede: ensure_loaded retorna cedo
    return db


# --- 1-4: detecção de versão (pura) --------------------------------------- #

def test_detect_jquery_from_script_src():
    d = chk.detect_versions("", {}, ["https://cdn.x.com/jquery-2.1.4.min.js"])
    assert {"jquery": "2.1.4"} == {c["library"]: c["version"] for c in d}
    assert d[0]["source"] == "script_src" and d[0]["kind"] == "js"


def test_detect_wordpress_from_meta_generator():
    html = '<meta name="generator" content="WordPress 5.8.1" />'
    d = chk.detect_versions(html, {}, [])
    assert any(c["library"] == "wordpress" and c["version"] == "5.8.1" for c in d)


def test_detect_wordpress_from_ver_query():
    html = '<script src="/wp-includes/js/wp-emoji-release.min.js?ver=6.4.2"></script>'
    d = chk.detect_versions(html, {}, [])
    assert any(c["library"] == "wordpress" and c["version"] == "6.4.2" for c in d)


def test_detect_bootstrap_and_headers():
    d = chk.detect_versions(
        "", {"X-Powered-By": "PHP/8.1.2", "Server": "nginx/1.22.1"},
        ["https://cdn.x.com/bootstrap-3.3.7.min.js"])
    got = {c["library"]: c["version"] for c in d}
    assert got["bootstrap"] == "3.3.7" and got["php"] == "8.1.2" and got["nginx"] == "1.22.1"


def test_detect_inline_version_banner():
    d = chk.detect_versions("<!-- jQuery v2.2.4 -->", {}, [])
    assert any(c["library"] == "jquery" and c["version"] == "2.2.4"
               and c["source"] == "inline" for c in d)


# --- 5-6: lookup Retire.js -------------------------------------------------- #

def test_retirejs_lookup_vulnerable():
    db = CVEDatabase()
    db.load_from_dict(MOCK_RETIRE)
    cves = db.lookup_js("jquery", "2.1.4")
    ids = {c["id"] for c in cves}
    assert ids == {"CVE-2020-11022", "CVE-2020-11023"}  # o vuln <1.9.0 não casa
    assert db.recommended_upgrade("jquery", "2.1.4") == "3.5.0"


def test_retirejs_lookup_safe_version():
    db = CVEDatabase()
    db.load_from_dict(MOCK_RETIRE)
    assert db.lookup_js("jquery", "3.7.1") == []
    assert db.covers("jquery") is True and db.covers("desconhecida") is False


# --- 7: severidade dinâmica ------------------------------------------------ #

def test_dynamic_severity_from_cvss():
    assert severity_from_cves([{"cvss": 9.8, "severity": ""}]) == Severity.CRITICA
    assert severity_from_cves([{"cvss": 7.2, "severity": ""}]) == Severity.ALTA
    assert severity_from_cves([{"cvss": 5.0, "severity": ""}]) == Severity.MEDIA
    # sem CVSS: cai para a label textual do Retire.js
    assert severity_from_cves([{"cvss": None, "severity": "high"}]) == Severity.ALTA
    assert max_cvss([{"cvss": None}, {"cvss": 6.1}]) == 6.1


# --- 8, 11: INCONCLUSO ----------------------------------------------------- #

def test_no_version_detected_is_inconclusive(monkeypatch):
    _mock_db()
    monkeypatch.setattr(chk, "fetch", _fake_fetch("<html><body>sem scripts</body></html>"))
    r = asyncio.run(chk.check("https://x.com.br"))
    assert r.status == Status.INCONCLUSO


def test_unassessable_only_cms_is_inconclusive(monkeypatch):
    # WordPress detectado mas Retire.js não cobre CMS e NVD está off => INCONCLUSO.
    _mock_db()
    monkeypatch.setattr(cve_db, "NVD_ENABLED", False)
    monkeypatch.setattr(chk, "NVD_ENABLED", False)
    html = '<meta name="generator" content="WordPress 5.8.1">'
    monkeypatch.setattr(chk, "fetch", _fake_fetch(html))
    r = asyncio.run(chk.check("https://x.com.br"))
    assert r.status == Status.INCONCLUSO
    assert any(c["library"] == "wordpress" for c in r.details["components"])


# --- 9, 10: cache TTL + fallback ------------------------------------------- #

def test_cache_expired_triggers_redownload(tmp_path, monkeypatch):
    cache = tmp_path / "retire.json"
    cache.write_text('{"old": {}}')
    old = time.time() - 25 * 3600
    os.utime(cache, (old, old))  # >24h => expirado
    db = CVEDatabase(cache_path=str(cache), ttl_seconds=24 * 3600)

    async def _dl(self):
        return {"jquery": {"vulnerabilities": []}}
    monkeypatch.setattr(CVEDatabase, "_download", _dl)
    data = asyncio.run(db.ensure_loaded())
    assert "jquery" in data and "old" not in data  # re-baixou


def test_download_fail_uses_previous_cache(tmp_path, monkeypatch):
    cache = tmp_path / "retire.json"
    cache.write_text('{"jquery": {"vulnerabilities": []}}')
    old = time.time() - 25 * 3600
    os.utime(cache, (old, old))  # expirado => tenta baixar
    db = CVEDatabase(cache_path=str(cache))

    async def _dl(self):  # download falha
        return None
    monkeypatch.setattr(CVEDatabase, "_download", _dl)
    data = asyncio.run(db.ensure_loaded())
    assert "jquery" in data  # usou o cache velho (fail-open)


def test_no_cache_no_network_is_failopen(tmp_path, monkeypatch):
    db = CVEDatabase(cache_path=str(tmp_path / "missing.json"))

    async def _dl(self):
        return None
    monkeypatch.setattr(CVEDatabase, "_download", _dl)
    assert asyncio.run(db.ensure_loaded()) == {}


# --- 12, 14: CheckResult com CVEs (integração) ----------------------------- #

def test_check_fail_with_cve_details(monkeypatch):
    _mock_db()
    html = '<script src="/assets/jquery-2.1.4.min.js"></script>'
    monkeypatch.setattr(chk, "fetch", _fake_fetch(html))
    r = asyncio.run(chk.check("https://x.com.br"))
    assert r.status == Status.FAIL
    assert r.severity == Severity.MEDIA          # medium (sem CVSS) -> MEDIA
    comp = r.details["components"][0]
    assert comp["library"] == "jquery" and comp["version"] == "2.1.4"
    assert len(comp["cves"]) == 2 and "3.5.0" in comp["recommendation"]
    assert r.details["total_cves"] == 2
    assert "jQuery 2.1.4" in r.evidence and "CVE" in r.evidence


def test_check_pass_when_component_up_to_date(monkeypatch):
    _mock_db()
    html = '<script src="/assets/jquery-3.7.1.min.js"></script>'
    monkeypatch.setattr(chk, "fetch", _fake_fetch(html))
    r = asyncio.run(chk.check("https://x.com.br"))
    assert r.status == Status.PASS


# --- 13: classificação OWASP ----------------------------------------------- #

def test_check30_classification():
    assert chk.ORDER == 30 and chk.CHECK_ID == "check_30_vulnerable_components"
    assert (chk.OWASP, chk.CWE, chk.LGPD) == (
        "A06:2025 Vulnerable and Outdated Components", "CWE-1104", "Art. 46")
    assert classify("check_30_vulnerable_components") == (
        "A06:2025 Vulnerable and Outdated Components", "CWE-1104", "Art. 46")
