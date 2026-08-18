# KL-169 — Priorização de e-mails: pessoais primeiro, genéricos como fallback

**Card:** KL-169 · **Prioridade:** Highest · **Tipo:** Fix (pipeline de e-mail)
**Data:** 2026-08-18

## Problema — 3 tentativas que não resolveram

| Card | Abordagem | Resultado |
|---|---|---|
| **KL-146** | Priorizou pessoais (+15) sobre genéricos (-10) **no lead score** | Só ordena o score, **não a seleção** de candidatos → sem efeito prático |
| **KL-167** | **Filtrou** genéricos (`ALERT_SKIP_GENERIC=true`) | Bloqueou **97%** do pool BR (`contato@`/`sac@`/`info@` são o e-mail principal das PMEs) → pipeline parou |
| **KL-168** | **Desligou** o filtro (`ALERT_SKIP_GENERIC=false`) | Genéricos voltaram → **25% de bounce** (12/48). Inaceitável |

**Causa-raiz:** o sistema tratava a seleção como **binária** (envia ou bloqueia). O correto não é
filtrar nem bloquear — é **PRIORIZAR**: pessoais primeiro, genéricos só quando não há pessoal.

## Solução — priorizar na SELEÇÃO (a query), não no envio

O ciclo busca só `ALERT_FETCH_CAP` (200) candidatos de um pool de **milhares**. Portanto **o `ORDER BY`
da query de elegibilidade é o que decide QUEM entra no ciclo.** Se os pessoais ficarem no fim da
ordenação, são truncados pelo `LIMIT` e o ciclo manda só genérico (o que gerou os 25% de bounce).

### 1. `store.get_eligible_targets_for_alert` — ORDER BY pessoais primeiro
```sql
ORDER BY CASE WHEN t.contact_email ~* %s THEN 1 ELSE 0 END ASC,  -- pessoais (0) antes de genéricos (1)
         t.last_scan_score ASC,                                   -- piores scores primeiro (urgência)
         (lower(t.domain) = split_part(lower(t.contact_email),'@',2)) DESC,  -- e-mail no domínio (qualidade)
         t.last_scan_at ASC                                       -- desempate estável (fix livelock)
LIMIT %s
```
O regex (`%s`) é `GENERIC_PREFIX_SQL_REGEX`, parametrizado (constante, nunca input do usuário).

### 2. Removido o filtro binário
- `ALERT_SKIP_GENERIC` — removido do worker (class attr, `__init__`, `_reload_settings`) e do
  `_CONFIG_PARAMS` do painel (`api/main.py`).
- `is_generic_alert_email()` (filtro) e `GENERIC_ALERT_SKIP_PREFIXES` — removidos.
- `blocked_generic` — removido dos stats de `_verify_and_filter`. Genéricos **NUNCA** são bloqueados.

### 3. Classificar em vez de filtrar
`alert_scoring.is_generic_email(email)` — **classificador** (não filtro), 7 prefixos de negócio BR:
`contato`/`sac`/`info`/`comercial`/`vendas`/`atendimento`/`suporte`. Usado só para ORDENAR e para o
breakdown de stats. O **mesmo** regex vive em `GENERIC_PREFIX_SQL_REGEX` (single source of truth) —
há teste de consistência SQL↔Python (`test_sql_regex_matches_python_classifier`).

### 4. Stats do ciclo com breakdown por tipo
`run_cycle` agora reporta `sent_personal` / `sent_generic` (classificados no envio via
`is_generic_email`). O resumo do ciclo (`[alert] ciclo:`) e o `last_cycle_stats` (heartbeat →
`get_system_status`) mostram os dois. `blocked_generic` não existe mais.

## O que foi mantido
- **Intervalo mínimo por e-mail** (`ALERT_REALERT_MIN_DAYS`, 90d) → `blocked_recent_email` (KL-167/168).
- **3 filtros locais do KL-145** (sintaxe + MX + blocklist). MX fail-open com log (KL-168).
- **`_urgency_bucket`** no `run_cycle` (score `<70` primeiro) — reordena o que foi buscado.
- **`_email_type_factor`** (KL-146) segue no lead score, só reordenando.

## Testes
- **Novo** `tests/test_kl169_email_priority.py` (4 grupos): `GENERIC_PREFIXES` = os 7; `is_generic_email`
  (parametrizado, sem overmatch); shape do `GENERIC_PREFIX_SQL_REGEX`; **consistência SQL↔Python**.
- **`tests/test_alert_worker.py`:** `test_run_cycle_never_blocks_generics_and_counts_breakdown`
  (mix → todos enviados, `sent_personal=1`/`sent_generic=3`, sem `blocked_generic`) +
  `test_run_cycle_only_generics_still_sends` (só genéricos → enviados, não bloqueados).
- **Atualizados** `test_kl167_email_consolidation.py` (classificador em vez de filtro) e
  `test_kl168_alert_regression.py` (removidos os testes de `skip_generic`; MX/90d permanecem).
- **Suíte cheia verde:** `2487 passed, 1 skipped` (pytest) + `246 pass` (`npm run test:unit`).

## Validação em dev (`docker-compose.dev.yml` — Postgres real)
Inseridos 6 alvos (3 pessoais + 3 genéricos, scores variados) e chamado
`get_eligible_targets_for_alert`. Ordem retornada:
```
[PESS] score= 50  ana.silva@gmail.com
[PESS] score= 60  maria@kl169-d.com.br
[PESS] score= 80  joao@kl169-b.com.br
[GEN ] score= 20  sac@kl169-c.com.br
[GEN ] score= 30  contato@kl169-a.com.br
[GEN ] score= 40  info@kl169-e.com.br
```
✅ Pessoais primeiro (score ASC dentro do grupo), genéricos depois (score ASC) — nenhum bloqueado.

## Validação pós-deploy (produção)
Aguardar 2-3 ciclos e via `get_system_status` (`last_cycle_stats`):
- `sent > 0`;
- `sent_personal > sent_generic` (pessoais priorizados) quando há pessoais no pool;
- `blocked_generic` NÃO aparece nos stats;
- se só há genérico no pool, `sent_generic > 0` (não bloqueados);
- **bounce rate** deve cair vs. KL-168 (25%) — pessoais têm ~3,6% vs ~8,7% dos genéricos.

## Nota operacional
Remover a chave `ALERT_SKIP_GENERIC` do `admin_settings` na VM (era medida emergencial do KL-168;
o worker não a lê mais, então é inócua, mas limpa o painel):
```sql
DELETE FROM admin_settings WHERE key = 'ALERT_SKIP_GENERIC';
```

## Arquivos alterados
- `discovery/store.py` — ORDER BY personal-first em `get_eligible_targets_for_alert`.
- `discovery/alert_scoring.py` — `GENERIC_PREFIXES` (7) + `is_generic_email` (classificador) + `GENERIC_PREFIX_SQL_REGEX`.
- `discovery/alert_worker.py` — remove `skip_generic`/`blocked_generic`; add `sent_personal`/`sent_generic`.
- `api/main.py` — remove `ALERT_SKIP_GENERIC` do `_CONFIG_PARAMS`.
- Tests: `test_kl169_email_priority.py` (novo) + `test_alert_worker.py` + `test_kl167_*` + `test_kl168_*`.
- `CLAUDE.md` (§4 Targeting/lead-scoring, §8, §11).
