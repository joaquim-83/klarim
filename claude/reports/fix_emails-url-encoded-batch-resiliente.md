# Fix — Sanitizar e-mails URL-encoded + batch resiliente a e-mails inválidos

- **Tipo:** Bugfix urgente (sem card Jira)
- **Data:** 2026-07-10
- **Executor:** Claude CLI (Opus 4.8)
- **Commit:** `fix: sanitize URL-encoded emails in contact extraction + resilient batch sending`

---

## Incidente

O Alert Worker parou de enviar desde ~21:24 UTC. Um e-mail com `%20` no início
(`%20contato@envioz.com.br`) passou pelo filtro do `contact.py` e **envenenou o
batch inteiro**: o Resend Batch API é **tudo-ou-nada** — 1 e-mail inválido faz ele
rejeitar os 50 (`422: Invalid 'to' field`). Resultado: 50+ alertas com
`status=failed` e `email_id=null`, e o worker travado.

**Causa raiz:** o regex de e-mail permite `%` no local-part (usado em URL-encoding),
e o `contact.py` **não fazia `unquote()`** nos e-mails extraídos do HTML; o Alert
Worker também não validava o formato antes de montar o batch.

## Fix 1 — `_clean_email` no `contact.py`

Nova função `_clean_email(raw)`: **URL-decode** (`%20`→espaço, `%40`→@) + remove
espaços/tabs/quebras/nbsp + lowercase. Aplicada em `_collect_emails` (todo e-mail
extraído sai limpo) **e** no `PATCH /targets/{id}/email` (edição manual). Assim
`%20contato@envioz.com.br` vira `contato@envioz.com.br` **antes** de validar/gravar.

## Fix 2 — Batch resiliente no Alert Worker

- **Solução A (previne):** `_validate_batch` agora limpa o e-mail e, se mudou,
  **conserta no banco** (self-healing) e usa o limpo; depois rejeita **formato
  inválido** (`_EMAIL_RE`) marcando o alvo `descartado`. O e-mail ruim nunca entra
  no batch.
- **Solução B (rede de segurança):** `_send_with_split` — se o batch ainda der 422,
  divide ao meio e retenta recursivamente até **isolar** o culpado (batch de 1 que
  falha). Envia os 49 bons, descarta o 1 ruim. Erro de infra (não-422) **propaga** e
  loga tudo como `failed` **sem** descartar (não pune e-mails bons por um 5xx).

## Fix 3 — Limpar e-mails sujos já no banco

`POST /api/admin/clean-emails` (JWT): itera todos os alvos com e-mail, aplica
`_clean_email`; conserta os que mudaram e ficaram válidos, descarta os
irrecuperáveis (sem `@` após limpar). Retorna `{total, cleaned, discarded, examples}`.

## Validação

- `_clean_email`: `%20contato@envioz.com.br`→`contato@envioz.com.br`,
  `contato@test.com\n`→`contato@test.com`, `a%40b.com`→`a@b.com`, tab/nbsp removidos.
- `_collect_emails` devolve e-mails sem `%20`/espaços.
- `_validate_batch`: e-mail sujo → limpo + self-heal no banco + mantido; inválido →
  `descartado`.
- `_send_with_split`: 3 bons + 1 ruim → 3 enviados, 1 isolado; erro de infra propaga.
- `run_cycle` com 1 e-mail ruim no batch → 2 enviados, 1 descartado.
- `PATCH /targets/{id}/email` com `%20…` → gravado limpo.
- `POST /admin/clean-emails` → conserta/descarta corretamente.
- **Suite: 214 passed, 1 skipped.**

## Ação na VM (após o deploy)

1. Limpar os e-mails sujos existentes:
   ```bash
   curl -s -X POST https://painel.klarim.net/api/admin/clean-emails \
     -H "Authorization: Bearer <JWT do painel>" | python3 -m json.tool
   ```
2. Os 50+ alvos com `status='failed'` continuam elegíveis (o `failed` no `alert_log`
   não bloqueia; a elegibilidade olha `last_alert_at`), então voltam ao funil no
   próximo ciclo do Alert Worker — agora com o batch resiliente.

## Notas

- O split-retry só age em **422/invalid**; erros de infra (5xx/rede) propagam para não
  descartar e-mails bons.
- O `_clean_email` no `_collect_emails` conserta a captação nova; o `clean-emails`
  conserta o legado; o `_validate_batch` é a última barreira (self-healing em runtime).
