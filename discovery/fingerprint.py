"""Detecção de plataforma a partir do HTML servido (fingerprinting)."""

from __future__ import annotations

from scanner.checks.base import domain_of

FINGERPRINTS = {
    "duda": ["cdn-website.com", "multiscreensite.com", "/_dm/s/rt/"],
    "wix": ["wixsite.com", "parastorage.com", "static.wixstatic.com"],
    "squarespace": ["squarespace.com", "sqsp.net", "static1.squarespace"],
    "shopify": ["cdn.shopify.com", "myshopify.com"],
    "wordpress": ["/wp-content/", "/wp-includes/", 'name="generator" content="wordpress'],
    "cra": ["create-react-app", "/static/js/main.", "/static/css/main."],
}

# Ordem de verificação: plataformas mais específicas primeiro.
_ORDER = ["duda", "wix", "squarespace", "shopify", "wordpress", "cra"]


def detect_platform(url: str, html: str) -> str:
    """Retorna o nome da plataforma detectada, ou 'unknown'."""
    haystack = (html or "").lower()
    host = domain_of(url)
    for platform in _ORDER:
        for sig in FINGERPRINTS[platform]:
            s = sig.lower()
            if s in haystack or s in host:
                return platform
    return "unknown"
