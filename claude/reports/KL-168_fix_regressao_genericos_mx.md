# KL-168 — Fix regressão do KL-167: filtro de genéricos + blocked_mx

**Card:** KL-168 · **Prioridade:** Highest · **Tipo:** Bug (regressão do KL-167)
**Data:** 2026-08-18

## Problema

O alert worker **parou de enviar e-mails por ~24h**. Último ciclo (18/08 08:17 UTC):

| Métrica | Valor |
|---|---|
| Elegíveis fetchados | 200 |
| `blocked_generic` | 192 (97%) |
| Passaram o filtro de genéricos | 8 |
| `blocked_mx` | 8 (100% dos que passaram) |
| **Enviados** | **0** |

Duas causas, ambas introduzidas pelo KL-167:

1. **Filtro de genéricos agressivo demais + ligado por default.** O KL-167 adicionou
   `ALERT_SKIP_GENERIC=true` (default ON) descartando `contato@`/`atendimento@`/`sac@`/`info@`/
   `comercial@`/`vendas@`. Na base brasileira esses prefixos são o **e-mail principal de negócio**
   de muitas PMEs (não descartáveis como em mercados com e-mail pessoal do dono) — ~39% dos
   elegíveis. Consumidos os poucos pessoais nos primeiros ciclos do dia, o backlog fica 100%
   genérico e o worker para.
2. **`blocked_mx` pegava os pessoais restantes.** Os 8 que passaram o filtro de genéricos caíram
   **todos** na verificação de MX — anômalo. `NoAnswer` (domínio existe mas sem registro MX)
   estava sendo tratado como `no_mx` definitivo, gerando falso `blocked_mx` em domínios BR que só
   publicam registro A (mail via *implicit MX*, RFC 5321 §5).

## Fixes

### Fix 1 — Filtro de genéricos: opt-in (default OFF) + lista reduzida a 2

- `discovery/alert_worker.py`: `skip_generic` passa a **default FALSE** (class default + `__init__`
  lê `ALERT_SKIP_GENERIC` com default `"false"`; só `"true"`/`"1"` liga). O operador liga no painel
  se quiser testar.
- `discovery/alert_scoring.py`: `GENERIC_ALERT_SKIP_PREFIXES` reduzido de 6 → **`("contato", "sac")`**
  (os dois de maior bounce). Quando o filtro está ligado, só esses dois deixam de receber; o resto
  (`info@`/`comercial@`/`vendas@`/`atendimento@`) volta a receber, apenas **reordenado** pelo
  `_email_type_factor` (KL-146).
- `api/main.py` (`_CONFIG_PARAMS`): default do toggle no painel `ALERT_SKIP_GENERIC` → `"false"`.
- **Toggle já funcionava ao vivo:** `run_cycle` chama `_reload_settings` no topo de cada ciclo, que
  relê `admin_settings > .env > default` (fail-open). Um `UPDATE admin_settings` no painel vale no
  ciclo seguinte, sem redeploy. Documentado no comentário.

### Fix 2 — `blocked_mx`: fail-open no `NoAnswer` + timeout maior + logging

- `notifier/email_verifier.py::_resolve_mx_sync`:
  - **`NoAnswer` → `unknown`** (antes `no_mx`). Domínio existe mas sem MX ⇒ e-mail ainda entregável
    via registro A (*implicit MX*). Só **NXDOMAIN** (domínio não existe) segue como `no_mx`
    definitivo. Como o filtro é **fail-open** (só `no_mx` rejeita), `unknown` passa — corrige o
    falso `blocked_mx`.
  - Timeout do resolver configurável **`ALERT_MX_TIMEOUT`** (default **10s**, era 5s fixo) — folga
    para DNS BR lento.
- Novo `email_verifier.email_mx_status(email, redis) -> 'ok'|'no_mx'|'unknown'|'invalid'` (reusa o
  cache de `_mx_lookup`, uma resolução/domínio/24h). O `_has_mx`/`_email_has_mx` foram refatorados
  sobre `_mx_lookup` (mantêm a assinatura; `_has_mx` continua compatível com `verify_local`). O
  cache Redis passou a guardar o **status** (`ok`/`no_mx`) aceitando o legado `"1"`/`"0"`.
- `discovery/alert_worker.py::_verify_and_filter`: cada `blocked_mx` agora é **logado**
  (`logger.warning`, e-mail mascarado + domínio + status). Se 100% cair em `blocked_mx` de novo, o
  log revela na hora se é o resolver do container (deveria dar `unknown`, que passa) ou domínios
  realmente sem MX.

### Fix 3 — Intervalo de 90 dias: confirmado que NULL passa (sem mudança de lógica)

`store.recently_alerted_emails` retorna **só** os e-mails que TÊM alerta `sent` nos últimos N dias.
O filtro no worker é `if email in recent_emails`. E-mail nunca alertado (`alert_log`/`last_alert_at`
NULL) **não está no set** → passa. Não há `AND` que exclua NULLs. Comportamento correto; adicionados
testes que provam (`test_never_alerted_email_passes_90d_filter`, `test_realert_disabled_when_zero_days`).

## Testes

- **Novo:** `tests/test_kl168_alert_regression.py` (12 testes) — default OFF, env liga, lista de 2,
  `email_mx_status` (ok/no_mx/unknown/invalid), `NoAnswer→unknown`, `NXDOMAIN→no_mx`,
  `_verify_and_filter` bloqueia só `no_mx` (unknown passa), 90d deixa nunca-alertado passar.
- **Atualizados:** `test_kl167_email_consolidation.py` (lista reduzida a 2, genéricos que saíram
  agora passam), `test_alert_worker.py` (`test_run_cycle_skips_generic_emails_when_toggle_on` +
  novo `test_run_cycle_default_does_not_skip_generics`).
- **Suíte cheia verde:** `2451 passed, 1 skipped` (pytest) + `246 pass` (`npm run test:unit`).

## Validação em dev (`docker-compose.dev.yml`)

- DNS/MX resolvido de dentro do container `api` (mesma imagem dos workers): `email_mx_status` p/
  gmail/hotmail/uol/etc. e `dns.resolver` diretos — ver seção de validação abaixo.

## Validação pós-deploy (produção)

Após o próximo ciclo do alert worker:
- `sent > 0` (e-mails saindo).
- `blocked_generic` = 0 (filtro OFF) — ou `< 50%` se o operador ligar.
- `blocked_mx < 20%` (não 100%).
- Nenhum envio dos subdomínios aposentados (`alertas`/`aviso`/`perfil.klarim.net`) — os guards do
  KL-167 (`RETIRED_SENDER_DOMAINS`) seguem intactos.
- Se `blocked_mx` reaparecer alto, ler os `logger.warning [alert] blocked_mx:` p/ ver domínio+status.

## Nota operacional

O `ALERT_SKIP_GENERIC` foi forçado para `false` no banco (`UPDATE admin_settings`) como medida
emergencial. Este fix torna isso o **default permanente no código** — o registro emergencial no
banco agora só espelha o default (pode ser removido ou mantido, é idempotente).

## Arquivos alterados

- `discovery/alert_worker.py` — default `skip_generic=False`; MX filter com status + logging.
- `discovery/alert_scoring.py` — `GENERIC_ALERT_SKIP_PREFIXES = ("contato", "sac")`.
- `notifier/email_verifier.py` — `NoAnswer→unknown`, `ALERT_MX_TIMEOUT`, `_mx_lookup`/`email_mx_status`.
- `api/main.py` — default do `_CONFIG_PARAMS["ALERT_SKIP_GENERIC"]` = `"false"`.
- `tests/test_kl168_alert_regression.py` (novo) + `test_kl167_email_consolidation.py` + `test_alert_worker.py`.
- `CLAUDE.md` — §4 (targeting/MX), §8 (estado atual), §11 (índice de cards).
