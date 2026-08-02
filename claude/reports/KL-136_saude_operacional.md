# KL-136 — Saúde operacional do pipeline de alerta

**Data:** 2026-08-02
**Contexto crítico:** funil de alertas travado (200 elegíveis → 149 pulados por baixa
qualidade → 8 verificados → **4 enviados**), bounce **14,1%** no dia, backlog **2.934** que
não escoa, score médio dos leads **24,9**.

Seis correções (4 P0, 2 P1/P2), tudo backend, testado offline. **Nenhuma quebra os testes
existentes** — os que assumiam os defaults antigos foram atualizados.

---

## Fix 1 (P0) — Lead scoring: penalidade role-based -15 → -5

**Arquivo:** `discovery/alert_scoring.py`

No Brasil, `contato@`/`vendas@`/`sac@` é o e-mail **padrão** de PME — não indicador de baixa
qualidade. O -15 (KL-85/KL-110) barrava a maioria dos leads na *action zone* (score 50-89):

```
contato@ na action zone:  +10 (corp) +20 (action) -15 (role) = 15  <  threshold 20  → REJEITADO
com -5:                    +10 (corp) +20 (action)  -5 (role) = 25  >  threshold 20  → PASSA
```

- Penalidade configurável por env `ALERT_ROLE_PENALTY` (default **-5**), lida **a cada
  chamada** via `_role_penalty()` (mesmo padrão do gate de deliverability; fail-safe p/ valor
  inválido).
- **Duplicação verificada:** havia dois sinais de caixa de função — `role_based_prefix` (por
  prefixo, KL-85) e `email_role_account` (status `role` da Reoon, KL-110). **Não** eram
  duplicados (o segundo só entra `if not role_penalized`). Ambos passaram a usar `_role_penalty()`
  → mesma penalidade, nunca dobram. Nenhum removido (não havia duplicação real a remover).

---

## Fix 2 (P0) — Gates separados por status em `is_safe_to_send`

**Arquivo:** `notifier/email_verifier.py`

`catch_all` respondia por ~37% dos bounces (12 de 32 em 48h): o servidor aceita QUALQUER caixa
no SMTP-check, mas a caixa pode não existir. O gate único de 20 (herdado do `inbox_full`) era
permissivo demais.

- Novo gate **separado e mais alto** p/ `catch_all`: `ALERT_CATCH_ALL_SCORE_GATE` (default
  **30**, `>` não `>=`), via `_catch_all_gate()`.
- `inbox_full` segue no `ALERT_UNSAFE_SCORE_GATE` (20).
- `unknown` **continua sempre bloqueado** (KL-128, inalterado); `safe`/`valid`/`role` sempre
  enviam; block-statuses nunca.

| status | score 25 | score 31 | regra |
|---|---|---|---|
| catch_all | ❌ (≤30) | ✅ (>30) | `> ALERT_CATCH_ALL_SCORE_GATE` (30) |
| inbox_full | ✅ (>20) | ✅ | `> ALERT_UNSAFE_SCORE_GATE` (20) |
| unknown | ❌ | ❌ | sempre bloqueado |

---

## Fix 3 (P1) — `blocked_known` breakdown (regressão do KL-130)

**Arquivo:** `discovery/alert_worker.py`

O ciclo mostrava `blocked_known: 43` sem dizer QUAIS status poluíam. Investigação:

1. **Query de elegíveis (`_ALERT_ELIGIBLE_WHERE`):** já exclui TODOS os terminais —
   `unknown`+`power`, `disabled`, `invalid`, `disposable`, `spamtrap` (NULL-safe via COALESCE,
   KL-130). **Nada faltando.**
2. **Partição (`_verify_and_filter`):** o `blocked_known` já é filtrado **ANTES** do subset de
   verificação (KL-129) — não consome vaga de API. **Sem regressão.**
3. **Adicionado o breakdown:** coleta o status EFETIVO de cada já-barrado e loga
   `[alert] blocked_known breakdown: {catch_all: N, disabled: M, …}` (nos dois caminhos: com key
   e no modo degradado). Revela o que polui o fetch (tipicamente `catch_all` com score baixo e
   `unknown` de fonte não-power, que a query não exclui por design — re-verificáveis quando o TTL
   expira).

---

## Fix 4 (P0) — Verificação Reoon parada + saldo + fail-safe

**Arquivos:** `notifier/email_verifier.py`, `discovery/alert_worker.py`, `api/main.py`,
`discovery/store.py`

**Causa raiz provável (a confirmar na VM):** `REOON_API_KEY` ausente/sem saldo → o alert worker
caía no **fail-open** do KL-110 (não-verificados passavam) → enviava SEM verificar → os 12
bounces "sem verificação" das 48h; e `reoon_balance: null`, 85% dos e-mails sem verificação.

- **Fail-open → fail-safe:** se `REOON_API_KEY` **existe** mas o saldo está **esgotado**
  (0/negativo), o worker NÃO verifica novas caixas — **defere TODAS** as não-verificadas
  (`cap=0`, contam em `deferred`, **não** são enviadas). Os já-verificados-OK (`sendable`)
  seguem. Saldo `None` (ilegível) = fail-open: NÃO trata como esgotado. Só consulta o saldo
  quando há caixas novas a verificar (evita HTTP à toa — inclusive nos testes).
- **Cache 1h** `reoon:balance` no Redis, **compartilhada** entre API e worker.
- **`GET /system/status`** ganhou o bloco `email_verification`:
  `{reoon_balance, reoon_balance_warning (<1000 OU None), unverified_count, verified_last_cycle,
  deferred_last_cycle, reoon_exhausted}`.
- **`by_source` só mostra `power`** (não é bug): os 16k unverified têm `email_verify_source=NULL`
  (coluna do KL-125 nunca teve backfill). Documentado; não corrigido (é ausência de backfill).

**Comando de diagnóstico do saldo (rodar na VM):**
```bash
docker exec klarim-api-1 python3 -c "
import asyncio
from notifier.email_verifier import check_balance
print('Balance:', asyncio.run(check_balance()))"
```
Se `None` → `REOON_API_KEY` ausente no `.env` da VM (é o cenário mais provável). Se `0` → sem
crédito; o fail-safe agora **defere** em vez de enviar sem verificar.

---

## Fix 5 (P1) — Re-scan worker com 0 elegíveis

**Arquivos:** `discovery/store.py`, `discovery/rescan_worker.py`

O ciclo dava `eligible: 0, rescanned: 0` com 36.755 scans. A query de elegibilidade é
**correta** (sem filtro escondido): 

```sql
status IN ('scanned','alerted') AND contact_email IS NOT NULL
  AND last_scan_at < NOW() - RESCAN_AGE_DAYS days   -- default 30
```

- **`store.rescan_diagnostics(days)`** decompõe o funil: `engaged → engaged_with_email →
  eligible → too_recent`. O worker o chama **só quando `eligible=0`** e loga:
  `[rescan] 0 elegíveis (janela 30d): engajados=…, com_email=…, elegíveis=…, recentes_demais=…`.
  Isso revela em produção SE o problema é a **janela** (todos re-escaneados recentemente) ou o
  **pool** (poucos engajados-com-e-mail — o mais provável, já que o pipeline moveu alvos p/
  `sem_contato`/`descartado`/`alerted` e a descoberta cria em `discovered`, inelegível).

**Query p/ conferir na VM:**
```sql
SELECT count(*) FROM targets
WHERE last_scan_at < NOW() - INTERVAL '7 days'
  AND status NOT IN ('descartado','sem_contato','unsubscribed','discovered');
```
Se >0 e o worker ainda diz 0, é a janela de 30d; baixar `RESCAN_AGE_DAYS` (editável ao vivo).

---

## Fix 6 (P2) — Divergências de métrica

**Arquivos:** `discovery/store.py`, `claude.md`

1. **`sent_month` (157) < `sent_week` (5.349):** são **fontes diferentes**, não bug —
   `sent_month` = `count_proactive_emails_this_month` (PROATIVO: alert_log+rescan_log,
   mês-**calendário**), `sent_week` = `email_metrics.sent_week` (email_log, **TODOS** os tipos,
   7 dias móveis). No dia 1 do mês `sent_month` < `sent_week` é **esperado**. Aplicado o boundary
   **explicitamente UTC** (`date_trunc('month', NOW() AT TIME ZONE 'UTC')`) — as colunas
   `sent_at`/`rescanned_at` são `TIMESTAMP` naive-UTC; sem isso o corte usava a TZ da sessão.
2. **`scans.today` dashboard vs system_status:** medem coisas diferentes — dashboard = scans
   **manuais** (`source != 'discovery'`, KL-95); system_status = **todos** os scans do dia
   (`scan_today_stats`, incl. worker discovery). Documentado (autoritativa por contexto).
3. **`account_created` funnel vs server_metrics:** **server-side é autoritativo** (KL-92) —
   `COUNT(*) FROM users`; o funil vinha do tracker.js (client-side, inflado por pre-fetch).
   Documentado.

**Fontes autoritativas** ficaram registradas no `claude.md` (bloco após o card KL-133).

---

## Testes

- **Novo:** `tests/test_kl136_operational_health.py` — **23 testes**: role penalty
  default/env/inválido + cenário `contato@` action-zone passa (25) / falha com -15 (15); gate de
  catch_all (30) e inbox_full (20) separados + env override; `unknown` sempre bloqueado;
  fail-safe de saldo (esgotado defere; positivo verifica; None não bloqueia; não consulta sem
  não-verificados; ainda envia já-verificados); breakdown de `blocked_known` logado; shape do
  diagnóstico de re-scan.
- **Atualizados** aos novos defaults: `test_kl85_scoring.py` (role -5 + env), `test_kl110_email_
  verifier.py` (gate catch_all 30 + inbox_full 20 + role status -5), `test_kl127_pipeline_
  integration.py`, `test_kl129_subset_priority.py` / `test_kl130_exclude_terminals.py` (mock de
  `check_balance` p/ manter offline).
- **`1956 pytest passed, 1 skipped`** (era 1926; +30 líquido).

---

## Deploy

- Commit + push → CI (test + deploy). Após o deploy, na VM:
  - Conferir o saldo Reoon (comando acima). Se `REOON_API_KEY` faltar, configurar no `.env` e
    recriar os containers.
  - Acompanhar o `[alert] blocked_known breakdown: …` e o `[rescan] 0 elegíveis: …` nos logs
    (`docker logs klarim-discovery-1`).
  - Ajuste fino sem deploy: `ALERT_CATCH_ALL_SCORE_GATE` (30), `ALERT_ROLE_PENALTY` (-5),
    `ALERT_UNSAFE_SCORE_GATE` (20) — via `.env` (recriar container) ou painel (`admin_settings`).
- Sem flush Redis necessário (nenhuma mudança de `scoring.py`/check).

## Env vars novas (docs/DEPLOY.md)
- `ALERT_ROLE_PENALTY=-5`
- `ALERT_CATCH_ALL_SCORE_GATE=30`
