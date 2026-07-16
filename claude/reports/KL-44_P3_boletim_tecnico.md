# KL-44 P3 — Boletim de segurança + técnico vinculado + laudo compartilhável

**Card:** KL-44 (fase P3) · **Depende de:** P1 ✅ + P2 ✅ · **Data:** 2026-07-16

Fecha o loop "email push > dashboard pull": boletim recorrente por plano, técnico
vinculado (vetor de crescimento orgânico) e laudo compartilhável (link + WhatsApp).

---

## O que foi implementado (por bloco)

### Bloco 1 — Modelo de dados (`discovery/store.py`)
- Tabelas `technician_links`, `shared_reports`, `bulletins` + coluna `users.role`
  (owner|technician|both). Tudo idempotente no `_SCHEMA`.
- ~25 store methods: técnico (create/get/revoke/accept/auto-link/clients/search), laudo
  (create/get_by_code/register_access), boletim (create/last/list_users_due/stats), scan
  helpers (`get_latest_scan_id`/`_full`, `get_user_target_vigilias`). `create_user` ganhou `role`.

### Bloco 2 — Backend API
- **Técnico** (`/account/technician/*`, JWT usuário): invite (cria vínculo + laudo + e-mail,
  10/h/IP), revoke, links, search (só `found/user_id/name`), accept-invite, clients (dono
  **mascarado**).
- **Laudo**: `POST /account/shared-report/create` (código CSPRNG + url + `whatsapp_url`);
  `GET /public/laudo/{code}` (público, **sem PII**, TTL 30d, 30/h/IP, incrementa acesso).
- **Signup de técnico**: `SignupBody.role`/`invite`; `_create_account_record` cria com role +
  **auto-vincula** convites pendentes do e-mail. `_user_public` expõe `role`.
- Enriquecimento de FALHAS extraído para **`reporter/laudo.py::enrich_fails`** (compartilhado
  API ↔ worker; import do reporter guardado).

### Bloco 3 — Bulletin worker (`discovery/bulletin_worker.py`)
- No container `discovery` (asyncio.gather), heartbeat, `worker_control` (`"bulletin"`).
  Ciclo 1 h; às `BULLETIN_HOUR_UTC` (13h) determina as frequências devidas (free=mensal dia 1,
  pro=semanal seg, agency=diário úteis). Por (dono, site): monta score+tendência+vigílias+ação
  prioritária, cria laudo, envia ao dono (proativo) e ao técnico ativo (transacional), grava
  `bulletins` + `email_log`. Config: `BULLETIN_ENABLED` (admin_settings), `BULLETIN_HOUR_UTC`.

### Bloco 4 — Templates plain text (`notifier/bulletin.py` + mailer)
- `build_owner_bulletin` / `build_technician_bulletin` / `build_technician_invite` (puros).
- `KlarimMailer.send_bulletin_owner` (proativo `bulletin`), `send_bulletin_technician`
  (transacional `bulletin_technician`), `send_technician_invite` (`technician_invite`).

### Bloco 5 — Frontend + admin + MCP
- **`/laudo/[code].astro`** (SSR, sem JS, `noindex`): ação prioritária + FALHAS
  (severidade/evidência/OWASP-CWE/correção) + todos os checks + CTA técnico. Nginx: `laudo`
  nas rotas Astro (não-cache, o acesso conta).
- **SiteDetail**: seção "Técnico responsável" (`TechnicianSection`: convidar/revogar +
  compartilhar laudo + WhatsApp). **Dashboard**: "Sites dos meus clientes" (técnico).
  **Cadastrar** `?role=technician[&invite=]` (texto adaptado + auto-vínculo).
- **Admin**: `GET /admin/bulletin-stats`, `/admin/technician-links`. **MCP**:
  `get_bulletin_stats`, `list_technician_links`.

---

## Regras invioláveis (respeitadas)
1. `contact_email` nunca exposto (laudo/técnico). 2. E-mail do dono **mascarado** ao técnico.
3. Scanner/profiler intocados. 4. Boletim do dono **plain text** via `klarimscan.com`.
5. Técnico via `seguranca@klarim.net` (transacional). 6. Reply-To `scan@klarim.net` (KL-67).
7. Rate limit Redis+fallback em todos os endpoints novos. 8. Código laudo/convite **CSPRNG**.
9. Laudo **expira em 30d**.

## Decisões / desvios
- **Laudo `noindex`**: a spec pedia "SSR para SEO"; um laudo por código aleatório não é
  conteúdo de SEO e indexá-lo exporia scans amplamente → mantive SSR (funciona sem JS, é o
  valor real) mas com `noindex` (decisão de segurança).
- **`enrich_fails` compartilhado** (`reporter/laudo.py`) em vez de duplicar no worker/API.
- **Tracking `site_events` do boletim**: a tabela `bulletins` já é a fonte de analytics
  (total/freq/técnico); não dupliquei o evento no site_events (evita acoplar o worker à API).
- **Boletim começa habilitado** (diferente da vigília, que começa pausada) — mas só dispara
  no horário certo por plano; controlável via `worker_control`/`BULLETIN_ENABLED`.

## Testes
`tests/test_kl44_p3_bulletin.py` (builders + `enrich_fails` + helpers/frequências do worker) +
endpoints de técnico/laudo/signup-role em `tests/test_accounts.py` (FakeStore estendido).
`test_mcp_server` cobre as tools novas.

## Pós-deploy
O worker envia no próximo horário (13h UTC) por plano. Para testar já, dá para disparar um
boletim manual/forçar via ajuste de `BULLETIN_HOUR_UTC` no painel, ou aguardar a próxima janela.

## Arquivos
- **Backend:** `discovery/store.py`, `api/main.py`, `discovery/bulletin_worker.py`,
  `discovery/worker.py`, `discovery/worker_control.py`, `notifier/{email_client,bulletin}.py`,
  `reporter/laudo.py`, `mcp_server/tools/system.py`.
- **Frontend:** `web/src/pages/laudo/[code].astro`, `web/src/components/account/
  {TechnicianSection,SiteDetail,Dashboard,SignupForm}.jsx`, `web/src/pages/cadastrar.astro`,
  `frontend/nginx/{http.conf,https.conf.template}`.
- **Testes/Docs:** `tests/{test_kl44_p3_bulletin,test_accounts,test_mcp_server}.py`,
  `claude.md`, `docs/{API,ARCHITECTURE,SECURITY}.md`, este relatório.
