# KL-47A + KL-50 L5 — IA para classificação de setor + extração de contato/perfil

**Cards:** KL-47 (parte A) + KL-50 (camada 5) · **Prioridade:** CRÍTICA.
**Problema:** o classificador por regex deixa ~57% dos alvos em `outro` e a extração por
regex ~39% em `sem_contato`. Uma **única** chamada ao **GPT-4o mini** resolve os dois.
**Custo:** ~US$0,001/site (~US$3,5 para os ~4,7k `sem_contato`).

---

## `scanner/ai_enrichment.py` (novo)

`httpx` direto — **sem** o SDK `openai` (mantém o projeto leve).
- `call_openai(system, user)` — gpt-4o-mini, `response_format={"type":"json_object"}` (JSON
  garantido), temperatura 0.1 (consistência), chave de `OPENAI_API_KEY`. **Nunca levanta**:
  qualquer erro de rede/parse → `None`.
- `SYSTEM_PROMPT` + `build_user_prompt(domain, text, current)` — trunca o texto em **3000
  chars** (controle de custo) e injeta os dados atuais para a IA corrigir/confirmar.
- `extract_clean_text(html)` — strip de script/style/tags → texto visível.
- `ai_enrich(domain, html, current_profile)` — orquestra numa **única** chamada; normaliza o
  `sector` para o enum (fora dele → `outro`).
- `merge_ai_into_profile(profile, ai)` — aplica a IA **só nos campos vazios**.
- **Opt-in/fail-open:** `AI_ENRICHMENT_ENABLED = bool(OPENAI_API_KEY)`. Sem a chave, tudo é
  regex-only, **zero impacto**.

## Regra de ouro (inviolável) — a IA complementa, nunca sobrescreve

- **Perfil:** `merge_ai_into_profile` só preenche campo **vazio** (company_name, description,
  email, phone, whatsapp). Se o regex já achou, mantém.
- **Setor:** `store.ai_update_classification` (source `ai`) só atualiza no SQL quando o alvo
  é **fraco** (`sector='outro' OR classification_confidence<0.5`) e **nunca**
  `classification_source='manual'`; e só quando a IA volta com setor ≠ `outro` e conf > 0.7.
- **Contato via IA:** só quando o regex não achou; passa pela **mesma validação de MX**
  (KL-24) antes de tirar o alvo de `sem_contato` — o funil nunca recebe e-mail sem MX.

## Integração

- **Scan worker** (`scanner/main.py::_ai_enrich_profile`): inline, após o `_enrich_profile`
  do KL-50, antes do `upsert_site_profile`. Best-effort (erro só loga).
- **`scripts/enrich_batch.py`**: a IA entra quando o regex não achou e-mail; `await
  asyncio.sleep(1)` entre chamadas (rate limit OpenAI). O e-mail da IA é validado por MX
  (`_validate_ai_email`) antes de `update_target_email` + enfileirar scan.
- **5 setores novos** (a IA classifica, o regex não): `saude`, `tecnologia`, `industria`,
  `agencia`, `consultoria` — em `SECTORS` e `PRICE_TIERS` (tier só p/ analytics, preço único).

## Configuração (segredo — nunca no git)

`OPENAI_API_KEY` vive **só** no `/opt/klarim/.env` da VM. Os serviços `api`/`worker`/
`discovery` já usam `env_file: .env` → a chave é propagada **sem** mudar o
`docker-compose.yml`. `os.environ.get("OPENAI_API_KEY")`; ausente ⇒ regex-only. Opcional
`OPENAI_MODEL` (padrão `gpt-4o-mini`).

## Testes (`tests/test_ai_enrichment.py`, 13)

Classificação (hotel/ecommerce/cauda-longa consultoria), extração de contato/descrição,
fallback (sem chave → None, call_openai → None, httpx explode → None sem levantar), setor
inválido → `outro`, **complementa-não-sobrescreve** (mantém e-mail/telefone do regex,
preenche vazio), truncagem em 3000 chars. Mock de `call_openai`/`httpx` — offline.

## Deploy / validação

Código lê `os.environ`. Passos na VM: adicionar `OPENAI_API_KEY` ao `/opt/klarim/.env`,
redeploy (recria os containers com o novo env), validar `ai_enrich` num alvo real. **Sem
flush de `scan:*`** — a IA não altera o score de segurança (só setor/perfil comercial).

## Arquivos

**Novos:** `scanner/ai_enrichment.py`, `tests/test_ai_enrichment.py`, este relatório.
**Alterados:** `scanner/main.py` (`_ai_enrich_profile`), `scripts/enrich_batch.py`,
`discovery/store.py` (`ai_update_classification`), `discovery/classifier.py` (5 setores),
`claude.md`, `README.md`. **Sem** mudança em `docker-compose.yml` nem em `requirements.txt`
(httpx já existe).
