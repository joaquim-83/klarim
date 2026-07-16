# KL-68 — Fluxo de reivindicação de site: UX + verificação de propriedade

**Card:** KL-68 · **Prioridade:** Highest · **Data:** 2026-07-16

Corrige o fluxo de reivindicação a partir do perfil público e adiciona verificação de
propriedade em **2 tiers** + **guarda de domínios públicos/institucionais**.

---

## O que foi implementado (por bloco)

### Bloco 1 — Banco (`discovery/store.py`, no `_SCHEMA` idempotente)
- `user_sites` ganhou `verified_at TIMESTAMPTZ` + `verification_method VARCHAR(20)`
  (`auto_email` | `code_verification` | NULL).
- Nova tabela **`ownership_verifications`** (user_id, target_id, method, code, attempts,
  status, TTL 30 min) + 2 índices.
- Métodos novos: `site_has_owner`, `mark_site_verified`, `create_ownership_verification`,
  `get_pending_ownership_verification`, `bump_ownership_attempt`, `mark_ownership_verified`,
  `get_target_owner`, `revoke_ownership`, `ownership_stats`, `list_user_sites_min`,
  `remove_user_sites_by_ids`.

### Bloco 2 — Backend (`api/`)
- **`api/domain_guard.py` (novo, puro):** `BLOCKED_DOMAINS` (~90) + `BLOCKED_PATTERNS`
  (.gov.br/.edu.br/…), `is_blocked_domain(domain) → (bool, reason)`, `get_block_message`.
  Normaliza `www.`/URL/subdomínio.
- **Signup/Login com claim** (`_process_claim`, compartilhado): aplica o domain guard,
  respeita o limite do plano, vincula o site e faz **auto-verificação Tier 1** (e-mail ==
  `contact_email`, first-come-first-served). Retorna `claim: {site_added, is_owner,
  ownership_verification_available, blocked_domain, domain}`. `AccountLoginBody` ganhou `url`.
- **Endpoints de ownership** (namespace `/account/*`, JWT de usuário):
  `POST /account/ownership/request-verification` (código ao `contact_email`, rate limit
  5/h/IP, retorna só `email_hint` mascarado), `POST /account/ownership/verify` (3 tentativas,
  TTL 30 min, constant-time), `GET /account/ownership/status`.
- **`POST /account/sites`** ganhou o domain guard (**422** para domínio bloqueado) + Tier 1.
- **`GET /public/profile/{domain}`** ganhou `owner_verified`, `claimable`, `block_message`
  (nunca expõe quem é o dono nem o e-mail).
- **Admin:** `GET /targets/{id}` anexa `owner`; `POST /targets/{id}/revoke-ownership`;
  `GET /admin/ownership-stats`; `POST /admin/clean-blocked-sites?dry_run=`.
- **E-mail** (`notifier`): template `ownership_verification.html` +
  `KlarimMailer.send_ownership_verification` (transacional via `seguranca@klarim.net`,
  registrado no `email_log`, `email_type=ownership_verification`).

### Bloco 3 — Perfil público (`web/src/pages/site/[domain].astro`)
- CTA **movido para acima do fold** (logo após o score) e trocado por uma **ilha React
  `ClaimSite`** (`client:load`) com 4 estados: deslogado → cadastrar; logado sem
  monitorar → monitorar; logado monitorando sem dono → verificar; logado dono → painel.
  Domínio bloqueado → mensagem educativa + "Verificar meu site → /scan".
- Badge **"✓ Dono verificado"** perto do score quando `owner_verified`.

### Bloco 4 — Cadastro/Login/Recuperar
- **Card de contexto** `ClaimContext.astro` (SSR: domínio + score + semáforo) em
  `/cadastrar` e `/entrar` quando há `?url=`.
- **Preservação de `?url=`/`?email=`** em todos os links de navegação (Entrar ↔ Cadastrar
  ↔ Recuperar). Login passou a aceitar/enviar `url` (claim).
- **Redirect pós-auth** com toast no Dashboard: `?claimed=` (virou dono) / `?added=`
  (só monitorou) / `?blocked=1` (conta criada, domínio público).

### Bloco 5 — Verificação (`OwnershipVerification.jsx`)
- Ilha reutilizável (solicitar código → digitar → verificar, com tentativas restantes),
  usada no perfil público (`ClaimSite`) e no dashboard (`SiteDetail` → `OwnershipSection`).

### Bloco 6 — Admin + MCP + testes + docs
- **Admin:** AlvoDetalhe mostra o dono + botão **Revogar propriedade**; Clientes ganhou
  **"Remover sites bloqueados"** (dry-run + confirmação).
- **MCP:** nova tool `get_ownership_stats`; `get_user_accounts` já traz `is_owner`.
- **Testes:** `tests/test_accounts.py` — domain guard, auto-verify Tier 1 (match / não
  match), signup domínio bloqueado, add-site 422, fluxo request→verify, código sem
  contato (400), 3 erros → travado, 2º usuário → 409, status. FakeStores de
  `test_kl51_f4_profiles`/`test_kl56_admin_inbox` ganharam `site_has_owner` (drift).
- **Docs:** `claude.md`, `docs/API.md`, `docs/ARCHITECTURE.md`, `docs/SECURITY.md`.

---

## Regras invioláveis (todas respeitadas)

1. **`contact_email` NUNCA exposto** — comparação Tier 1 server-side; Tier 2 só devolve
   `email_hint` mascarado.
2. **Scanner intocado** (passivo). **Profiler/IA intocado.**
3. **Rate limit Redis + fallback** em todas as rotas novas.
4. **Revisão de segurança:** first-come (anti-sequestro, 409), código CSPRNG 6 díg./TTL
   30 min/3 tentativas/constant-time, domain guard, e-mail de verificação **transacional**.

## Desvios do plano (decisões)

- **Pre-fill do e-mail com `contact_email` (Bloco 3, Estado 1): NÃO implementado** — isso
  violaria a regra inviolável #1 (expor `contact_email` no SSR público). A auto-verificação
  Tier 1 continua funcionando (comparação **server-side**); o link de claim leva só `?url=`.
  O pre-fill de e-mail permanece só para o e-mail já verificado no scan (`?email=`, KL-25).
- **Paths** dos endpoints de ownership sob **`/account/ownership/*`** (não `/api/ownership/*`),
  para reusar o namespace de JWT de usuário e não colidir com o middleware admin.
- **CTA secundário no fim da página:** removido em favor do único CTA (ilha) acima do fold,
  para não duplicar a ilha/fetch e evitar inconsistência de estado.

## Testes

Suíte offline completa **verde** (`pytest`). Novos testes de KL-68 em `tests/test_accounts.py`.

## Arquivos

- **Backend:** `api/domain_guard.py` (novo), `api/main.py`, `discovery/store.py`,
  `notifier/email_client.py`, `notifier/templates/ownership_verification.html` (novo),
  `mcp_server/tools/system.py`.
- **Frontend:** `web/src/components/account/{ClaimSite,OwnershipVerification,ClaimContext}` (novos),
  `SignupForm/LoginForm/ForgotForm/Dashboard/SiteDetail.jsx`, `web/src/pages/{site/[domain],
  cadastrar,entrar,recuperar-senha}.astro`, `web/src/lib/admin/adminApi.js`,
  `web/src/components/admin/{AlvoDetalhePage,ClientesPage}.jsx`.
- **Testes/Docs:** `tests/{test_accounts,test_kl51_f4_profiles,test_kl56_admin_inbox}.py`,
  `claude.md`, `docs/{API,ARCHITECTURE,SECURITY}.md`, este relatório.
