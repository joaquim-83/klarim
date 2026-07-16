# KL-69 — Gestão de usuários: página unificada + ações admin + notificações + termos

**Card:** KL-69 · **Prioridade:** High · **Depende de:** KL-68 ✅ · **Data:** 2026-07-16

Funde "Gestão de Clientes" + "Assinantes" numa página **Usuários** com ações reais
(remover site, desativar/reativar conta) + notificações por e-mail + enforcement de
`is_active` no login + termos de uso.

---

## O que foi implementado (por bloco)

### Bloco 1 — Backend (`api/main.py`, `discovery/store.py`)
- **`is_active`**: já existia em `users` (sem migration). O login (`POST /account/login`)
  já bloqueava; a **mensagem** foi atualizada para "Sua conta foi desativada. Entre em
  contato com seguranca@klarim.net." (**403**).
- Endpoints (JWT admin, prefixo `/admin`, **rate limit 30/min/IP** Redis+fallback):
  - `POST /admin/users/{user_id}/remove-site {target_id, notify}` — valida vínculo, revoga
    posse (`ownership_verifications='revoked'`), remove `user_sites`, notifica (`site_removed`).
  - `POST /admin/users/{user_id}/deactivate {notify}` — `is_active=false` + `account_deactivated`.
  - `POST /admin/users/{user_id}/reactivate {notify}` — `is_active=true` + `account_reactivated`.
  - **`clean-blocked-sites` melhorado**: em `dry_run` devolve `items` (domínio + e-mail);
    ao aplicar, **notifica cada dono** (`site_removed`) e retorna `notified`.
- **Store**: `set_user_active`, `mark_ownership_revoked`; `list_users_with_sites` agora
  junta a **assinatura** (`sub_status`/`sub_plan`/`trial_ends_at`) e traz `verified_at`/
  `verification_method` por site (payload único, sem N+1).

### Bloco 2 — E-mails (`notifier/`)
- Templates `site_removed.html`, `account_deactivated.html`, `account_reactivated.html`
  (transacionais, dark, via `seguranca@klarim.net`).
- `KlarimMailer.send_site_removed` / `send_account_deactivated` / `send_account_reactivated`
  + `EMAIL_TYPES` (`site_removed`/`account_deactivated`/`account_reactivated`) → registrados
  no `email_log`.

### Bloco 3 — Frontend: página unificada (`web/`)
- **`UsuariosPage.jsx`** (island `client:only="react"`) + `pages/painel/usuarios.astro`:
  tabela (e-mail, nome, plano, status, sites, dono, criação, último login, ativo), filtros
  (plano/status/ativo) + busca por e-mail, **linha expansível** com sites (remover, com
  confirmação + checkbox de notificação), assinatura (read-only), e desativar/reativar
  conta. Botão global **"Remover sites bloqueados"** (dry-run → preview → confirmar).
- `adminApi.js`: `users`, `removeUserSite`, `deactivateUser`, `reactivateUser`.

### Bloco 4 — Rotas antigas
- Menu (`AdminShell.jsx`): removidos "Gestão de Clientes" e "Assinantes"; adicionado
  **"Usuários"** entre Visão geral e Alvos.
- `clientes.astro`, `assinantes.astro`, `monitorados.astro` → **redirect 301** para
  `/painel/usuarios`. Componentes antigos **mantidos** (não deletados).

### Bloco 5 — Termos + testes + MCP + docs
- **Termos** (`termos.astro`): seção "Domínios elegíveis para monitoramento" após "Uso do serviço".
- **MCP**: `get_user_accounts` já traz `is_active` (via `list_users_with_sites`); nova tool
  **`admin_remove_user_site`** (`user_id`, `target_id`, `notify`).
- **Testes** (`tests/test_accounts.py`): remove-site (revoga posse + notifica), remove-site
  404, deactivate/reactivate + notificação, **login desativado → 403**, ação admin sem JWT
  → 401, clean-blocked-sites dry-run (preview, sem alterar) + apply (remove só o bloqueado
  + notifica). `test_mcp_server` cobre as tools novas.
- **Docs**: `claude.md`, `docs/API.md`, `docs/SECURITY.md`.

---

## Regras invioláveis (respeitadas)
1. `contact_email` **nunca exposto** — endpoints públicos intocados.
2. Scanner/profiler intocados.
3. Rate limit Redis+fallback nos endpoints admin novos.
4. E-mails transacionais via `seguranca@klarim.net`, registrados no `email_log`.
5. Endpoints admin exigem JWT admin (prefixo `/admin`).

## Desvios / decisões
- **`/painel/assinantes` foi redirecionada** para `/painel/usuarios` conforme o card. A
  `AssinantesPage.jsx` (mudança de plano / extensão de trial) fica **inacessível pela
  navegação e por URL** — as ações de plano voltam na página unificada em **KL-44 P6**
  (decisão do card). ⚠️ Se precisar gerenciar planos antes disso, basta reverter o redirect
  de `assinantes.astro` temporariamente. As 9 contas atuais são todas free ex-trial.
- A página usa o endpoint existente **`/admin/clients`** (enriquecido) — não criei
  `/admin/users` novo, para evitar duplicação.
- Busca por e-mail é **client-side sem debounce** (volume pequeno; debounce só faria sentido
  para busca server-side).

## Testes
Suíte offline **verde** (`pytest`). Novos testes de KL-69 em `tests/test_accounts.py`.

## Pós-deploy (operacional)
Executar a limpeza retroativa no painel (**Usuários → Remover sites bloqueados**) ou via
`POST /admin/clean-blocked-sites` — remove os `gmail.com`/`python.org` de `user_sites` e
notifica os donos.

## Arquivos
- **Backend:** `api/main.py`, `discovery/store.py`, `notifier/email_client.py`,
  `notifier/templates/{site_removed,account_deactivated,account_reactivated}.html`,
  `mcp_server/tools/system.py`.
- **Frontend:** `web/src/components/admin/UsuariosPage.jsx` (novo), `AdminShell.jsx`,
  `web/src/lib/admin/adminApi.js`, `web/src/pages/painel/{usuarios,clientes,assinantes,
  monitorados}.astro`, `web/src/pages/termos.astro`.
- **Testes/Docs:** `tests/{test_accounts,test_mcp_server}.py`, `claude.md`,
  `docs/{API,SECURITY}.md`, este relatório.
