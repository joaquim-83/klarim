# KL-62 — Rastreabilidade unificada de e-mails (email_log + blocklist central)

## Problema

O diagnóstico (`claude/reports/KL-62_diagnostico_email.md`) mapeou **20 caminhos** de
envio via Resend, mas só **4** eram rastreados (`alert_log`+`rescan_log`). Os outros 16
— notificação de perfil consultado, código de verificação, cron de monitoramento,
relatórios, recuperação, reset de senha, etc. — saíam **sem registro, sem checagem de
blocklist e sem contabilidade de bounce**. Sintoma: a página Sistema mostrava "0 e-mails
hoje" enquanto o Resend registrava envios; bounce rate no escuro; risco à reputação do
domínio.

## Solução (1 ponto cobre os 20 caminhos)

**Todo** e-mail passa por `KlarimMailer._send`/`_send_batch`. Centralizando **log +
blocklist** aí, os 20 caminhos ficam cobertos **por construção** — nenhum chamador
precisa lembrar de logar.

### 1. Tabela `email_log` (`discovery/store.py`, via `ensure_schema`)

`email_id` (correlação com o webhook de bounce), `to_email`, `email_type`, `subject`,
`target_id`, `domain`, `status` (`sent`/`bounced`/`failed`/`blocked`/`complained`),
`blocked_reason`, `error`, `sent_at`, `source`, `batch_id` + 5 índices. **Não substitui**
`alert_log`/`rescan_log` (que continuam) — é a camada de contabilidade acima.

### 2. Centralização no `KlarimMailer` (`notifier/email_client.py`) — o cerne

- Construtor aceita `store` (injetável nos testes; produção usa o singleton lazy
  `get_target_store`, import tardio → sem ciclo). `_get_store`/`_is_blocked`/`_log_email`.
- **`_send(params, *, email_type, target_id, domain, source, skip_blocklist)`**: checa a
  blocklist (exceto transacionais) → loga `blocked` e **não envia**; senão envia e loga
  `sent` (com `email_id`) ou `failed` (+ erro, re-levanta). `_is_blocked` é **fail-open**
  (erro → envia); `_log_email` é **fire-and-forget** (nunca derruba o envio).
- **`_send_batch`**: loga cada e-mail com `batch_id`, honra a blocklist e **preserva o
  alinhamento** do retorno `ids` (bloqueado → `None` na posição — o `AlertWorker._send_
  with_split` mapeia `ids[i]`→`alerts[i]` posicionalmente; quebrar isso corromperia o
  `email_id` gravado por alvo). `types` opcional dá o `email_type` por item (alert vs
  alert_score100).
- **`EMAIL_TYPES`** (18 tipos) exportado; `email_type` + `skip_blocklist` fixos por método.

**Regra transacional × proativo:** o usuário tem direito de receber o que **pediu** →
`verification_code`, `report_*`, `password_reset`, `account_deleted`, `recovery`, `test`,
`contact` usam `skip_blocklist=True` (mas **são registrados**). E-mail **proativo**
(`alert`, `evolution`, `profile_view`, `account_evolution`, `monitor_*`) **respeita a
blocklist** — fecha a lacuna do KL-24 em todos os caminhos (antes só o Alert Worker
batch honrava). `send_alert`/`send_report` aceitam override de `email_type` (admin →
`admin_alert`/`admin_report`).

### 3. Métricas + atividade + bounce (leem do `email_log`)

- `store.email_metrics` → `sent_today/week/month` (exclui `test`), `blocked_today`,
  `failed_today`, `by_type`. `email_health` (bounce rate) idem — cobre os 20 caminhos.
  A página Sistema reflete sem mudança de frontend (as chaves já existem).
- **Atividade recente** (`/system/activity`) intercala e-mails (`type=email`/
  `email_blocked`, com tipo+destino+status) com os scans. Frontend: cores novas no
  `Sistema.jsx`.
- **Webhook** `/webhooks/resend` marca `email_log` **e** `alert_log` por `email_id`
  (`mark_email_status_by_email_id`). **Backfill** `/admin/process-bounces` lê do
  `email_log` (`get_sent_emails_for_bounce_check`, superset dos últimos 7 dias) e marca
  ambos.

### 4. API + MCP

- `GET /email/log` (JWT admin — sob o prefixo protegido `/email/`): filtros
  `email_type`/`status`/`to_email`/`source` + legenda `types`.
- MCP `get_email_log` (**45 tools** no total).

### 5. Migração do histórico

`scripts/backfill_email_log.py` → `store.migrate_email_log`: copia `alert_log`+`rescan_log`
para o `email_log`, **idempotente** (dedup por `source`+`to_email`+`sent_at`+`email_id`
via `NOT EXISTS`/`IS NOT DISTINCT FROM`). Rodar 1× na VM após o deploy para as métricas
não zerarem.

## Testes

`tests/test_kl62_email_log.py` (28 testes): centralização no mailer (log sent/blocked/
failed, skip_blocklist transacional, fail-open da blocklist, fire-and-forget do log, batch
com alinhamento preservado + tipos por item, sem store ainda envia), store SQL com cursor
falso (log_email normaliza to_email, email_metrics/email_health leem do email_log,
list_email_log filtra, mark/migrate idempotente, bounce-check), API (endpoint protegido +
lista + filtros, webhook marca email_log, atividade inclui e-mails), MCP get_email_log.
Fix de regressão (FakeStore drift): `test_bounce.py` ganhou `mark_email_status_by_email_id`;
`test_mcp_server.py` ganhou `get_email_log` em READ_TOOLS.

**Suíte offline completa: `722 passed, 1 skipped`.**

## Regras invioláveis respeitadas

- Log **por construção** no `KlarimMailer` (nenhum chamador precisa lembrar).
- Log **fire-and-forget** (nunca derruba o envio); blocklist **fail-open** (erro de infra
  nunca bloqueia um envio).
- Proativo respeita a blocklist; transacional pode ignorá-la mas **sempre** é registrado.
- `alert_log`/`rescan_log` **não** foram alterados; o webhook de bounce foi **estendido**,
  não removido.
- `ids` do batch mantém o alinhamento 1:1 (não quebra o `AlertWorker`).

## Operação (pós-deploy)

Rodar a migração uma vez para as métricas não zerarem:

```bash
gcloud compute ssh --zone us-central1-a instance-20260706-112125 \
  --project project-b08050df-fa4e-49ac-919
cd /opt/klarim && sudo docker compose exec -T api python scripts/backfill_email_log.py
```

**Config:** nenhuma variável nova. Recomendado conferir no painel do Resend que o webhook
`/api/webhooks/resend` cobre 100% dos e-mails da conta (não só os do funil), para o
bloqueio real-time valer para todos os caminhos.

## Arquivos

**Novos:** `scripts/backfill_email_log.py`, `tests/test_kl62_email_log.py`.

**Alterados:** `notifier/email_client.py`, `notifier/__init__.py`, `discovery/store.py`,
`api/main.py`, `mcp_server/tools/system.py`, `frontend/src/pages/admin/Sistema.jsx`,
`tests/test_bounce.py`, `tests/test_mcp_server.py`, `claude.md`, `README.md`.
