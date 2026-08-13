"""KL-164 — DSAR/DPO em múltiplas páginas + X-XSS-Protection informativo.

Offline: as funções puras (`_dsar_signal`/`_dpo_signal`/`_privacy_candidate_urls`/
`augment_privacy_checks`) não tocam a rede; a integração de `scan_privacy` usa um
`base.fetch` fake por URL. Garante o fix E que NÃO virou pass-always."""
from __future__ import annotations

import asyncio

import pytest

import scanner.privacy_checks as pc
import scanner.checks.base as base


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# =========================================================================== #
# Sinais por página (puros)
# =========================================================================== #

def test_dsar_signal_variants():
    # link de direitos (vocabulário DSAR)
    assert pc._dsar_signal("<a href='/direitos'>Seus direitos</a>",
                           pc.extract_links("<a href='/direitos'>Seus direitos</a>"), "/x")
    # texto de direitos no corpo
    assert pc._dsar_signal("<p>Exercer seus direitos como titular</p>", [], "/privacidade")
    # e-mail de privacidade
    assert pc._dsar_signal("<a href='mailto:privacidade@x.com'>fale</a>", [], "/contato")
    # form numa página DEDICADA a direitos
    assert pc._dsar_signal("<form></form>", [], "/lgpd")


def test_dsar_signal_negative():
    # form numa página genérica (/contato) NÃO é sinal de DSAR (evita pass-always)
    assert not pc._dsar_signal("<form>contato</form>", [], "/contato")
    # página comum sem vocabulário de direitos
    assert not pc._dsar_signal("<p>Bem-vindo à loja</p>", [], "/sobre")


def test_dpo_signal():
    assert pc._dpo_signal("<p>Encarregado de Proteção de Dados: João</p>")
    assert pc._dpo_signal("Nosso DPO responde em 48h")
    assert not pc._dpo_signal("<p>Página institucional sem nada relevante</p>")


# =========================================================================== #
# Candidatos de URL (puros)
# =========================================================================== #

def test_candidate_urls_fixed_paths_and_cap():
    urls = pc._privacy_candidate_urls("https://ex.com/", [], True, True)
    assert len(urls) <= pc._PRIVACY_EXTRA_MAX
    assert "https://ex.com/privacidade" in urls  # prioridade
    assert all(u.startswith("https://ex.com/") for u in urls)


def test_candidate_urls_footer_links_same_origin_only():
    links = [("/meus-direitos", "exercer seus direitos"),
             ("https://facebook.com/x", "direitos autorais externos")]
    urls = pc._privacy_candidate_urls("https://ex.com/", links, True, False)
    assert "https://ex.com/meus-direitos" in urls          # link interno de direitos entra
    assert not any("facebook.com" in u for u in urls)       # externo NUNCA entra


def test_candidate_urls_only_needed():
    # só DPO precisa → não traz os paths exclusivos de DSAR (ex.: /direitos)
    urls = pc._privacy_candidate_urls("https://ex.com/", [], False, True)
    assert "https://ex.com/direitos" not in urls
    assert "https://ex.com/privacidade" in urls


# =========================================================================== #
# Augmentação (pura) — upgrade FAIL→PASS, nunca o contrário
# =========================================================================== #

def _failing_checks():
    return [
        {"id": "dsar_channel", "name": "Canal de direitos do titular", "status": "FAIL",
         "evidence": "x"},
        {"id": "dpo_info", "name": "Identificação do Encarregado (DPO)", "status": "FAIL",
         "evidence": "y"},
    ]


def test_augment_upgrades_from_pages():
    checks = _failing_checks()
    pc.augment_privacy_checks(checks, [
        {"path": "/privacidade", "html": "<p>Encarregado (DPO): contato</p>"},
        {"path": "/lgpd", "html": "<form>Exercer seus direitos como titular</form>"},
    ])
    by = {c["id"]: c for c in checks}
    assert by["dsar_channel"]["status"] == "PASS" and "/lgpd" in by["dsar_channel"]["evidence"]
    assert by["dpo_info"]["status"] == "PASS" and "/privacidade" in by["dpo_info"]["evidence"]


def test_augment_not_pass_always():
    checks = _failing_checks()
    pc.augment_privacy_checks(checks, [
        {"path": "/contato", "html": "<form>contato genérico</form>"},
        {"path": "/sobre", "html": "<p>Somos uma loja de sapatos.</p>"},
    ])
    by = {c["id"]: c for c in checks}
    assert by["dsar_channel"]["status"] == "FAIL"
    assert by["dpo_info"]["status"] == "FAIL"


def test_augment_never_downgrades_pass():
    checks = [{"id": "dsar_channel", "name": "x", "status": "PASS", "evidence": "ok"},
              {"id": "dpo_info", "name": "y", "status": "PASS", "evidence": "ok"}]
    pc.augment_privacy_checks(checks, [{"path": "/vazia", "html": "<p>nada</p>"}])
    assert all(c["status"] == "PASS" for c in checks)


# =========================================================================== #
# Integração scan_privacy — fetch fake por URL
# =========================================================================== #

class _Resp:
    def __init__(self, url, html, status=200):
        self.url = url
        self.text = html
        self.status_code = status
        self.headers = {"content-type": "text/html"}


def _install_fetch(monkeypatch, pages: dict):
    async def _fetch(url, method="GET", **kw):
        html, status = pages.get(url, ("<p>404</p>", 404))
        return _Resp(url, html, status)
    monkeypatch.setattr(base, "fetch", _fetch)
    monkeypatch.setattr(base, "looks_like_html", lambda r: True)


HOME = ("<html><body><h1>Hero</h1><footer>"
        "<a href='/privacidade'>Privacidade</a></footer></body></html>")


def test_scan_privacy_multipage_upgrades(monkeypatch):
    pages = {
        "https://ex.com": (HOME, 200),
        "https://ex.com/privacidade": ("<p>Encarregado (DPO) e Proteção de Dados.</p>", 200),
        "https://ex.com/lgpd": ("<form>Exercer seus direitos do titular</form>", 200),
    }
    _install_fetch(monkeypatch, pages)
    result = _run(pc.scan_privacy("https://ex.com"))
    by = {c["id"]: c for c in result["checks"]}
    assert by["dsar_channel"]["status"] == "PASS"
    assert by["dpo_info"]["status"] == "PASS"
    # score reflete o upgrade (recomputado após a augmentação)
    assert result["score"] == sum(1 for c in result["checks"] if c["status"] == "PASS")


def test_scan_privacy_no_signals_stays_failing(monkeypatch):
    # homepage sem sinais + páginas internas inexistentes (404) → dsar/dpo continuam FAIL.
    _install_fetch(monkeypatch, {"https://sem.com": (HOME, 200)})
    result = _run(pc.scan_privacy("https://sem.com"))
    by = {c["id"]: c for c in result["checks"]}
    assert by["dpo_info"]["status"] == "FAIL"
    # /privacidade existe no footer mas 404 → sem sinal DSAR/DPO
    assert by["dsar_channel"]["status"] == "FAIL"
