# KL-23 — Batch sending (Resend Pro): envio em lote + fim do throttle Free + idempotency

- **Card Jira:** KL-23
- **Data:** 2026-07-09
- **Executor:** Claude CLI (Opus 4.8)
- **Depende de:** KL-8 (e-mail), KL-12 (Alert Worker), KL-13 (Re-scan Worker)
- **Commit:** `feat(KL-23): implement Resend Pro batch sending, remove Free throttle, add idempotency`

---

## Objetivo

O plano **Resend Pro** ($20/mês) subiu os limites para **50.000 e-mails/mês, sem
teto diário, batch de até 100 por request**. O throttle antigo (4 alertas/ciclo,
90/dia, pausa de 5s entre envios) existia só por causa do limite Free (100/dia) e
estava **travando o funil**: ~350 alvos escaneados esperavam alerta, drenando a
~90/dia (≈4 dias). Com batch sending (200/ciclo, ciclo de 30min), o backlog de ~350
drena em **~2 ciclos (< 40 min)** — 200 no primeiro, o resto no seguinte.

## O que mudou

### Parte 1 — Batch no `KlarimMailer` (`notifier/email_client.py`)

- **`send_alert_batch(alerts)`** e **`send_evolution_batch(evolutions)`** — renderizam
  o template por item (Jinja2, reusando os novos helpers `_alert_params` /
  `_evolution_params`, que também servem o envio único) e mandam **até 100 e-mails em
  1 request**. Retornam `{"sent", "failed", "ids"}` com os IDs na ordem do input.
- **Idempotency key** — `batch_idempotency_key(items)`: `"batch_" + sha256(emails
  ordenados + data UTC)[:32]`. Determinística e **independente da ordem**; reenviar o
  mesmo batch no mesmo dia (retry após timeout/erro de rede) reusa a chave e o Resend
  **não duplica** (válida 24h).
- **`_send_batch_raw(payloads, key)`** — o SDK Python do Resend não expõe o header de
  idempotência no `Batch.send()`, então falamos com `POST https://api.resend.com/
  emails/batch` via **httpx**, com `Idempotency-Key`. Erros 4xx/5xx ou de rede viram
  `KlarimMailerError`. `_send_batch` conta sucesso/falha a partir do `data[]`.

### Parte 2 — Alert Worker em lote (`discovery/alert_worker.py`)

- `run_cycle()` reescrito: busca **todos** os elegíveis (sem cap por ciclo), agrupa em
  batches de `ALERT_BATCH_SIZE` (50) e envia `ALERT_BATCHES_PER_CYCLE` (4) batches por
  ciclo (**200 alertas/ciclo**), com pausa `ALERT_BATCH_PAUSE` (10s) entre batches.
  Resposta 2xx do Resend ⇒ batch aceito ⇒ marca `alerted` + `log_alert` para cada
  alvo; falha ⇒ `log_alert(status='failed')` **sem** marcar `alerted`.
- **`build_alert_payload`** monta o dict do alerta a partir dos campos já trazidos
  pelo JOIN de `get_eligible_targets_for_alert` (sem N+1); cai para `get_scan` se
  faltar. **`send_alert_for_target`** (envio único) foi mantido para os disparos
  manuais da API.
- **Throttle antigo removido:** `MAX_ALERTS_PER_HOUR`, `MAX_ALERTS_PER_DAY`,
  `MAX_ALERTS_PER_CYCLE` e a pausa de 5s entre envios. **Mantidos:** janela de 30 dias
  por alvo, filtro de descadastrados e de e-mail válido (tudo na query de elegíveis).

### Parte 3 — Re-scan Worker em lote (`discovery/rescan_worker.py`)

- O re-scan continua **individual** (cada site precisa ser varrido), mas o e-mail de
  evolução agora é **adiado** (`send_email=False`) e despachado em **lote** ao fim do
  ciclo (`_flush_pending_batch` → `send_evolution_batch`), incluindo pendências de
  ciclos anteriores. Removidos `max_hour`/`max_day`/`_throttle_ok`/`_flush_pending`
  (unitário).

### Parte 4 — Cota mensal (substitui o throttle hora/dia)

- **`store.count_proactive_emails_this_month()`** (KL-23) soma `alert_log` +
  `rescan_log` do **mês corrente** (`date_trunc('month', NOW())`). Substitui e remove
  `count_proactive_emails_last_hours()`.
- **`ALERT_MONTHLY_LIMIT`** (45.000 — reserva 5k dos 50k do Pro p/ transacionais) é o
  **único teto**, compartilhado por alertas e evoluções. Ao atingir, o worker para com
  log; no Re-scan, o e-mail fica pendente para o próximo ciclo.
- **Não sobrepassa:** a busca de elegíveis já é limitada a `min(cap_do_ciclo, cota
  restante)`, e há um segundo corte em tempo real por batch (contador `skipped`).

### Parte 5 — Dashboard e API

- **`GET /api/system/status`** → `email_metrics` agora traz `sent_today`, `sent_week`,
  `sent_month`, `monthly_limit`, `monthly_usage_pct`, `backlog`; o bloco do worker
  `alert` troca `throttle_limit` por `sent_month`/`monthly_limit`/`backlog`.
- **`GET /api/config`** troca `max_alerts_per_hour/day/cycle` por `alert_batch_size`,
  `alert_batches_per_cycle`, `alert_batch_pause`, `alert_monthly_limit`.
- **Frontend:** `Sistema.jsx` (card Alert com "Este mês X/45000" + "Backlog"; cartões
  de e-mail com uso mensal e backlog) e `Config.jsx` (linhas de batch).
- Os disparos manuais (`POST /targets/{id}/alert`, `/admin/resend-alert`) mantêm o
  **bypass** da cota, agora com log explícito.

### Parte 6 — Variáveis de ambiente (`.env.example`)

- **Removidas:** `MAX_ALERTS_PER_CYCLE`, `MAX_ALERTS_PER_HOUR`, `MAX_ALERTS_PER_DAY`.
- **Adicionadas:** `ALERT_BATCH_SIZE=50`, `ALERT_BATCHES_PER_CYCLE=4`,
  `ALERT_BATCH_PAUSE=10`, `ALERT_MONTHLY_LIMIT=45000` (`ALERT_INTERVAL_MINUTES=30` já
  existia).

## Testes

- `tests/test_notifier.py` — idempotency key (determinística/ordem-independente),
  `send_alert_batch`/`send_evolution_batch` (contagem + IDs + truncagem em 100),
  `_send_batch_raw` (endpoint + header `Idempotency-Key` + `Authorization`, e erro
  4xx → `KlarimMailerError`), via httpx falso.
- `tests/test_alert_worker.py` — `build_alert_payload` (JOIN + fallback `get_scan`),
  ciclo em 1 batch, split em 3 batches (120 alvos), cota mensal cheia (0 envios) e
  cap de fetch (para em 10), falha de batch (loga `failed`, não marca `alerted`), sem
  mailer.
- `tests/test_rescan_worker.py` — ciclo reescaneia e depois manda evoluções em batch,
  adia quando a cota mensal está cheia, e faz flush de pendências antigas.
- **Suite completa: 160 passed, 1 skipped** (o skip é o scan online opt-in).

## Capacidade

`ALERT_BATCH_SIZE`×`ALERT_BATCHES_PER_CYCLE` = 200/ciclo × 2 ciclos/h × ~12h úteis ≈
**4.800 alertas/dia** de capacidade. O backlog de ~350 drena em **~2 ciclos
(< 40 min)**. Para drenar 350 num único ciclo, subir `ALERT_BATCHES_PER_CYCLE` p/ 7
(ou `ALERT_BATCH_SIZE` p/ 100) no `.env` da VM.

## Deploy — passos manuais na VM

1. Editar `/opt/klarim/.env`: remover as antigas e (opcionalmente) fixar as novas
   (têm default no código):
   ```bash
   sed -i '/MAX_ALERTS_PER_HOUR/d;/MAX_ALERTS_PER_DAY/d;/MAX_ALERTS_PER_CYCLE/d' /opt/klarim/.env
   ```
2. Confirmar `RESEND_API_KEY` (Pro) e `RESEND_FROM` (domínio `klarim.net` verificado).
3. `sudo bash /opt/klarim/deploy/deploy.sh` (o CI também faz no push para `main`).

## Regra inviolável (atualizada)

A **cota mensal** (`ALERT_MONTHLY_LIMIT`) passa a proteger a reputação do domínio e o
custo do plano — nunca remover o teto mensal nem estourar os 50k/mês do Resend Pro;
manter sempre a reserva para e-mails transacionais.
