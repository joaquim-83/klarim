"""KL-165 — fingerprint de plataforma exige same-origin + múltiplos sinais.

Regressão do falso positivo real: telecomsip.com.br foi classificado como WordPress por
referências `wp-content` de domínios de TERCEIROS (i0.wp.com, 1000logos.net) e um honeypot
`/wp-admin` 200. Cobre o helper `is_same_origin`, a exigência de 2+ sinais same-origin para
WordPress e a remoção do marcador cross-origin `cdn.shopify.com` (embed → não é Shopify).
Offline — sem rede, sem Postgres.
"""

from __future__ import annotations

from scanner.tech_detector import detect_tech_stack, is_same_origin


def _names(result):
    return {t["name"] for t in result["technologies"]}


def _tech(result, name):
    return next((t for t in result["technologies"] if t["name"] == name), None)


# --------------------------------------------------------------------------- #
# is_same_origin — helper puro
# --------------------------------------------------------------------------- #

def test_same_origin_relative_url_is_same_origin():
    assert is_same_origin("/wp-content/themes/x/style.css", "telecomsip.com.br") is True
    assert is_same_origin("wp-includes/js/jquery.js", "telecomsip.com.br") is True


def test_same_origin_exact_and_subdomain():
    assert is_same_origin("https://telecomsip.com.br/wp-content/x.js", "telecomsip.com.br")
    assert is_same_origin("https://cdn.telecomsip.com.br/x.js", "telecomsip.com.br")
    # www no alvo → apex e subdomínios ainda casam (comparação pelo domínio registrável).
    assert is_same_origin("https://telecomsip.com.br/x.js", "www.telecomsip.com.br")


def test_same_origin_third_party_is_not():
    assert is_same_origin("https://i0.wp.com/fiberhomebrasil.com.br/wp-content/x.png",
                          "telecomsip.com.br") is False
    assert is_same_origin("https://1000logos.net/wp-content/logo.png",
                          "telecomsip.com.br") is False
    # look-alike não é same-origin (sem o ponto separador).
    assert is_same_origin("https://eviltelecomsip.com.br/x.js", "telecomsip.com.br") is False


def test_same_origin_no_domain_is_conservative():
    # Sem domínio, URL absoluta não é confiável; relativa continua same-origin.
    assert is_same_origin("https://telecomsip.com.br/wp-content/x.js", "") is False
    assert is_same_origin("/wp-content/x.js", "") is True


def test_same_origin_ignores_non_http_schemes():
    assert is_same_origin("data:image/png;base64,AAAA", "x.com.br") is True
    assert is_same_origin("mailto:a@b.com", "x.com.br") is True


# --------------------------------------------------------------------------- #
# WordPress — same-origin + 2 sinais
# --------------------------------------------------------------------------- #

def test_wordpress_cross_origin_refs_do_not_classify():
    """O caso telecomsip.com.br: wp-content só de terceiros → NÃO é WordPress."""
    html = (
        '<img src="https://i0.wp.com/fiberhomebrasil.com.br/wp-content/uploads/logo.png">'
        '<img src="https://1000logos.net/wp-content/uploads/brand.png">'
        '<a href="/wp-admin/">login</a>'  # honeypot 200 — não é sinal
    )
    r = detect_tech_stack({}, html, {}, {}, domain="telecomsip.com.br")
    assert _tech(r, "wordpress") is None


def test_wordpress_same_origin_two_signals_classifies():
    html = (
        '<link href="https://blog.com.br/wp-content/themes/astra/style.css" rel="stylesheet">'
        '<script src="https://blog.com.br/wp-includes/js/jquery.min.js"></script>'
    )
    r = detect_tech_stack({}, html, {}, {}, domain="blog.com.br")
    assert _tech(r, "wordpress") is not None


def test_wordpress_single_same_origin_signal_insufficient():
    # Só /wp-content/ same-origin (1 sinal fraco, sem generator/cookie) → não classifica.
    html = '<img src="https://loja.com.br/wp-content/uploads/foto.jpg">'
    r = detect_tech_stack({}, html, {}, {}, domain="loja.com.br")
    assert _tech(r, "wordpress") is None


def test_wordpress_generator_alone_classifies():
    # Generator é sinal FORTE same-origin (declaração da própria origem) → basta.
    html = '<meta name="generator" content="WordPress 6.5.2">'
    r = detect_tech_stack({}, html, {}, {}, domain="site.com.br")
    t = _tech(r, "wordpress")
    assert t and t["version"] == "6.5.2"


def test_wordpress_cookie_alone_classifies():
    # Cookie wp_settings é same-origin (setado pelo servidor) → sinal forte.
    r = detect_tech_stack({"Set-Cookie": "wp_settings-1=abc; path=/"}, "", {}, {},
                          domain="site.com.br")
    assert _tech(r, "wordpress") is not None


def test_wordpress_relative_paths_count_without_domain():
    # URLs relativas são same-origin mesmo sem domínio informado.
    html = ('<link href="/wp-content/themes/x/s.css"><script src="/wp-includes/js/a.js">'
            '</script>')
    r = detect_tech_stack({}, html, {}, {})
    assert _tech(r, "wordpress") is not None


def test_wordpress_real_blog_regression():
    """Blog WordPress real (generator + assets same-origin) segue classificado."""
    html = (
        '<meta name="generator" content="WordPress 6.4.3">'
        '<link rel="https://api.w.org/" href="https://meublog.com.br/wp-json/">'
        '<script src="https://meublog.com.br/wp-includes/js/wp-emoji-release.min.js"></script>'
    )
    r = detect_tech_stack({}, html, {}, {}, domain="meublog.com.br")
    assert _tech(r, "wordpress") is not None


# --------------------------------------------------------------------------- #
# Shopify — embed cross-origin não classifica; same-origin sim
# --------------------------------------------------------------------------- #

def test_shopify_external_embed_does_not_classify():
    html = (
        '<script src="https://cdn.shopify.com/s/assets/buy_button.js"></script>'
        '<script>var c = ShopifyBuy.buildClient({domain: "x.myshopify.com"});</script>'
    )
    r = detect_tech_stack({}, html, {}, {}, domain="siteinstitucional.com.br")
    assert _tech(r, "shopify") is None


def test_shopify_same_origin_header_classifies():
    r = detect_tech_stack({"x-shopify-stage": "production"}, "", {}, {},
                          domain="minhaloja.com.br")
    assert _tech(r, "shopify") is not None


def test_shopify_same_origin_cookie_family_classifies():
    r = detect_tech_stack({"Set-Cookie": "_shopify_y=xyz; path=/"}, "", {}, {},
                          domain="minhaloja.com.br")
    assert _tech(r, "shopify") is not None


# --------------------------------------------------------------------------- #
# Não-regressão de outras plataformas / detecções cross-origin legítimas
# --------------------------------------------------------------------------- #

def test_astro_generator_not_wordpress():
    # klarim.net (Astro) — generator Astro é detectado; NÃO vira WordPress.
    html = '<meta name="generator" content="Astro v4.15.0">'
    r = detect_tech_stack({}, html, {}, {}, domain="klarim.net")
    assert _tech(r, "astro") is not None
    assert _tech(r, "wordpress") is None


def test_third_party_services_still_cross_origin():
    # Serviços de terceiros (analytics/pagamento/chat) SÃO cross-origin por design — não
    # sofrem same-origin: continuam detectados.
    html = ('<script src="https://www.googletagmanager.com/gtag/js?id=G-ABC"></script>'
            '<script src="https://js.stripe.com/v3"></script>')
    r = detect_tech_stack({}, html, {}, {}, domain="qualquer.com.br")
    assert {"google_analytics_4", "stripe"} <= _names(r)
