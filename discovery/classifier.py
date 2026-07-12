"""Classificação de setor + faixa de preço (refino do KL-11).

Estratégia em **cascata de 3 camadas**, da pista mais confiável para a menos.
Cada camada devolve uma confiança 0.0–1.0; a primeira que passa o corte vence:

  1. **Domínio** — o dono escolheu o nome deliberadamente (0.9–0.95). Ex.:
     `hotelverdegreen.com.br` → `hotel`. É a pista mais forte.
  2. **Cabeçalho** — `<title>`, `<h1>`, meta description (peso 5×; 0.7–0.8).
     Uma keyword no título vale muito mais que no corpo.
  3. **Conteúdo limpo** — texto visível do body, **sem** `<nav>/<footer>/
     <header>/<script>/<style>` (peso 1×; ≥ 0.5).

Sem pista suficiente ⇒ `('outro', 0.0)`. Keywords **ambíguas** ("reserva",
"produto", "entrega") só contam se houver uma âncora do mesmo setor no texto
(co-ocorrência) — evita que "todos os direitos reservados" vire "hotel".

`classify_sector` é síncrono de propósito: é CPU puro (sem I/O), então segue a
regra do projeto de reservar `async` para I/O.
"""

from __future__ import annotations

import re
import unicodedata
from html import unescape
from typing import Dict, Optional, Tuple
from urllib.parse import urlsplit

# --------------------------------------------------------------------------- #
# Dicionários de setor
# --------------------------------------------------------------------------- #

# Camada 1: pistas no próprio domínio (o dono batizou o site). Sem acento.
DOMAIN_PATTERNS: Dict[str, list] = {
    "hotel": ["hotel", "pousada", "hostel", "resort", "inn"],
    "clinica": ["clinica", "clinic", "odonto", "dent", "medic", "saude", "fisio",
                "psico", "nutri", "veterinar"],
    "escola": ["escola", "colegio", "educa", "ensino", "cursos", "academ", "universid"],
    "ecommerce": ["loja", "shop", "store", "comercio", "mercado", "outlet"],
    "condominio": ["condomi", "residen"],
    "juridico": ["advog", "juridi", "advocacia", "direito", "legal"],
    "contabilidade": ["contab", "contad", "fiscal", "tribut"],
    "restaurante": ["restaur", "pizza", "burger", "gastro", "buffet", "cafe",
                    "padaria", "confeitaria"],
    "imobiliaria": ["imob", "imovei", "imovel", "realt"],
    "automotivo": ["auto", "veicul", "carro", "motor", "oficina", "funilaria", "mecanica"],
}

# Camadas 2 e 3: keywords "âncora" (fortes) por setor. Armazenadas SEM acento —
# o texto é "folded" (minúsculo + sem acento) antes de contar, então "clínica"
# e "clinica" casam igual.
SECTOR_KEYWORDS: Dict[str, list] = {
    "hotel": ["hotel", "pousada", "hospedagem", "hospede", "diaria", "check-in",
              "check-out", "quarto", "suite", "hostel", "resort", "cafe da manha"],
    "clinica": ["clinica", "consultorio", "odontolog", "dentista", "paciente",
                "agendamento", "fisioterap", "psicolog", "nutricion", "veterinar",
                "exame", "medico"],
    "escola": ["escola", "colegio", "educacao", "aluno", "matricula", "ensino",
               "professor", "pedagog", "vestibular", "creche", "bercario"],
    "ecommerce": ["carrinho", "comprar", "frete", "catalogo", "checkout", "estoque",
                  "adicionar ao carrinho", "parcelamento", "cupom"],
    "condominio": ["condominio", "morador", "sindico", "assembleia", "portaria",
                   "area comum", "taxa condominial"],
    "juridico": ["advogado", "advocacia", "juridico", "tribunal", "oab", "litigio",
                 "peticao", "processo judicial"],
    "contabilidade": ["contabilidade", "contador", "fiscal", "tributar", "imposto",
                      "escrituracao", "folha de pagamento", "simples nacional"],
    "restaurante": ["restaurante", "cardapio", "delivery", "gastronomia", "pizzaria",
                    "hamburgueria", "buffet", "confeitaria", "padaria", "prato"],
    "imobiliaria": ["imobiliaria", "imovel", "imoveis", "corretor", "apartamento",
                    "locacao", "financiamento imobiliario", "aluguel"],
    "automotivo": ["veiculo", "oficina mecanica", "funilaria", "seminovo",
                   "concessionaria", "pneu", "revisao automotiva", "automovel"],
}

# Keywords ambíguas: só contam para o setor se uma âncora do mesmo setor também
# aparecer no texto (co-ocorrência). Sem acento; casam por substring.
AMBIGUOUS: Dict[str, str] = {
    "reserva": "hotel",     # "reserva de quarto" vs. "direitos reservados"
    "produto": "ecommerce",  # loja vs. "produto interno" (contabilidade)
    "entrega": "ecommerce",  # e-commerce vs. "entrega de serviço"
}

# Faixa de preço por setor (mantida). Valores casam com payments.PRICING.
PRICE_TIERS: Dict[str, str] = {
    "hotel": "standard",           # R$ 29
    "restaurante": "basic",        # R$ 19
    "ecommerce": "professional",   # R$ 39
    "escola": "professional",      # R$ 39
    "clinica": "enterprise",       # R$ 49
    "juridico": "enterprise",      # R$ 49
    "contabilidade": "professional",  # R$ 39
    "condominio": "standard",      # R$ 29
    "imobiliaria": "standard",     # R$ 29
    "automotivo": "basic",         # R$ 19
    # Setores da IA (KL-47A) — o regex não os detecta. Tier só p/ analytics (preço único R$19).
    "saude": "enterprise",
    "tecnologia": "professional",
    "industria": "professional",
    "agencia": "standard",
    "consultoria": "professional",
    "outro": "standard",           # R$ 29
}

# TLDs a remover do domínio para achar o "nome" escolhido pelo dono.
_TLDS = (".com.br", ".net.br", ".org.br", ".gov.br", ".com", ".net", ".org",
         ".br", ".io", ".co", ".app", ".dev")

_BLOCK_RE = re.compile(
    r"<(script|style|noscript|svg|nav|footer|header)\b[^>]*>.*?</\1>", re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


# --------------------------------------------------------------------------- #
# Helpers de texto
# --------------------------------------------------------------------------- #

def _fold(text: str) -> str:
    """Minúsculo + sem acento (NFKD) — casamento robusto de keywords."""
    if not text:
        return ""
    norm = unicodedata.normalize("NFKD", text)
    return "".join(c for c in norm if not unicodedata.combining(c)).lower()


def _host_name(url: str) -> str:
    """Host do URL sem o TLD (mantém subdomínios). Ex.: 'hotelx.com.br' → 'hotelx'."""
    raw = url if "://" in (url or "") else "https://" + (url or "")
    host = urlsplit(raw).netloc.split("@")[-1].split(":")[0].lower()
    for suffix in _TLDS:
        if host.endswith(suffix):
            return host[: -len(suffix)]
    return host


def extract_visible_text(html: str) -> str:
    """Texto visível do HTML, sem ruído de navegação nem código.

    Remove blocos `<script>/<style>/<noscript>/<svg>/<nav>/<footer>/<header>`
    (conteúdo incluso), depois todas as tags restantes, e desescapa entidades.
    """
    if not html:
        return ""
    text = _BLOCK_RE.sub(" ", html)
    text = _TAG_RE.sub(" ", text)
    text = unescape(text)
    return _WS_RE.sub(" ", text).strip()


def _extract_head_text(html: str) -> str:
    """Concatena `<title>`, todos os `<h1>` e as meta descriptions."""
    if not html:
        return ""
    parts = []
    parts += re.findall(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    parts += re.findall(r"<h1[^>]*>(.*?)</h1>", html, re.I | re.S)
    for tag in re.findall(
        r"<meta[^>]+(?:name=[\"']description[\"']|property=[\"']og:description[\"'])[^>]*>",
        html, re.I,
    ):
        m = re.search(r"content=[\"'](.*?)[\"']", tag, re.I | re.S)
        if m:
            parts.append(m.group(1))
    text = _TAG_RE.sub(" ", " ".join(parts))  # limpa tags aninhadas no h1
    return unescape(text)


def _score_sectors(text: str, weight: float) -> Dict[str, float]:
    """Contagem ponderada de keywords por setor num texto.

    Uma keyword ambígua só entra se o setor já tem ≥1 âncora no texto.
    """
    folded = _fold(text)
    if not folded:
        return {}
    scores: Dict[str, float] = {}
    for sector, keywords in SECTOR_KEYWORDS.items():
        base = sum(folded.count(kw) for kw in keywords)
        if base <= 0:
            continue  # sem âncora → nem ambíguas contam (co-ocorrência)
        extra = sum(folded.count(kw) for kw, sec in AMBIGUOUS.items() if sec == sector)
        scores[sector] = (base + extra) * weight
    return scores


# --------------------------------------------------------------------------- #
# Camadas
# --------------------------------------------------------------------------- #

def classify_by_domain(url: str) -> Optional[Tuple[str, float]]:
    """Camada 1 — pistas no domínio. 1 padrão ⇒ 0.9; ≥2 do mesmo setor ⇒ 0.95."""
    name = _host_name(url)
    if not name:
        return None
    best: Optional[Tuple[str, float, int]] = None
    for sector, patterns in DOMAIN_PATTERNS.items():
        matches = sum(1 for p in patterns if p in name)
        if matches == 0:
            continue
        conf = 0.95 if matches >= 2 else 0.9
        if best is None or conf > best[1] or (conf == best[1] and matches > best[2]):
            best = (sector, conf, matches)
    return (best[0], best[1]) if best else None


def classify_by_head(html: str) -> Optional[Tuple[str, float]]:
    """Camada 2 — title + h1 + meta. ≥2 matches ⇒ 0.8; 1 match ⇒ 0.7."""
    scores = _score_sectors(_extract_head_text(html), 1.0)
    if not scores:
        return None
    best = max(scores, key=scores.get)
    n = int(round(scores[best]))
    if n >= 2:
        return (best, 0.8)
    if n >= 1:
        return (best, 0.7)
    return None


def classify_by_content(html: str) -> Optional[Tuple[str, float]]:
    """Camada 3 — head (peso 5×) + body limpo (peso 1×). Domina ⇒ 0.6; senão 0.5."""
    scores: Dict[str, float] = {}
    for source, weight in ((_extract_head_text(html), 5.0), (extract_visible_text(html), 1.0)):
        for sector, sc in _score_sectors(source, weight).items():
            scores[sector] = scores.get(sector, 0.0) + sc
    if not scores:
        return None
    best = max(scores, key=scores.get)
    best_score = scores[best]
    total = sum(scores.values())
    frac = best_score / total if total else 0.0
    if best_score >= 3 and frac >= 0.6:
        return (best, 0.6)
    if best_score >= 1:
        return (best, 0.5)
    return None


# --------------------------------------------------------------------------- #
# Orquestração
# --------------------------------------------------------------------------- #

def classify_sector(html: Optional[str], url: str = "") -> Tuple[str, str, float]:
    """Classifica em cascata. Retorna (setor, price_tier, confiança 0.0–1.0)."""
    if url:
        r = classify_by_domain(url)
        if r and r[1] >= 0.9:
            return (r[0], PRICE_TIERS[r[0]], r[1])
    if html:
        r = classify_by_head(html)
        if r and r[1] >= 0.7:
            return (r[0], PRICE_TIERS[r[0]], r[1])
        r = classify_by_content(html)
        if r and r[1] >= 0.5:
            return (r[0], PRICE_TIERS[r[0]], r[1])
    return ("outro", PRICE_TIERS["outro"], 0.0)
