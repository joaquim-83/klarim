# KL-61 — Gestão de Leads (scan_leads + scoring PQL + admin + MCP)

## Objetivo

Transformar cada e-mail que verifica um scan (KL-25) em um **lead** com **score
comportamental** (modelo PQL — *Product Qualified Lead*) e classificação
(`cold`/`warm`/`hot`/`pql`), sem CRM externo, sem alterar o fluxo de scan e sem
enviar e-mail automático. O lead é só **registro + pontuação** do interesse já
demonstrado no produto, para o operador priorizar quem contatar.

## O que foi entregue

### 1. Scoring puro — `api/lead_scoring.py`

Módulo **sem I/O** (não importa `api.main`/`store`), 100% testável offline.

- `SCORING_RULES`: email_verified 10, scan_completed 15, score_below_70 10,
  score_below_50 20 (cumulativo com <70), account_created 25, monitoring_added 30,
  multiple_scans 20 (2+ URLs distintas), rescan 15 (total_scans > distinct),
  corporate_email 5.
- `DECAY_RULES`: inactive_14d −15.
- `CLASSIFICATION_THRESHOLDS`: cold 0–20 · warm 21–40 · hot 41–60 · pql 61+.
- `GENERIC_DOMAINS`: gmail/hotmail/outlook/yahoo(.com.br)/uol/… (define
  `is_corporate_email`).
- `calculate_lead_score(data) → (score, classification)`: score **mínimo 0** (nunca
  negativo), classificação **SEMPRE** derivada do score (`classify`).
- `score_breakdown(data)`: lista `[{key,label,points,applied}]` — a composição do
  score no detalhe do lead.

### 2. Tabela `scan_leads` + métodos — `discovery/store.py`

Criada via `ensure_schema` (sem Alembic). 1 linha por **e-mail** (UNIQUE, `LOWER()`),
agrega os scans (total, `urls_scanned`/`domains_scanned` TEXT[], best/worst/last
score, last_domain, sector, platform), flags (`has_account`/`account_id`,
`has_monitoring`, `is_corporate_email`, `opted_out`), `lead_score`+`classification`
(CHECK) e os campos **manuais** `tags`/`notes`.

- `_recalc_lead_row(cur, id)`: recomputa score+classificação **na mesma transação**
  (a linha agregada é a fonte da verdade). Chamado por todo caminho de escrita.
- `upsert_scan_lead` (UPSERT idempotente por e-mail, dedup de ARRAY, trata score
  NULL), `set_lead_account` (UPSERT create-if-missing, +conta), `set_lead_monitoring`
  (UPDATE-only), `list_leads` (filtros + contagem por classificação), `get_lead`
  (+ scans do e-mail via `scanned_by_email`), `lead_stats`, `lead_funnel`,
  `update_lead` (só tags/notes/opted_out + recalc), `recalculate_all_leads`,
  `backfill_leads`.
- `lead_stats` inclui os analytics do KL-57: conversão por setor, **setores com maior
  dor** (menor avg `worst_score`) e **taxa PQL**.

### 3. Captura automática (fire-and-forget) — `api/main.py`

`_safe_lead(coro)` engole exceção (nunca derruba o chamador). Três ganchos:

- **Scan público**: no `_ingest_scan_bg` (já roda em background, depois da resposta do
  scan) → `upsert_scan_lead(email, url, score, sector, platform)`.
- **Signup**: `account_signup` → `_spawn(_safe_lead(set_lead_account(email, id)))`.
- **Monitoramento**: `account_add_site` → `_spawn(_safe_lead(set_lead_monitoring(email)))`.

O lead é criado **após** o scan/ação — nunca no caminho síncrono.

### 4. API admin (prefixo `/leads`, JWT)

`/leads` entrou em `_PROTECTED_PREFIXES`. Endpoints: `GET /leads` (lista+filtros+
`by_classification`), `GET /leads/{id}` (detalhe + `score_breakdown` injetado),
`GET /leads/stats`, `GET /leads/funnel`, `PATCH /leads/{id}` (**só**
tags/notes/opted_out — `LeadUpdateBody` nem tem lead_score/classification, então são
impossíveis de setar à mão), `POST /leads/recalculate`. As rotas
`/leads/stats|funnel|recalculate` são declaradas **antes** de `/leads/{id}`.

### 5. Painel `/painel/leads`

- `Leads.jsx`: cards clicáveis por classificação (filtro), busca (e-mail/domínio),
  filtro com/sem conta, métricas (total, com conta, monitorando, taxa PQL, score
  médio, corporativos), **setores com maior dor**, botão **Recalcular scores**,
  tabela paginada colorida por classificação.
- `LeadDetalhe.jsx`: **barra de score** + composição (breakdown com pontos
  aplicados/não) + dados do lead + scans do e-mail + editor de tags/notas/opt-out.
- Item **Leads** no menu lateral (entre Alertas e Pagamentos), rotas + lazy imports
  no `App.jsx`, métodos no `lib/adminApi.js`.

### 6. MCP (3 tools) — `mcp_server/tools/leads.py`

`list_leads`, `get_lead_stats`, `get_lead_funnel` (leitura, via `_guard`). Total do
servidor MCP: **45 tools**.

### 7. Backfill — `scripts/backfill_leads.py`

`docker compose exec -T api python scripts/backfill_leads.py [--dry-run]`. Agrega
`scans.scanned_by_email` (NOT NULL), cruza com `users`/`user_sites`, **idempotente**
(ON CONFLICT DO UPDATE recomputa dos scans e preserva tags/notes/opted_out).

## Testes

- **`tests/test_kl61_leads.py`** (40 testes): scoring puro (regras, decaimento,
  limiares, corporativo, score ≥0, classificação sempre derivada, exemplos da spec),
  store com cursor falso (`_lead_domain`, `_recalc_lead_row` warm/pql, upsert dispara
  recalc, e-mail inválido ignorado), API via TestClient+FakeStore (proteção JWT,
  filtros, stats, funil, breakdown injetado, 404, PATCH só campos manuais, PATCH
  **não** seta score/classification, recalculate, `_safe_lead` engole exceção).
- **`tests/test_mcp_server.py`**: `list_leads`/`get_lead_stats`/`get_lead_funnel`
  em `READ_TOOLS` + FakeStore + 4 testes de execução das tools.
- **Fix de regressão** (FakeStore drift): `test_accounts.py` e
  `test_kl57_account_dashboard.py` ganharam `set_lead_account`/`set_lead_monitoring`
  nos seus FakeStores (os ganchos de signup/add_site passaram a chamá-los).

**Resultado:** `694 passed, 1 skipped` (suíte offline completa). Build do frontend
admin (Vite) e sintaxe JSX validados.

## Regras invioláveis respeitadas

- `lead_score`/`classification` **sempre calculados** — nunca editáveis à mão.
- Captura **fire-and-forget**, **após** o scan — nunca bloqueia nem derruba.
- Não altera `site_events`, não cria CRM externo, não envia e-mail automático.
- E-mails normalizados com `LOWER()`; `scanned_by_email` NULL é filtrado.
- Backfill idempotente; migrations via `ensure_schema`.

## Operação (pós-deploy)

Rodar o backfill uma vez na VM para popular os leads a partir dos scans existentes:

```bash
gcloud compute ssh --zone us-central1-a instance-20260706-112125 \
  --project project-b08050df-fa4e-49ac-919
cd /opt/klarim && sudo docker compose exec -T api python scripts/backfill_leads.py
```

Se as regras de score mudarem no futuro: `POST /api/leads/recalculate` (ou rodar o
backfill de novo).

## Arquivos

**Novos:** `api/lead_scoring.py`, `mcp_server/tools/leads.py`,
`scripts/backfill_leads.py`, `frontend/src/pages/admin/Leads.jsx`,
`frontend/src/pages/admin/LeadDetalhe.jsx`, `tests/test_kl61_leads.py`.

**Alterados:** `discovery/store.py`, `api/main.py`, `mcp_server/tools/__init__.py`,
`frontend/src/App.jsx`, `frontend/src/components/admin/AdminLayout.jsx`,
`frontend/src/lib/adminApi.js`, `tests/test_mcp_server.py`, `tests/test_accounts.py`,
`tests/test_kl57_account_dashboard.py`, `CLAUDE.md`, `README.md`.
