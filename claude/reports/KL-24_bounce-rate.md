# KL-24 — Reduzir bounce rate: validação MX + marcar bounces + webhook Resend

- **Card Jira:** KL-24
- **Data:** 2026-07-09
- **Executor:** Claude CLI (Opus 4.8)
- **Depende de:** KL-23 (batch sending), KL-19 (filtro de e-mail)
- **Commit:** `fix(KL-24): reduce bounce rate with MX validation, bounce webhook, and auto-pause`

---

## Objetivo (urgente)

Bounce rate em **10,67%** (37 bounces permanentes de 506 envios). Limite seguro
< 4%; acima de 10% o Resend pode **suspender a conta** e provedores (Gmail/Outlook)
podem **blacklistar `klarim.net`**. Complaint rate 0% ⇒ o conteúdo não é o problema,
são **endereços inválidos**. Quatro camadas de defesa, da captação ao pós-envio.

## O que mudou

### Parte 1 — Validação de MX na captação (`discovery/contact.py`)

- `_mx_status(domain)` (via **dnspython**) devolve tri-estado **`ok | no_mx |
  unknown`**: `no_mx` = NXDOMAIN/NoAnswer (o e-mail vai bouncar); `unknown` =
  timeout/sem lib (**fail-open** — não descarta por engano). `@lru_cache(10000)`
  em `_mx_status_cached` evita DNS repetido para o mesmo domínio.
- `extract_email(html, url, validate_mx=True)` agora escolhe o **primeiro candidato
  cujo domínio tem MX** (DNS roda **fora do event loop** via `asyncio.to_thread`).
  `_is_valid_email` continua só sintático (KL-19); a checagem de MX é a nova etapa.
- `dnspython>=2.4,<3.0` adicionado ao `requirements.txt`.

### Parte 2 — Backfill dos bounces existentes (`POST /admin/process-bounces`, JWT)

Checa no Resend (`KlarimMailer.get_email_event` → `GET /emails/{id}`, `last_event`)
o status de cada alerta enviado (distintos, concorrência limitada a **8**). Bounce
→ `discard_target_by_email` + `block_email` + `alert_log.status='bounced'`.
Idempotente (só reprocessa o que ainda está `sent`). **Rodar uma vez na VM após o
deploy** para marcar os 37 bounces (Parte 8).

### Parte 3 — Webhook do Resend (`POST /webhooks/resend`, público)

- Valida a assinatura **Svix** (`verify_resend_signature` + `RESEND_WEBHOOK_SECRET`;
  **401** se inválida; sem secret configurado, aceita — para funcionar antes de o
  operador criar o webhook no painel).
- `email.bounced` **permanente** → descarta o alvo + blocklist + marca no log;
  **transitório** (soft/temporary/delivery_delayed) é **ignorado** (pode ser caixa
  cheia). `email.complained` → `mark_unsubscribed` + blocklist (spam é mais grave).

### Parte 4 — Validação pré-envio no Alert Worker

`_validate_batch` roda antes de montar o batch: remove (e marca `descartado`) alvos
na **blocklist** (`is_email_blocked`) e com **domínio sem MX** (`ALERT_VALIDATE_MX`).
Os removidos entram no stat `invalid` do ciclo.

### Parte 5 — Métricas de bounce no dashboard

- `GET /system/email-health` → `total_sent`, `delivered`, `bounced_permanent`,
  `bounced_transient` (não rastreado — bounce transitório não descarta),
  `complained`, `bounce_rate`, `bounce_status` (`ok`/`warning`/`critical`),
  `blocklist_size`.
- Tela **Sistema** ganhou o card **"Saúde de e-mail (bounce)"** com semáforo de
  risco (🟢 <2% · 🟡 2–4% · 🔴 >4%) + bounces/complaints/blocklist.

### Parte 6 — Pausa automática por bounce rate

`_check_bounce_health` roda no início de cada ciclo do Alert Worker: se o bounce
rate passar de `ALERT_MAX_BOUNCE_RATE` (**8%**) com amostra ≥
`ALERT_BOUNCE_MIN_SAMPLE` (**20**), **pausa** os envios e loga
`[alert] ⚠️ envios pausados — bounce rate X% (limite 8%)`. O ciclo retorna
`{paused: true}` sem enviar. Retomada: corrigir bounces (ou subir o threshold).

### Blocklist (`email_blocklist`)

Tabela nova (no `ensure_schema`): `email` (unique), `domain`, `reason`, `created_at`.
Bloqueio **por e-mail** (guarda o domínio para análise, mas **não** descarta
endereços irmãos válidos do mesmo domínio). Métodos no store: `block_email`,
`is_email_blocked`, `blocklist_size`, `discard_target_by_email`,
`mark_alert_status_by_email_id`, `get_sent_alerts_for_bounce_check`, `email_health`.

## Testes

- `tests/test_discovery.py` — tri-estado do MX, `extract_email` rejeita domínio sem
  MX / pula o sem-MX e pega o próximo, e o **cache** evita lookup repetido.
- `tests/test_bounce.py` (novo) — assinatura Svix (ok/adulterada/múltiplas versões),
  `get_email_event` (parse + erro), `_bounce_status`, e o webhook (bounce permanente
  descarta, transitório mantém, complaint descadastra, assinatura ruim → 401,
  endpoint público).
- `tests/test_alert_worker.py` — `_validate_batch` (blocklist descarta), ciclo valida
  antes de enviar, **pausa** com bounce rate crítico e **não pausa** abaixo da
  amostra mínima.
- **Suite completa: 178 passed, 1 skipped** (o skip é o scan online opt-in).

## Parte 8 — Ação imediata na VM (após o deploy)

```bash
# marca os 37 bounces existentes como descartados + blocklist
curl -s -X POST https://painel.klarim.net/api/admin/process-bounces \
  -H "Authorization: Bearer <JWT do painel>"
```

E configurar no `.env` da VM (+ redeploy) o segredo do webhook do Resend:
```env
RESEND_WEBHOOK_SECRET=whsec_...   # criado em resend.com/webhooks
```
Webhook no painel Resend: `https://klarim.net/api/webhooks/resend`, eventos
`email.bounced` + `email.complained`.

## Notas de design

- **Fail-open no MX** (`unknown` passa): um timeout de DNS não deve descartar um
  alvo possivelmente válido. Só o `no_mx` definitivo rejeita.
- **Blocklist por e-mail, não por domínio:** evita descartar `vendas@empresa.com.br`
  só porque `contato@empresa.com.br` bouncou. O domínio fica guardado para análise.
- **`bounced_transient` não é rastreado:** só marcamos/rastreamos bounces
  permanentes (os que justificam descarte). Transitórios são ignorados de propósito.
