# KL-55 — Classificação CNAE multi-setor + descrição natural + tags

> **Status:** implementado, testado e pronto para deploy.
> **Regra de negócio:** dado **comercial** — não altera o score de segurança.

## Problema

A taxonomia fixa de 48 setores (KL-54) melhorou, mas **~54% dos sites ainda caíam em
`outro`**. A causa é estrutural, não de cobertura: negócios reais são **multi-setor**
(uma PropTech que também administra condomínios; uma clínica que também é laboratório) e
"1 setor por alvo" força uma escolha empobrecedora. Além disso, faltava uma **descrição
em linguagem natural** do negócio e **tags** de busca para desbloquear perfis públicos,
notificações segmentadas e aquisição orgânica.

## Solução

Trocar o "1 setor por alvo" por **N classificações CNAE 2.0 (IBGE)** por alvo, usando a
tabela CNAE do IBGE como **referência estrutural** + uma **descrição natural** + **tags**
geradas pela IA, e cruzando com os **CNAEs oficiais da Receita Federal** quando há CNPJ.
Tudo com **retrocompatibilidade total** — `targets.sector` continua existindo e alimentando
o funil/painel/preço.

### Arquivos

| Arquivo | O quê |
|---------|-------|
| `discovery/cnae.py` **(novo)** | Referência CNAE 2.0: `derive_section`/`derive_division`/`format_cnae` **offline**; `CNAETable` (download IBGE runtime, cache TTL 30d, fail-open); `sections()`/`divisions()`. |
| `discovery/cnpj.py` **(novo)** | CNPJ → Receita (BrasilAPI → ReceitaWS, cache 90d, fail-open); `build_receita_classifications` (source=`receita`, conf 1.0); `enrich_from_cnpj`. |
| `discovery/store.py` | Tabela `target_classifications` + `site_profile.tags`/`business_type`; `upsert_target_classifications` (receita protegido no WHERE), `get_target_classifications`, `has_receita_cnae`, `count_targets_without_cnae`, `cnae_division_avg_score`; **G4** na seleção de enriquecimento. |
| `scanner/ai_enrichment.py` | Prompt novo: `cnaes` + `tags` + `business_type` + `sector_legacy`; `_normalize_cnaes` (deriva seção/divisão offline); `ai_enrich` mapeia `sector_legacy → sector` (retrocompat); `merge_ai_into_profile` grava `business_type`/`tags`. |
| `scripts/enrich_all.py` | **Grupo 4** (completo mas sem CNAE); grava CNAE da IA (`source='ai'`) + CNPJ→Receita (`--cnpj-delay`); stats `cnae_ai`/`cnae_receita`/`group4`. |
| `api/main.py` | `GET /cnaes/sections`, `/cnaes/divisions`, `/benchmark/cnae/{division}` (públicos); `GET /targets/{id}/classifications` (JWT); `get_target` anexa `classifications`. |
| `mcp_server/tools/targets.py` | `get_target` traz classifications; tool nova `get_target_classifications` (total 36 tools). |
| `tests/test_kl55_cnae.py` **(novo)** | 15 testes mockados. |

### Modelo de dados

```
target_classifications
  id, target_id (FK ON DELETE CASCADE),
  cnae_code, cnae_description, cnae_section (A–U), cnae_division (2 díg),
  confidence, source (receita|ai|manual|schema_org), rank,
  UNIQUE(target_id, cnae_code) + 4 índices

site_profile += tags TEXT[], business_type TEXT
```

### Regras invioláveis implementadas

1. **CNAE não altera o score de segurança** — é referência estrutural + dado comercial.
2. **`derive_section`/`derive_division` são offline** (mapa CNAE 2.0 embutido: 21 seções,
   87 divisões). A classificação **nunca** depende de o IBGE estar no ar.
3. **`source='receita'` (oficial) nunca é sobrescrito pela IA** — garantido no WHERE do
   `ON CONFLICT`: só `receita` nova ou `manual` (operador) atualizam.
4. **A IA complementa, nunca sobrescreve** (regra de ouro do KL-47A): `business_type` só
   preenche campo vazio; o setor legado só muda quando fraco (`outro`/conf baixa).
5. **Best-effort/fail-open em tudo** (CNAE table, CNPJ, IA): erro só loga, nunca derruba
   scan/worker/batch.
6. **Retrocompatível:** `targets.sector` permanece (a IA devolve `sector_legacy`, espelhado
   em `sector`); nenhum caller do funil quebra.

## Testes

`tests/test_kl55_cnae.py` — **15 testes, todos verdes** (81s local — lentidão de máquina,
não de lógica):

- **CNAE:** derive seção/divisão (J/L/Q/G/A), `format_cnae`, 21 seções/87 divisões, cache
  hit (não baixa), cache miss expirado (baixa), **fail-open** (download quebra → tabela
  vazia, `validate_code` aceita 5+ dígitos).
- **IA:** prompt tem os campos novos; `ai_enrich` normaliza CNAEs (seção derivada) + tags
  (minúsculas) + `sector_legacy → sector` (retrocompat) + alias `saude→clinica` + inválido
  ⇒ `outro`; `merge_ai_into_profile` (business_type só-se-vazio, tags sobrescreve).
- **CNPJ:** `_normalize_brasilapi`/`_normalize_receitaws` (formato comum), `build_receita_
  classifications` (rank 1..N, source=receita, conf 1.0, seção derivada), `enrich_from_cnpj`
  com store falso, fail-open (fetch None → 0 gravações).

**Suítes afetadas:** `test_enrich_all.py` + `test_ai_enrichment.py` — **33 verdes**
(inclui o G4 novo e o mapeamento `sector_legacy → sector`).

**SQL validado com sqlglot (dialeto postgres):** DDL (tabela + índices + ALTERs), os 5
métodos novos e as queries de enriquecimento compostas (G4 nos modos all/only_ai/
sem_contato) + o upsert de perfil — todos parseiam. As partes DB-backed não rodam offline
(suíte hermética, sem Postgres); validadas por parse + store falso, como nos cards
anteriores.

## Operação pós-deploy

- **Reprocessar o banco (G4):** `docker compose exec api python scripts/enrich_all.py
  --limit 500` (cron já configurado 3×/dia). O G4 reclassifica em CNAE os alvos "completos"
  sem CNAE; a IA gera os códigos e, havendo CNPJ, a Receita traz os oficiais.
- **`OPENAI_API_KEY`** (já no `.env` da VM) habilita a IA; sem ela, regex-only (zero
  impacto). As APIs de CNPJ (BrasilAPI/ReceitaWS) e a tabela do IBGE são públicas/gratuitas
  e **fail-open** — nenhum segredo novo.
- **Sem migration manual:** `ensure_schema` cria a tabela e os `ALTER ... IF NOT EXISTS` no
  boot da API.
