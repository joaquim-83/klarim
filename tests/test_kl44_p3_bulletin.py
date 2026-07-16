"""KL-44 P3 — builders de boletim + enrich_fails + helpers do worker (puros, offline)."""

from __future__ import annotations

from datetime import datetime, timezone

from notifier import bulletin as b
from reporter.laudo import enrich_fails


def test_owner_bulletin_text():
    txt = b.build_owner_bulletin({
        "domain": "usecognato.com.br", "score": 73, "semaphore": "amarelo",
        "trend": "up", "delta": 5,
        "vigilias": {"ssl": "ok", "domain": "ok", "score": "warning", "email": "ok", "reputation": "ok"},
        "vigilia_alerts": ["Score: requer atenção"],
        "top_action": {"name": "HSTS ausente", "evidence": "sem header",
                       "fix": "adicione o header HSTS", "technical": "Strict-Transport-Security: max-age=31536000"},
        "technician_masked": None, "code": "A7K2M9",
        "whatsapp_url": "https://wa.me/?text=abc"})
    assert "Boletim de segurança — usecognato.com.br" in txt
    assert "73/100" in txt and "Subiu 5" in txt
    assert "HSTS ausente" in txt and "/laudo/A7K2M9" in txt
    assert "wa.me" in txt
    assert "<" not in txt   # plain text, sem HTML
    assert "R$" not in txt  # sem preço


def test_owner_bulletin_with_technician():
    txt = b.build_owner_bulletin({
        "domain": "x.com.br", "score": 90, "semaphore": "verde", "trend": "stable", "delta": 0,
        "vigilias": {}, "technician_masked": "t***o@empresa.com.br", "code": "X"})
    assert "t***o@empresa.com.br" in txt
    assert "Estável" in txt


def test_technician_bulletin_text():
    txt = b.build_technician_bulletin({
        "domain": "x.com.br", "score": 60, "semaphore": "amarelo", "trend": "down", "delta": -3,
        "owner_masked": "d***o@x.com.br", "pass_count": 40, "code": "C0DE1234",
        "fails": [{"name": "CSP fraca", "severity": "ALTA", "evidence": "unsafe-inline",
                   "owasp": "A05:2025", "cwe": "CWE-693", "fix": "remova unsafe-inline"}]})
    assert "Laudo técnico — x.com.br" in txt
    assert "d***o@x.com.br" in txt
    assert "[ALTA] CSP fraca" in txt and "A05:2025" in txt
    assert "/laudo/C0DE1234" in txt
    assert "role=technician" in txt


def test_invite_text():
    txt = b.build_technician_invite({
        "domain": "x.com.br", "score": 73, "semaphore": "amarelo",
        "owner_masked": "d***o@x.com.br", "code": "LAUDO123", "invite_code": "INV12345"})
    assert "d***o@x.com.br vinculou você" in txt
    assert "/laudo/LAUDO123" in txt
    assert "INV12345" in txt and "invite=INV12345" in txt


def test_subjects():
    assert b.owner_subject("x.com.br", "Jul/2026") == "x.com.br — Boletim de segurança Jul/2026"
    assert b.technician_subject("x.com.br", 73) == "Laudo técnico — x.com.br (73/100)"
    assert "convidou você" in b.invite_subject("João", "x.com.br")


def test_trend_text():
    assert "Subiu" in b.trend_text("up", 5)
    assert "Caiu" in b.trend_text("down", -3)
    assert "Estável" in b.trend_text("stable", 0)


def test_enrich_fails_orders_by_severity():
    checks = [
        {"check_id": "check_06", "name": "X-Frame", "status": "FAIL", "severity": "MEDIA"},
        {"check_id": "check_01", "name": "HTTPS", "status": "FAIL", "severity": "CRITICA"},
        {"check_id": "check_02", "name": "HSTS", "status": "FAIL", "severity": "ALTA"},
        {"check_id": "check_10", "name": "OK check", "status": "PASS", "severity": "BAIXA"},
    ]
    fails = enrich_fails(checks)
    assert [f["name"] for f in fails] == ["HTTPS", "HSTS", "X-Frame"]  # CRÍTICA→ALTA→MÉDIA
    assert all("owasp" in f for f in fails)


def test_worker_helpers():
    from discovery.bulletin_worker import _gen_code, _mask_email, _semaphore, _whatsapp_url
    assert len(_gen_code(8)) == 8
    assert _mask_email("joao@empresa.com.br") == "j***o@empresa.com.br"
    assert _semaphore(95) == "verde" and _semaphore(60) == "amarelo" and _semaphore(30) == "vermelho"
    assert "wa.me" in _whatsapp_url("x.com.br", 73, "ABC")


def test_worker_frequencies_due():
    from discovery.bulletin_worker import BulletinWorker
    w = BulletinWorker()
    w.hour_utc = 13
    # segunda-feira 13h UTC → daily + weekly (não é dia 1)
    monday = datetime(2026, 7, 13, 13, tzinfo=timezone.utc)   # 13/07/2026 é segunda
    due = w._frequencies_due(monday)
    assert "daily" in due and "weekly" in due and "monthly" not in due
    # fora do horário → nada
    assert w._frequencies_due(datetime(2026, 7, 13, 9, tzinfo=timezone.utc)) == []
    # dia 1 (quarta) 13h → daily + monthly
    day1 = datetime(2026, 7, 1, 13, tzinfo=timezone.utc)      # 01/07/2026 é quarta
    due1 = w._frequencies_due(day1)
    assert "daily" in due1 and "monthly" in due1
