"""KL-84 — sinônimos de setor: resolvidos ANTES de consultar a tabela `sectors`. Se a IA
retorna 'advocacia', o sinônimo resolve para 'juridico' (já existe) → sem proposta redundante.
Resolução case-insensitive; espaços/hífens viram underscore (igual a `normalize_sector`)."""

from __future__ import annotations

SYNONYMS = {
    "advocacia": "juridico",
    "advogado": "juridico",
    "dentista": "odontologia",
    "loja_virtual": "ecommerce",
    "loja_online": "ecommerce",
    "hospedagem": "hotel",
    "pousada": "hotel",
    "hamburgueria": "lanchonete",
    "pizzaria": "restaurante",
    "churrascaria": "restaurante",
    "cafeteria": "padaria_confeitaria",
    "barbearia": "salao",
    "manicure": "salao",
    "fisioterapeuta": "fisioterapia",
    "psicologo": "psicologia",
    "nutricionista": "nutricao",
    "personal_trainer": "academia",
    "crossfit": "academia",
    "autoescola": "automotivo",
    "mecanica": "automotivo",
    "creche": "escola",
    "faculdade": "escola",
    "universidade": "escola",
    "grafica": "industria",
    "transportadora": "transporte",
    "mudanca": "transporte",
    "contador": "contabilidade",
    "corretora_imoveis": "imobiliaria",
}


def resolve_synonym(slug: str) -> str:
    """Normaliza + resolve sinônimo. Retorna o slug canônico (ou o próprio, normalizado)."""
    s = (slug or "").strip().lower().replace(" ", "_").replace("-", "_")
    return SYNONYMS.get(s, s)
