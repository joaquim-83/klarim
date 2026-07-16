"""KL-67 — validações de qualidade do profiler (puras, offline)."""

from __future__ import annotations

from scanner import profiler as p


# --- telefone --------------------------------------------------------------- #

def test_validate_phone():
    assert p.validate_phone("(11) 94444-4444") == "11944444444"   # celular SP válido
    assert p.validate_phone("11 3333-4444") == "1133334444"       # fixo válido
    assert p.validate_phone("+55 11 3333-4444") == "1133334444"   # tira o +55
    assert p.validate_phone("(04) 3333-4444") is None             # DDD inexistente
    assert p.validate_phone("3333-4444") is None                  # sem DDD (curto)
    assert p.validate_phone("11 8888-4444-9") is None             # tamanho errado
    assert p.validate_phone("11844444444") is None                # celular sem o 9


# --- redes sociais ---------------------------------------------------------- #

def test_validate_social_handle():
    assert p.validate_social_handle("facebook", "people") is None       # genérico
    assert p.validate_social_handle("facebook", "facebook.com/people") is None
    assert p.validate_social_handle("twitter", "https://twitter.com/intent/tweet?text=x") is None
    assert p.validate_social_handle("instagram", "usecognato") == "usecognato"
    assert p.validate_social_handle("instagram", "https://instagram.com/usecognato/") == "usecognato"
    assert p.validate_social_handle("instagram", "a") is None           # curto demais


def test_handle_matches_domain():
    assert p.handle_matches_domain("usecognato", "usecognato.com.br") is True
    assert p.handle_matches_domain("@usecognato", "usecognato.com.br") is True
    assert p.handle_matches_domain("comendadorburguerbr", "movenegocios.com.br") is False
    assert p.handle_matches_domain("cognato", "usecognato.com.br") is True   # substring 4+


# --- endereço --------------------------------------------------------------- #

def test_validate_address():
    assert p.validate_address("av navbar-nav d-flex container") is None   # CSS raspado
    assert p.validate_address("Centro") is None                           # curto demais
    assert p.validate_address("Rua das Flores, 123, Centro, CEP 01000-000").startswith("Rua")
    assert p.validate_address("Avenida Paulista, 1000, São Paulo") is not None


# --- descrição -------------------------------------------------------------- #

def test_validate_description():
    assert p.validate_description("Share your videos with friends, family, and the world") is None
    assert p.validate_description("Just another WordPress site") is None
    assert p.validate_description("curta") is None
    assert p.validate_description(
        "Somos uma empresa de contabilidade que atua no mercado com foco em pequenas empresas") is not None
    # inglês: quase nenhuma palavra PT distinta → rejeitado
    assert p.validate_description(
        "We are a company that provides accounting services for small businesses and startups today") is None


# --- integração: apply_quality_filters -------------------------------------- #

def test_apply_quality_filters():
    prof = {
        "phone": "(04) 3333-4444",                        # DDD inválido → None
        "address": "av navbar-nav d-flex container",      # CSS → None
        "description": "Share your videos with friends",  # template → None
        "instagram": "comendadorburguerbr",               # não bate domínio → low_confidence
        "facebook": "people",                             # genérico → None
        "linkedin": "movenegocios",                       # bate domínio → mantém
    }
    p.apply_quality_filters(prof, "movenegocios.com.br")
    assert prof["phone"] is None
    assert prof["address"] is None
    assert prof["description"] is None
    assert prof["facebook"] is None
    assert prof["instagram"] == "comendadorburguerbr"
    assert "instagram" in prof["low_confidence_fields"]
    assert prof["linkedin"] == "movenegocios"
    assert "linkedin" not in prof["low_confidence_fields"]


def test_apply_quality_filters_keeps_good_phone():
    prof = {"phone": "(11) 3333-4444"}
    p.apply_quality_filters(prof, "x.com.br")
    assert prof["phone"] == "(11) 3333-4444"       # válido → reformatado e mantido
    assert prof["low_confidence_fields"] == []
