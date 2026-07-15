# KL-44 P2 — Vigílias core (SSL, domínio, score, e-mail, reputação)

**Card:** KL-44 (Guardião Digital) — Prompt 2
**Data:** 2026-07-15
**Depende de:** P1 (planos + assinaturas — entregue)
**Escopo:** 2 tabelas + lógica das 5 vigílias + worker (6h) + 5 templates + 6 endpoints +
2 MCP tools + 1 página admin Astro + seed. O worker **começa pausado** (ativação via MCP
após verificação).

As vigílias são monitoramento silencioso contínuo: só incomodam o dono do site quando
algo importa. Rodam para contas **Pro/Agency** (o `free` não tem vigília no plano).

---

## Parte 1 — Tabelas (`discovery/store.py`, idempotentes)

- **`vigilias`** — 1 por `(user_id, site_domain, tipo)`; `enabled`, `last_check_at`,
  `next_check_at`, `last_status` (ok/warning/critical/error), `last_data` JSONB (guarda o
  estado anti-spam), `alert_count`, `last_alert_at`. Índices `next_check_at WHERE enabled`
  e `user_id`.
- **`vigilia_alerts`** — histórico de alertas (severity, title, message, action_text,
  data, `email_sent`, `email_id`, `read_at`).

Store methods novos: `upsert_vigilia` (idempotente, re-habilita no upgrade),
`disable_user_vigilias_except` (downgrade), `get_due_vigilias`, `update_vigilia_after_check`,
`create_vigilia_alert`, `mark_vigilia_alert_sent`, `list_vigilias`, `get_vigilia`,
`list_vigilia_alerts`, `vigilia_stats`, `get_user_vigilias`, `get_user_vigilia_alerts`,
`get_all_monitored_sites`, e `get_recent_scans_with_checks` (os 2 últimos scans **com**
`checks_json`).

## Parte 2 — Lógica das 5 vigílias (`api/vigilias.py`, módulo puro)

Cada `check_*` recebe `(store, domain, last_data)` e devolve um dict homogêneo
(`status/should_alert/severity/subject/title/message/action_text/data`). **100% passivo.**

- **SSL** — lê `details.days_left` do check 03 do último scan. Thresholds 30/14/7 (warning)
  e 1 (critical). **Anti-spam por threshold** (`last_data.alerted_thresholds`); renovação
  do cert limpa os thresholds (novo mergulho volta a alertar).
- **Domínio** — lookup **RDAP** (RFC 7480): `.br` → `rdap.registro.br`, fallback
  `rdap.org`. Cache 24h no Redis. Thresholds 60/30/14 (warning) e 7 (critical). RDAP
  indisponível → `status='error'` (retry no próximo ciclo), nunca alerta falso.
- **Score** — compara o score dos 2 últimos scans. Alerta se caiu > 5 pontos **ou** saiu
  do verde (≥90 → <90). Anti-spam por `last_alerted_scan_id` (1 alerta por par de scans).
- **E-mail** — SPF/DKIM/DMARC (checks 21-23) que passaram de PASS → FAIL entre os 2 últimos
  scans. Anti-spam por scan.
- **Reputação** — HIBP/Safe Browsing (checks 28-29) = FAIL → alerta **crítico**. Só alerta
  quando é NOVO (não repete para a mesma blacklist).

`run_vigilia_check` é o dispatcher (nunca levanta — tipo inválido/erro → status de erro).

## Parte 3 — Worker (`discovery/vigilia_worker.py`)

`VigiliaWorker` roda no container `discovery` (compõe no `asyncio.gather` do `worker.py`),
ciclo padrão **6h**. Por ciclo: `get_due_vigilias` (vencidas) → por vigília **enforcement
de plano** (`plans.get_subscription`, com expiração lazy do trial; se o plano não permite
o tipo, **desativa** a vigília; erro transiente → pula sem desativar) → `run_vigilia_check`
(com `asyncio.wait_for` de 30s) → se `should_alert`: cria `vigilia_alert` + envia e-mail
(best-effort, proativo → respeita a blocklist) → `update_vigilia_after_check` reagenda.
Teto `max_per_cycle` (100), pausa 1s entre checks de **domínio** (rate limit RDAP), erro
em uma vigília **não** derruba as outras. Heartbeat `worker:vigilia:status`.

- **`worker_control`** ganhou `"vigilia"` em `WORKERS` + `_CONFIG_KEYS`
  (`cycle_hours`/`max_per_cycle`). `pause_worker`/`resume_worker`/`get_worker_control`
  (MCP) já reconhecem o novo worker; `_HB_KEYS` inclui `vigilia`.
- **Criação automática:** `account_add_site` cria (fire-and-forget) as vigílias do plano
  para o site novo; a **mudança de plano** (`PATCH /admin/subscriptions/{id}/plan` +
  bulk) chama `_sync_user_vigilias` (upgrade cria, downgrade desativa — nunca deleta).

## Parte 4 — Templates (`notifier/templates/vigilia_*.html`) + mailer

5 templates dark-mode (ícone de severidade, mensagem acessível, bloco **"Texto para o
técnico"** copiável, CTA → `/site/{domínio}`, footer com link do dashboard). Um único
método `KlarimMailer.send_vigilia_alert(tipo, ...)` escolhe o template por tipo
(`email` → `vigilia_email_security.html`). `EMAIL_TYPES` ganhou `vigilia_ssl/domain/score/
email/reputation` (e `signup_verification`, pendente do fix anterior). Assuntos conforme
o card (SSL "expira em N dias", reputação "🔴 URGENTE").

## Parte 5 — Endpoints

- **Admin (Bearer):** `GET /admin/vigilias/stats`, `GET /admin/vigilias` (filtros
  tipo/status/user_id/domain + paginação), `GET /admin/vigilias/{id}` (+ histórico),
  `GET /admin/vigilia-alerts`. Sob o prefixo `/admin` (JWT admin).
- **Usuário (cookie/Bearer JWT):** `GET /account/vigilias`, `GET /account/vigilia-alerts`
  — filtrados por `user_id` da sessão (**IDOR-safe**, nunca expõem outra conta), rate
  limit 10/min/user (`_redis_allow`). Registrados em `adminApi.js`.

## Parte 6 — Página admin Astro (`/painel/vigilias`)

`vigilias.astro` + `VigiliaPage.jsx` (client:only, `AdminShell active="vigilias"`): KPIs
(ativas, por status, alertas hoje/7d), filtros (tipo/status/busca de domínio), tabela
(domínio, tipo, status, dado relevante, último check, alertas, conta) e **modal** com o
histórico de alertas. Item **Vigílias** na sidebar (após Assinantes, ícone de olho).

## Parte 7 — MCP tools (`mcp_server/tools/vigilia.py`)

`get_vigilia_stats` e `list_vigilia_alerts` (leitura, via `_guard`). Registradas no
`__init__` (**49 tools** no total) e na whitelist do `test_mcp_server`.

## Parte 8 — Seed (`scripts/seed_vigilias.py`)

Para cada `(usuário, site monitorado)`, cria as vigílias que o plano permite
(`next_check_at=now`). **Pausa o worker `vigilia` primeiro** (`worker_control.pause`) — o
worker só dispara após `resume_worker vigilia` via MCP. `--dry-run` / `--no-pause`.

## Parte 9 — Testes (`tests/test_vigilias.py`, 23 testes)

Lógica dos 5 checks (alerta + anti-spam + saudável), domínio via RDAP mockado, dispatcher
(tipo inválido / nunca levanta), worker cycle (cria alerta, enforcement de plano,
`worker_control` pausado), `worker_control` reconhece `vigilia`, endpoints admin (auth
obrigatória + dados), endpoint de usuário (**IDOR** — só a própria conta) e a renderização
dos 5 templates via `send_vigilia_alert`. Offline (sem rede/Postgres).

## Segurança

- Endpoints admin sob JWT admin; endpoints de usuário sob JWT de usuário **filtrados por
  `user_id`** (IDOR) + rate limit 10/min. Inputs validados (Pydantic/coerção + clamp de
  limit/offset). Nenhum dado de outra conta é exposto.
- Vigília é **proativa** → respeita a blocklist (KL-24/62) e é registrada no `email_log`.
- Worker **fail-open** no `worker_control`, mas **começa pausado** via seed; enforcement de
  plano nunca desativa por erro transiente de lookup.

## Deploy / pós-deploy (manual, pelo dono)

1. `git push` → CI (Test + Build web + Nginx check + Deploy). Schema roda no
   `ensure_schema`.
2. `docker compose exec -T api python scripts/seed_vigilias.py` (cria as vigílias + deixa
   o worker pausado).
3. Verificar `/painel/vigilias` no browser + `get_vigilia_stats` no MCP.
4. `resume_worker vigilia` via MCP para ativar. Primeiro ciclo em ≤6h (ou reiniciar o
   container discovery para rodar após o warmup).

## Regra inviolável

As vigílias são **passivas** e **não alteram o score de segurança**. O worker começa
pausado e só o dono o ativa. Enforcement de plano é servidor-autoritativo (Pro/Agency);
o e-mail de vigília respeita a blocklist. Anti-spam por threshold/scan/blacklist evita
repetir o mesmo alerta.
