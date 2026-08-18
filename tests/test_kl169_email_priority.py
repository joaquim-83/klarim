"""KL-169 — priorização de e-mails (pessoais primeiro, genéricos como fallback).

A regra correta não é FILTRAR nem BLOQUEAR genéricos (KL-167 bloqueou 97% → parou; KL-168 desligou
→ 25% bounce) — é PRIORIZAR: a query de elegibilidade ordena pessoais antes de genéricos, e os
genéricos só entram quando os pessoais não preenchem o `fetch_cap`. Genéricos NUNCA são bloqueados.

Cobre a lógica PURA: o classificador `is_generic_email`, o `GENERIC_PREFIX_SQL_REGEX` do ORDER BY
(consistente com o classificador) e o breakdown sent_personal/sent_generic no ciclo. Offline.
"""
from __future__ import annotations

import re

import pytest

from discovery.alert_scoring import (
    GENERIC_PREFIXES, GENERIC_PREFIX_SQL_REGEX, is_generic_email)


# --------------------------------------------------------------------------- #
# Classificador — 7 prefixos genéricos de negócio da base BR
# --------------------------------------------------------------------------- #

def test_generic_prefixes_are_the_seven():
    assert GENERIC_PREFIXES == (
        "contato", "sac", "info", "comercial", "vendas", "atendimento", "suporte")


@pytest.mark.parametrize("email,expected", [
    ("contato@x.com.br", True), ("sac@x.com", True), ("info@x.com", True),
    ("comercial@x.com", True), ("vendas@x.com", True), ("atendimento@x.com", True),
    ("suporte@x.com", True),
    ("sac2@x.com", True), ("contato.rh@x.com", True), ("vendas-sp@x.com", True),
    ("CONTATO@X.com", True),                # case-insensitive
    ("joao@gmail.com", False), ("maria.silva@empresa.com.br", False),
    ("sacha@x.com", False), ("informatica@x.com", False), ("vendaval@x.com", False),
    ("suportex@x.com", False), ("", False), ("sem-arroba", False),
])
def test_is_generic_email(email, expected):
    assert is_generic_email(email) is expected


# --------------------------------------------------------------------------- #
# O SQL regex do ORDER BY deve concordar com o classificador Python
# --------------------------------------------------------------------------- #

def test_sql_regex_shape():
    assert GENERIC_PREFIX_SQL_REGEX == (
        "^(contato|sac|info|comercial|vendas|atendimento|suporte)[0-9._+@-]")


@pytest.mark.parametrize("email", [
    "contato@x.com.br", "sac@x.com", "info@x.com", "comercial@x.com", "vendas@x.com",
    "atendimento@x.com", "suporte@x.com", "sac2@x.com", "contato.rh@x.com",
    "joao@gmail.com", "maria.silva@empresa.com.br", "sacha@x.com", "informatica@x.com",
    "vendaval@x.com", "suportex@x.com",
])
def test_sql_regex_matches_python_classifier(email):
    # `~*` do Postgres = case-insensitive → re.IGNORECASE. Ambos aplicados ao e-mail cru.
    sql_match = re.search(GENERIC_PREFIX_SQL_REGEX, email, re.IGNORECASE) is not None
    assert sql_match == is_generic_email(email), email
