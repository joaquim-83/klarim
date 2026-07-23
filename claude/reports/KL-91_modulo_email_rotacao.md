# KL-91 — Módulo de e-mail com rotação de subdomínios

**Card:** KL-91 (High) · **Status:** ✅ implementado e validado localmente · **Deploy:**
pendente de validação do dono (regra do card: "Deploy após validação").

---

## 1. Problema

Os alertas de segurança da Klarim caíam no spam. Três causas somadas:

1. **Linguagem de urgência + links trackáveis** no corpo (parecia e-mail de marketing/scam).
2. **Domínio único** de envio proativo (`alerta@klarim.net`), compartilhado com o transacional
   — todo bounce/complaint do cold degradava a reputação usada pela confirmação de conta.
3. Volume disparado em **batch** (50 de uma vez), sem cadência.

## 2. Solução (o que mudou)

Um **módulo de e-mail cold** dedicado, com 3 frentes:

| Frente | Antes | Agora (KL-91) |
|---|---|---|
| Conteúdo | HTML/texto com CTA + link HMAC (KL-82 S3) | **3 variantes de texto puro SEM links** (informativa/setorial/educativa) |
| Opt-out | link `List-Unsubscribe` https (one-click) | **por resposta** ("responda com remover") + header `List-Unsubscribe` mailto |
| Remetente | `alerta@klarim.net` (único) | **rotação** `scan@alertas.klarim.net` + `scan@aviso.klarim.net` |
| Envio | batch 50 (Resend Batch API) | **individual**, cooldown **30-60s** entre e-mails |
| Cota | `ALERT_DAILY_LIMIT` global | + **limite diário POR remetente** (`ALERT_SENDER_DAILY_LIMIT`, warmup) |
| Segurança | anti-bounce global | + **circuit breaker por remetente** (bounce >5% → pausa) |
| Observabilidade | `get_email_health` agregado | + **`by_domain`** + `email_log.template_variant` |

O `klarim.net` fica **exclusivo do transacional** (isolamento de reputação): `load_senders`
descarta qualquer remetente cujo domínio seja `klarim.net` cru.

## 3. Arquivos

### Novo — `notifier/cold_alert.py` (puro/testável, sem I/O)
- **Templates** `build_cold_email(variant, domain, score, sector_label, sector_avg)` → `(subject, text)`.
  As 3 variantes do card, em **PT-BR com acentuação correta** (decisão: texto sem acento parece
  *mais* spam/scam, não menos — a acentuação correta não é sinal de spam). Sem links, sem emoji,
  sem urgência, sem CTA, sem preço. A variante 2 (setorial) cai para a 1 se faltar setor/média.
- `choose_variant(has_sector_data)` — com setor → 1/2/3; sem → 1/3.
- `load_senders(env)` — CSV `ALERT_SENDER_EMAILS` → defaults; **guard** de isolamento + dedup.
- `pick_sender(senders, counts, daily_limit)` — round-robin pelo remetente de **menor volume no dia**;
  `None` quando todos bateram o limite/pausaram.
- `flag_high_bounce(senders, by_domain, max_rate, min_sample)` — circuit breaker (muta `status`).
- `list_unsubscribe_reply_header()` — `{"List-Unsubscribe": "<mailto:scan@klarim.net?subject=remover>"}`.
  **Sem** `List-Unsubscribe-Post: One-Click` (o One-Click do RFC 8058 exige URL https; combiná-lo com
  mailto é malformado e **pioraria** a entrega — o oposto do objetivo do card).

### `notifier/email_client.py`
- `send_cold_alert(...)` — envia texto puro, `from` rotacionado, header opt-out; loga
  `template_variant` + `from_domain`; **proativo** (respeita blocklist).
- `_send(..., template_variant=)` propaga a variante ao `email_log`.
- **`DRY_RUN_EMAIL`** — `_send_sync` curto-circuita (retorna `dryrun_*`, não fala com o Resend) mas
  o `email_log` é gravado normalmente (dev exercita rotação/variante/limites sem enviar).

### `discovery/alert_worker.py`
- `run_cycle` reescrito de **batch → envio individual** com rotação + cooldown + limite por remetente
  + circuit breaker + **deadline de ciclo** (não estoura o intervalo do worker). Mantidos todos os
  guards existentes (worker_control, `STOP_ALERTS`, bounce global, cota mensal, `ALERT_DAILY_LIMIT`
  global, validação de e-mail KL-24, lead scoring KL-85). Um envio ruim (422) descarta o alvo e
  segue; erro de infra loga falha e **aborta o ciclo** (o restante vai para o próximo).
- `build_alert_payload` — agora traz `sector_label` + `sector_avg` (variante 2); largou risco/link.
- `send_alert_for_target` (disparo manual `/targets/{id}/alert`, MCP `send_alert_to_target`) — mesmo
  formato cold (1º remetente).
- Config nova em `__init__`/`_reload_settings`: `sender_daily_limit`, `send_interval_min/max`,
  `sender_max_bounce_rate`. Removido `_send_with_split` (era específico do batch).

### `discovery/store.py`
- `email_log.template_variant SMALLINT` (idempotente, `ADD COLUMN IF NOT EXISTS`).
- `log_email(..., template_variant=)`.
- `count_alerts_sent_today_by_domain()` — base da rotação + limite por remetente (reseta à meia-noite).
- `email_health_by_domain()` — sent/delivered/bounced/complained/bounce_rate por `from_domain`.

### `api/main.py`
- `GET /system/email-health` → campo **`by_domain`** (com `bounce_status` por remetente), fail-open.
  Flui automaticamente para o MCP `get_email_health`.
- `_CONFIG_PARAMS` + `api_config`: `ALERT_SENDER_DAILY_LIMIT` (editável no painel, 0-5000).

### `.env.dev`
- `ALERT_SENDER_EMAILS`, `ALERT_SENDER_DAILY_LIMIT=100`, `ALERT_SEND_INTERVAL_MIN/MAX=0` (sem espera
  em dev). Nota de como exercitar a rotação com `DRY_RUN_EMAIL=true` + chave fake.

## 4. Cadência de ramp-up (operacional, sem redeploy)

O `ALERT_SENDER_DAILY_LIMIT` é editável no painel (admin_settings > .env). Plano do card:

```
Dias 1-3:   100/remetente  = 200/dia
Dias 4-7:   250/remetente  = 500/dia
Dias 8-14:  500/remetente  = 1.000/dia
Dia 15+:    750/remetente  = 1.500/dia
```

O `ALERT_DAILY_LIMIT` global (default 5000) continua como teto de segurança acima disso.

## 5. Circuit breaker

A cada ciclo, `email_health_by_domain` alimenta `flag_high_bounce`: um remetente com bounce > 5%
(amostra ≥ 20) é **pausado** naquele ciclo (log `CRITICAL`), e a rotação usa só o outro. Se ambos
pausarem/esgotarem, o ciclo é pulado (`sender_limit_reached`/`senders_paused` nas stats).

## 6. Segurança (revisão obrigatória)

- **Remetentes fora do código** — só e-mails/domínios em `ALERT_SENDER_EMAILS` (env); nenhuma
  credencial hardcoded. Os subdomínios estão verificados no Resend (DKIM/SPF/DMARC — feito pelo dono).
- **Isolamento** — `load_senders` recusa `klarim.net` cru → o cold nunca envia pelo domínio transacional.
- **Sem novos endpoints/inputs** — o `by_domain` é leitura admin (prefixo `/system` → não exposto ao
  público; o `/admin`/JWT continua). Nenhum dado sensível novo (`contact_email` nunca aparece).
- **Fail-open** — falha em `email_health_by_domain`/`count_*_by_domain` não pausa o worker nem derruba
  o painel (try/except, comportamento conservador: ninguém é pausado por falha de infra).
- **Blocklist + validação** — o caminho cold passa por `_validate_batch` (blocklist, MX, e-mail sujo)
  e por `send_cold_alert` → `_send` (blocklist de novo). Opt-out por resposta respeitado via blocklist.

## 7. Testes

- **Novo `tests/test_kl91_cold_alert.py` (+24):** variantes sem links/plain-text/opt-out, variante 2
  com média + fallback, `choose_variant`, `load_senders` (defaults/env/isolamento/dedup), `pick_sender`
  (round-robin/esgotado/pausado), `flag_high_bounce` (threshold/amostra), header mailto, `send_cold_alert`
  (texto puro + reply-to + header + log com variante/domínio + blocklist), `DRY_RUN_EMAIL`,
  `email-health.by_domain` (+ fail-open), config editável.
- **`tests/test_alert_worker.py` reescrito** para envio individual: rotação entre 2 remetentes,
  limite por remetente, esgotamento, circuit breaker por bounce, isolamento de e-mail ruim (422),
  abort em erro de infra. Removidos os 2 testes de `_send_with_split` (batch).
- **`tests/test_alert_sender_migration.py`** — `_prep_worker` ganhou os atributos novos.
- **Full suite:** `1588 passed, 1 skipped` (+33). Frontend inalterado (98 node --test).

## 8. Validação manual

Feito localmente (offline): rotação, variantes, fallback da variante 2, guard de isolamento,
`pick_sender`, header opt-out, `by_domain`. **Checklist do card** (a fazer em dev/prod com o dono):

1. E-mail de teste de cada remetente (`scan@alertas.` / `scan@aviso.`) chega na inbox.
2. Texto puro (sem HTML) · 3. Sem links clicáveis · 4. Header `List-Unsubscribe` presente.
5. Worker rotaciona entre os 2 · 6. Para ao bater o limite diário · 7. Cooldown 30-60s.
8. `email_log` grava `from_domain` + `template_variant` · 9. Transacional segue de `klarim@klarim.net`.

Em dev: `DRY_RUN_EMAIL=true` + `RESEND_API_KEY=re_dev` (fake) exercita 5-8 sem enviar.

## 9. Fora de escopo / pendências

- **Opt-out por resposta = manual (Opção A do card):** as respostas "remover" caem no inbox
  `scan@klarim.net`; o operador põe na blocklist. A automação por IMAP (Opção B) fica para depois.
- **Threshold do lead scoring (KL-85)** — **não** mexido (gerido à parte via env, conforme o card).
- **`profile_view` e `bulletin`** seguem em `alerta@klarim.net` (`_proactive_from`) — o KL-91 cobriu
  só o alerta. Podem migrar para a rotação num card futuro se necessário.
- **Builders antigos** (`build_alert_text`, alert-access HMAC do KL-82 Slice 3) ficam no código; o
  ciclo automático não os usa. ⚠️ Trade-off consciente: o alerta cold perde o link de acesso direto
  ao resultado (Fluxo 2/KL-82 S3 e Fluxo C/KL-99) em troca de entrega na inbox — um e-mail no spam
  tem 0% de conversão de qualquer forma. Revertível.

## 10. Deploy (quando o dono aprovar)

1. Confirmar `alertas.klarim.net` e `aviso.klarim.net` verificados no Resend (feito).
2. `.env` da VM: `ALERT_SENDER_EMAILS=scan@alertas.klarim.net,scan@aviso.klarim.net`,
   `ALERT_SENDER_DAILY_LIMIT=100` (warmup). Os intervalos (30/60s) e o breaker (5%) usam os defaults.
3. Push → CI (test + `nginx -t` + deploy). **Recriar o container** `discovery` (lê env no boot) e `api`.
4. **Sem flush Redis** necessário (scoring/checks não mudaram). Acompanhar `get_email_health.by_domain`
   nos primeiros dias e subir o `ALERT_SENDER_DAILY_LIMIT` pela cadência do §4.
