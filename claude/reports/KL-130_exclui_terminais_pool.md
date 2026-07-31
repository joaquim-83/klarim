# KL-130 — Excluir unknowns definitivos do pool de elegíveis + destravar 3.247 e-mails

**Data:** 2026-07-31 · **Status:** ✅ código + testes + docs; **1904 pytest passed**, SQL validado
no Postgres 16 da VM. **Deploy:** via commit/push (CI/CD) + cleanup one-off.

---

## Problema (10 alertas/dia em vez de centenas)

Mesmo após o KL-129 (partição prioriza os não-verificados), os logs davam `verified: 0 (API)`. A
causa NÃO era a partição — era a **query de elegíveis**. `get_eligible_targets_for_alert` busca
`fetch_cap`=200 alvos **ordenados por `last_scan_at ASC`**. Havia **173 `unknown`+`power`** (velhos,
já verificados, sempre barrados pelo gate KL-128) que ordenavam na frente e **enchiam o batch de
200** → os **3.247 e-mails NOVOS** (`email_verified=false`) ficavam no fim da fila e nunca eram
buscados → a partição não tinha o que verificar. Diagnóstico na VM confirmou: pool = 3.168 null-null
(novos) + 173 unknown+power + 74 valid + 18 catch_all + 7 unknown-null. `bengazzi2012@hotmail.com`
(target 70444) = um dos 3.247, `email_verified=f`, 0 alertas. REOON_API_KEY presente (len 32).

## Solução

### 1. Exclui status terminais do `_ALERT_ELIGIBLE_WHERE` (`discovery/store.py`)
```sql
AND NOT (COALESCE(t.email_verify_status,'') = 'unknown' AND COALESCE(t.email_verify_source,'') = 'power')
AND (t.email_verify_status IS NULL OR t.email_verify_status NOT IN ('disabled','invalid','disposable','spamtrap'))
```
⚠️ **Bug de NULL pego na validação:** o 1º draft `NOT (email_verify_status = 'unknown' AND …)` faz
`NULL = 'unknown'` → NULL, e `WHERE … AND NOT(NULL)` **exclui** as linhas de status NULL — ou seja,
os 3.247 novos que eu quero INCLUIR. Validando na VM, elegíveis caíam de **3.444 → 92**. Corrigido
com `COALESCE(...,'')` (NULL vira `''` ≠ 'unknown' → mantém). Re-validado: **3.444 → 3.272**, 0
unknown+power, 3.247 novos preservados, bengazzi passa.

### 2. Aposenta unknown+power (sai do pool de vez)
- **No worker** (`_verify_one`): um alvo verificado como `unknown` via Power → `update_status(id,
  'sem_contato')` (NÃO blocklist — o e-mail não é "ruim", só não confirmável) → não volta ao pool.
- **Retroativo:** `store.retire_unknown_power_targets()` + `scripts/retire_unknown_power.py`
  (`--dry-run`) marca `sem_contato` os ~173 já existentes. Idempotente.
- O filtro SQL (item 1) é a defesa contínua; a mudança de status é a limpeza permanente.

### 3. Investigação da partição KL-129 (item 3)
A partição estava **correta** (`email_verified=false` → `unverified` → subset → Power; tests do
KL-129 confirmam). O `verified:0` era 100% causado pela query trazendo só terminais. Adicionado log
de diagnóstico: `[alert] KL-130 partição: N sendable, N blocked_known, N unverified (de N) → subset
N`; contador `retired_unknown`; label do ciclo `verify KL-130: …`.

### 4. Fluxo rápido (meta: scan → alerta em ≤2 ciclos)
Com o pool limpo, o fetch de 200 enche com os NOVOS → verificados via Power NESTE ciclo → safe →
alerta no mesmo ciclo; excedente no próximo (`deferred`).

## Testes (1904 pytest passed)
- **Novos** (`tests/test_kl130_exclude_terminals.py`, +5): WHERE contém as exclusões + é **NULL-safe**
  (COALESCE + `IS NULL OR NOT IN`); worker marca `sem_contato` um novo unknown+power (sem blocklist);
  safe envia e NÃO aposenta; disabled → blocklist+descartado (não `retired_unknown`); método
  `retire_unknown_power_targets` emite o UPDATE certo. SQL validado no Postgres 16 da VM.

## Validação pós-deploy
1. Rodar a limpeza: `docker compose exec api python -m scripts.retire_unknown_power` (marca os 173).
2. Próximo ciclo: `[alert] KL-130 partição: … unverified > 0` e `verify KL-130: … verif (API) > 0`,
   `blocked_known ~0`, `sent > 0`.
3. `diff`/md5 de `alert_worker.py`+`store.py` entre containers = idêntico.
4. `bengazzi2012@hotmail.com` (70444) verificado via Power e, se safe, alertado em ≤2 ciclos.
5. Fechar KL-130 no Jira.

## Arquivos
- Alterados: `discovery/store.py` (`_ALERT_ELIGIBLE_WHERE` + `retire_unknown_power_targets`),
  `discovery/alert_worker.py` (retire unknown+power + log de partição + contador), `CLAUDE.md`.
- Novos: `scripts/retire_unknown_power.py`, `tests/test_kl130_exclude_terminals.py`,
  `claude/reports/KL-130_exclui_terminais_pool.md`.
