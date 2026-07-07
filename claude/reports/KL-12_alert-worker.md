# KL-12 — Alert Worker (disparo automático + throttle + unsubscribe) + calibração do semáforo

- **Card Jira:** KL-12
- **Data:** 2026-07-07
- **Executor:** Claude CLI (Opus 4.8)
- **Depende de:** KL-11 (Discovery Worker / `targets` / `scans`), KL-8 (e-mail Resend)
- **Commit:** `feat(KL-12): add Alert Worker with throttle, unsubscribe, and semaphore calibration`

---

## Objetivo

Fechar o funil de aquisição: os alvos que o Discovery Worker escaneou e que têm
falhas passam a **receber automaticamente** o alerta gratuito por e-mail (o anzol).
Antes disso, **recalibrar o semáforo** para que "verde" só apareça quando o site
realmente está bem — nota alta com falha grave não é verde.

## Parte 0 — Calibração do semáforo (`scanner/scoring.py`)

Regra nova:

- 🟢 **Verde** — score **≥ 90 E zero FALHAS** de severidade **Alta/Crítica**.
- 🟡 **Amarelo** — score ≥ 50 (ou ≥ 90 mas com FALHA Alta/Crítica).
- 🔴 **Vermelho** — score < 50.

`_semaphore(score, has_high_fail)` recebe o flag `has_high_fail` calculado no
`compute_score` a partir de `fails_by_severity` (Crítica ou Alta > 0). Motivo de
negócio: um site 91/100 com 2 falhas Altas não pode exibir "tudo certo".

- **Impacto medido:** `verdegreen` (86, 2 FALHAS Altas) → **amarelo** (já era);
  `klarim.net` (100, 0 falhas) → **verde**. Caso-limite validado: **91 + 2 FALHAS
  Altas → amarelo** (score alto, mas não engana).
- **Ripple:** `notifier.semaphore_from_score` (usado só na entrega do PDF, onde não
  há detalhe de severidade) subiu o corte de verde de 80 → 90 para coerência.
  Frontend/PDF/e-mail de alerta usam o **semáforo autoritativo do scan** (que já
  considera a falha alta), então não têm texto de threshold a mudar.

## Parte 1 — Alert Worker (`discovery/alert_worker.py`)

`AlertWorker.run_cycle()`:

1. Busca elegíveis: `store.get_eligible_targets_for_alert()` — `status='scanned'`,
   `contact_email` não nulo, `fail_count > 0`, `last_alert_at` nulo ou > 30 dias.
2. **Throttle global:** lê `count_alerts_last_hours(1)` e `(24)` no `alert_log`; se
   já bateu `MAX_ALERTS_PER_HOUR` (10) ou `MAX_ALERTS_PER_DAY` (50), não envia.
3. Por alvo (enquanto houver orçamento): monta contagem por severidade a partir do
   `checks_json` do scan, envia via `KlarimMailer.send_alert` (com link de
   descadastro), marca `status='alerted'` + `last_alert_at`, registra em `alert_log`,
   e **pausa 5s** (protege a reputação do domínio no Resend). Excedentes viram
   `throttled`.

`start()` = loop a cada `ALERT_INTERVAL_HOURS` (1h). Falha em um alvo não derruba o
ciclo (registra `status='failed'` no log e segue).

A função `send_alert_for_target(store, mailer, target)` é compartilhada entre o
worker e o endpoint de disparo manual.

## Parte 2 — Banco (`discovery/store.py`)

Tabela **`alert_log`** (`target_id` FK, `contact_email`, `score`, `semaphore`,
`fail_count`, `email_id`, `status`, `sent_at` + índices por target e por data),
criada no `ensure_schema`. Novos métodos: `get_eligible_targets_for_alert`,
`mark_target_alerted` (status + `last_alert_at` + `alert_count`), `mark_unsubscribed`,
`log_alert`, `count_alerts_last_hours`, `list_alerts`, `alert_stats`.

## Parte 3 — Descadastro (unsubscribe)

- `notifier.unsubscribe_token(email, secret)` — HMAC-SHA256 do e-mail (32 chars);
  `build_unsubscribe_link` monta `…/api/unsubscribe?email&token`.
- Rodapé do `alert.html` mostra o link real quando há token (fallback `mailto:`).
- `GET /api/unsubscribe` valida o token em tempo constante (`hmac.compare_digest`);
  válido → `mark_unsubscribed` (`status='unsubscribed'`) + página HTML de sucesso;
  inválido → HTTP 400 + página de erro.
- Segredo `UNSUBSCRIBE_SECRET` gerado **na VM** (`openssl rand -hex 32`) — nunca no
  repositório.

## Parte 4 — Mesmo container do Discovery

`discovery/worker.py` `main()` agora roda
`asyncio.gather(DiscoveryWorker().start(), AlertWorker().start())` — os dois loops
convivem no serviço `discovery` do compose. Sem novo container.

## Parte 5 — API

- `GET /api/alerts` — histórico (`alert_log`), filtro por `target_id`.
- `GET /api/alerts/stats` — hoje / semana / mês / total.
- `POST /api/targets/{id}/alert` — disparo **manual** (ignora throttle e janela de
  30 dias); útil para testes e casos pontuais.
- `GET /api/unsubscribe` — descadastro.

## Variáveis de ambiente (`.env`)

`MAX_ALERTS_PER_HOUR=10`, `MAX_ALERTS_PER_DAY=50`, `ALERT_INTERVAL_HOURS=1`,
`UNSUBSCRIBE_SECRET=` (gerado na VM). Adicionadas ao `.env.example` (sem valores
sensíveis).

## Validação

- **Testes:** `tests/test_alert_worker.py` (10 casos): token HMAC (round-trip,
  normalização case/trim, rejeição de adulteração), `build_unsubscribe_link`,
  contagem por severidade, `send_alert_for_target` (marca + loga; exige e-mail),
  `run_cycle` (envia elegíveis, throttle global, teto por alvo, sem mailer).
  `tests/test_checks.py` (calibração do semáforo, falha alta bloqueia verde) e
  `tests/test_notifier.py` (corte 90). **Suíte total: 61 passed, 1 skipped.**
- **Endpoint offline:** `/api/unsubscribe` com token válido chega ao DB (HMAC ok);
  token inválido → 400 sem tocar no banco. Rotas registradas: `/alerts`,
  `/alerts/stats`, `/targets/{id}/alert`, `/unsubscribe`.
- **Produção (VM):** validado pós-deploy — ver seção abaixo.

## Validação em produção (pós-deploy) — confirmada

CI/CD verde (test + deploy). Após o deploy, o cache Redis (KL-9) ainda servia o
semáforo antigo do `verdegreen` (calculado sob a regra pré-KL-12); as chaves
`scan:*` foram limpas para forçar o recálculo. Resultados:

- [x] **Semáforo:** `verdegreen` (86, 2 FALHAS Altas) → **amarelo**; `klarim.net`
      (100, 0 falhas) → **verde**.
- [x] **Env + container:** `UNSUBSCRIBE_SECRET` (64 hex) + `MAX_ALERTS_*` +
      `ALERT_INTERVAL_HOURS` na `.env` da VM; container `discovery` no ar com **os
      dois loops** (`[alert] iniciado` + `[discovery] iniciado` → `asyncio.gather`).
- [x] **Disparo:** `POST /api/targets/1/alert` (alvo de teste apontado para o
      e-mail do próprio operador) → `sent:true` + `email_id`; `GET /api/alerts` e
      `/alerts/stats` refletiram (`today:1`); alvo → `status='alerted'`,
      `alert_count=1`, `last_alert_at` preenchido.
- [x] **Skip no re-disparo:** após alertar, `get_eligible_targets_for_alert()` →
      **0** (excluído por status e pela janela de 30 dias).
- [x] **Unsubscribe:** token **válido** → HTTP 200 + página de sucesso, alvo →
      `status='unsubscribed'`; token **inválido** → HTTP 400.
- [x] **Throttle:** coberto por teste unitário (teto por hora corta o excedente);
      não forçado em produção para não enviar 10 e-mails reais.

Artefatos de validação limpos ao final (e-mail do alvo → NULL, `status='scanned'`,
`alert_log` zerado) — o `verdegreen` é um negócio real e não deve reter o e-mail
do operador nem um alerta de teste.

## Critérios de aceite

- [x] Semáforo recalibrado (verde ≥ 90 **e** zero FALHA Alta/Crítica).
- [x] `alert_worker.py` com `AlertWorker` (elegibilidade + throttle + envio + log).
- [x] Tabela `alert_log` + métodos no store.
- [x] Unsubscribe HMAC (token, link no rodapé, endpoint, segredo na VM).
- [x] Alert Worker no mesmo container do Discovery (`asyncio.gather`).
- [x] API `/alerts`, `/alerts/stats`, `/targets/{id}/alert`, `/unsubscribe`.
- [x] Env vars documentadas em `.env.example`.
- [x] Testes (61 passed, 1 skipped).
- [x] Documentação (`claude.md` §16, `README.md`).
- [x] Relatório em PT-BR.
- [x] Deploy + validação em produção + commit/push.

## Follow-ups

- Dívida do KL-3 ainda de pé: stores conectam por `POSTGRES_*` (não `DATABASE_URL`,
  cuja senha base64 tem `/`).
- Descadastro por domínio de contato compartilhado (mesmo dono, vários alvos) já é
  coberto (`mark_unsubscribed` casa por `contact_email`).
- Métrica de conversão alerta → pagamento (fechar o loop do funil) fica para um card
  futuro.
