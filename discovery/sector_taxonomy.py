"""Taxonomia de setores do Klarim (KL-54) — **fonte da verdade única**.

Todos os módulos que precisam de setor importam daqui: o classificador por regex
(`discovery/classifier.py`), a IA (`scanner/ai_enrichment.py`), o profiler
(`scanner/profiler.py`), a API (`api/main.py`) e os scripts de reprocessamento.

- O **setor** é a classificação fina (o que a IA retorna) — 48 setores + `outro`.
- O **macro-setor** agrupa setores para benchmarks/rankings — 13 macros + `outro`.
  É **derivável** por lookup (`get_macro`), sem coluna nova no banco.

Sem impacto no score de segurança. Módulo **puro** (zero imports internos) — pode
ser importado de qualquer camada sem risco de ciclo.
"""

from __future__ import annotations

SECTOR_TAXONOMY = {
    # ── ALIMENTAÇÃO & BEBIDAS ──
    "restaurante":         {"macro": "alimentacao",   "label": "Restaurante"},
    "bar_lanchonete":      {"macro": "alimentacao",   "label": "Bar / Lanchonete / Hamburgueria"},
    "padaria_confeitaria": {"macro": "alimentacao",   "label": "Padaria / Confeitaria"},
    "delivery":            {"macro": "alimentacao",   "label": "Delivery / Food Truck"},

    # ── SAÚDE ──
    "clinica":             {"macro": "saude",          "label": "Clínica Médica"},
    "odontologia":         {"macro": "saude",          "label": "Odontologia"},
    "farmacia":            {"macro": "saude",          "label": "Farmácia / Manipulação"},
    "laboratorio":         {"macro": "saude",          "label": "Laboratório / Diagnóstico"},
    "psicologia":          {"macro": "saude",          "label": "Psicologia / Terapia"},
    "veterinaria":         {"macro": "saude",          "label": "Veterinária"},
    "hospital":            {"macro": "saude",          "label": "Hospital / Pronto-socorro"},
    "nutricao":            {"macro": "saude",          "label": "Nutrição / Saúde Funcional"},

    # ── BELEZA & BEM-ESTAR ──
    "salao_barbearia":     {"macro": "beleza",         "label": "Salão / Barbearia"},
    "estetica_spa":        {"macro": "beleza",         "label": "Estética / Spa"},
    "academia":            {"macro": "beleza",         "label": "Academia / Pilates / Yoga"},

    # ── COMÉRCIO ──
    "ecommerce":           {"macro": "comercio",       "label": "E-commerce / Loja Online"},
    "loja_moda":           {"macro": "comercio",       "label": "Moda / Calçados / Acessórios"},
    "otica":               {"macro": "comercio",       "label": "Ótica"},
    "supermercado":        {"macro": "comercio",       "label": "Supermercado / Mercearia"},
    "petshop":             {"macro": "comercio",       "label": "Pet Shop"},
    "material_construcao": {"macro": "comercio",       "label": "Material de Construção"},
    "moveis_decoracao":    {"macro": "comercio",       "label": "Móveis / Decoração"},
    "eletronicos":         {"macro": "comercio",       "label": "Informática / Eletrônicos"},

    # ── SERVIÇOS PROFISSIONAIS ──
    "contabilidade":       {"macro": "servicos",       "label": "Contabilidade"},
    "juridico":            {"macro": "servicos",       "label": "Advocacia / Jurídico"},
    "consultoria":         {"macro": "servicos",       "label": "Consultoria"},
    "agencia":             {"macro": "servicos",       "label": "Agência / Marketing / Design"},
    "tecnologia":          {"macro": "servicos",       "label": "Tecnologia / Software / TI"},
    "seguros_financeiro":  {"macro": "servicos",       "label": "Seguros / Financeiro"},
    "rh_recrutamento":     {"macro": "servicos",       "label": "RH / Recrutamento"},
    "grafica":             {"macro": "servicos",       "label": "Gráfica / Impressão"},

    # ── IMOBILIÁRIO & CONSTRUÇÃO ──
    "imobiliaria":         {"macro": "imoveis",        "label": "Imobiliária"},
    "construtora":         {"macro": "imoveis",        "label": "Construtora / Incorporadora"},
    "arquitetura":         {"macro": "imoveis",        "label": "Arquitetura / Design de Interiores"},
    "condominio":          {"macro": "imoveis",        "label": "Condomínio / Administradora"},

    # ── AUTOMOTIVO ──
    "automotivo":          {"macro": "automotivo",     "label": "Oficina / Concessionária / Autopeças"},

    # ── EDUCAÇÃO ──
    "escola":              {"macro": "educacao",       "label": "Escola"},
    "curso_idiomas":       {"macro": "educacao",       "label": "Curso Livre / Idiomas"},
    "faculdade":           {"macro": "educacao",       "label": "Faculdade / Ensino Superior"},

    # ── HOSPEDAGEM & TURISMO ──
    "hotel":               {"macro": "turismo",        "label": "Hotel / Pousada"},
    "turismo_viagens":     {"macro": "turismo",        "label": "Turismo / Agência de Viagens"},

    # ── EVENTOS & ENTRETENIMENTO ──
    "eventos_buffet":      {"macro": "eventos",        "label": "Eventos / Buffet / Cerimonial"},
    "fotografia":          {"macro": "eventos",        "label": "Fotografia / Vídeo / Produtora"},

    # ── INDÚSTRIA ──
    "industria":           {"macro": "industria",      "label": "Indústria / Fábrica"},

    # ── TRANSPORTE ──
    "transporte":          {"macro": "transporte",     "label": "Transporte / Logística"},

    # ── INSTITUCIONAL ──
    "religioso":           {"macro": "institucional",  "label": "Igreja / Instituição Religiosa"},
    "ong_associacao":      {"macro": "institucional",  "label": "ONG / Associação / Sindicato"},
    "governo":             {"macro": "institucional",  "label": "Governo / Órgão Público"},

    # ── CATCH-ALL ──
    "outro":               {"macro": "outro",          "label": "Outro"},
}

# Sets derivados para validação rápida.
VALID_SECTORS = set(SECTOR_TAXONOMY.keys())
MACRO_SECTORS = sorted({v["macro"] for v in SECTOR_TAXONOMY.values()})

# Labels legíveis dos macro-setores (para dropdowns/rankings).
MACRO_LABELS = {
    "alimentacao":   "Alimentação & Bebidas",
    "saude":         "Saúde",
    "beleza":        "Beleza & Bem-estar",
    "comercio":      "Comércio",
    "servicos":      "Serviços Profissionais",
    "imoveis":       "Imobiliário & Construção",
    "automotivo":    "Automotivo",
    "educacao":      "Educação",
    "turismo":       "Hospedagem & Turismo",
    "eventos":       "Eventos & Entretenimento",
    "industria":     "Indústria",
    "transporte":    "Transporte",
    "institucional": "Institucional",
    "outro":         "Outro",
}

# Aliases de setores antigos que foram desmembrados/renomeados na taxonomia nova.
# O genérico `saude` da IA antiga (KL-47A) virou os setores finos de saúde — sem um
# alias, os 3 alvos com `saude` iriam para `outro`. Mapeamos para `clinica` (a IA
# refina no batch). Adicione aqui qualquer setor legado que suma da taxonomia.
SECTOR_ALIASES = {
    "saude": "clinica",
}


def get_macro(sector: str) -> str:
    """Macro-setor de um setor. `outro` se não encontrado."""
    entry = SECTOR_TAXONOMY.get(sector)
    return entry["macro"] if entry else "outro"


def get_label(sector: str) -> str:
    """Label legível de um setor. O próprio valor se não encontrado."""
    entry = SECTOR_TAXONOMY.get(sector)
    return entry["label"] if entry else sector


def normalize_sector(sector: str) -> str:
    """Normaliza um setor (vindo da IA ou de input): minúsculo, `_` no lugar de
    espaço/hífen, resolve aliases legados. Setor inválido ⇒ `outro`."""
    if not sector:
        return "outro"
    sector = sector.strip().lower().replace(" ", "_").replace("-", "_")
    sector = SECTOR_ALIASES.get(sector, sector)
    return sector if sector in VALID_SECTORS else "outro"
