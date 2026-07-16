"""KL-44 P4 — detecção de typosquatting/phishing (puro, testável, sem deps).

O discovery worker já consome os CT logs; aqui só comparamos cada domínio novo com os
domínios monitorados. `is_typosquat` devolve o tipo de similaridade ou None. Leitura de
dado público (CT logs) — 100% passivo.
"""

from __future__ import annotations

from typing import Optional, Tuple


def levenshtein(s1: str, s2: str) -> int:
    """Distância de edição (sem dependência)."""
    if len(s1) < len(s2):
        return levenshtein(s2, s1)
    if not s2:
        return len(s1)
    prev = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        curr = [i + 1]
        for j, c2 in enumerate(s2):
            curr.append(min(prev[j + 1] + 1, curr[j] + 1, prev[j] + (c1 != c2)))
        prev = curr
    return prev[-1]


# Substituições comuns de phishing (homoglyphs / typos visuais).
_HOMOGLYPHS = {"l": "1", "o": "0", "i": "1", "rn": "m", "vv": "w", "cl": "d"}


def _registrable(domain: str) -> str:
    """Domínio registrável simplificado (sem `www.`, lowercase)."""
    d = (domain or "").strip().lower().rstrip(".")
    if d.startswith("www."):
        d = d[4:]
    return d


def _label(domain: str) -> str:
    """Primeiro rótulo (o nome antes do TLD)."""
    return _registrable(domain).split(".")[0]


def is_typosquat(monitored_domain: str, candidate: str) -> Optional[Tuple[str, int]]:
    """(tipo, distância) se `candidate` é suspeitamente parecido com `monitored_domain`;
    None se não. Tipos: 'levenshtein', 'homoglyph', 'tld_variant'. Nunca acusa o próprio
    domínio."""
    mon = _registrable(monitored_domain)
    cand = _registrable(candidate)
    if not mon or not cand or mon == cand:
        return None
    mon_label, cand_label = _label(mon), _label(cand)
    if len(mon_label) < 4:   # nomes muito curtos geram falso-positivo demais
        # só pega variação de TLD do mesmo nome exato
        return ("tld_variant", 0) if mon_label == cand_label else None

    # Mesmo nome, TLD diferente (ex.: usecognato.com.br → usecognato.net).
    if mon_label == cand_label:
        return ("tld_variant", 0)

    # Homoglyph: aplica cada substituição comum e compara.
    for orig, repl in _HOMOGLYPHS.items():
        if orig in mon_label and mon_label.replace(orig, repl) == cand_label:
            return ("homoglyph", 1)

    # Levenshtein 1-2 no rótulo (typo de digitação).
    dist = levenshtein(mon_label, cand_label)
    if 0 < dist <= 2:
        return ("levenshtein", dist)
    return None


def similarity_label(similarity_type: str) -> str:
    return {
        "levenshtein": "nome quase idêntico (erro de digitação)",
        "homoglyph": "caracteres trocados (ex.: o→0, l→1)",
        "tld_variant": "mesmo nome, extensão diferente",
    }.get(similarity_type, "domínio parecido")
