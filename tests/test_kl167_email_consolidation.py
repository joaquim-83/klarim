"""KL-167 — consolidação de e-mail em 2 domínios (klarim.net transacional + klarimscan.com
cold) + targeting (pular genéricos, intervalo 90d por e-mail, foco em score baixo).

Cobre a lógica PURA nova: `is_generic_alert_email`, `_urgency_bucket`, a consolidação dos
remetentes cold (`cold_alert` + `email_client`) e os guards de domínio aposentado. Offline.
"""

from __future__ import annotations

import pytest

from notifier import cold_alert as c
from notifier.email_client import KlarimMailer
from discovery.alert_scoring import is_generic_alert_email, GENERIC_ALERT_SKIP_PREFIXES
from discovery.alert_worker import _urgency_bucket


# --------------------------------------------------------------------------- #
# is_generic_alert_email — genéricos pulados; pessoais preservados (sem overmatch)
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("email", [
    "contato@empresa.com.br", "atendimento@x.com", "sac@loja.com.br",
    "info@site.com", "comercial@y.com.br", "vendas@z.com.br",
    "sac2@loja.com.br", "contato.rh@empresa.com.br", "vendas-sp@z.com.br",
    "CONTATO@Empresa.com.br",   # case-insensitive
])
def test_generic_emails_are_skipped(email):
    assert is_generic_alert_email(email) is True


@pytest.mark.parametrize("email", [
    "joana@empresa.com.br", "sacha@x.com",          # 'sac' é prefixo mas 'sacha' não casa
    "informatica@y.com.br", "informacoes@z.com",    # 'info...' sem fronteira → não casa
    "vendaval@x.com.br",                            # 'vendas' vs 'vendaval' → não casa
    "joao.silva@empresa.com.br", "", "sem-arroba",
])
def test_personal_emails_are_not_skipped(email):
    assert is_generic_alert_email(email) is False


def test_skip_list_matches_card():
    assert set(GENERIC_ALERT_SKIP_PREFIXES) == {
        "contato", "atendimento", "sac", "info", "comercial", "vendas"}


# --------------------------------------------------------------------------- #
# _urgency_bucket — score < 70 primeiro; 85+ por último
# --------------------------------------------------------------------------- #

def test_urgency_bucket_by_score():
    assert _urgency_bucket({"last_scan_score": 45}) == 0    # urgente
    assert _urgency_bucket({"last_scan_score": 69}) == 0
    assert _urgency_bucket({"last_scan_score": 70}) == 1
    assert _urgency_bucket({"last_scan_score": 84}) == 1
    assert _urgency_bucket({"last_scan_score": 85}) == 2    # não urgente
    assert _urgency_bucket({"last_scan_score": 100}) == 2
    assert _urgency_bucket({"scan_score": 50}) == 0         # fallback p/ scan_score
    assert _urgency_bucket({}) == 2                          # sem score → não urgente


# --------------------------------------------------------------------------- #
# Consolidação dos remetentes cold (klarimscan.com)
# --------------------------------------------------------------------------- #

def test_cold_default_is_klarimscan():
    assert c.DEFAULT_SENDER_EMAILS == ("scan@klarimscan.com",)
    assert [s.from_domain for s in c.load_senders({})] == ["klarimscan.com"]


def test_retired_domains_frozen_set():
    assert c.RETIRED_SENDER_DOMAINS == frozenset({
        "klarim.net", "alertas.klarim.net", "aviso.klarim.net", "perfil.klarim.net"})


def test_load_senders_never_empty_even_with_retired_env():
    # Robustez: env legado só com aposentados → nunca fica sem remetente (cai no default).
    for env in ("scan@alertas.klarim.net", "scan@aviso.klarim.net,scan@perfil.klarim.net",
                "scan@klarim.net"):
        s = c.load_senders({"ALERT_SENDER_EMAILS": env})
        assert [x.from_domain for x in s] == ["klarimscan.com"]


# --------------------------------------------------------------------------- #
# email_client — cold em klarimscan.com; boletim transacional em klarim.net
# --------------------------------------------------------------------------- #

def _m():
    return KlarimMailer("re_x", "Klarim <klarim@klarim.net>", store=None)


def test_proactive_and_profile_default_to_klarimscan(monkeypatch):
    monkeypatch.delenv("ALERT_FROM_EMAIL", raising=False)
    monkeypatch.delenv("ALERT_FROM_NAME", raising=False)
    monkeypatch.delenv("PROFILE_VIEW_FROM_EMAIL", raising=False)
    monkeypatch.delenv("PROFILE_VIEW_FROM_NAME", raising=False)
    assert _m()._proactive_from() == "Klarim <scan@klarimscan.com>"
    assert _m()._profile_view_from() == "Klarim <notifica@klarimscan.com>"


def test_cold_senders_ignore_retired_env(monkeypatch):
    monkeypatch.setenv("ALERT_FROM_EMAIL", "scan@aviso.klarim.net")
    monkeypatch.setenv("PROFILE_VIEW_FROM_EMAIL", "notifica@perfil.klarim.net")
    monkeypatch.delenv("ALERT_FROM_NAME", raising=False)
    monkeypatch.delenv("PROFILE_VIEW_FROM_NAME", raising=False)
    assert _m()._proactive_from() == "Klarim <scan@klarimscan.com>"
    assert _m()._profile_view_from() == "Klarim <notifica@klarimscan.com>"


def test_bulletin_from_is_transactional(monkeypatch):
    monkeypatch.setenv("ALERT_FROM_EMAIL", "scan@klarimscan.com")  # cold, não afeta o boletim
    monkeypatch.delenv("BULLETIN_FROM_EMAIL", raising=False)
    monkeypatch.delenv("BULLETIN_FROM_NAME", raising=False)
    assert _m()._bulletin_from() == "Klarim <alerta@klarim.net>"
    # override para um domínio aposentado é ignorado → volta ao transacional
    monkeypatch.setenv("BULLETIN_FROM_EMAIL", "x@perfil.klarim.net")
    assert _m()._bulletin_from() == "Klarim <alerta@klarim.net>"


def test_recently_alerted_emails_empty_input_short_circuits():
    # Sem e-mails candidatos, retorna vazio sem tocar no banco (assíncrono).
    import asyncio
    from discovery.store import TargetStore
    store = TargetStore.__new__(TargetStore)   # sem conexão — não deve ser usada
    assert asyncio.run(store.recently_alerted_emails([], 90)) == set()
    assert asyncio.run(store.recently_alerted_emails(["", "  "], 90)) == set()
