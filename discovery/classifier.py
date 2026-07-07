"""Classificação de setor + faixa de preço a partir do conteúdo HTML."""

from __future__ import annotations

import re
from typing import Tuple

SECTOR_KEYWORDS = {
    "hotel": ["hotel", "pousada", "hospedagem", "reserva", "hóspede", "diária", "check-in", "check-out"],
    "clinica": ["clínica", "consultório", "médico", "saúde", "paciente", "agendamento", "dentista"],
    "escola": ["escola", "colégio", "educação", "aluno", "matrícula", "ensino", "professor"],
    "ecommerce": ["loja", "comprar", "carrinho", "produto", "frete", "entrega", "catálogo"],
    "condominio": ["condomínio", "morador", "síndico", "assembleia", "portaria", "unidade"],
    "juridico": ["advogado", "advocacia", "jurídico", "escritório", "processo", "direito"],
    "contabilidade": ["contabilidade", "contador", "fiscal", "tributário", "imposto"],
    "restaurante": ["restaurante", "cardápio", "menu", "reserva", "delivery", "gastronomia"],
}

PRICE_TIERS = {
    "hotel": "standard",
    "restaurante": "basic",
    "ecommerce": "professional",
    "escola": "professional",
    "clinica": "enterprise",
    "juridico": "enterprise",
    "contabilidade": "professional",
    "condominio": "standard",
    "outro": "standard",
}


def classify_sector(html: str) -> Tuple[str, str]:
    """Retorna (setor, price_tier). Conta ocorrências de keywords por setor."""
    text = (html or "").lower()
    best_sector, best_score = "outro", 0
    for sector, keywords in SECTOR_KEYWORDS.items():
        score = 0
        for kw in keywords:
            # \b não funciona bem com acentos; conta ocorrências simples.
            score += len(re.findall(re.escape(kw), text))
        if score > best_score:
            best_sector, best_score = sector, score
    return best_sector, PRICE_TIERS.get(best_sector, "standard")
