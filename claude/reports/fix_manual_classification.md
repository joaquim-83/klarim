# Fix — Classificação manual de setor pelo dashboard admin

**Tipo:** Melhoria de operação (sem card Jira)
**Data:** 2026-07-08

## Problema

Quando o classificador automático erra (ex.: hotel virou "restaurante"), o admin
não tinha como corrigir sem SQL direto no banco. Como o **preço do relatório
depende do setor**, a correção manual precisa ser fácil e **protegida** contra
sobrescrita pelo automático.

## Parte 1 — Backend

- **Coluna `targets.classification_source`** (`VARCHAR(20) DEFAULT 'auto'`):
  `auto` (classificador) · `domain` (reclassify-domains) · `manual` (operador).
  Migração idempotente no `_SCHEMA` (`ADD COLUMN IF NOT EXISTS`).
- **`PATCH /targets/{id}/classify {sector, price_tier?}`** (JWT). Valida o setor
  contra `PRICE_TIERS`, deriva o tier do setor se omitido, valida o tier contra
  `PRICING` (basic/standard/professional/enterprise). Grava `source='manual'`,
  `confidence=1.0`. Retorna o alvo atualizado (404 se não existe). Lógica de
  validação isolada em `_resolve_classification`.
- **`POST /admin/classify-batch {target_ids, sector, price_tier?}`** (JWT) — massa;
  retorna `{updated, sector, price_tier}`.
- **Store:** `manual_classify` (RETURNING *) e `manual_classify_batch`
  (`WHERE id = ANY(...)`, retorna `rowcount`).
- **Proteção do manual (invariante central):**
  - `register_target` (UPSERT do discovery/ingest) passou a **preservar** setor/
    tier/confiança/source quando o alvo já é `manual` (via `CASE WHEN
    targets.classification_source='manual'`). Assim discovery/re-scan **nunca**
    apagam uma correção do operador.
  - `reclassify-domains` e `reclassify-all` **pulam** alvos `manual`
    (log `[reclassify] pulando target N (classificação manual)`); os UPDATEs de
    reclassify têm `AND classification_source IS DISTINCT FROM 'manual'` como guarda
    extra. `bulk_update_classification` grava `source='domain'`;
    `update_classification` grava `source='auto'`.

## Parte 2–4 — Frontend

- **Componente reutilizável `components/admin/SectorEditor.jsx`:**
  - `SectorBadge` — badge do setor com indicador de confiança (≥0.8 sólido ·
    0.5–0.79 pontilhado · <0.5 cinza com "?") **+ 🔒 quando `source='manual'`**.
  - `SectorEditor` — badge + botão ✏️ que abre `<select>` inline (11 setores com
    rótulos amigáveis) + ✓/✗. Ao salvar chama `PATCH .../classify`, atualiza o
    badge e emite o toast "Setor atualizado para <setor>".
  - `SECTOR_OPTIONS`/`SECTOR_LABEL` exportados (fonte única dos setores).
- **Detalhe do alvo (`/painel/alvos/:id`):** o campo "Setor" usa o `SectorEditor`
  (edição inline + 🔒).
- **Lista de alvos (`/painel/alvos`):**
  - **Edição rápida** (Parte 3): `SectorEditor` na célula de setor — reclassifica
    sem abrir o detalhe.
  - **Ação em massa** (Parte 4): checkbox por linha + "selecionar todos", barra que
    aparece com a seleção → `<select>` de setor + "Classificar selecionados"
    (`POST /admin/classify-batch`), com estado "Classificando N…" e resumo.
- **`adminApi`:** helper `patch` + `classifyTarget(id, sector, tier?)` e
  `classifyBatch(ids, sector, tier?)`.

## Parte 5 — Testes (`tests/test_manual_classify.py`, 9 testes)

- `_resolve_classification`: deriva tier (clinica→enterprise, hotel→standard,
  restaurante→basic), honra tier explícito, rejeita setor/tier inválidos (422).
- Endpoints protegidos (401 sem token); PATCH com tier derivado (store recebe
  `(5,'clinica','enterprise')`); setor inválido → 422; alvo inexistente → 404;
  batch → `{updated:3,...}`.

**Suíte completa: 139 passed, 1 skipped.** Frontend: `npm run build` OK.

## Validação (mapeada aos itens da tarefa)

| # | Item | Cobertura |
|---|------|-----------|
| 1 | Editar no detalhe → badge com 🔒 | `SectorEditor` no AlvoDetalhe (source='manual' → 🔒) |
| 2 | Editar na lista inline | `SectorEditor` na célula de setor |
| 3 | Edição em massa (5 → hotel → 5) | checkbox + `classify-batch` (teste `test_classify_batch`) |
| 4 | reclassify não sobrescreve manual | skip nos loops + CASE no UPSERT + guarda no UPDATE |
| 5 | tier automático (clinica→enterprise) | `test_resolve_derives_tier_from_sector` + `test_patch_classify_derives_tier` |
| 6 | setor inválido → erro | `test_patch_classify_invalid_sector` (422) |

## Arquivos

- `discovery/store.py` (coluna, `register_target` CASE, `manual_classify`,
  `manual_classify_batch`, `update_classification`/`bulk_update_classification`
  com source + guarda, `all_targets_for_reclassify` traz `classification_source`)
- `api/main.py` (imports, `_resolve_classification`, `PATCH /targets/{id}/classify`,
  `POST /admin/classify-batch`, skip manual nos reclassify)
- `frontend/src/components/admin/SectorEditor.jsx` (novo)
- `frontend/src/pages/admin/Alvos.jsx`, `AlvoDetalhe.jsx`, `lib/adminApi.js`
- `tests/test_manual_classify.py` (novo), `CLAUDE.md`
