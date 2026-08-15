# KL-167 — Consolidar e-mail em 2 domínios (resolver spam)

**Status:** implementado e validado (dev) · pronto para deploy
**Prioridade:** Highest · **Tipo:** Tarefa
**Arquivos:** `notifier/cold_alert.py`, `notifier/email_client.py`, `discovery/alert_worker.py`,
`discovery/alert_scoring.py`, `discovery/store.py`, `api/main.py`, `tests/*`, `CLAUDE.md`, `docs/DEPLOY.md`

---

## 1. Problema

Alertas caindo no spam. Volume cold fracionado em **5 domínios**, nenhum com reputação
suficiente: `alertas.klarim.net` 5,51% hard + 8,11% soft; `aviso.klarim.net` 5,71% hard +
7,93% soft — **território de blacklist**. `klarimscan.com` (2,03% hard) e `klarim.net` (1,19%)
saudáveis mas subaproveitados.

## 2. Solução — 2 domínios com propósito claro

| Domínio | Papel | Remetentes |
|---|---|---|
| **klarim.net** | Transacional / warm (quem JÁ tem relação) | `klarim@` (confirmação, reset, relatório, vigília, KYC), `alerta@` (boletim), `privacidade@` (LGPD) |
| **klarimscan.com** | Cold / primeiro contato | `scan@` (alertas proativos), `notifica@` (perfil consultado) |

Se `klarimscan.com` cair no spam, **não** contamina o `klarim.net` (isolamento de reputação).

---

## 3. O que mudou

### Consolidação dos remetentes cold (klarimscan.com, sem rotação)
- **`notifier/cold_alert.py`:** `DEFAULT_SENDER_EMAILS` → `("scan@klarimscan.com",)`. Novo
  `RETIRED_SENDER_DOMAINS` (`klarim.net` + `alertas.`/`aviso.`/`perfil.klarim.net`) —
  `load_senders` os **descarta sempre** e cai no default se o env só tiver aposentados
  (nunca fica sem remetente). A máquina de rotação/breaker (`pick_sender`/`flag_high_bounce`)
  fica no código (aceita >1 mailbox em klarimscan.com no futuro), mas em produção é 1 domínio.
- **`notifier/email_client.py`:**
  - `_proactive_from()` (alerta cold, `send_alert`) → default `scan@klarimscan.com`.
  - `_profile_view_from()` (perfil consultado) → default `notifica@klarimscan.com`.
  - Guard `_cold_from_email` + `_RETIRED_COLD_DOMAINS`: um `.env` legado apontando p/ domínio
    aposentado é **ignorado** → cai no default consolidado.
  - **Boletim desacoplado:** `send_bulletin_owner` saiu do `_proactive_from` e passou a usar o
    novo `_bulletin_from()` (`BULLETIN_FROM_EMAIL`, default `alerta@klarim.net`). O boletim vai a
    quem tem conta/opt-in → é transacional, fica no **klarim.net**, e a mudança do cold não o
    arrasta para o domínio de spam.
- **Transacional / LGPD inalterados** (klarim.net / `privacidade@klarim.net`).

### Targeting (reduz bounce → protege reputação) — `discovery/alert_worker.py`
1. **Pula caixas genéricas** (`ALERT_SKIP_GENERIC`, default on): `contato@`/`atendimento@`/`sac@`/
   `info@`/`comercial@`/`vendas@` não recebem mais alerta (bounce 8,7% vs 3,6% pessoais).
   `alert_scoring.is_generic_alert_email` (fronteira de palavra — `sac2@` casa, `sacha@`/
   `informatica@` NÃO). **Muda o KL-146:** genéricos não só desciam na fila — agora são filtrados.
2. **Intervalo mínimo de 90 dias por E-MAIL** (`ALERT_REALERT_MIN_DAYS`): janela por ALVO
   subiu 30→90d (`_ALERT_ELIGIBLE_WHERE`) + novo `store.recently_alerted_emails` cobre o mesmo
   e-mail em alvos diferentes.
3. **Foco em score baixo:** `_urgency_bucket` prioriza sites `<70` (urgência real); `85+` por
   último (sort estável preserva a ordem por lead score dentro da faixa).

Stats por ciclo: `KL-167[bloq_genérico / bloq_realerta90d]`. Ambos os knobs são editáveis no
painel (`_CONFIG_PARAMS`).

---

## 4. Robustez — deploy NÃO exige mexer no `.env` da VM

Os guards de domínio aposentado tornam o código **auto-corretivo**: mesmo que o `.env` atual da
VM ainda liste `ALERT_SENDER_EMAILS=…,scan@alertas.klarim.net,scan@aviso.klarim.net`,
`ALERT_FROM_EMAIL=alerta@klarim.net` e `PROFILE_VIEW_FROM_EMAIL=notifica@perfil.klarim.net`,
após o deploy:
- cold alert + perfil → **klarimscan.com** (aposentados descartados);
- send_alert (`_proactive_from`) vê `alerta@klarim.net` (domínio retired-cold) → **klarimscan.com**;
- boletim (`_bulletin_from`) → **alerta@klarim.net** (klarim.net), independente do `ALERT_FROM_EMAIL`.

Limpar o `.env` da VM (abaixo) é **recomendado** (clareza), mas **não é pré-requisito**.

```
ALERT_SENDER_EMAILS=scan@klarimscan.com
ALERT_FROM_EMAIL=scan@klarimscan.com
ALERT_FROM_NAME=Klarim
PROFILE_VIEW_FROM_EMAIL=notifica@klarimscan.com
PROFILE_VIEW_FROM_NAME=Klarim
# (BULLETIN_FROM_EMAIL não precisa; default alerta@klarim.net)
```

⚠️ **Warmup:** com 1 só domínio cold, o teto efetivo diário = `ALERT_SENDER_DAILY_LIMIT` (era
×3 com 3 remetentes). `klarimscan.com` já roda ~500/dia saudável; monitorar e subir o knob no
painel conforme a reputação. Menos volume cold é **intencional** (o card ataca o spam).

---

## 5. Validação (dev — `docker-compose.dev.yml`, `DRY_RUN_EMAIL=true`)

Rodado o mailer real (mesmo código do dev) capturando o `from` por canal, com os defaults:

| Canal | Remetente | Domínio |
|---|---|---|
| 1. cold alert (rotação) | `Klarim <scan@klarimscan.com>` | klarimscan.com ✅ |
| 2. alerta proativo (`_proactive_from`) | `Klarim <scan@klarimscan.com>` | klarimscan.com ✅ |
| 3. profile_view | `Klarim <notifica@klarimscan.com>` | klarimscan.com ✅ |
| 4. confirmação (transacional) | `Klarim <klarim@klarim.net>` | klarim.net ✅ |
| 5. LGPD confirmação | `privacidade@klarim.net` | klarim.net ✅ |
| 6. boletim ao dono | `Klarim <alerta@klarim.net>` | klarim.net ✅ |

**Resultado:** cold=klarimscan.com · transacional/LGPD/boletim=klarim.net · **zero** envio por
`alertas/aviso/perfil.klarim.net`. (Checks 1, 2, 3, 5 do card; SPF/DKIM do klarimscan.com já
verificados no Resend — envio real não roda em dev.)

---

## 6. Testes

- **Novo** `tests/test_kl167_email_consolidation.py` (27 casos): `is_generic_alert_email`
  (genéricos/variantes vs pessoais sem overmatch), `_urgency_bucket`, default/guard/fallback dos
  remetentes cold, boletim transacional, `recently_alerted_emails` short-circuit.
- **Novos no `test_alert_worker.py`:** skip de genéricos, skip de re-alerta 90d, priorização por score.
- **Alinhados** (comportamento mudou, card-mandated): `test_kl91_cold_alert`, `test_email_pipeline`,
  `test_kl108`, `test_alert_sender_migration`, `test_kl101_profile_view`, `test_alert_plain_text`,
  `test_alert_worker` — rotação de 2 subdomínios → 1 domínio consolidado (a máquina de rotação/
  breaker é exercitada com 2 remetentes de TESTE `*.example.com`).
- **Suite:** `pytest` **2439 passed, 1 skipped**; `npm run test:unit` **246 passed**.

---

## 7. Segurança

- Nenhum endpoint/fluxo novo; só remetentes + filtros de envio. Reputação **isolada** (cold ≠
  transacional). Menos bounce (genéricos filtrados + intervalo 90d) → menos risco de blacklist.
- `_send` continua registrando tudo no `email_log` (`from_domain`), a blocklist aprendente e os
  circuit breakers (KL-24/108) seguem valendo. Opt-out por resposta + `List-Unsubscribe` intactos.

## 8. DNS / Resend
Sem mudança no DNS. `klarimscan.com` já verificado (SPF+DKIM). Subdomínios antigos ficam no DNS
(respostas pendentes), mas não enviam mais nada (guards no código).

## 9. Deploy
Deploy direto após validação (regra do card). Pronto = push + GitHub Actions (test+deploy) 100%
verde. Registrar em `claude/DEPLOY_HISTORY.md` quando verde; verificar em prod que novos envios
saem de `@klarimscan.com` (cold) e `@klarim.net` (transacional) — MCP `get_email_log`/`from_domain`.
