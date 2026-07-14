# Cobertura MCP + fix da divergência de dados de scan

**Data:** 2026-07-14 · **Prioridade:** alta

## 1. Divergência de `last_scan_at` (PRIORITÁRIO) — resolvida

**Diagnóstico (produção):** o `get_system_status`/`GET /system/status` reportava o
`scan.last_scan_at` a partir do **heartbeat do worker** (Redis `worker:scan:status`), enquanto
a página Scans do painel lê o **banco** (`list_scans`).

Query na VM:
```
worker:scan:status  last_scan_at = 16:24:51   (heartbeat)
MAX(scans.scanned_at) = 16:24:34              (banco)
list_scans(limit=5) first = 16:24:34          (== banco → o PAINEL estava certo)
```

**Causa:** o worker seta `last_scan_at = datetime.now()` no loop **depois** do `enrich_profile`
(crawl ~8s) e **mesmo quando o scan não persiste** (score None, exceção no save). Então o
heartbeat **avança além do banco** (no relato do card, 29 min). O `list_scans` NÃO filtra scans
recentes — o painel sempre mostrou o dado certo (o MCP é que estava adiantado).

**Fix:** `scan.last_scan_at` agora vem de **`store.last_scan_at()`** (`MAX(scans.scanned_at)`) —
a mesma fonte do painel → **MCP == painel**. O valor do heartbeat vira `scan.worker_last_activity`
(liveness/transparência).

## 2. Nova tool `get_dashboard_stats`
Expõe os **mesmos totalizadores** da home do painel (`GET /admin/dashboard-stats`): alvos,
scans (total/manual/auto/hoje/7d/média/semáforo/score 100), perfis/landings, contas, alertas +
inbox não lidas. Via `store.dashboard_summary()` + `inbox_unread_count()`.

## 3. Tools existentes enriquecidas (consistência)
- **`get_target_stats`** ganhou `profiles` (total, `with_description`/IA, `with_cnae`,
  `public_visible`) — via novo `store.profile_counts()`.
- **`get_scan_stats`** ganhou `manual`/`automated` (`scanned_by_email`), `today`, `last_7_days`,
  `score_100_count` — o `store.scan_stats()` agora espelha `dashboard_summary().scans`, então
  o `/scans/stats` do painel também ganha os campos.
- **`get_system_status`** — crons + disco **pulados** de propósito (o container `api` não lê o
  crontab nem o disco do host; o card permitia pular). O fix do `last_scan_at` está aqui.

## 4. Novas tools
- **`get_user_accounts`** — contas de usuário + sites (reusa `admin_clients` /
  `list_users_with_sites`).
- **`get_enrichment_status`** — backlog do enriquecimento por grupo (G1 sem perfil, G2 sem IA,
  G3 sem descrição, G4 sem CNAE, via `count_enrichment_groups`) + `sem_contato` sem scan (KL-60,
  `count_unscanned_targets`).
- **`search_inbox`** — busca no inbox por texto (assunto/remetente/preview, ILIKE), `source`
  (webhook/contact_form) e `unread_only`. `list_inbox_messages` ganhou `search`; o `GET
  /admin/inbox` também (aditivo, para o painel poder usar).

## 5. Contagem de tools
**38 → 42** (`get_dashboard_stats`, `get_enrichment_status`, `get_user_accounts` em `system.py`;
`search_inbox` no novo `mcp_server/tools/inbox.py`, registrado no `__init__`). Doc atualizada
(CLAUDE.md §44, README).

## Testes
`tests/test_mcp_server.py`: +6 (`get_dashboard_stats`, `get_scan_stats` manual/auto,
`get_enrichment_status`, `get_user_accounts`, `search_inbox`, `get_target_stats` com profiles) +
as 4 novas tools na lista de registro; `FakeStore` estendido. Full-suite + CI verdes.

## Regra inviolável
MCP e painel mostram os **mesmos dados** — datas/contagens vêm sempre do **banco**, nunca de
heartbeat em memória (que diverge). Toda tool nova é **leitura**, passa pelo `_guard` e reusa
métodos/endpoints existentes (sem lógica duplicada).
