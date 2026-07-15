# KL-44 P1 — Fundação de planos, assinaturas e admin de serviços/assinantes

**Card:** KL-44 (Guardião Digital) — Prompt 1 (fundação)
**Data:** 2026-07-15
**Escopo:** tabelas + lógica de trial + enforcement + 13 endpoints + 2 MCP tools + 2 páginas
admin (Astro) + fix do backfill de leads. **Nenhum serviço premium** — só a infraestrutura que
os prompts P2–P6 vão usar.

## ⚠️ Adaptação crítica de schema

O card assume `accounts(id)`, mas **não existe tabela `accounts`** neste codebase: a "conta" é a
tabela **`users`** (KL-51 f3), que já tem `plan` e `max_sites`. Portanto `account_id` (nome do
card, da API e das funções) **referencia `users(id)`**. Isso está documentado no schema e na API.

## Parte 1 — Tabelas (migration idempotente)

Adicionadas ao `_SCHEMA` de `discovery/store.py` (roda em todo `ensure_schema`, idempotente):
- **`plans`** — 3 planos com 28 colunas de limites/features. Seed dos 3 (`free`/`pro`/`agency`)
  via `INSERT … ON CONFLICT (id) DO NOTHING`.
- **`subscriptions`** — 1 por conta (`UNIQUE(account_id)`), FK `users(id)` + `plans(id)`;
  `status` (trial|active|free|expired|cancelled), `trial_ends_at`, `expires_at`, etc.
- **`subscription_history`** — auditoria de mudanças (old/new plan+status, changed_by, reason).

## Parte 2 — Lógica de negócio

- **`api/plans.py`** (novo) — orquestra o store + calcula datas: `get_plan`/`get_plans`,
  `get_subscription` (merge plano+assinatura + **expiração lazy do trial na leitura**),
  `create_subscription` (trial = now+30d), `change_plan` (trial→free vira free; trial→pago
  mantém o trial), `extend_trial`, `set_status`, `get_subscription_stats`, `list_subscribers`,
  `seed_existing_accounts`. O store é resolvido em runtime (`_store()`) → respeita monkeypatch.
- **Store** (`discovery/store.py`) — CRUD cru: `list_plans`/`get_plan`/`update_plan`,
  `get_subscription_row`/`upsert_subscription`/`update_subscription`, `log_subscription_change`/
  `list_subscription_history`, `subscription_group_counts`/`count_trials_expiring`/
  `list_subscribers`/`users_without_subscription`, e `backfill_leads_from_accounts` (P6).
- **Hook no signup** (`account_signup`): `_spawn(_safe_lead(plans.create_subscription(user_id,
  "pro", is_trial=True)))` — **fire-and-forget**, nunca bloqueia nem derruba o signup. Toda conta
  nova começa com Pro trial de 30 dias (transparente nesta fase).
- **Enforcement** (`account_add_site`): `_effective_plan_limits(user)` usa o `max_sites` do plano
  da assinatura, com **fallback resiliente** para `users.max_sites` se a assinatura não existir/
  falhar. Sites existentes **nunca** são removidos (o card manda pausar, não deletar — o worker
  do P2 checará o limite). A expiração do trial acontece na leitura de `get_subscription`.
- **Seed de contas existentes** (`plans.seed_existing_accounts` + `scripts/seed_subscriptions.py`):
  conta < 30 dias → Pro trial (`trial_ends_at = created_at + 30d`); ≥ 30 dias → Free. Idempotente.

## Parte 3 — API (13 endpoints)

Admin (Bearer admin, prefixo `/admin` já protegido): `GET /admin/plans`, `GET|PUT
/admin/plans/{id}`, `GET /admin/subscriptions/stats`, `GET /admin/subscriptions`,
`GET /admin/subscriptions/{account_id}`, `.../history`, `PATCH .../plan|trial|status`,
`POST /admin/subscriptions/bulk`. Público: `GET /account/subscription` (auth de usuário).
⚠️ `/stats` e `/bulk` são declarados **antes** de `/{account_id}` (senão "stats" viraria id).
Registrados no objeto `admin` de `web/src/lib/admin/adminApi.js`.

## Parte 4 — Páginas admin (Astro, padrão da migração KL-51)

`client:only="react"` + `AdminShell` wrapper + `<a href>` (zero react-router). Sidebar ganhou
**Serviços** (entre Gestão de Clientes e Sistema) e **Assinantes** (ícones SVG novos).
- **`/painel/servicos`** (`ServicosPage.jsx`): 3 cards de plano com todos os limites (✅/❌ nas 8
  vigílias, preço, sites, boletim, planos de ação, histórico, concorrentes, LGPD, widget, PDF,
  export, API) + contador de assinantes por plano + **modal de edição** (todos os campos, `PUT
  /admin/plans/{id}`).
- **`/painel/assinantes`** (`AssinantesPage.jsx`): KPIs (total, por plano, trials ativos,
  expirando 7d, conversão), filtros (plano/status/busca), tabela (e-mail, plano [dropdown inline
  → muda], status badge, trial restante, sites x/máx, última atividade, +30d trial, histórico),
  **seleção múltipla + ação em grupo** (mudar plano / estender trial / mudar status) e **modal de
  histórico**.
- **Nginx:** nenhuma mudança — a Fase 2 da migração já roteia `^/painel(/|$)` → Astro.

## Parte 5 — MCP tools

`mcp_server/tools/subscriptions.py` (registrado no `__init__`): **`get_subscription_stats`** e
**`list_subscribers`**. `test_mcp_server.py` atualizado (whitelist das tools).

## Parte 6 — Fix do backfill de leads

`store.backfill_leads_from_accounts()`: varre `users` sem lead (LEFT JOIN `scan_leads`) e cria o
lead com `has_account=True` (+ `has_monitoring` via `user_sites`), score/classe via
`calculate_lead_score`. Cobre contas que entraram via alerta→signup **sem scan público** (ex.:
`usecognato@gmail.com`), que o backfill do KL-61 (só `scanned_by_email`) não pegava. Rodado pelo
`scripts/seed_subscriptions.py`.

## Parte 7 — Testes

`tests/test_subscriptions.py` (novo, fake store que **persiste**): trial (criar/expirar-na-
leitura/estender), free, change_plan (trial→free e trial→pago), stats, seed idempotente, e os
endpoints admin (plans/update/stats-antes-de-id/change-plan/extend/bulk) + `/account/subscription`.
`tests/test_accounts.py`: FakeStore ganhou stubs **não-persistentes** de assinatura → o hook de
trial no signup e o enforcement caem no fallback (`users.max_sites`), mantendo os testes
existentes determinísticos (a lógica por plano é coberta no test_subscriptions).

## Deploy — feito e verificado

- **Commit** `972b408`. **CI success** (Test **751 passed, 1 skipped** + Build web (Astro) +
  Nginx config check + Deploy to GCP VM). O schema (3 tabelas + seed dos 3 planos) rodou no
  deploy via `ensure_schema`.
- **Seed rodado na VM** (`docker compose exec -T api python scripts/seed_subscriptions.py`) →
  **`assinaturas criadas: {total: 8, pro_trial: 8, free: 0}`** (as 8 contas < 30 dias → Pro
  trial) e **`leads de contas criados: 1`** (= `usecognato@gmail.com`, a conta que entrou via
  alerta→signup sem scan público — o fix da Parte 6). Isso valida **end-to-end contra o Postgres
  de produção** as novas store methods + `plans.py` + o backfill.

## Verificação pós-deploy

| Item | Resultado |
|---|---|
| Schema (plans/subscriptions/subscription_history + seed) | ✅ criado no deploy (`ensure_schema`) |
| 8 contas com assinatura (Pro trial) | ✅ seed: `pro_trial: 8` |
| `usecognato@gmail.com` vira lead | ✅ seed: `leads de contas criados: 1` (fix P6) |
| `/painel/servicos` e `/painel/assinantes` servidos | ✅ **HTTP 200 + HTML Astro válido** (curl); bundle `/_astro/*` carrega |
| Endpoints admin (plans/subscriptions) | ✅ testados localmente (751 passed) + as mesmas funções rodaram no seed em prod |
| MCP tools registradas | ✅ no build; ⚠️ o conector Claude.ai Klarim precisa reconectar p/ listá-las |
| Screenshot visual das 2 páginas no browser | ⚠️ **não capturado nesta sessão** — a extensão do Chrome não tem permissão de site para `painel.klarim.net` neste tab group (sessões anteriores tinham). As páginas servem 200/HTML válido; a limitação é do browser-automation, não do app. |

**Nota honesta:** não consegui um screenshot visual das 2 páginas admin nesta sessão (bloqueio de
permissão de site do browser para o subdomínio painel). A verificação por curl (200 + HTML Astro
válido), o seed rodando em produção (8 assinaturas + 1 lead) e o CI/`build-web` verde cobrem o
funcional; o operador pode abrir `painel.klarim.net/painel/servicos` para conferir visualmente.

## Regra inviolável (para os prompts P2–P6)

`account_id` == `users.id`. `classification`/`lead_score` seguem sempre calculados (KL-61). O
enforcement de sites usa o plano da assinatura com fallback para `users.max_sites` — nunca
remove sites existentes. O hook de trial no signup é fire-and-forget (nunca bloqueia o signup).
