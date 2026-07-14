# KL-60 — Desacoplar scan do e-mail + fix formulário de contato

**Card Jira:** KL-60 · **Data:** 2026-07-14 · **Prioridade:** crítica

Dois entregáveis: (0) o formulário de contato passa a gravar direto no inbox (bug ativo);
(1) o discovery passa a escanear **todo site acessível**, com ou sem e-mail — sobe o scan
rate de ~35% para ~100% dos sites acessíveis.

---

## Fix 0 — Formulário de contato → inbox (feito primeiro)

**Problema:** `POST /api/contact` só enviava via Resend, que pode entrar em loop (mesmo
domínio sender/dest) — a mensagem se perdia.

**Fix:** o endpoint grava a mensagem **direto no `inbox_messages`** (fonte de verdade)
**antes** de tentar o e-mail. O envio via Resend virou **best-effort** (try/except, só loga).

- **Coluna nova** `inbox_messages.source` (`ALTER … ADD COLUMN IF NOT EXISTS source TEXT
  DEFAULT 'webhook'`): `webhook` (Hostinger) | `contact_form` (site).
- `insert_inbox_message` grava o `source`; `POST /contact` usa `source='contact_form'`,
  `message_id=contact-<uuid>`, `body_html` com escape + `<br>`.
- **Filtro no inbox:** `list_inbox_messages(..., source=None)` + `GET /admin/inbox?source=
  webhook|contact_form`. `_INBOX_COLS` ganhou `source` (lista/detalhe expõem).
- **Frontend (`Inbox.jsx`):** tabs **[Todos] [Emails] [Contato]** (`?source=`) + badge
  "Contato" nas mensagens do formulário. `unread-count` já inclui as de contato.

## KL-60 — Scan desacoplado do e-mail

**Antes:** `discovery/worker.py::_process_domain` só enfileirava para scan sites **com**
e-mail; sem e-mail → `sem_contato` e **NÃO** enfileirava. ~7,8k alvos (39%) presos.

**Depois:** todo site **acessível** (`html != None`) é enfileirado para scan, tenha e-mail
ou não. O `status` ainda reflete o e-mail (`sem_contato` = sem e-mail achado), mas o
`update_scan_result` (já existente) promove o alvo a `scanned` quando o scan completa. Só o
site **inacessível** vira `descartado` (sem enqueue). O e-mail, se houver, fica salvo p/
notificações; o `enrich_all`/`enrich_batch` continuam tentando achar e-mail depois.

**Opção A:** `sem_contato` continua significando "sem e-mail" (info), mas não bloqueia mais o
scan. Nada muda em scan worker / enrich_all / enrichment / alert worker / landing / rankings /
sitemap (já funcionam para qualquer alvo com scan/perfil).

**Backlog (`scripts/enqueue_unscanned.py`):** drena os `sem_contato` sem scan
(`last_scan_id IS NULL`) em **batches** (`--limit 500`, default), `rpush` na `klarim:scan_queue`
(`source='discovery'` → tier gratuito 15). `store.list_unscanned_targets`/
`count_unscanned_targets`. **Nunca enfileira tudo de uma vez** (7,8k a 50-100/h = dias). Rodar
1×/dia até drenar. Uso: `docker compose exec -T api python scripts/enqueue_unscanned.py
--limit 500` (ou `--dry-run`).

**Rate:** `WORKER_MAX_SCANS_PER_HOUR` (env, default 50). Recomendado subir p/ **100** na VM
(`.env` + restart do worker) — a vazão real é limitada pela duração do scan (worker único),
então 100 é seguro; se a VM sofrer, voltar a 50. Monitorar `scan.queue_size` via
`get_system_status` (MCP) / painel Sistema.

## Impacto (confirmado, sem mudança)
Scan worker, `enrich_all.py`, `scanner/enrichment.py`, alert worker (pausado), landing
`/site/{dom}`, rankings, sitemap — **nenhuma** mudança: já operam sobre qualquer alvo com
scan/perfil.

## Testes
`tests/test_kl60_decouple.py` (10, offline): discovery enfileira sem e-mail / com e-mail
(+ e-mail salvo) / inacessível → descartado (sem enqueue); contato grava no inbox
(`source='contact_form'`) inclusive quando o e-mail explode; e-mail inválido → 422; filtro
`?source=` (webhook/contact_form/inválido); inbox exige admin; script de backlog enfileira +
dry-run não conecta no Redis. Regressão: `test_kl56_admin_inbox` (FakeStore `list_inbox_
messages` ganhou `source`), `test_discovery` → verdes. Frontend admin build + full-suite +
CI validam o resto.

## Deploy (ops pós-merge)
1. Deploy via CI (código).
2. Na VM: `WORKER_MAX_SCANS_PER_HOUR=100` no `.env` + `docker compose up -d worker`.
3. `docker compose exec -T api python scripts/enqueue_unscanned.py --limit 500` (repetir
   diariamente até drenar; monitorar queue_size).

## Regra atualizada (inviolável)
A regra antiga "só escanear sites com e-mail" foi **revogada** (KL-60). Todo site acessível é
escaneado; o e-mail só governa **notificações**, não o scan. O backlog é drenado em batches —
nunca de uma vez. O contato **nunca se perde** (gravado no inbox antes do e-mail).
