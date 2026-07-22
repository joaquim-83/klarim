# KL-90 — Prompt 1: Endpoint `/account/dashboard-summary` (Dashboard v2)

**Data:** 2026-07-21
**Card:** KL-90 (Prompt 1 de 3)
**Ambiente:** desenvolvimento local (`docker-compose.dev.yml`). **NÃO houve deploy.**
**Objetivo:** endpoint agregado que alimenta o Dashboard v2 numa única chamada.

---

## Decisão de escopo (registrada)

O caminho `GET /account/dashboard-summary` **já existia** (KL-86), consumido pelo
`web/src/components/account/Dashboard.jsx`. O prompt pedia uma **shape v2 mais rica no
mesmo caminho**, mas dizia "não modificar rota existente" — contradição real.

Perguntei ao dono e a escolha foi **"Substituir pela v2"**: reescrevi o handler para a
shape v2. O `Dashboard.jsx` antigo quebra em DEV até o Prompt 2/3 reescrever o front —
coerente com "KL-90 = Dashboard v2". Nada foi deployado.

---

## O que foi entregue

### `api/dashboard.py` (novo)
Toda a lógica do endpoint, seguindo o padrão do projeto (**agregação bruta no store,
derivação PURA no módulo**):

- **Funções puras testáveis:** `build_categories`, `build_risks`, `build_score_history`,
  `build_trend`, `build_checklist`, `build_plan`, `build_monitoring`, `build_benchmark`,
  `ssl_days_from_checks`, `_pick_site`, `check_num`/`short_id`/`norm_severity`/`norm_status`.
- **Orquestrador `build_dashboard_summary(store, user, site_id)`** — faz as queries em
  **paralelo** (`asyncio.gather`, 2 rodadas) e monta o payload. Queries opcionais
  (benchmark, técnico) são **fail-open** (`_safe`) — uma falhar não derruba o dashboard.
- **`FIX_INLINE`** — mapa **canônico** de fix por plataforma (`{wordpress, nginx, apache}`)
  por número de check (~25 checks cobertos; os demais → `null`). Vive no código, **não
  depende do seed** → funciona em produção. `title`/`description`/`risk_message` vêm do
  `RISK_MESSAGES` do KL-20.
- **`CATEGORIES`** — 6 grupos fixos (tls/headers/supply/dns/content/osint) cobrindo os 48.

### `api/main.py` (handler repontado)
O handler virou uma casca fina:
```python
@app.get("/account/dashboard-summary")
async def account_dashboard_summary(request, site_id: Optional[int] = None):
    user = await auth_users.require_user(request)
    return await dashboard_v2.build_dashboard_summary(get_target_store(), user, site_id)
```

### Response (com site) — resumo da shape
`has_site`, `sites[]` (id/domain/score/semaphore — o seletor), `selected_site_id`,
`site` (score/semaphore/**trend** PT `subindo·caindo·estavel·primeiro`/trend_delta/
last_scan_at/next_scan_estimate/is_online/site_type/ssl_days_remaining), `benchmark`
(sector_label/rank_position/rank_total/sector_avg/above_average), `risks[]` (check_id
curto/severity/title/description/**fix_inline**), `categories[]` (6 × passed/total/status
+ `checks[]` com evidence/risk_message/fix_inline), `score_history`, `checklist`, `plan`
(name/status/expires_at/days_remaining/features), `monitoring` (vigílias/boletim/selo/
técnico), `profile` (company_name/phone/sector/confirmed).

### Sem site → payload reduzido
`{has_site:false, sites:[], selected_site_id:null, plan, checklist:[add_site, confirm_email]}`

### Regras de negócio
- **`site_id`**: seleciona o site (deve ser do usuário → **404** se não; ausente → primário
  = 1º monitorado).
- **`contact_email`/cnpj/whatsapp NUNCA** entram no payload (só campos explícitos).
- **trend** = comparação dos 2 últimos pontos do histórico (subindo/caindo/estavel/primeiro).
- **categoria status**: 0 falhas → ok · 1-2 → warning · ≥3 → critical.
- **checklist** (máx 5, priorizado): confirmar e-mail → corrigir FAIL crítica → alta →
  completar perfil → compartilhar (score ≥ 80) → ativar selo.
- **ssl_days_remaining**: vigília ssl (`last_data`) → senão a evidência do check de cert.

---

## Testes

`tests/test_kl90_dashboard_summary.py` — **20 testes** (offline, TestClient + FakeStore):
funções puras (categorias, riscos ordenados, trend, checklist, plano, monitoramento,
benchmark, seleção/404) + endpoint (com site/sem site/`?site_id=`/404/401/perf/
`contact_email` não vaza).

`tests/test_kl86_dashboard.py` — reduzido aos **7 testes de helper puro** (os testes do
endpoint antigo migraram para o de cima; os helpers KL-86 continuam existindo).

> **Dívida técnica anotada:** 5 helpers do KL-86 (`_dashboard_categories`, `_build_checklist`,
> `_score_trend`, `_vigilia_summary`, `_new_user_checklist`) ficaram órfãos (o endpoint não os
> usa mais). Não removidos para manter o diff focado no endpoint; cleanup é um follow-up trivial.
> `_build_categories` e `_ssl_expiry_days` seguem em uso por outros pontos (scan result KL-82).

---

## Validação local (executada)

| Verificação | Resultado |
|---|---|
| `GET /account/dashboard-summary` (primário) | shape v2 completa; hotel 83/🟡, trend estavel, ssl 247, type wordpress |
| benchmark | hotelaria rank 1/13, avg 57, above_average true |
| risks | ordenados por severidade (critica→alta→media); `fix_inline` {wordpress,nginx,apache} |
| categories | 6 grupos com passed/total/status |
| `?site_id=58` (loja 42) | seleciona a loja; risco SPF ("Qualquer um pode enviar e-mail…") presente |
| `?site_id=999999` (não é do usuário) | **404** |
| usuário sem site (`novo@teste.com.br`) | `has_site:false`, Free, checklist add_site/confirm_email |
| sem auth | **401** |
| performance | **~47–59ms** (alvo < 500ms) |
| `contact_email` no payload | **não vaza** |
| `pytest` dashboard (KL-86 + KL-90 + KL-57) | **43 passed** |
| `pytest` suíte completa (env CI) | **1526 passed, 1 skipped** (baseline 1510 + 20 novos − 4 removidos) |

---

## Não faz parte deste prompt

- **Nenhum deploy** (sem push/CI/VM).
- O **frontend** do Dashboard v2 (consumir esta shape) vem nos Prompts 2/3.
- O endpoint já funciona com o seed **e** com dados reais de produção (o `FIX_INLINE` e
  os riscos são canônicos, não dependem do seed).
