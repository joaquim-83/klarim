# KL-95 — Corrigir divergências de métricas no dashboard Analytics

**Status:** ✅ Concluído
**Data:** 2026-07-21
**Tipo:** fix

## Problema

O dashboard Analytics admin (`/painel` → Analytics) mostrava métricas que **contavam
requests HTTP no `access_log`** em vez de **ações reais de negócio**. Resultado: números
divergentes das outras telas do próprio painel (Usuários, Eventos).

| KPI | O que mostrava | Fonte errada | Valor real |
|-----|----------------|--------------|------------|
| **Contas criadas** | 9 | POST `/signup` no access_log (contava tentativas, algumas bloqueadas por rate-limit) | 19 (tabela `users`) |
| **Scans** | 69 | requests no access_log (incluía MCP, bots, `/scan/result` de cache) | ~35 (scans manuais reais) |
| **Jornada pré-signup** | poluída com `/admin/inbox/unread-count`, `/account/me`, `/events` repetidos | polling admin/sistema no caminho | caminho humano limpo |
| **Pre-fetch bots antigos** | contavam como humanos visitando centenas de sites | classificador do KL-92 P4 só marca IP **novo** | reclassificados |

## Causa raiz

O `access_log` é um log de **requests** — ótimo para "visitantes" e tráfego, péssimo para
"quantas contas/scans aconteceram". Um POST `/signup` bloqueado por rate-limit é um request,
mas **não** é uma conta. Um `GET /scan/result` servido do cache é um request, mas **não** é
um scan novo. E o MCP/bots batem nesses endpoints o tempo todo.

## Correções

### 1 + 2 — "Contas criadas" e "Scans" vêm da fonte autoritativa

`discovery/store.py::al_server_metrics` (KPIs) e `al_daily_series` (tendência):

- **"Contas criadas"** = `COUNT(*) FROM users WHERE created_at >= %s AND created_at < %s`.
- **"Scans"** = `COUNT(*) FROM scans WHERE scanned_at >= %s AND scanned_at < %s AND source
  IS DISTINCT FROM 'discovery'` — scans **manuais** (público + admin + `sync`), excluindo o
  worker de discovery (que gera milhares/dia). Não existe `source='manual'` literal no banco,
  então o critério correto é "tudo que não é o worker automático". Casa com o evento
  `scan_completed` da aba Eventos.

`al_daily_series` foi reescrita com **3 queries de fontes autoritativas** (visitantes BR do
access_log filtrado, scans de `scans`, contas de `users`) unidas por dia — cada série da sua
tabela, nada mais derivado do access_log.

Removidas as constantes/consulta antigas de POST-signup no access_log.

### 3 — Reclassificação retroativa de pre-fetch de e-mail

O classificador de pre-fetch (Gmail/Outlook/EOP — ranges `66.x`/`40.x`/`104.47`) do KL-92 P4
só marca **IPs novos**; o histórico ficou `is_bot=false` e aparecia como humano visitando
centenas de sites (os servidores de e-mail pré-carregam os links dos alertas).

- **`store.reclassify_prefetch_bots(ranges)`** — `UPDATE access_log SET is_bot=true,
  bot_reason='email_prefetch' WHERE is_bot=false AND ip_address <<= ANY(%s::cidr[])`.
  **Idempotente** (só toca `is_bot=false`) — pode rodar N vezes. Retorna a contagem.
- **`scripts/reclassify_prefetch_bots.py`** — one-off para a VM; usa o MESMO
  `_EMAIL_PREFETCH_CIDRS` do classificador (fonte única).
- **Boot da API** — `_reclassify_prefetch_bots_bg` roda no lifespan (best-effort), então ranges
  recém-adicionados ao classificador são aplicados ao histórico automaticamente no deploy.

### 4 — Jornada pré-signup sem polling/admin

- **SQL** (`al_pre_signup_journeys`): `_JOURNEY_EXCLUDE` filtra `/admin/%`, `/painel/%`,
  `/mcp/%`, `/account/me`, `/events`, `/health`, `/favicon.ico` — some o barulho de polling
  administrativo e de healthcheck no caminho.
- **Derivação** (`api/admin_analytics.py`): `_dedup_consecutive` colapsa passos **consecutivos**
  iguais (10× o mesmo endpoint de polling → 1 passo), aplicado em `steps_before`/`steps_after`.

## Definição das métricas (documentada)

Adicionada a `docs/ARCHITECTURE.md` e ao `claude.md` §9 a **definição autoritativa** de cada
métrica (o que cada card conta e por quê), para não regredir: contas = `users`, scans manuais =
`scans` menos discovery, visitantes = access_log filtrado, jornada = sem paths de sistema.

## Validação

- **SQL validado contra Postgres 16** (container descartável): `reclassified=2` / `UPDATE 2`
  (o `<<= ANY(::cidr[])` funciona), `scans_manual=3` (exclui 2 de discovery), `accounts=2`
  (de `users`), jornada mantém `/scan/result,/site/a,/site/b` e exclui `/admin/inbox/unread-count`.
- **Testes:** `tests/test_kl95_metrics.py` (+7) — contrato de fonte (`al_server_metrics`/
  `al_daily_series`), método `reclassify_prefetch_bots` idempotente com CIDR, script usa CIDRs
  compartilhados, `_JOURNEY_EXCLUDE`, e dedup de consecutivos (unitário + via jornada).
- **Suíte completa:** 1510 passed, 1 skipped (backend) + 96 node --test (frontend).

## Pós-deploy (VM)

Rodar uma vez a reclassificação do histórico (o boot já faz isso, mas confirma):

```bash
sudo docker exec klarim-api-1 python -m scripts.reclassify_prefetch_bots
```

Verificar no painel: "Contas criadas" bate com Usuários; "Scans" bate com o `scan_completed`
de Eventos; nenhum IP `66.x`/`40.x`/`104.47` aparece como humano; jornada sem polling admin.

## Arquivos

- `discovery/store.py` — `al_server_metrics`, `al_daily_series`, `reclassify_prefetch_bots`,
  `_JOURNEY_EXCLUDE` + `al_pre_signup_journeys`.
- `api/admin_analytics.py` — `_dedup_consecutive`, `assemble_pre_signup_journeys`.
- `api/main.py` — `_reclassify_prefetch_bots_bg` + spawn no lifespan.
- `scripts/reclassify_prefetch_bots.py` (novo).
- `tests/test_kl95_metrics.py` (novo, +7).
- `docs/ARCHITECTURE.md`, `claude.md` — definição das métricas.
