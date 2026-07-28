# KL-125 — Reverificação Power dos `unknown` + `email_verify_source` + bloqueio definitivo

**Data:** 2026-07-28 · **Status:** ✅ implementado, **1881 pytest passed** (+8 KL-125).
**Deploy:** pendente de push + CI. **Emergencial (28/07, já na VM):** 3.703 unknowns resetados
(`email_verified=false, email_verify_status=NULL`) + cache Redis limpo; o worker já reverifica.

---

## Problema

55 de 86 bounces (64%) em 3 dias vieram de e-mails verificados como **`unknown` pela Bulk API**
da Reoon. A Bulk é menos precisa que o **Power mode** para servidores brasileiros — quando
reverificados individualmente via Power, muitos viram `disabled` (score 4/100, não enviável).

O gate do KL-122 (`is_safe_to_send`: `unknown`/`catch_all`/`inbox_full` enviam se `lead_score>20`)
tratava `unknown` como "incerto mas talvez válido". Na prática, `unknown` da Bulk = "a API não
conseguiu verificar" = **alto risco de bounce**. `unknown` e `catch_all` são situações diferentes:
`catch_all` = servidor aceita tudo (risco moderado); `unknown` = servidor não respondeu ao SMTP
check (a caixa provavelmente não existe ou recusa verificação — alto risco).

## Solução

### 1. `is_safe_to_send` — `unknown` NUNCA envia (`notifier/email_verifier.py`)
Separado de `catch_all`/`inbox_full`: `unknown → False` **independente do lead_score**.
`catch_all`/`inbox_full` seguem o gate `> ALERT_UNSAFE_SCORE_GATE` (default 20). Os demais status
(`safe`/`valid`/`role`/`invalid`/`disabled`/`disposable`/`spamtrap`) **inalterados**.

### 2. Campo `targets.email_verify_source` (`discovery/store.py`)
`ALTER TABLE ... ADD COLUMN IF NOT EXISTS email_verify_source VARCHAR(10)` no `ensure_schema`.
Valores: `power` (alta) · `quick` (média) · `bulk` (baixa p/ BR) · `local` (básica).
`update_target_email_verification` ganhou o parâmetro `source` (grava via `COALESCE(%s, ...)` —
`source=None` preserva o valor anterior, não sobrescreve com NULL).

### 3. Alert worker: reverificar `unknown` via Power (`discovery/alert_worker.py::_verify_and_filter`)
Reescrito por-alvo (função pura `_verify_one` → `(t, keep, counters)`):
- **Regra 1** — `unknown` de fonte NÃO-power → **reverifica via Power**. Persiste `source=power`.
  `block` → blocklist (`power_verify_{status}`) + descarta. `unknown` de novo (2×) → **não envia,
  NÃO blocklist** (pode ser servidor temporário) — o `source=power` gravado faz o próximo ciclo
  pular. Resolveu (safe/catch_all/…) → `is_safe_to_send` decide.
- **Regra 2** — `unknown` de `source=power` → **skip imediato** (não regasta crédito Reoon).
- **Regra 3** — demais status → fluxo normal KL-110 (fresco no DB → cache; senão Power).
- **Fallback (Reoon fora)** → **NÃO persiste** (não condena o alvo) → retry no próximo ciclo; um
  `unknown` não confirmado nunca é enviado. Exceção de infra → fail-open (mantém).
- **Teto (`EMAIL_VERIFY_MAX_PER_CYCLE`=60):** só o topo por score toca a API; os `unknown` além do
  teto são pulados (voltam ao topo e são reverificados depois). **Sem `REOON_API_KEY`** a
  reverificação é no-op, mas um `unknown` conhecido ainda **não** é enviado (filtro barato, sem API).

### 4. `source` nos fluxos + cleanup (`scripts/cleanup_email_backlog.py`)
Fase 0 (local) → `source=local`; Fase 1 (Bulk API) → `source=bulk`; alert worker (Power) →
`source=power`. (`quick` fica reservado — o profiler ainda não persiste verificação.)

### 5. Stats por fonte + KL-57
`email_verification_stats` ganhou `by_source` (power/quick/bulk/local/unverified) —
`GET /system/email-verification-stats` + MCP `get_email_verification_stats`. O ciclo do worker loga
e retorna a **conversão** `unknown → safe/blocked/unknown` (`reverified_safe`/`reverified_blocked`/
`reverified_unknown`) — calibra a confiança na Bulk API (KL-57).

## Segurança
- `REOON_API_KEY` só do `.env` (nunca em log/código); cache por SHA-256; semáforo de 5 chamadas.
- Reverificação **fail-open** (Reoon fora nunca trava o pipeline nem condena o alvo).
- Nenhum novo dado sensível exposto; `_mask` nos logs de e-mail.

## Testes
- **Novos** (`tests/test_kl125_unknown_reverify.py`, +8): reverify bulk→safe (persiste power),
  reverify→disabled (blocklist+descarta), reverify→unknown 2× (não envia, não blocklist, grava
  power), fallback não persiste, `unknown`/power skip sem API, `safe`/bulk fresco envia sem API,
  `rest` além do teto descarta unknown, no-key ainda descarta unknown.
- **Atualizados:** `test_kl110_email_verifier.py` (is_safe_to_send unknown→False; `by_source` no
  stats; `_MiniStore.source`; block `power_verify_`), `test_email_pipeline.py` (unknown nunca envia).
- `pytest`: **1881 passed, 1 skipped**.

## Validação pós-deploy
1. `GET /system/email-verification-stats` mostra `by_source` (power crescendo conforme o worker roda).
2. Log do ciclo: `reverify N (→safe X, →bloq Y, →unknown Z)`.
3. Bounce rate hard dos cold senders cai (o `unknown` da Bulk deixa de ser enviado).
4. `docker compose -f docker-compose.dev.yml` — seed + worker (sem REOON_API_KEY: no-op mas não
   envia unknown). Fechar KL-125 no Jira após a queda de bounce em 7d.

## Arquivos
- Alterados: `notifier/email_verifier.py`, `discovery/store.py`, `discovery/alert_worker.py`,
  `scripts/cleanup_email_backlog.py`, `api/main.py` (docstring), `mcp_server/tools/system.py`
  (docstring), `docs/API.md`, `CLAUDE.md`, `tests/test_kl110_email_verifier.py`,
  `tests/test_email_pipeline.py`.
- Novos: `tests/test_kl125_unknown_reverify.py`, `claude/reports/KL-125_unknown_reverify.md`.
