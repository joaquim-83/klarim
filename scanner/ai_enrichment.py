"""Enriquecimento por IA (GPT-4o mini) — setor + contatos + perfil (KL-47A / KL-50 L5).

O classificador por regex deixa ~57% dos alvos como "outro" e a extração por regex ~39%
como "sem_contato". Uma **única** chamada ao GPT-4o mini resolve os dois: classifica o
setor (inclui cauda longa que o regex nunca pega), extrai contatos em texto corrido e gera
uma descrição do negócio. Custo ~US$0,001/site (~3k tokens in + 500 out).

**Fail-open / opt-in:** sem `OPENAI_API_KEY` no ambiente, toda a IA é silenciosamente
desligada e o scanner funciona 100% como antes (regex only). Qualquer erro de rede/parse
retorna ``None`` — nunca derruba o scan. Usa **httpx direto** (sem o SDK ``openai``).

**Regra de ouro:** a IA **complementa** o regex, nunca sobrescreve. Só preenche campo vazio
de perfil; o setor só é atualizado quando a classificação atual é fraca (`outro`/conf baixa,
garantido no store) e **nunca** um alvo classificado manualmente.
"""

from __future__ import annotations

import json
import os
import re
from typing import Optional

import httpx

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_URL = "https://api.openai.com/v1/chat/completions"

AI_ENRICHMENT_ENABLED = bool(OPENAI_API_KEY)

# Setores válidos: os 10 originais + 5 que a IA classifica e o regex não.
SECTORS = {
    "hotel", "clinica", "ecommerce", "restaurante", "escola",
    "imobiliaria", "juridico", "contabilidade", "automotivo", "condominio",
    "saude", "tecnologia", "industria", "agencia", "consultoria", "outro",
}

_MAX_TEXT_CHARS = 3000

SYSTEM_PROMPT = (
    "Você é um analista de inteligência comercial que examina sites brasileiros.\n"
    "Analise o texto extraído de um site e retorne um JSON com os campos abaixo.\n"
    "Responda APENAS com JSON válido, sem markdown, sem explicação.\n\n"
    "Campos obrigatórios:\n"
    '- "sector": um dos valores exatos: hotel, clinica, ecommerce, restaurante, escola, '
    "imobiliaria, juridico, contabilidade, automotivo, condominio, saude, tecnologia, "
    "industria, agencia, consultoria, outro\n"
    '- "sector_confidence": float 0.0 a 1.0\n'
    '- "company_name": nome da empresa (limpo, sem slogan)\n'
    '- "description": resumo do negócio em 1-2 frases em português\n'
    '- "contacts_found": objeto com campos opcionais:\n'
    '  - "email": email comercial encontrado no texto (null se não encontrado)\n'
    '  - "phone": telefone no formato (DD) XXXXX-XXXX (null se não encontrado)\n'
    '  - "whatsapp": número WhatsApp com DDI+DDD (null se não encontrado)\n'
    '- "business_type": tipo de negócio em 3-5 palavras (ex: "software jurídico", '
    '"hotel boutique", "clínica odontológica")'
)

# Remoção de script/style/tags para extrair texto limpo (alimenta a IA barato).
_SCRIPT_RE = re.compile(r"<script[^>]*>.*?</script>", re.DOTALL | re.IGNORECASE)
_STYLE_RE = re.compile(r"<style[^>]*>.*?</style>", re.DOTALL | re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def extract_clean_text(html: str, max_chars: int = _MAX_TEXT_CHARS) -> str:
    """Texto visível do HTML (sem script/style/tags), truncado para controlar custo."""
    text = _SCRIPT_RE.sub(" ", html or "")
    text = _STYLE_RE.sub(" ", text)
    text = _TAG_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    return text[:max_chars]


def build_user_prompt(domain: str, text: str, current_data: Optional[dict] = None) -> str:
    """Prompt do usuário com o texto do site (truncado) + dados atuais para correção."""
    current_data = current_data or {}
    truncated = text[:_MAX_TEXT_CHARS] if len(text) > _MAX_TEXT_CHARS else text
    parts = [f"Domínio: {domain}\n\nTexto extraído do site:\n{truncated}"]
    if current_data.get("company_name"):
        parts.append(f"\nNome atual no cadastro: {current_data['company_name']}")
    if current_data.get("sector") and current_data["sector"] != "outro":
        parts.append(f"Setor atual: {current_data['sector']}")
    return "\n".join(parts)


async def call_openai(system: str, user: str, max_tokens: int = 500) -> Optional[dict]:
    """Chamada única à API OpenAI. Retorna o JSON parseado ou ``None`` em qualquer erro."""
    if not OPENAI_API_KEY:
        return None
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                OPENAI_URL,
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}",
                         "Content-Type": "application/json"},
                json={
                    "model": OPENAI_MODEL,
                    "messages": [{"role": "system", "content": system},
                                 {"role": "user", "content": user}],
                    "max_tokens": max_tokens,
                    "temperature": 0.1,
                    "response_format": {"type": "json_object"},
                },
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            return json.loads(content)
    except Exception as exc:  # noqa: BLE001 - IA é best-effort, nunca derruba o scan
        print(f"[ai] chamada OpenAI falhou: {exc!r}", flush=True)
        return None


async def ai_enrich(domain: str, html_text: str, current_profile: Optional[dict] = None,
                    current_sector: str = "outro") -> Optional[dict]:
    """Enriquece os dados do site com IA numa única chamada. ``None`` se indisponível.

    Retorna ``{sector, sector_confidence, company_name, description, contacts_found,
    business_type}``. O ``sector`` é normalizado para ``outro`` se vier fora do enum.
    """
    if not OPENAI_API_KEY:
        return None
    text = extract_clean_text(html_text) if "<" in (html_text or "") else (html_text or "")
    prompt = build_user_prompt(domain, text, current_profile)
    result = await call_openai(SYSTEM_PROMPT, prompt)
    if not result:
        return None
    if result.get("sector") not in SECTORS:
        result["sector"] = "outro"
    try:
        result["sector_confidence"] = float(result.get("sector_confidence") or 0.0)
    except (TypeError, ValueError):
        result["sector_confidence"] = 0.0
    return result


def merge_ai_into_profile(profile: dict, ai: dict) -> list:
    """Aplica os dados da IA ao ``profile`` **só nos campos vazios**. Retorna o que mudou."""
    changed = []
    contacts = ai.get("contacts_found") or {}
    mapping = [
        ("company_name", ai.get("company_name")),
        ("description", ai.get("description")),
        ("commercial_email", contacts.get("email")),
        ("phone", contacts.get("phone")),
        ("whatsapp", contacts.get("whatsapp")),
    ]
    for field, value in mapping:
        if value and not profile.get(field):
            profile[field] = value
            changed.append(field)
    return changed
