"""KL-26 — regressão de score. Fixtures determinísticas de CheckResult → score/semáforo
exatos. Detecta mudança de peso/threshold (alerta INTENCIONAL, não bug). Sem rede/scanner real.

Referência (pesos atuais): CRITICA=5 · ALTA=3 · MEDIA=2 · BAIXA=1. INCONCLUSO é neutro
(fora do denominador). Semáforo: verde ≥90 E zero FAIL alta/crítica · amarelo ≥50 · vermelho <50.
"""
from __future__ import annotations

from scanner.checks.base import CheckResult, Status, Severity
from scanner.scoring import compute_score, GREEN_THRESHOLD, YELLOW_THRESHOLD, SEVERITY_WEIGHT


def _r(status, severity, name="c"):
    return CheckResult(name=name, status=status, severity=severity)


def _pass(sev, n=1):
    return [_r(Status.PASS, sev) for _ in range(n)]


def _fail(sev, n=1):
    return [_r(Status.FAIL, sev) for _ in range(n)]


# --------------------------------------------------------------------------- #
# Extremos + neutralidade do INCONCLUSO
# --------------------------------------------------------------------------- #

def test_all_pass_is_100_verde():
    b = compute_score(_pass(Severity.CRITICA, 5))
    assert b.score == 100 and b.semaphore == "verde"


def test_all_fail_is_0_vermelho():
    b = compute_score(_fail(Severity.ALTA, 5))
    assert b.score == 0 and b.semaphore == "vermelho"


def test_inconclusive_is_neutral():
    # 1 PASS CRITICA + 1 INCONCLUSO CRITICA → 5/5 = 100 (não 5/10).
    results = _pass(Severity.CRITICA, 1) + [_r(Status.INCONCLUSO, Severity.CRITICA)]
    b = compute_score(results)
    assert b.score == 100 and b.inconclusive == 1


def test_empty_or_all_inconclusive_is_zero():
    b = compute_score([_r(Status.INCONCLUSO, Severity.ALTA) for _ in range(3)])
    assert b.score == 0  # considered == 0 → score 0


# --------------------------------------------------------------------------- #
# Determinismo
# --------------------------------------------------------------------------- #

def test_scoring_is_deterministic():
    results = _pass(Severity.CRITICA, 3) + _fail(Severity.ALTA, 2) + _pass(Severity.MEDIA, 4)
    scores = {compute_score(results).score for _ in range(3)}
    assert len(scores) == 1  # exatamente o mesmo score nas 3 rodadas


def test_flipping_one_check_lowers_score():
    base = _pass(Severity.ALTA, 10)
    worse = _pass(Severity.ALTA, 9) + _fail(Severity.ALTA, 1)
    assert compute_score(worse).score < compute_score(base).score


# --------------------------------------------------------------------------- #
# Limites do semáforo (tabela da spec)
# --------------------------------------------------------------------------- #

def test_semaphore_90_zero_high_fail_is_verde():
    # 9 PASS BAIXA + 1 FAIL BAIXA → 9/10 = 90; FAIL baixa não rebaixa → verde.
    b = compute_score(_pass(Severity.BAIXA, 9) + _fail(Severity.BAIXA, 1))
    assert b.score == 90 and b.semaphore == "verde"


def test_semaphore_90_with_alta_fail_is_amarelo():
    # 9 PASS ALTA (27) + 1 FAIL ALTA (3) → 27/30 = 90, mas FAIL alta rebaixa → amarelo.
    b = compute_score(_pass(Severity.ALTA, 9) + _fail(Severity.ALTA, 1))
    assert b.score == 90 and b.semaphore == "amarelo"


def test_semaphore_89_is_amarelo():
    b = compute_score(_pass(Severity.BAIXA, 89) + _fail(Severity.BAIXA, 11))
    assert b.score == 89 and b.semaphore == "amarelo"


def test_semaphore_50_is_amarelo():
    b = compute_score(_pass(Severity.BAIXA, 5) + _fail(Severity.BAIXA, 5))
    assert b.score == 50 and b.semaphore == "amarelo"


def test_semaphore_49_is_vermelho():
    b = compute_score(_pass(Severity.BAIXA, 49) + _fail(Severity.BAIXA, 51))
    assert b.score == 49 and b.semaphore == "vermelho"


def test_semaphore_critica_fail_blocks_verde_even_high_score():
    # 20 PASS CRITICA (100) + 1 FAIL CRITICA (5) → 100/105 = 95, mas FAIL crítica → amarelo.
    b = compute_score(_pass(Severity.CRITICA, 20) + _fail(Severity.CRITICA, 1))
    assert b.score >= GREEN_THRESHOLD and b.semaphore == "amarelo"
    assert b.fails_by_severity.get(Severity.CRITICA) == 1


# --------------------------------------------------------------------------- #
# Sites de referência
# --------------------------------------------------------------------------- #

def _reference_klarim_like():
    """Todos os checks passam, severidades variadas → deve dar 100 verde."""
    return (_pass(Severity.CRITICA, 4) + _pass(Severity.ALTA, 6)
            + _pass(Severity.MEDIA, 3) + _pass(Severity.BAIXA, 2))


def _reference_problematic():
    """Sem HTTPS/HSTS/CSP (falhas críticas/altas) → deve dar < 50 vermelho."""
    return (_fail(Severity.CRITICA, 3) + _fail(Severity.ALTA, 4)
            + _pass(Severity.BAIXA, 2))


def test_reference_secure_site_is_100_verde():
    b = compute_score(_reference_klarim_like())
    assert b.score == 100 and b.semaphore == "verde"
    assert b.failed == 0


def test_reference_problematic_site_is_red():
    b = compute_score(_reference_problematic())
    assert b.score < YELLOW_THRESHOLD and b.semaphore == "vermelho"
    assert b.fails_by_severity.get(Severity.CRITICA, 0) >= 1


# --------------------------------------------------------------------------- #
# Detecção de mudança de peso/threshold (ALERTA intencional, não bug)
# --------------------------------------------------------------------------- #

def test_severity_weights_unchanged():
    # Se alguém alterar os pesos, este teste falha para forçar revisão consciente.
    assert SEVERITY_WEIGHT[Severity.CRITICA] == 5
    assert SEVERITY_WEIGHT[Severity.ALTA] == 3
    assert SEVERITY_WEIGHT[Severity.MEDIA] == 2
    assert SEVERITY_WEIGHT[Severity.BAIXA] == 1


def test_thresholds_unchanged():
    assert GREEN_THRESHOLD == 90 and YELLOW_THRESHOLD == 50


def test_reference_mix_exact_score_guards_weight_change():
    # Mix fixo: PASS C(5)+A(3)+M(2)+B(1)=11 earned; FAIL B(1) → considered 12.
    # score = round(100*11/12) = 92 com os pesos atuais.
    results = (_pass(Severity.CRITICA, 1) + _pass(Severity.ALTA, 1)
               + _pass(Severity.MEDIA, 1) + _pass(Severity.BAIXA, 1)
               + _fail(Severity.BAIXA, 1))
    b = compute_score(results)
    expected = 92
    assert b.score == expected, (
        f"Expected {expected}, got {b.score} — delta {b.score - expected} "
        f"de mudança de peso/threshold em scoring.py (revisar se intencional).")
    assert b.semaphore == "verde"  # 92 >= 90 e sem FAIL alta/crítica
