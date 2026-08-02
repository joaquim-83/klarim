# KL-137 — Simplificação radical do pipeline de e-mail

**Data:** 2026-08-02

O pipeline de alertas consumiu 10 cards (KL-108..KL-136) e ficou mais complexo a cada
intervenção (gates de score, trust-downgrade, catch_all condicional, reverificação por
source, priorização de subset) — e o resultado PIOROU (bounce oscilando, volume 4-400/dia,
e-mails sem link geravam ~7 visitas/semana). Este card **remove a complexidade acumulada**
e adiciona um link. A regra de envio passou de ~50 linhas de condicionais para **2 regras**;
o score deixou de FILTRAR e passou só a ORDENAR.

---

## Parte 1 — Link no e-mail (mantendo text/plain)

Decisão de 02/08 registrada no card: **NÃO migrar para HTML, NÃO multipart, NÃO logo** — só
adicionar UM link. As 3 variantes cold e o `profile_view` voltam a ter link (revertendo o
"sem links" do KL-91):

- **Cold (`notifier/cold_alert.py`):** `report_link(domain, "alerta")` +
  `_report_link_block` → `Veja o relatório completo do seu site:\n
  https://klarim.net/site/{domain}?utm_source=alerta&utm_medium=email`, inserido depois da
  mensagem do score, antes da assinatura, nas 3 variantes (`_variant1/2/3`).
- **Profile view (`notifier/email_client.py::build_profile_view_text`):**
  `...?utm_source=profile_view&utm_medium=email`.
- **UTM:** só `utm_source` + `utm_medium` (sem `utm_campaign`). Continua text/plain (sem HTML).

## Parte 2 — `is_safe_to_send` em 2 regras

`notifier/email_verifier.py`:

```python
SENDABLE_STATUSES = frozenset({"safe", "valid", "role"})

def is_safe_to_send(result, lead_score=0):
    status = result.status if isinstance(result, VerifyResult) else str(result)
    return status in SENDABLE_STATUSES
```

`safe`/`valid`/`role` → envia (0% bounce comprovado via Power); **tudo o resto** → não.
`lead_score` fica na assinatura por compatibilidade mas é **ignorado** na decisão. Removidos
`_unsafe_score_gate()`/`_catch_all_gate()` + constantes `_UNSAFE_SCORE_GATE`/`_CATCH_ALL_SCORE_GATE`.

## Parte 3 — Lead scoring só ORDENA, não filtra

- **`discovery/alert_worker.py::_apply_alert_scoring`** reescrito: grava o `alert_quality_score`
  de TODOS e retorna `(targets, avg)` — **sem** filtrar por threshold. Removidos
  `skipped_low_quality`, `ALERT_SCORE_THRESHOLD`/`self.alert_score_threshold` e o log de skip.
  O `run_cycle` continua ordenando por score DESC (maior primeiro); o excedente do send-cap vai
  para o próximo ciclo.
- **`discovery/alert_scoring.py`:** removidas as penalidades de deliverability (`catch_all` -10,
  `unknown` -5) — a deliverability é decidida binariamente pelo `is_safe_to_send`. Mantidos os
  fatores de QUALIDADE (corporate +10, action-zone +20, urgência +10), a penalidade de `role`
  (-5, `ALERT_ROLE_PENALTY`) e a de bounce-domínio (-40).

**`_verify_and_filter` reescrito** (regra binária): particiona frescos (`from_cache`) vs
não-verificados → verifica os não-verificados via Power até o cap (`EMAIL_VERIFY_MAX_PER_CYCLE`;
excedente = `deferred`) → aplica `is_safe_to_send` a todos → `sendable` vs `blocked`. Stats do
ciclo: `verified/from_cache/sendable/blocked/deferred/errors` (+`reoon_exhausted` p/ o
`/system/status`). Sem trust-downgrade, sem aposentar unknown, sem breakdown de blocked_known.

## Parte 4 — Limpeza de código

**Removido:** `_unsafe_score_gate`/`_catch_all_gate` (+ `ALERT_UNSAFE_SCORE_GATE`/
`ALERT_CATCH_ALL_SCORE_GATE`), `store.trusted_recipient_domains` + trust-downgrade
(`ALERT_TRUST_DOMAIN_DOWNGRADE`), `ALERT_SCORE_THRESHOLD` + filtro, o marcar
`unknown`→`sem_contato` em ciclo, os counters `skipped_low_quality`/`skipped_gate`/
`blocked_known`/`trust_downgraded`/`retired_unknown`, o helper morto `_mask_email` e o import
`Counter` do `alert_worker`.

**Mantido (NÃO removido):** circuit breaker hard-bounce (KL-108), verificação Reoon Power
(decisão binária), blocklist (invalid/disabled/disposable/spamtrap), List-Unsubscribe (KL-102),
rotação de senders (KL-91), `email_verify_status`/`email_verify_source` + cache de verificação,
fail-safe de saldo Reoon (KL-136: saldo 0 → defere), lead scoring p/ ORDENAÇÃO,
`ALERT_ROLE_PENALTY`, o SQL `_ALERT_ELIGIBLE_WHERE` (já exclui unknown+power do fetch) e
`retire_unknown_power_targets` (limpeza retroativa via script one-off).

**`docs/DEPLOY.md`:** removidas as 3 env vars + nota de pós-deploy para apagá-las do `.env` da VM.

## Testes

- `test_kl91_cold_alert`/`test_kl101_profile_view`/`test_alert_plain_text`/
  `test_alert_sender_migration`: link presente + continua text/plain (sem HTML).
- `test_kl110_email_verifier`: `is_safe_to_send` binária + `SENDABLE_STATUSES`; removidos os
  testes de gate; penalidades catch_all/unknown → ausentes.
- `test_kl127`/`129`/`130`/`136`: reescritos para a regra binária e os novos stats
  (`sendable`/`blocked`); removidos os testes de trust-downgrade e de aposentar-unknown.
- `test_kl85_scoring`/`test_alert_worker`: `_apply_alert_scoring` retorna `(targets, avg)` e
  não filtra; `run_cycle` envia todos, ordenados por score.
- **1948 pytest passed, 1 skipped** (suite completa).

## Deploy / pós-deploy

- Commit + push → CI (test + deploy). Após o deploy, na VM: apagar do `.env`
  `ALERT_UNSAFE_SCORE_GATE`, `ALERT_CATCH_ALL_SCORE_GATE`, `ALERT_TRUST_DOMAIN_DOWNGRADE` (são
  ignoradas, mas confundem) e revisar o override `ALERT_SENDER_MAX_BOUNCE_RATE`.
- **Monitorar pós-deploy:** volume de envio (deve subir — o filtro por threshold sumiu, mas só
  `safe`/`valid`/`role` enviam) e o bounce (deve cair/estabilizar — catch_all/unknown/inbox_full
  não enviam mais). O CTR dos alertas deve subir (agora há link).
- Sem flush Redis (nenhuma mudança de `scoring.py`/check de scanner).

## Nota de segurança

O link aponta para o **perfil público** (`/site/{domain}`, sem PII). Nenhum endpoint novo; a
regra binária é mais conservadora que a anterior (envia MENOS status). List-Unsubscribe e opt-out
por resposta inalterados.
