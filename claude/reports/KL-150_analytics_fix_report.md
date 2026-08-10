# KL-150 — Fix Analytics: números da "Visão Geral" não batem com a realidade

**Data:** 10/08/2026 · **Escopo:** `/painel/analytics` → aba **Visão geral** (KPIs + tendência)
**Regra do card:** diagnosticar ANTES de implementar (relatório com o resultado de cada query SQL);
**NÃO alterar** engine de scan, rate limiting nem site público; **sem deploy sem validação visual**.

---

## 1. Sintoma reportado (pelo fundador, 10/08/2026)

| KPI (Visão geral, "Hoje") | Painel mostrava | Realidade | Veredito |
|---|---|---|---|
| Contas criadas | **2** | 1 | 🐛 bug (fuso) |
| Scans | **98** | 2 (manuais) | 🐛 bug (rescan) |
| Visitantes BR | **355** | "muito alto" | ✅ correto (tráfego real) |
| Bots filtrados | **495.117** | "impossível/dia" | ✅ correto (rótulo confuso) |

---

## 2. Diagnóstico — resultado de CADA query (produção, `klarim-db-1`)

> Todas as colunas de tempo (`users.created_at`, `scans.scanned_at`, `access_log.created_at`) são
> `TIMESTAMP` **naive representando UTC**. `SHOW timezone` na sessão do Postgres = **UTC** (confirmado),
> então comparar um bound `timestamptz` contra a coluna naive é correto. O dia de Brasília (BRT, UTC-3,
> **sem horário de verão** desde 2019) começa às **03:00 UTC**.

### [1] Contas criadas hoje — UTC vs Brasília
```sql
SELECT
  (SELECT count(*) FROM users WHERE created_at >= date_trunc('day', now()))                      AS contas_utc_hoje,
  (SELECT count(*) FROM users
     WHERE created_at >= (now() AT TIME ZONE 'America/Sao_Paulo')::date::timestamp
                          AT TIME ZONE 'America/Sao_Paulo')                                       AS contas_brt_hoje;
```
```
 contas_utc_hoje | contas_brt_hoje
-----------------+-----------------
               2 |               1
```
**Causa-raiz:** `resolve_period('today')` usava a **meia-noite UTC**. Uma conta criada às **23:28 BRT**
(= 02:28 UTC do dia seguinte) caía na janela "hoje" do painel, mas em Brasília é de ONTEM. O "hoje" UTC
inclui ~3h da noite anterior brasileira → inflava o dia. **2 (UTC) → 1 (BRT).**

### [2] Scans hoje (dia BRT) — filtro antigo vs novo
```sql
WITH b AS (SELECT (now() AT TIME ZONE 'America/Sao_Paulo')::date::timestamp
                   AT TIME ZONE 'America/Sao_Paulo' AS ini)
SELECT
  (SELECT count(*) FROM scans, b WHERE scanned_at >= b.ini)                                       AS scans_todos,
  (SELECT count(*) FROM scans, b WHERE scanned_at >= b.ini AND source IS DISTINCT FROM 'discovery')               AS scans_filtro_antigo,
  (SELECT count(*) FROM scans, b WHERE scanned_at >= b.ini AND COALESCE(source,'') NOT IN ('discovery','rescan')) AS scans_filtro_novo;
```
```
 scans_todos | scans_filtro_antigo | scans_filtro_novo
-------------+---------------------+-------------------
        1059 |                 119 |                 3
```

### [3] Scans hoje (dia BRT) por SOURCE — de onde vinha a inflação
```sql
WITH b AS (SELECT (now() AT TIME ZONE 'America/Sao_Paulo')::date::timestamp
                   AT TIME ZONE 'America/Sao_Paulo' AS ini)
SELECT COALESCE(source,'(null)') AS source, count(*)
  FROM scans, b WHERE scanned_at >= b.ini GROUP BY 1 ORDER BY 2 DESC;
```
```
  source   | count
-----------+-------
 discovery |   940
 rescan    |   116
 public    |     3
```
**Causa-raiz:** o KPI "Scans (manuais)" do KL-95 excluía só `source='discovery'`. Mas o **worker de
re-scan** (`source='rescan'`, ~116/dia) continuava contando → o KPI era dominado pelo worker automático
(o fundador via ~98-119, não os poucos scans reais). Scans **manuais** = `admin` (painel) + `public`
(scanner público). **119 (filtro antigo) → 3 (filtro novo).**

### [4] Visitantes BR & Bots filtrados hoje (dia BRT) — mesma lógica do painel
```sql
-- visitantes = COUNT(DISTINCT ip) is_bot=false + exclui user-agent de bot (_BOT_UA_RE)
-- bots_filtrados = COUNT(*) de REQUISIÇÕES com is_bot=true OU user-agent de bot
```
```
 visitantes_br_so_isbot | visitantes_br_painel | bots_filtrados_requisicoes
------------------------+----------------------+----------------------------
                    399  |                 356  |                    493.079
```
**Veredito:** **NÃO são bugs de número.**
- **Visitantes BR = 356** são **IPs únicos** reais do Brasil, já sem bots por classificação (399) e sem
  os que têm user-agent de bot (356). É tráfego orgânico legítimo de um site público — o número está
  correto; a percepção de "alto" vinha da falta de contexto no rótulo.
- **Bots filtrados = 493.079** são **REQUISIÇÕES** de bot/crawler/scanner no dia, **não** visitantes nem
  IPs únicos. Um scanner de segurança público é alvo ativo de sondagem (ver `docs/SECURITY.md`) — meio
  milhão de requests de bot/dia é plausível e está corretamente **separado** dos visitantes. O problema
  era **rótulo confuso** (parecia "visitantes bloqueados"), não o número.

### [5] Verificação end-to-end (query no estilo exato da aplicação)
```sql
SHOW timezone;  -- UTC
SELECT count(*) FROM users  WHERE created_at  >= TIMESTAMPTZ '2026-08-10 03:00:00+00'
                              AND created_at  <  TIMESTAMPTZ '2026-08-11 03:00:00+00';           -- 1
SELECT count(*) FROM scans  WHERE scanned_at  >= TIMESTAMPTZ '2026-08-10 03:00:00+00'
                              AND scanned_at  <  TIMESTAMPTZ '2026-08-11 03:00:00+00'
                              AND COALESCE(source,'') NOT IN ('discovery','rescan');             -- 3
```
Bound tz-aware (meia-noite BRT = 03:00 UTC) contra coluna naive, sessão UTC → **contas=1, scans=3**,
idêntico ao diagnóstico BRT. **A correção entrega os números certos ponta a ponta.**

---

## 3. Correções (2 bugs reais + 2 rótulos)

### Fix 1 — Fuso: "hoje" = dia-calendário de Brasília (`api/admin_analytics.py`)
`resolve_period('today')` passou a calcular a meia-noite **BRT** e convertê-la para o instante **UTC**
(que é o formato dos bounds já usado no resto do módulo). `7d/30d/90d` seguem janelas móveis a partir de
`now` (inalterado). `_period_meta` já deriva as datas do bound → o banner do painel rotula o dia BRT.
```python
_BRT = ZoneInfo("America/Sao_Paulo")
if period == "today":
    s_brt = now.astimezone(_BRT).replace(hour=0, minute=0, second=0, microsecond=0)
    s = s_brt.astimezone(timezone.utc)
    e = s + timedelta(days=1)
```

### Fix 2 — Scans manuais excluem discovery **E** rescan (`discovery/store.py`)
`al_server_metrics` (KPI) e `al_daily_series` (tendência): `source IS DISTINCT FROM 'discovery'` →
`COALESCE(source,'') NOT IN ('discovery','rescan')` (NULL-safe). Manuais = `admin` + `public`.

### Fix 3 — Rótulos + banner de período + tooltips (`web/.../AdminAnalytics.jsx`)
- KPI **"Scans" → "Scans manuais"** (deixa claro que não é o volume dos workers).
- **Banner de período** acima dos KPIs: `📅 Hoje · 10/08/2026 · horário de Brasília` (ou
  `Últimos N dias · início a fim · horário de Brasília`) — o operador vê exatamente qual janela está vendo.
- **Tooltip (ⓘ)** em cada KPI dizendo o que conta e a fonte:
  - Visitantes BR — "IPs únicos do Brasil (exclui bots por classificação e por user-agent)."
  - Scans manuais — "Scans iniciados por pessoas (painel + scanner público). NÃO inclui os workers automáticos discovery/re-scan."
  - Contas criadas — "Contas na tabela `users` criadas no período (dia de Brasília)."
  - Bots filtrados — "REQUISIÇÕES de bot bloqueadas (crawlers/scanners) no período — não são visitantes nem IPs únicos."

**Nada tocado** na engine de scan, no rate limiting ou no site público.

---

## 4. Validação

**Backend:** `pytest -q` → **2321 passed, 1 skipped**.
- Novo `tests/test_kl150_analytics_period.py` (4): "hoje" = dia BRT; conta às 23:28 BRT não é "hoje";
  `now` antes da meia-noite UTC ainda é o dia BRT anterior; `_period_meta` rotula o dia BRT; 7d é janela móvel.
- `tests/test_kl83_analytics.py::test_period_today_bounds` atualizado (bound "hoje" agora 03:00 UTC).
- `tests/test_kl95_metrics.py` atualizado (asserção `NOT IN ('discovery','rescan')`).

**Frontend:** `npm run test:unit` = 221 passed · `npm run build` OK.

**Navegador (dev, `/painel/analytics`):**
- Banner "Hoje" → `📅 Hoje · 10/08/2026 · horário de Brasília` ✅
- Banner "7 dias" → `📅 Últimos 7 dias · 03/08/2026 a 09/08/2026 · horário de Brasília` ✅
- KPIs: **Visitantes BR ⓘ · Scans manuais ⓘ · Contas criadas ⓘ · Bots filtrados ⓘ** (todos com tooltip) ✅
- **Zero erro** no console.

---

## 5. Arquivos alterados

| Arquivo | Mudança |
|---|---|
| `api/admin_analytics.py` | `resolve_period('today')` → dia-calendário de Brasília (BRT→UTC) |
| `discovery/store.py` | `al_server_metrics` + `al_daily_series`: scans manuais excluem `discovery` E `rescan` |
| `web/src/components/admin/AdminAnalytics.jsx` | banner de período (Brasília) + rótulo "Scans manuais" + tooltips por KPI |
| `tests/test_kl150_analytics_period.py` | **novo** (4 testes de fuso BRT) |
| `tests/test_kl83_analytics.py` | bound "hoje" atualizado |
| `tests/test_kl95_metrics.py` | asserção do filtro de scans atualizada |

---

## 6. Definição autoritativa das métricas (Visão geral)

| KPI | O que conta | Fonte | Período default | Fuso |
|---|---|---|---|---|
| **Visitantes BR** | `COUNT(DISTINCT ip_address)` do Brasil, `is_bot=false` E sem user-agent de bot | `access_log` (IP real, server-side) | **7 dias** | dia de Brasília |
| **Scans manuais** | `COUNT(*)` em `scans` com `source ∈ {admin, public}` (exclui workers `discovery`+`rescan`) | tabela `scans` | 7 dias | dia de Brasília |
| **Contas criadas** | `COUNT(*)` em `users` no período | tabela `users` | 7 dias | dia de Brasília |
| **Bots filtrados** | `COUNT(*)` de **requisições** com `is_bot=true` OU user-agent de bot (volume, não IPs) | `access_log` | 7 dias | dia de Brasília |

- **Período default = `7d`** (janela móvel de 7 dias a partir de agora). "Hoje" = dia-calendário de
  **Brasília** (BRT, UTC-3). `30d/90d` = janelas móveis. `custom` ≤ 90 dias.
- **"Scans manuais" ≠ `/painel/sistema` "Scans hoje":** o Sistema (`scan_today_stats`) conta **TODOS** os
  scans do dia incluindo os workers — medem coisas diferentes por design (ver `CLAUDE.md`, "Fontes
  autoritativas de métrica").

**PRONTO PARA REVISÃO VISUAL** — sem deploy.
