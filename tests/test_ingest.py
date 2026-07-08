"""Testes da ingestão de scans no banco (KL-17) — offline, HTML mockado."""

from __future__ import annotations

import asyncio

import discovery.ingest as ing


class FakeScore:
    def __init__(self, score, semaphore, passed, failed, inconclusive):
        self.score, self.semaphore = score, semaphore
        self.passed, self.failed, self.inconclusive = passed, failed, inconclusive


class FakeReport:
    def __init__(self, score):
        self.score = score

    def to_dict(self):
        return {"results": [{"status": "FAIL", "severity": "ALTA"}]}


class FakeStore:
    def __init__(self):
        self.registered = []
        self.scans = []
        self.updated = []

    async def register_target(self, url, domain, platform, sector, tier, email,
                              source="ct_log", status="discovered", confidence=0.0):
        self.registered.append({"url": url, "domain": domain, "platform": platform,
                                "sector": sector, "email": email, "source": source,
                                "status": status, "confidence": confidence})
        return 42

    async def save_scan(self, target_id, url, score, semaphore, pass_count, fail_count,
                        inconclusive_count, checks_json, source="discovery"):
        self.scans.append({"target_id": target_id, "score": score, "source": source})
        return 100

    async def update_scan_result(self, target_id, scan_id, score):
        self.updated.append((target_id, scan_id, score))


def test_ingest_registers_enriches_and_tags_source(monkeypatch):
    async def fake_html(url):
        return '<a href="mailto:contato@hotelx.com.br">x</a> hotel pousada reserva diária check-in'
    monkeypatch.setattr(ing, "_fetch_html", fake_html)

    store = FakeStore()
    report = FakeReport(FakeScore(86, "amarelo", 12, 2, 1))
    meta = asyncio.run(ing.ingest_scan(store, "https://www.hotelx.com.br", report, "admin"))

    assert meta["target_id"] == 42 and meta["scan_id"] == 100
    assert meta["contact_email"] == "contato@hotelx.com.br"
    assert meta["sector"] == "hotel"
    reg = store.registered[0]
    assert reg["source"] == "admin" and reg["status"] == "scanned" and reg["domain"] == "hotelx.com.br"
    assert store.scans[0]["source"] == "admin"
    assert store.updated == [(42, 100, 86)]


def test_ingest_without_html_is_graceful(monkeypatch):
    async def none_html(url):
        return None
    monkeypatch.setattr(ing, "_fetch_html", none_html)

    store = FakeStore()
    report = FakeReport(FakeScore(50, "amarelo", 5, 5, 1))
    meta = asyncio.run(ing.ingest_scan(store, "https://x.com.br", report, "public"))

    assert meta["contact_email"] is None and meta["platform"] == "unknown"
    assert store.registered[0]["source"] == "public"
    assert store.scans[0]["source"] == "public"
