# KL-84 — Taxonomia Aberta de Setores

**Card:** KL-84 · **Prioridade:** High · **Data:** 2026-07-19

Troca os **48 setores fixos** (KL-54, `SECTOR_TAXONOMY`) por uma taxonomia **dinâmica**: a IA
pode **propor** setores novos, o admin **cura** (aprova/mescla/rejeita), os aprovados aparecem
automaticamente em `/setores` e uma **reclassificação retroativa** (usando as descrições já
extraídas, sem re-scan) derruba o 'outro' de ~15-20% para <5%.

---

## A. Banco — tabela `sectors`

`discovery/store.py::_SCHEMA` ganha a tabela (idempotente, no `ensure_schema`):

```
sectors(id, slug UNIQUE, label, macro_sector,
        status ∈ official·proposed·approved·rejected·merged,
        merged_into → sectors(slug), site_count, first_seen, approved_at, approved_by, created_at)
+ índices (status, macro_sector)
```

**Seed** (`store.seed_sectors`, chamado no `ensure_schema`): insere os 48 oficiais do
`SECTOR_TAXONOMY` como `official` (`ON CONFLICT(slug) DO NOTHING`) e inicializa `site_count` via
`GROUP BY` em `targets`. Fail-open — um erro na seed não impede o boot.

## B. IA propõe setores novos

`scanner/ai_enrichment.py`:
- `build_system_prompt(known_sectors)` — prompt com a lista de setores **conhecidos dinâmica**
  (48 oficiais + aprovados vindos da tabela, **cache 1h** no chamador). Novos campos:
  `is_new_sector`, `sector_label`, `macro_sector_suggestion`. A IA prefere um setor conhecido; se
  nenhum encaixa mas o negócio tem setor claro, **propõe** um slug em snake_case.
- `ai_enrich(..., known_sectors=)` — setor conhecido → normaliza (alias/clamp como antes); setor
  **novo** (`is_new_sector`) → **preserva** o slug sanitizado (`[a-z0-9_]`, máx 50) em vez de virar
  'outro'. `scanner/enrichment.py` busca os setores aprovados (cache 1h em-processo) e passa.

## C. Fluxo `process_classification` (puro, testável)

`discovery/sector_classification.py::process_classification(store, ai_result)`:
1. resolve **sinônimo** (`sector_synonyms.py`) antes de tudo;
2. consulta a tabela: setor `merged` → segue `merged_into`; `rejected` → 'outro'; existente →
   usa e incrementa `site_count`;
3. `is_new_sector` e slug inédito → cria **proposta** (`create_proposed_sector`, macro validada
   contra o conjunto de 16 macros; slug sanitizado);
4. slug desconhecido sem `is_new_sector` → 'outro'.
Retorna `{sector, confidence, action∈existing·merged·proposed·fallback}`. Best-effort no enrich
(exceção nunca derruba o scan).

## D. Sinônimos

`discovery/sector_synonyms.py` — `SYNONYMS` (advocacia→juridico, dentista→odontologia,
pousada→hotel, hamburgueria→lanchonete, pizzaria→restaurante, barbearia→salao, …) +
`resolve_synonym` (normaliza case/espaço/hífen). Evita proposta redundante de setor que já existe
com outro nome.

## E. Página admin `/painel/setores`

`web/src/components/admin/SetoresPage.jsx` (ilha `client:only`, entrada no `AdminShell`):
- **contadores** (classificados, 'outro' + %, emergentes, aprovados, oficiais);
- **setores emergentes** (propostos): cartão com exemplos sob demanda + **Aprovar / Mesclar em… /
  Rejeitar**;
- **taxonomia viva** (official+approved): tabela filtrável por macro, com contagem de sites e link
  para `/setor/{slug}`.

## F. Endpoints admin (`api/admin_sectors.py`, prefixo `/admin` → middleware JWT)

| Método | Path | O quê |
|---|---|---|
| GET | `/admin/sectors?status=` | stats + emergentes + taxonomia |
| GET | `/admin/sectors/{slug}/examples` | domínios de exemplo |
| POST | `/admin/sectors/{slug}/approve` | proposto → approved (`approved_by` = `sub` do JWT) |
| POST | `/admin/sectors/{slug}/merge` | body `{merge_into}`; reclassifica sites (exceto manual) |
| POST | `/admin/sectors/{slug}/reject` | proposto → rejected; sites voltam p/ 'outro' (exceto manual) |

## G. Reclassificação retroativa — `scripts/reclassify_sectors.py`

`--scope outro|all --dry-run --limit --batch`. Usa a **descrição já extraída**
(`site_profile.description`/`business_type`/`tags`) — **sem re-scan, sem tocar score/checks**.
Passa cada alvo pelo mesmo `process_classification`. Protege `manual`/`receita`
(`store.reclassify_target_sector` só grava se a fonte não for uma dessas — mas **permite** rever
um alvo já `ai`, o caso comum de 'outro'). Rate limit **≤500 chamadas de IA/hora**. Ao final,
recomputa os `site_count`. **Roda manualmente na VM** (nunca em CI).

## H. Público filtrado por status

`/public/sectors` e `/public/sector/{slug}` (`api/main.py`) agora filtram pela tabela `sectors`
(`_sector_taxonomy_map`, cache 1h em-processo): só `official`/`approved` aparecem; `proposed`/
`rejected`/`merged` → **404**. Rótulos dos aprovados novos vêm da tabela. Fail-open: sem tabela,
comporta-se como antes.

## I. MCP tools

- `get_sector_stats` (read) — saúde da taxonomia (por status, 'outro' + %, emergentes com
  contagem).
- `classify_target_sector(target_id)` (write) — reclassifica 1 alvo por IA usando a descrição já
  extraída (sem re-scan), pela taxonomia aberta, protegendo `manual`/`receita`.

## Testes

`tests/test_kl84_sectors.py` — **37 testes** offline: sinônimos (6), `sanitize_slug` (4),
`process_classification` (9: existente/sinônimo/merged/rejected/novo→proposto/desconhecido/macro
inválida/'outro'/sanitize), prompt dinâmico + parsing de setor novo no `ai_enrich` (6), endpoints
admin (12: auth 401, list, examples 404, approve/approve-404/approve-macro-422, merge/merge-dest-
422/merge-self-422, reject/reject-404). `test_mcp_server` +2 tools. `test_kl51_f5_enrichment`
ajustado (mock aceita `known_sectors` + FakeStore ganha os métodos de setor). **Suíte: 1163
passed** (era 1126). Build Astro verde.

## Segurança / regras

- Endpoints admin-only (prefixo `/admin` → middleware JWT); slugs **sanitizados** e queries
  **parametrizadas**.
- Reclassificação **NUNCA** sobrescreve `manual`/`receita`, **NUNCA** altera score/checks/scan.
- AI enrichment segue a regra de ouro (só preenche campo vazio); a proposta de setor não muda
  isso.
- `contact_email` continua **nunca** exposto (endpoints de setor não tocam nele).
- Script de reclassificação é **manual** (VM), com rate limit de IA e `--dry-run`.

## Execução (pós-deploy)

1. A seed dos 48 oficiais roda sozinha no `ensure_schema` do próximo boot da API — verificar:
   `docker compose exec api python -c "import asyncio;from discovery.store import get_target_store as g;print(asyncio.run(g().sector_taxonomy_stats()))"`.
2. **Flush `scan:*` no Redis** só é necessário se um scan mudar (não é o caso aqui — setor não
   entra no score).
3. Reclassificação retroativa (opcional, quando quiser derrubar o 'outro'):
   `docker compose exec api python -m scripts.reclassify_sectors --dry-run` para calibrar, depois
   sem `--dry-run`.
