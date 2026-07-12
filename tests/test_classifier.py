"""Testes do classificador de setor em cascata (refino do KL-11) — offline."""

from __future__ import annotations

from discovery.classifier import (
    classify_by_domain,
    classify_by_head,
    classify_by_content,
    classify_sector,
    extract_visible_text,
    PRICE_TIERS,
)


# --- Camada 1: domínio ----------------------------------------------------- #

def test_domain_hotel():
    assert classify_by_domain("https://hotelverdegreen.com.br") == ("hotel", 0.9)


def test_domain_clinica_two_patterns_higher_confidence():
    sector, conf = classify_by_domain("https://clinicaodonto.com.br")
    assert sector == "clinica"
    assert conf == 0.95  # 'clinica' + 'clinic' → ≥2 padrões (KL-54: 'odonto' → odontologia)


def test_domain_unknown_is_none():
    assert classify_by_domain("https://xyztech.com.br") is None


def test_domain_strips_tld_and_subdomain():
    assert classify_by_domain("https://www.pousadacostera.com.br")[0] == "hotel"


# --- Camada 2: título / h1 / meta ------------------------------------------ #

def test_title_escola():
    html = '<html><head><title>Colégio São Paulo - Educação Infantil</title></head></html>'
    assert classify_by_head(html)[0] == "escola"


def test_head_two_matches_high_confidence():
    html = '<head><title>Restaurante Sabor</title></head><body><h1>Cardápio</h1></body>'
    sector, conf = classify_by_head(html)
    assert sector == "restaurante" and conf == 0.8


def test_head_empty_is_none():
    assert classify_by_head("<html><body><p>x</p></body></html>") is None


# --- Camada 3: conteúdo limpo ---------------------------------------------- #

def test_content_ignores_footer():
    html = '''<html><body>
        <main><h1>Hotel Praia Azul</h1><p>Reservas e hospedagem</p></main>
        <footer>Escola de Surf parceira ao lado</footer>
    </body></html>'''
    assert classify_by_content(html)[0] == "hotel"  # não "escola"


def test_extract_visible_text_removes_script_and_nav():
    html = ('<nav>Menu Escola Colégio</nav><script>var x="clinica dentista"</script>'
            '<main>Hotel pousada hospedagem</main>')
    text = extract_visible_text(html).lower()
    assert "hotel" in text and "pousada" in text
    assert "clinica" not in text and "colégio" not in text.lower()


# --- Ambíguos: co-ocorrência ----------------------------------------------- #

def test_reserva_sem_contexto_nao_e_hotel():
    # "reservados" contém "reserva" mas sem âncora de hotel → não classifica
    assert classify_by_content("<p>Todos os direitos reservados</p>") is None


def test_reserva_com_contexto_e_hotel():
    html = "<p>Faça sua reserva de quarto no nosso hotel com diária promocional</p>"
    assert classify_by_content(html)[0] == "hotel"


# --- Orquestração (cascata) ------------------------------------------------ #

def test_cascade_domain_wins_over_content():
    # domínio diz hotel; corpo cita escola — domínio (0.9) decide
    html = "<body>escola colégio educação matrícula ensino</body>"
    sector, tier, conf = classify_sector(html, "https://hotelmar.com.br")
    assert sector == "hotel" and conf >= 0.9 and tier == PRICE_TIERS["hotel"]


def test_cascade_returns_tier_and_confidence():
    # KL-54: a taxonomia fina desmembrou "clínica odontológica" em `odontologia`;
    # o preço é único ⇒ tier `standard` para todos os setores.
    sector, tier, conf = classify_sector(
        "<head><title>Clínica Odontológica</title></head>", "https://site-generico.com.br")
    assert sector == "odontologia" and tier == "standard" and 0.0 < conf <= 1.0


def test_cascade_fallback_outro():
    sector, tier, conf = classify_sector(
        "<p>página institucional sem nada específico</p>", "https://empresa123.com.br")
    assert sector == "outro" and tier == "standard" and conf == 0.0


def test_cascade_no_html_uses_domain_only():
    sector, tier, conf = classify_sector(None, "https://advocaciasilva.com.br")
    assert sector == "juridico" and conf >= 0.9


def test_price_tiers_cover_all_sectors():
    from discovery.classifier import DOMAIN_PATTERNS, SECTOR_KEYWORDS
    for sec in set(DOMAIN_PATTERNS) | set(SECTOR_KEYWORDS):
        assert sec in PRICE_TIERS, f"faltou tier para {sec}"
