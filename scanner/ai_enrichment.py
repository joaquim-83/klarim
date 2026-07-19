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

from discovery.sector_taxonomy import VALID_SECTORS, normalize_sector
from discovery.cnae import derive_section, derive_division, format_cnae

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_URL = "https://api.openai.com/v1/chat/completions"

AI_ENRICHMENT_ENABLED = bool(OPENAI_API_KEY)

# Setores válidos: a taxonomia completa do Klarim (KL-54 — 48 setores + outro).
# Fonte da verdade em discovery/sector_taxonomy.py; mantido como alias local para
# compatibilidade com quem importava `SECTORS` daqui.
SECTORS = VALID_SECTORS

_MAX_TEXT_CHARS = 3000

# Lista de setores (sem `outro`) para o prompt — gerada da taxonomia, nunca à mão.
_SECTOR_LIST = ", ".join(sorted(VALID_SECTORS - {"outro"}))

# KL-55: CNAE (estrutura) + descrição/tags (identidade). O prompt pede classificação
# multi-setor via CNAE 2.0 do IBGE + descrição em linguagem natural + tags. Mantém
# `sector_legacy` + `sector_confidence` para a retrocompatibilidade com `targets.sector`.
SYSTEM_PROMPT = (
    "Você é um analista de inteligência comercial que examina sites brasileiros.\n"
    "Analise o texto extraído de um site e retorne um JSON com os campos abaixo.\n"
    "Responda APENAS com JSON válido, sem markdown, sem explicação.\n\n"
    "Campos obrigatórios:\n"
    '- "description": descrição do negócio em 1-3 frases, linguagem natural, PT-BR.\n'
    "  Descreva O QUE a empresa realmente faz, não o que a CNAE diz.\n"
    '- "business_type": tipo do negócio em 3-5 palavras (ex: "PropTech SaaS", '
    '"Segurança patrimonial", "Hamburgueria artesanal", "Clínica odontológica").\n'
    '- "company_name": nome da empresa (limpo, sem slogan); null se não estiver claro.\n'
    '- "tags": lista de 5-10 palavras-chave do negócio para busca, em português, '
    'minúsculas (ex: ["proptech", "saas", "gestão condominial", "airbnb"]).\n'
    '- "cnaes": lista de 2-5 códigos CNAE 2.0 do IBGE mais relevantes, em ordem de '
    "relevância (mais relevante primeiro). Cada item:\n"
    '  - "code": código no formato classe (ex: "62.01-5") ou grupo (ex: "80.1")\n'
    '  - "description": descrição oficial da atividade CNAE\n'
    '  - "confidence": float 0.0 a 1.0\n'
    "  Uma empresa pode ter múltiplos CNAEs — liste todos os aplicáveis.\n"
    '- "sector_legacy": setor do negócio. PREFIRA um destes valores exatos (taxonomia '
    "conhecida): " + _SECTOR_LIST + ", outro.\n"
    "  Se NENHUM encaixar bem mas o negócio tiver um setor claro e específico, PROPONHA um "
    'novo setor: escolha um slug curto em snake_case (ex: "clinica_veterinaria", '
    '"loja_pet", "estudio_tatuagem") e marque "is_new_sector": true. Use "outro" APENAS '
    "quando o negócio for genérico/indefinido.\n"
    '- "sector_confidence": float 0.0 a 1.0 (confiança no sector_legacy).\n'
    '- "is_new_sector": true SÓ quando sector_legacy NÃO está na lista conhecida acima '
    "(setor novo proposto); false caso contrário.\n"
    '- "sector_label": rótulo humano do setor em PT-BR (ex: "Clínica Veterinária"); '
    "obrigatório quando is_new_sector=true.\n"
    '- "macro_sector_suggestion": macro-categoria do setor novo, um de: alimentacao, '
    "saude, beleza, comercio, servicos, imoveis, automotivo, educacao, turismo, eventos, "
    "industria, transporte, tecnologia, financeiro, institucional, outro "
    "(obrigatório quando is_new_sector=true).\n"
    '- "contacts_found": objeto com campos opcionais:\n'
    '  - "email": email comercial (null se não encontrado)\n'
    '  - "phone": telefone no formato (DD) XXXXX-XXXX (null se não encontrado)\n'
    '  - "whatsapp": número WhatsApp com DDI+DDD (null se não encontrado)'
)


def build_system_prompt(known_sectors: Optional[list] = None) -> str:
    """Prompt do sistema com a lista de setores CONHECIDOS dinâmica (KL-84). `known_sectors`
    (slugs official+approved vindos da tabela, cache 1h no chamador) é anexada à lista base
    para a IA reusar setores já aprovados antes de propor novos. Sem lista → prompt base."""
    if not known_sectors:
        return SYSTEM_PROMPT
    extra = sorted({str(s).strip().lower() for s in known_sectors if str(s).strip()}
                   - VALID_SECTORS - {"outro"})
    if not extra:
        return SYSTEM_PROMPT
    return SYSTEM_PROMPT + ("\n\nSetores adicionais já aprovados (reuse-os quando encaixarem, "
                            "com is_new_sector=false): " + ", ".join(extra))

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


def _normalize_cnaes(raw) -> list:
    """Limpa a lista de CNAEs da IA: formata o código, deriva seção/divisão (offline),
    valida a confiança. Descarta itens sem código utilizável."""
    out = []
    for c in (raw or []):
        if not isinstance(c, dict):
            continue
        code = format_cnae(c.get("code") or "")
        if len(re.sub(r"\D", "", code)) < 3:  # nem grupo (3 dígitos)
            continue
        try:
            conf = float(c.get("confidence") or 0.0)
        except (TypeError, ValueError):
            conf = 0.0
        out.append({
            "code": code,
            "description": (str(c.get("description") or "").strip() or None),
            "confidence": conf,
            "section": derive_section(code),
            "division": derive_division(code),
        })
    return out[:5]


async def ai_enrich(domain: str, html_text: str, current_profile: Optional[dict] = None,
                    current_sector: str = "outro", known_sectors: Optional[list] = None) -> Optional[dict]:
    """Enriquece os dados do site com IA numa única chamada. ``None`` se indisponível.

    Retorna ``{description, business_type, company_name, tags, cnaes, sector_legacy,
    sector, sector_confidence, is_new_sector, sector_label, macro_sector_suggestion,
    contacts_found}``. **Retrocompatibilidade:** `sector` espelha `sector_legacy`
    (KL-84: setor NOVO — is_new_sector=true — preserva o slug sanitizado em vez de virar
    'outro'; o `process_classification` decide criar a proposta)."""
    if not OPENAI_API_KEY:
        return None
    text = extract_clean_text(html_text) if "<" in (html_text or "") else (html_text or "")
    prompt = build_user_prompt(domain, text, current_profile)
    result = await call_openai(build_system_prompt(known_sectors), prompt, max_tokens=900)
    if not result:
        return None
    # KL-84 — taxonomia aberta: setor conhecido → normaliza (alias/clamp). Setor NOVO
    # (is_new_sector) → preserva o slug sanitizado (só [a-z0-9_], máx 50) p/ o process_classification.
    is_new = bool(result.get("is_new_sector"))
    raw = str(result.get("sector_legacy") or result.get("sector") or "outro").strip().lower()
    if is_new and raw and raw != "outro" and raw not in VALID_SECTORS:
        legacy = re.sub(r"[^a-z0-9_]", "", raw.replace(" ", "_").replace("-", "_"))[:50] or "outro"
        result["is_new_sector"] = legacy != "outro"
    else:
        # setor conhecido (ou vazio): normaliza; alias saude→clinica; inválido⇒outro.
        legacy = normalize_sector(raw)
        result["is_new_sector"] = False
    result["sector_legacy"] = legacy
    result["sector"] = legacy
    try:
        result["sector_confidence"] = float(result.get("sector_confidence") or 0.0)
    except (TypeError, ValueError):
        result["sector_confidence"] = 0.0
    # CNAEs (formata + deriva seção/divisão offline) e tags (strings limpas, minúsculas).
    result["cnaes"] = _normalize_cnaes(result.get("cnaes"))
    tags = result.get("tags")
    result["tags"] = ([str(t).strip().lower() for t in tags if str(t).strip()][:10]
                      if isinstance(tags, list) else [])
    return result


def merge_ai_into_profile(profile: dict, ai: dict) -> list:
    """Aplica os dados da IA ao ``profile``. Campos de identidade só preenchem VAZIO
    (regra de ouro); **tags** a IA é a fonte, então sobrescreve. Retorna o que mudou."""
    changed = []
    contacts = ai.get("contacts_found") or {}
    mapping = [
        ("company_name", ai.get("company_name")),
        ("description", ai.get("description")),
        ("business_type", ai.get("business_type")),  # KL-55
        ("commercial_email", contacts.get("email")),
        ("phone", contacts.get("phone")),
        ("whatsapp", contacts.get("whatsapp")),
    ]
    for field, value in mapping:
        if value and not profile.get(field):
            profile[field] = value
            changed.append(field)
    tags = ai.get("tags")  # KL-55: tags são da IA (sobrescreve se vier lista não vazia)
    if tags:
        profile["tags"] = tags
        changed.append("tags")
    return changed
