"""KL-92 Prompt 3 — parser do access_log do Nginx (cobertura completa) + classificação
simplificada. Testa `parse_line` (pura), `classify_bot_simple`, e o `NginxLogParser`
(leitura incremental, rotação, truncação, batch insert). Offline (usa um arquivo temporário
e um store fake). Ver `api/nginx_log_parser.py` e `api/bot_classifier.py`."""

from __future__ import annotations

import os
import tempfile

import pytest

import api.nginx_log_parser as nlp
import api.bot_classifier as bc


def _line(ip="189.28.100.42", method="GET", path="/site/hotel.com.br", status=200,
          referrer="-", ua="Mozilla/5.0", country="BR", rt="0.123", nbytes="512"):
    return (f'{ip} - - [21/Jul/2026:00:30:30 +0000] "{method} {path} HTTP/1.1" '
            f'{status} {nbytes} "{referrer}" "{ua}" country={country} rt={rt}')


# =========================================================================== #
# 1. classify_bot_simple (sem contexto de request)
# =========================================================================== #

def test_simple_datacenter():
    assert bc.classify_bot_simple("52.1.2.3", "Mozilla", "BR") == (True, "datacenter_ip")


def test_simple_crawler():
    assert bc.classify_bot_simple("189.1.2.3", "Googlebot/2.1", "BR") == (True, "crawler_ua")


def test_simple_us_prefetch():
    assert bc.classify_bot_simple("177.9.9.9", "Mozilla", "US") == (True, "prefetch_likely")


def test_simple_br_human():
    assert bc.classify_bot_simple("189.28.100.42", "Mozilla/5.0", "BR") == (False, None)


def test_simple_own_ip():
    assert bc.classify_bot_simple("34.135.194.208", "curl", "US") == (False, None)


def test_simple_empty_ip():
    assert bc.classify_bot_simple("-", "Mozilla", "BR") == (False, None)
    assert bc.classify_bot_simple("", "Mozilla", "US") == (False, None)


# =========================================================================== #
# 2. parse_line (pura)
# =========================================================================== #

def test_parse_site_page():
    r = nlp.parse_line(_line(path="/site/hotel.com.br", rt="0.123"))
    assert r["endpoint"] == "/site/hotel.com.br"
    assert r["domain_queried"] == "hotel.com.br"
    assert r["country_code"] == "BR"
    assert r["is_bot"] is False
    assert r["response_time_ms"] == 123
    assert r["source"] == "nginx"
    assert r["user_id"] is None


def test_parse_scan_url_domain():
    r = nlp.parse_line(_line(path="/scan?url=https://www.clinica.com.br"))
    assert r["endpoint"] == "/scan"                 # query removida do endpoint
    assert r["domain_queried"] == "clinica.com.br"


def test_parse_setor_no_domain():
    r = nlp.parse_line(_line(path="/setor/hotelaria", ip="189.9.9.9"))
    assert r["endpoint"] == "/setor/hotelaria"
    assert r["domain_queried"] is None              # slug não é domínio


def test_parse_skips_api():
    assert nlp.parse_line(_line(method="POST", path="/api/events")) is None
    assert nlp.parse_line(_line(path="/api/scan/result?url=x.com")) is None


def test_parse_skips_mcp():
    assert nlp.parse_line(_line(path="/mcp/messages/")) is None


def test_parse_skips_static_asset():
    assert nlp.parse_line(_line(path="/_astro/chunk.js")) is None
    assert nlp.parse_line(_line(path="/assets/logo.png")) is None
    assert nlp.parse_line(_line(path="/favicon.ico")) is None


def test_parse_skips_invalid_ip():
    assert nlp.parse_line(_line(ip="-", path="/")) is None       # sem CF-Connecting-IP
    assert nlp.parse_line(_line(ip="garbage", path="/")) is None


def test_parse_us_datacenter_is_bot():
    r = nlp.parse_line(_line(ip="52.1.2.3", path="/site/x.com", country="US"))
    assert r["is_bot"] is True and r["bot_reason"] == "datacenter_ip"


def test_parse_us_page_prefetch():
    r = nlp.parse_line(_line(ip="177.1.2.3", path="/", country="US"))
    assert r["is_bot"] is True and r["bot_reason"] == "prefetch_likely"


def test_parse_referrer_and_ua_dash_to_none():
    r = nlp.parse_line(_line(path="/", referrer="-", ua="-", ip="189.1.1.1"))
    assert r["referrer"] is None and r["user_agent"] is None


def test_parse_landing_root():
    r = nlp.parse_line(_line(path="/", ip="189.5.5.5"))
    assert r["endpoint"] == "/" and r["domain_queried"] is None


def test_parse_malformed_line_none():
    assert nlp.parse_line("this is not a valid nginx log line") is None
    assert nlp.parse_line("") is None


def test_parse_rt_dash():
    r = nlp.parse_line(_line(path="/", rt="-", ip="189.6.6.6"))
    assert r["response_time_ms"] is None


# =========================================================================== #
# 3. NginxLogParser — leitura incremental / rotação / truncação
# =========================================================================== #

class _FakeStore:
    def __init__(self):
        self.batches = []

    async def log_access_batch(self, records):
        self.batches.append(list(records))
        return len(records)


@pytest.fixture
def logfile():
    fd, path = tempfile.mkstemp(prefix="klarim_nginx_", suffix=".log")
    os.close(fd)
    yield path
    try:
        os.remove(path)
    except OSError:
        pass


def _write(path, *lines, mode="a"):
    with open(path, mode) as f:
        for ln in lines:
            f.write(ln + "\n")


@pytest.mark.asyncio
async def test_parser_inserts_new_lines(logfile):
    store = _FakeStore()
    _write(logfile, _line(path="/site/a.com", ip="189.1.1.1"),
           _line(path="/setor/x", ip="189.2.2.2"))
    p = nlp.NginxLogParser(store=store, log_path=logfile)
    n = await p.parse_new_lines()
    assert n == 2 and len(store.batches[0]) == 2
    assert store.batches[0][0]["source"] == "nginx"


@pytest.mark.asyncio
async def test_parser_incremental_offset(logfile):
    store = _FakeStore()
    _write(logfile, _line(path="/", ip="189.1.1.1"))
    p = nlp.NginxLogParser(store=store, log_path=logfile)
    assert await p.parse_new_lines() == 1
    assert await p.parse_new_lines() == 0          # nada novo → não relê
    _write(logfile, _line(path="/site/b.com", ip="189.2.2.2"))
    assert await p.parse_new_lines() == 1          # só a linha nova
    assert len(store.batches) == 2


@pytest.mark.asyncio
async def test_parser_skips_api_and_assets(logfile):
    store = _FakeStore()
    _write(logfile,
           _line(path="/api/events", method="POST", ip="189.1.1.1"),   # middleware cobre
           _line(path="/_astro/x.js", ip="189.2.2.2"),                 # asset
           _line(path="/site/c.com", ip="189.3.3.3"))                  # conta
    p = nlp.NginxLogParser(store=store, log_path=logfile)
    n = await p.parse_new_lines()
    assert n == 1                                   # só o /site/
    assert store.batches[0][0]["endpoint"] == "/site/c.com"


@pytest.mark.asyncio
async def test_parser_detects_rotation(logfile):
    store = _FakeStore()
    _write(logfile, _line(path="/", ip="189.1.1.1"))
    p = nlp.NginxLogParser(store=store, log_path=logfile)
    await p.parse_new_lines()
    assert p.offset > 0
    # simula rotação: recria o arquivo (novo inode) menor que o offset antigo
    os.remove(logfile)
    _write(logfile, _line(path="/site/new.com", ip="189.2.2.2"), mode="w")
    n = await p.parse_new_lines()
    assert n == 1                                   # releu do 0 (rotação detectada)


@pytest.mark.asyncio
async def test_parser_missing_file_is_noop():
    store = _FakeStore()
    p = nlp.NginxLogParser(store=store, log_path="/nonexistent/klarim/access.log")
    assert await p.parse_new_lines() == 0           # sem arquivo → no-op (dev sem volume)
    assert store.batches == []


def test_parser_truncate_resets_offset(logfile):
    # _read_new_lines sinaliza truncar quando passa de MAX_BYTES; _truncate zera offset.
    store = _FakeStore()
    p = nlp.NginxLogParser(store=store, log_path=logfile)
    _write(logfile, _line(path="/", ip="189.1.1.1"))
    p.offset = os.path.getsize(logfile)
    p._truncate()
    assert p.offset == 0 and os.path.getsize(logfile) == 0
