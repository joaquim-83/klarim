# KL-96 — Desativar alerta antigo + corrigir contadores

**Card:** KL-96 (URGENTE) · **Status:** ✅ (itens 1–2 já resolvidos pelo KL-91; 3–6 implementados)

---

## 0. Diagnóstico — a premissa urgente estava incorreta

O card assumia que o **alert worker antigo ainda enviava 785/dia** por `alerta@klarim.net` e
que o KL-91 não havia desativado o caminho velho, com **bounce 11,4%**. Os dados de produção
(email_log) contradizem isso:

| Alegação do card | Realidade (email_log) |
|---|---|
| "785/dia de alerta via `alerta@klarim.net`" | São **`profile_view`** (769/dia via `alerta@klarim.net`), **não alertas**. Bounce do profile_view = **0,7%** |
| "alert worker ainda usa o caminho antigo" | Pós-deploy KL-91 (hoje 19:03): **0 alertas** por `klarim.net`, **1** por `alertas.klarim.net`. Os 8 alertas de hoje pelo path antigo foram **antes** do deploy |
| "bounce 11,4%" | email_log all-time = **2,06%**. Últimos 7d: `alert` 7,3% (path ANTIGO, já desligado), `profile_view` 0,7% |
| "90 bounces fora da blocklist" | **0** endereços com bounce estão fora da blocklist — o webhook do Resend já os adiciona automaticamente |

**Confirmação do usuário:** os 785 foram enviados ANTES do deploy (ciclos da madrugada/manhã);
é o mesmo `run_cycle`, reescrito pelo KL-91 — não há dois serviços paralelos.

### §1 — verificação pedida (código pós-deploy)
`run_cycle` tem **uma única** chamada de envio: `mailer.send_cold_alert(...)`, cujo `from_address`
vem só de `cold_alert.pick_sender()` → `alertas.klarim.net`/`aviso.klarim.net`. `send_cold_alert`
**nunca** chama `_proactive_from()`. Grep confirma: `run_cycle` não tem `send_alert`/`send_alert_batch`/
`_alert_params`/`_proactive_from`. Os únicos usuários de `alerta@klarim.net` restantes são
`send_profile_view` e **dois endpoints admin manuais** (`/admin` scan-and-alert) — nunca o worker.
**→ §1 já está 100% resolvido pelo KL-91.** Nenhuma mudança de código necessária.

### §2 — blocklist dos bounces
`SELECT COUNT(DISTINCT to_email) FROM email_log WHERE status='bounced' AND to_email NOT IN
(SELECT email FROM email_blocklist)` = **0**. O webhook Svix do Resend já bloqueia todo hard bounce.
**→ §2 já está resolvido.** Nenhuma ação.

---

## 1. O que foi realmente implementado (itens 3–6)

A causa real das divergências de contadores: existiam **3 fontes** para "alertas enviados"
(`alert_log`, `email_log`, `count_proactive`) medindo coisas ligeiramente diferentes. O card §3
pede **fonte única = `email_log`**. Unifiquei tudo.

### §3 — contadores da página Alertas (fonte única = email_log)
- **`store.alert_stats()`** reescrito: contava de **`alert_log`** (rolling 24h) → agora conta de
  **`email_log`** `email_type IN ('alert','alert_score100')`, `status='sent'`, em **dia-calendário**
  (`>= date_trunc('day', NOW())` p/ hoje; `-7d`/`-30d` p/ semana/mês; total sem janela). Helper
  `_email_stats_fn(type_clause)` (literal interno, sem injeção). Isso alinha a página com o funil do
  Analytics (que já usa `email_log`) e o `get_email_health`.
- Efeito colateral bom: `/system/status` (Sistema) usa o mesmo `alert_stats()` → também fica coerente.
- **Validação VM (Postgres 16):** novo `alert_stats` = **5 / 3716 / 9556 / 9556** vs antigo `alert_log`
  = 20 / 3207 / **8613** (o 8613 batia com o "MÊS: 8613" que o card reportou — confirma que a página
  lia o `alert_log` incompleto; o `email_log` tem 943 alertas a mais all-time por ser o log completo).

### §4 — abas com dados/contadores separados
- **Aba "Alertas enviados":** nova coluna **REMETENTE** (`email_log.from_domain` via `LEFT JOIN` por
  `email_id`) — badge verde p/ os subdomínios cold (`alertas.`/`aviso.klarim.net`, KL-91), cinza p/ o
  antigo (`klarim.net`/`klarimscan.com`). `list_alerts` ganhou o JOIN (1:1, sem multiplicar linhas).
- **Aba "Consultas de perfil":** ganhou **contadores PRÓPRIOS** (HOJE/SEMANA/MÊS/TOTAL de `profile_view`
  enviados, `email_log`) via `store.profile_view_stats()` + `GET /alerts/profile-view-stats` +
  `admin.profileViewStats()`. Não repete mais os contadores dos alertas. A lista continua mostrando as
  consultas (site_events) com origem/UTM. Validação VM: **775 / 17430 / 18109 / 18109**.

### §5 — funil do Analytics "Emails enviados"
- O funil novo (`aa_funnel_raw`, usado pela página Analytics KL-83) **já** contava de `email_log` alert
  types (fix de 2026-07-20). Faltava alinhar as OUTRAS fontes: mudei `aa_metrics_raw.alerts_sent`
  (KPI "Alertas enviados") e o funil legado `analytics_funnel` (MCP `get_funnel`) de `alert_log` →
  `email_log` alert types. Agora **página Alertas + KPI + funil + funil legado** = mesma fonte.

### §6 — "Contas criadas"
- Já correto: `aa_metrics_raw.accounts` = `COUNT(*) FROM users WHERE created_at >= … < …` (KL-95).
  As "7 contas/7d" vêm da tabela `users` — pode incluir contas auto-criadas por HMAC/inline (KL-99),
  o que é esperado, não bug. **Nenhuma mudança**, só verificação.

---

## 2. Arquivos

- `discovery/store.py` — `_email_stats_fn` (helper) + `alert_stats` (email_log) + `profile_view_stats`
  (novo) + `list_alerts` (JOIN from_domain) + `aa_metrics_raw.alerts_sent` (email_log) +
  `analytics_funnel.emails_sent` (email_log).
- `api/main.py` — `GET /alerts/profile-view-stats` (novo).
- `web/src/lib/admin/adminApi.js` — `profileViewStats()`.
- `web/src/components/admin/AlertasPage.jsx` — coluna REMETENTE + `SenderBadge` + contadores próprios
  da aba de perfil.
- `tests/test_kl96_counters.py` — 5 testes (email_log como fonte, filtros distintos por aba, JOIN do
  remetente, endpoint). SQL validado contra o Postgres 16 da VM.

## 3. Segurança
Sem novos inputs de usuário. `_email_stats_fn` recebe apenas literais internos (`type_clause`) — sem
injeção. Endpoints sob o prefixo admin `/alerts` (JWT admin). `contact_email` já aparecia na página
Alertas (é operator-only). O JOIN por `email_id` é 1:1 (id único do Resend) — sem vazamento nem
multiplicação de linhas.

## 4. Testes
- Backend: **1593 passed, 1 skipped** (+5 KL-96). Frontend: `npm run test:unit` 0 falhas + `npm run
  build` verde.
- SQL novo validado contra o Postgres 16 real na VM (contadores + JOIN do remetente).

## 5. Recomendação (fora do escopo do card, mas é o risco de reputação REAL)
O `profile_view` envia **~15k/semana de e-mail cold** por `alerta@klarim.net`, no **mesmo domínio
`klarim.net`** que o transacional (confirmações de conta) precisa. Mesmo a 0,7% de bounce, esse volume
cold no domínio transacional é o risco de reputação de fato (foi o problema original que motivou o
KL-91). Isolá-lo exige um **novo subdomínio verificado no Resend** (não dá p/ jogar no `alertas.`/
`aviso.` — destruiria o warmup do alerta a 100/dia) + validação MX antes do envio. Fica como próximo
card, com decisão do dono (isolar / reduzir frequência / dedup por dono).
