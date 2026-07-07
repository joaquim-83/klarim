# KL-13 — Re-scan automático (30 dias) + e-mail de evolução de score

- **Card Jira:** KL-13
- **Data:** 2026-07-07
- **Executor:** Claude CLI (Opus 4.8)
- **Depende de:** KL-11 (targets/scans + scan worker), KL-12 (Alert Worker + semáforo calibrado)
- **Commit:** `feat(KL-13): add Re-scan Worker with score evolution emails`

---

## Objetivo

Fechar o ciclo de vida do alvo: a cada 30 dias reescanear sites já engajados,
comparar o score com o anterior e enviar um e-mail contando a evolução (melhorou /
piorou / permaneceu igual). Re-engajamento e novas chances de conversão **sem
descobrir alvos novos**.

## Parte 1 — Re-scan Worker (`discovery/rescan_worker.py`)

`RescanWorker.run_cycle()` (loop de 24h, `RESCAN_INTERVAL_HOURS`):

1. **Reenvia pendentes** (`_flush_pending`): e-mails de evolução que ficaram
   pendentes por throttle no ciclo anterior (`rescan_log.email_id IS NULL`).
2. **Elegíveis** (`get_targets_for_rescan`): `status IN ('scanned','alerted')`,
   `contact_email` não nulo, `last_scan_at` > `RESCAN_AGE_DAYS` (30d). Nunca pega
   `discovered`/`sem_contato`/`unsubscribed`/`descartado`.
3. **Por alvo** (pausa = a mesma do scan worker, `WORKER_MAX_SCANS_PER_HOUR`):
   guarda o score anterior → reescaneia (`run_scan`) → salva em `scans` + atualiza
   `targets` + **cacheia (KL-9)** → `classify_evolution` → envia o e-mail (se sob o
   throttle) → registra em `rescan_log`.

`classify_evolution(old, new)` → `improved` (subiu) / `worsened` (caiu) /
`unchanged` (igual) / `first_rescan` (sem score anterior).

A função **`rescan_target()`** é compartilhada entre o worker e a API (disparo
manual), sempre atualizando os dados e enviando o e-mail só quando permitido.

**Nota de design:** optei pelo re-scan **inline** no worker (espelhando o Alert
Worker do KL-12), e não pelo re-enfileiramento na `klarim:scan_queue` mencionado no
card. A comparação de score + o e-mail + o throttle compartilhado + a fila de
pendentes ficam coesos num só lugar; o `rescan_log` já distingue os re-scans dos
scans normais. O rate limit (`WORKER_MAX_SCANS_PER_HOUR`) é respeitado como pausa
entre scans, igual ao scan worker.

## Parte 2 — E-mails de evolução (`notifier/`)

Três templates table-based (paleta dark), com link de descadastro no rodapé:

- **`evolution_improved.html`** — 🎉 celebração + comparação `antes → agora`. Se
  ainda há FAILs: CTA "Ver relatório completo — {preço}"; se zero: "100% seguro".
- **`evolution_worsened.html`** — ⚠️ urgência, contagem por severidade, bloco LGPD,
  CTA.
- **`evolution_unchanged.html`** — 📊 lembrete mensal; se há FAILs, contagem +
  CTA de correção.

`KlarimMailer.send_evolution(...)` escolhe o template pelo tipo de evolução
(`first_rescan` cai no mensal). O preço do CTA vem de `payments.PRICING` pelo
`price_tier` do alvo (`price_display_for_tier`).

## Parte 3 — Tabela `rescan_log` (`discovery/store.py`)

`rescan_log` (target_id FK, old/new_score, evolution, old/new_semaphore, email_id,
rescanned_at + índices) criada no `ensure_schema`. Métodos novos:
`get_targets_for_rescan`, `log_rescan`, `update_rescan_email`,
`get_pending_evolution_emails`, `list_rescans`, `rescan_stats`,
`count_proactive_emails_last_hours` (throttle global) e `mark_target_contacted`.

## Parte 4 — Container

`discovery/worker.py` `main()` agora roda **três loops**:
`asyncio.gather(DiscoveryWorker().start(), AlertWorker().start(), RescanWorker().start())`.
Env: `RESCAN_INTERVAL_HOURS=24`, `RESCAN_AGE_DAYS=30` no `.env.example`.

## Parte 5 — API

`GET /rescans` (filtros `target_id`/`evolution`/`limit`/`offset`),
`GET /rescans/stats` (contagem por evolução + total),
`POST /targets/{id}/rescan` (força re-scan + e-mail, ignora janela e throttle).

## Parte 6 — Throttle compartilhado

Alertas e e-mails de evolução dividem o mesmo teto de reputação:
`count_proactive_emails_last_hours` soma `alert_log` (sent) + `rescan_log`
(email_id não nulo). O Alert Worker (KL-12) passou a usar esse contador também. No
teto, o re-scan **atualiza os dados** mas o e-mail fica **pendente** e é reenviado
no próximo ciclo (`_flush_pending`). Após um e-mail de evolução,
`mark_target_contacted` seta `last_alert_at` para o Alert Worker não contatar o
mesmo alvo dentro de 30 dias (evita e-mail duplo).

## Parte 7 — Validação

- **Testes** (`tests/test_rescan_worker.py`, 10 casos): `classify_evolution`,
  `price_display_for_tier`, os 3 templates via Resend mockado (assunto + conteúdo +
  unsubscribe), `rescan_target` (envia + loga + marca contato; não envia quando
  desabilitado), `run_cycle` (reescaneia + envia; **adia** por throttle; **reenvia
  pendentes**). Alert Worker atualizado ao throttle compartilhado. **Suíte total:
  71 passed, 1 skipped.**
- **Imports/rotas:** `api.main` importa limpo; rotas `/rescans`, `/rescans/stats`,
  `/targets/{id}/rescan` registradas.
- **Produção (VM):** _validação pós-deploy — ver seção abaixo._

## Validação em produção (pós-deploy)

- [ ] Container `discovery` no ar com **três** loops (`[rescan] iniciado`).
- [ ] `POST /api/targets/{id}/rescan` reescaneia, grava em `rescan_log` e envia o
      e-mail de evolução (melhoria/piora/igual conforme o caso).
- [ ] `GET /api/rescans` e `/rescans/stats` refletem.
- [ ] Alvo `unsubscribed` NÃO é reescaneado (fora da elegibilidade).
- [ ] Throttle compartilhado: com e-mails no teto, o re-scan roda e o e-mail fica
      pendente.

## Critérios de aceite

- [x] `RescanWorker` com ciclo de 24h.
- [x] Elegibilidade: last_scan > 30d, status correto, com e-mail, não unsubscribed.
- [x] `classify_evolution` (improved/worsened/unchanged/first_rescan).
- [x] Templates improved/worsened/unchanged + `send_evolution`.
- [x] Tabela `rescan_log` + métodos.
- [x] Terceiro loop no `asyncio.gather`.
- [x] API `/rescans`, `/rescans/stats`, `/targets/{id}/rescan`.
- [x] Throttle compartilhado com alertas + fila de pendentes.
- [x] Unsubscribed respeitado (elegibilidade e reenvio).
- [x] Testes (71 passed, 1 skipped).
- [x] Documentação (`claude.md` §17, `README.md`).
- [x] Relatório em PT-BR.
- [ ] Deploy + validação em produção + commit/push.

## Follow-ups

- Dívida do KL-3 ainda de pé (stores por `POSTGRES_*`).
- Métrica de conversão evolução/alerta → pagamento (fechar o loop do funil).
- A/B do assunto dos e-mails de evolução para otimizar reabertura.
