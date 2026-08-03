"""KL-141 Prompt 4 — script de notificação do Security Gate (e-mail via Resend + webhook).
Offline: httpx.post mockado; sem key/URL → degrada sem quebrar. Nunca vaza valor de credencial
(o report já traz só tipo+localização)."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import httpx
import pytest

_NOTIFY_PATH = Path(__file__).resolve().parent.parent / "scripts" / "security_gate_notify.py"


def _load():
    spec = importlib.util.spec_from_file_location("gate_notify", _NOTIFY_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


notify = _load()


def _report():
    return {
        "url": "https://klarim.net", "score": 75, "passed": False,
        "critical": 0, "high": 1, "medium": 0, "duration_ms": 16000, "error": None,
        "results": [
            {"check": "header_csp", "category": "headers", "path": "/",
             "status": "fail", "severity": "high", "detail": "CSP ausente"},
            {"check": "ssl_valid", "category": "ssl", "path": "/",
             "status": "pass", "severity": "critical", "detail": "cert ok"},
        ],
    }


class _Resp:
    def __init__(self, status=200, text="ok"):
        self.status_code = status
        self.text = text


def _capture_post(monkeypatch, status=200):
    calls = []

    def _p(url, **kw):
        calls.append((url, kw))
        return _Resp(status)
    monkeypatch.setattr(httpx, "post", _p)
    return calls


# =========================================================================== #
# Corpo/assunto (puros)
# =========================================================================== #

def test_email_subject_has_score_and_url():
    s = notify.email_subject(_report())
    assert "75/100" in s and "klarim.net" in s and "FAILED" in s


def test_email_body_lists_fails_not_passes():
    body = notify.email_body(_report())
    assert "CSP ausente" in body and "[HIGH]" in body
    assert "cert ok" not in body   # PASS não entra no corpo


def test_webhook_payload_has_findings():
    payload = notify.webhook_payload(_report())
    assert "klarim.net" in payload["text"] and "CSP ausente" in payload["text"]


# =========================================================================== #
# send_email
# =========================================================================== #

def test_send_email_sends_with_key(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_fake")
    calls = _capture_post(monkeypatch)
    assert notify.send_email(_report(), "seguranca@klarim.net") is True
    url, kw = calls[0]
    assert url == "https://api.resend.com/emails"
    assert kw["json"]["to"] == ["seguranca@klarim.net"]
    assert "75/100" in kw["json"]["subject"]


def test_send_email_no_key_no_crash(monkeypatch, capsys):
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    calls = _capture_post(monkeypatch)
    assert notify.send_email(_report(), "x@y.com") is False
    assert calls == []                                   # não tentou enviar
    assert "não enviado" in capsys.readouterr().out


def test_send_email_no_recipients(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_fake")
    calls = _capture_post(monkeypatch)
    assert notify.send_email(_report(), "") is False and calls == []


def test_send_email_resend_error_returns_false(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_fake")
    _capture_post(monkeypatch, status=422)
    assert notify.send_email(_report(), "x@y.com") is False


# =========================================================================== #
# send_webhook
# =========================================================================== #

def test_send_webhook_with_url(monkeypatch):
    calls = _capture_post(monkeypatch)
    assert notify.send_webhook(_report(), "https://hooks.example/x") is True
    assert calls[0][0] == "https://hooks.example/x"


def test_send_webhook_no_url_no_crash(monkeypatch, capsys):
    monkeypatch.delenv("GATE_WEBHOOK_URL", raising=False)
    calls = _capture_post(monkeypatch)
    assert notify.send_webhook(_report(), None) is False
    assert calls == [] and "não enviado" in capsys.readouterr().out


# =========================================================================== #
# main
# =========================================================================== #

def test_main_reads_report_and_dispatches(monkeypatch, tmp_path):
    path = tmp_path / "gate-report.json"
    path.write_text(json.dumps(_report()), encoding="utf-8")
    sent = {}
    monkeypatch.setattr(notify, "send_email", lambda rep, rcpt: sent.update(rep=rep, rcpt=rcpt))
    assert notify.main(["--report", str(path), "--channel", "email",
                        "--recipients", "a@b.com"]) == 0
    assert sent["rcpt"] == "a@b.com" and sent["rep"]["score"] == 75


def test_main_missing_report_no_crash(capsys):
    assert notify.main(["--report", "/tmp/kl141_no_such_report.json"]) == 0
    assert "Não foi possível ler" in capsys.readouterr().out
