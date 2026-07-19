"""KL-77 (Fase 2) — arquivamento dos responses brutos de scan no GCS. Offline.

Cobre o módulo puro (`scanner/gcs_archive.py`): serialização/compressão, caminho do
objeto, bypass por flag, resiliência (exceção engolida), contadores (Redis + memória)
e a captura do response bruto no `enrich_profile` (capture_raw). Nenhuma chamada real
ao GCS — o client é mockado.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import re
from datetime import datetime, timezone

import scanner.gcs_archive as ga
import scanner.enrichment as enr


NOW = datetime(2026, 7, 19, 15, 4, 5, tzinfo=timezone.utc)

RESP = {
    "http_status": 200,
    "response_time_ms": 812,
    "headers": {"server": "nginx", "content-type": "text/html"},
    "html": "<html>olá café ☕</html>",
    "dns": {"mx": ["mx.google.com"], "ns": ["ns1.x.com"]},
    "ssl": {"ok": True, "protocol": "TLSv1.3"},
}


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #

class FakeBlob:
    def __init__(self):
        self.data = None
        self.content_type = None

    def upload_from_string(self, data, content_type=None):
        self.data = data
        self.content_type = content_type


class FakeBucket:
    def __init__(self):
        self.blobs = {}
        self.paths = []

    def blob(self, path):
        self.paths.append(path)
        b = self.blobs.setdefault(path, FakeBlob())
        return b


class FakeRedis:
    def __init__(self):
        self.data = {}

    async def incr(self, k):
        self.data[k] = int(self.data.get(k, 0)) + 1
        return self.data[k]

    async def incrby(self, k, n):
        self.data[k] = int(self.data.get(k, 0)) + int(n)
        return self.data[k]

    async def expire(self, k, ttl):
        return True

    async def set(self, k, v):
        self.data[k] = v

    async def get(self, k):
        return self.data.get(k)


def _reset_mem():
    ga._MEM_STATS.update(date=None, count=0, bytes=0, errors=0,
                         last_upload_at=None, last_error=None)


# --------------------------------------------------------------------------- #
# Funções puras
# --------------------------------------------------------------------------- #

def test_archive_object_path_format():
    assert ga.archive_object_path(4242, NOW) == "2026/07/19/4242.json.gz"
    # bate com o critério de validação: YYYY/MM/DD/{scan_id}.json.gz
    assert re.fullmatch(r"\d{4}/\d{2}/\d{2}/4242\.json\.gz", ga.archive_object_path(4242, NOW))


def test_build_payload_has_all_keys():
    p = ga.build_archive_payload(10, 7, "https://x.com.br", "x.com.br", RESP, NOW)
    for key in ("target_id", "scan_id", "domain", "url", "timestamp", "http_status",
                "response_time_ms", "headers", "html", "html_size_bytes", "dns", "ssl"):
        assert key in p, f"faltou {key}"
    assert p["scan_id"] == 10 and p["target_id"] == 7
    assert p["domain"] == "x.com.br" and p["url"] == "https://x.com.br"
    assert p["timestamp"] == NOW.isoformat()
    assert p["headers"]["server"] == "nginx"
    assert p["dns"]["mx"] == ["mx.google.com"] and p["ssl"]["protocol"] == "TLSv1.3"


def test_html_size_bytes_is_utf8_length():
    # "café ☕" tem mais bytes que caracteres (acento + emoji) → mede bytes, não chars.
    p = ga.build_archive_payload(1, 1, "u", "d", {"html": "☕"}, NOW)
    assert p["html_size_bytes"] == len("☕".encode("utf-8")) == 3


def test_build_payload_tolerates_empty_response():
    p = ga.build_archive_payload(1, 1, "u", "d", {}, NOW)
    assert p["html"] == "" and p["html_size_bytes"] == 0
    assert p["headers"] == {} and p["dns"] == {} and p["ssl"] == {}


def test_serialize_roundtrips_and_compresses():
    p = ga.build_archive_payload(10, 7, "https://x.com.br", "x.com.br", RESP, NOW)
    blob = ga.serialize_payload(p)
    assert isinstance(blob, bytes)
    restored = json.loads(gzip.decompress(blob).decode("utf-8"))
    assert restored["scan_id"] == 10 and restored["html"] == RESP["html"]
    assert restored["ssl"]["protocol"] == "TLSv1.3"


def test_serialize_handles_datetime_via_default_str():
    # o snapshot TLS traz datetimes (datas do certificado) — default=str não deve quebrar.
    p = {"cert": {"not_after": NOW}}
    restored = json.loads(gzip.decompress(ga.serialize_payload(p)).decode("utf-8"))
    assert restored["cert"]["not_after"] == str(NOW)


# --------------------------------------------------------------------------- #
# Config / bypass
# --------------------------------------------------------------------------- #

def test_enabled_env_parsing(monkeypatch):
    monkeypatch.setenv("GCS_ENABLED", "true"); assert ga._enabled() is True
    monkeypatch.setenv("GCS_ENABLED", "TRUE"); assert ga._enabled() is True
    monkeypatch.setenv("GCS_ENABLED", "false"); assert ga._enabled() is False
    monkeypatch.setenv("GCS_ENABLED", "0"); assert ga._enabled() is False
    monkeypatch.delenv("GCS_ENABLED", raising=False); assert ga._enabled() is True  # default


def test_disabled_bypasses_upload_entirely(monkeypatch):
    monkeypatch.setenv("GCS_ENABLED", "false")

    def _boom():
        raise AssertionError("não deveria tocar no client GCS com GCS_ENABLED=false")
    monkeypatch.setattr(ga, "_get_bucket", _boom)

    ok = asyncio.run(ga.archive_scan_response(1, 1, "https://x.com.br", "x.com.br", RESP))
    assert ok is False


# --------------------------------------------------------------------------- #
# Upload (bucket mockado)
# --------------------------------------------------------------------------- #

def test_upload_success_compresses_and_sets_content_type(monkeypatch):
    monkeypatch.setenv("GCS_ENABLED", "true")
    _reset_mem()
    bucket = FakeBucket()
    monkeypatch.setattr(ga, "_get_bucket", lambda: bucket)

    ok = asyncio.run(ga.archive_scan_response(4242, 7, "https://x.com.br", "x.com.br", RESP))
    assert ok is True
    # caminho correto YYYY/MM/DD/{scan_id}.json.gz
    assert len(bucket.paths) == 1
    assert re.fullmatch(r"\d{4}/\d{2}/\d{2}/4242\.json\.gz", bucket.paths[0])
    blob = bucket.blobs[bucket.paths[0]]
    assert blob.content_type == "application/gzip"
    # conteúdo é o payload comprimido e recuperável
    restored = json.loads(gzip.decompress(blob.data).decode("utf-8"))
    assert restored["scan_id"] == 4242 and restored["domain"] == "x.com.br"
    assert restored["html"] == RESP["html"] and restored["headers"]["server"] == "nginx"


def test_upload_failure_is_swallowed(monkeypatch):
    monkeypatch.setenv("GCS_ENABLED", "true")
    _reset_mem()

    class BoomBucket:
        def blob(self, path):
            raise RuntimeError("bucket inexistente / sem permissão")
    monkeypatch.setattr(ga, "_get_bucket", lambda: BoomBucket())

    # não levanta — scan continua; retorna False; erro contabilizado em memória.
    ok = asyncio.run(ga.archive_scan_response(9, 1, "https://x.com.br", "x.com.br", RESP))
    assert ok is False
    assert ga._MEM_STATS["errors"] == 1 and ga._MEM_STATS["last_error"]


# --------------------------------------------------------------------------- #
# Contadores no Redis (visíveis à API/MCP em outro processo)
# --------------------------------------------------------------------------- #

def test_success_records_redis_counters(monkeypatch):
    monkeypatch.setenv("GCS_ENABLED", "true")
    _reset_mem()
    bucket = FakeBucket()
    monkeypatch.setattr(ga, "_get_bucket", lambda: bucket)
    redis = FakeRedis()

    asyncio.run(ga.archive_scan_response(1, 1, "https://x.com.br", "x.com.br", RESP, redis=redis))
    count_keys = [k for k in redis.data if k.endswith(":count")]
    bytes_keys = [k for k in redis.data if k.endswith(":bytes")]
    assert count_keys and redis.data[count_keys[0]] == 1
    assert bytes_keys and redis.data[bytes_keys[0]] > 0
    assert redis.data.get("klarim:gcs:last_upload_at")


def test_error_records_redis_error(monkeypatch):
    monkeypatch.setenv("GCS_ENABLED", "true")
    _reset_mem()

    class BoomBucket:
        def blob(self, path):
            raise RuntimeError("kaput")
    monkeypatch.setattr(ga, "_get_bucket", lambda: BoomBucket())
    redis = FakeRedis()

    asyncio.run(ga.archive_scan_response(1, 1, "https://x.com.br", "x.com.br", RESP, redis=redis))
    err_keys = [k for k in redis.data if k.endswith(":errors")]
    assert err_keys and redis.data[err_keys[0]] == 1
    assert "kaput" in redis.data.get("klarim:gcs:last_error", "")


# --------------------------------------------------------------------------- #
# get_archive_stats
# --------------------------------------------------------------------------- #

def test_stats_from_redis(monkeypatch):
    monkeypatch.setenv("GCS_ENABLED", "true")
    monkeypatch.setenv("GCS_BUCKET", "klarim-raw")
    bucket = FakeBucket()
    monkeypatch.setattr(ga, "_get_bucket", lambda: bucket)
    redis = FakeRedis()
    # 2 uploads → count=2, bytes>0
    asyncio.run(ga.archive_scan_response(1, 1, "https://a.com.br", "a.com.br", RESP, redis=redis))
    asyncio.run(ga.archive_scan_response(2, 1, "https://b.com.br", "b.com.br", RESP, redis=redis))

    stats = asyncio.run(ga.get_archive_stats(redis))
    assert stats["enabled"] is True and stats["bucket"] == "klarim-raw"
    assert stats["files_today"] == 2 and stats["bytes_today"] > 0
    assert stats["avg_bytes"] == round(stats["bytes_today"] / 2)
    assert stats["last_upload_at"] and stats["errors_today"] == 0


def test_stats_in_memory_fallback(monkeypatch):
    monkeypatch.setenv("GCS_ENABLED", "true")
    _reset_mem()
    bucket = FakeBucket()
    monkeypatch.setattr(ga, "_get_bucket", lambda: bucket)
    asyncio.run(ga.archive_scan_response(1, 1, "https://a.com.br", "a.com.br", RESP))  # sem redis

    stats = asyncio.run(ga.get_archive_stats(None))
    assert stats["files_today"] == 1 and stats["bytes_today"] > 0
    assert stats["last_upload_at"]


# --------------------------------------------------------------------------- #
# Captura no enrich_profile (integração com o worker)
# --------------------------------------------------------------------------- #

class _Resp:
    status_code = 200
    headers = {"server": "nginx"}
    text = "<html>hotel</html>"


class _EnrichStore:
    async def upsert_site_profile(self, tid, profile):
        pass

    async def list_sectors(self, statuses):
        return []


def _mock_enrich(monkeypatch, tls=None):
    async def _fetch(url, **kw):
        return _Resp()
    monkeypatch.setattr("scanner.checks.base.fetch", _fetch)
    monkeypatch.setattr("scanner.checks.dns_util.resolve_mx", lambda d: ["mx.a.com"])
    monkeypatch.setattr("scanner.checks.dns_util.resolve_ns", lambda d: ["ns.a.com"])

    async def _build(url, **kw):
        return {"description": "Hotel", "maturity_score": 6}
    monkeypatch.setattr("scanner.profiler.build_profile", _build)
    monkeypatch.setattr("scanner.ai_enrichment.AI_ENRICHMENT_ENABLED", False)

    async def _tls(host, port=443):
        return tls if tls is not None else {"ok": True, "protocol": "TLSv1.3"}
    monkeypatch.setattr("scanner.tls_analyzer.get_tls_info", _tls)


def test_enrich_capture_raw_returns_response(monkeypatch):
    _mock_enrich(monkeypatch)
    raw = asyncio.run(enr.enrich_profile(_EnrichStore(), 1, "https://a.com.br", 74,
                                         capture_raw=True))
    assert raw is not None
    assert raw["http_status"] == 200 and raw["html"] == "<html>hotel</html>"
    assert raw["headers"]["server"] == "nginx"
    assert raw["dns"]["mx"] == ["mx.a.com"] and raw["dns"]["ns"] == ["ns.a.com"]
    assert raw["ssl"]["protocol"] == "TLSv1.3"
    assert isinstance(raw["response_time_ms"], int)


def test_enrich_without_capture_returns_none(monkeypatch):
    # caminho público/anônimo (API): capture_raw=False → nada muda, retorno None.
    _mock_enrich(monkeypatch)
    raw = asyncio.run(enr.enrich_profile(_EnrichStore(), 1, "https://a.com.br", 74))
    assert raw is None


def test_enrich_capture_survives_tls_failure(monkeypatch):
    # SSL indisponível não impede a captura do resto (ssl fica {}).
    async def _boom_tls(host, port=443):
        raise RuntimeError("tls down")
    _mock_enrich(monkeypatch)
    monkeypatch.setattr("scanner.tls_analyzer.get_tls_info", _boom_tls)
    raw = asyncio.run(enr.enrich_profile(_EnrichStore(), 1, "https://a.com.br", 74,
                                         capture_raw=True))
    assert raw is not None and raw["ssl"] == {}
    assert raw["html"] == "<html>hotel</html>"
