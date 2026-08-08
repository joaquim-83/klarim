"""KL-153 — validadores puros (sem I/O, testáveis) usados pelo KYC do Security Gate.

`validate_cpf` NÃO consulta a Receita Federal — só valida o formato e os 2 dígitos verificadores
pelo algoritmo oficial (módulo 11). Retorna SEMPRE formatado (`000.000.000-00`) ou levanta
`ValueError`."""
from __future__ import annotations

import re


def _cpf_check_digit(digits: str) -> int:
    """Dígito verificador de `digits` (9 ou 10 dígitos) pelo módulo 11 oficial.
    Pesos decrescentes começando em len+1 (10→2 para o 1º DV, 11→2 para o 2º)."""
    weight = len(digits) + 1
    total = sum(int(d) * (weight - i) for i, d in enumerate(digits))
    remainder = (total * 10) % 11
    return 0 if remainder >= 10 else remainder


def validate_cpf(cpf: str) -> str:
    """Valida um CPF e devolve formatado (`000.000.000-00`).

    Aceita com ou sem formatação (pontos/traço/espaços são removidos). Rejeita:
    - qualquer coisa que não tenha exatamente 11 dígitos;
    - sequências repetidas (`111.111.111-11`, …);
    - CPF cujos 2 dígitos verificadores não conferem.

    Levanta `ValueError` se inválido.
    """
    raw = re.sub(r"\D", "", cpf or "")
    if len(raw) != 11:
        raise ValueError("CPF deve ter 11 dígitos.")
    if raw == raw[0] * 11:
        raise ValueError("CPF inválido (sequência repetida).")
    if _cpf_check_digit(raw[:9]) != int(raw[9]):
        raise ValueError("CPF inválido (1º dígito verificador).")
    if _cpf_check_digit(raw[:10]) != int(raw[10]):
        raise ValueError("CPF inválido (2º dígito verificador).")
    return f"{raw[:3]}.{raw[3:6]}.{raw[6:9]}-{raw[9:]}"
