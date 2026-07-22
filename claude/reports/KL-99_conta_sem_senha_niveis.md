# KL-99 — Conta sem senha + 3 níveis de confiança + verificação de domínio

**Prioridade:** Highest · **Status:** ✅ **DEPLOYADO EM PRODUÇÃO** (2026-07-22, commit `9ea8927`,
CI **4/4 verde**). TTL do HMAC do alerta reduzido de 30 → **7 dias** antes do deploy (2ª iteração).

## Problema

A `/cadastrar` recebia ~360 visitas/semana e convertia **1,1%** (4 contas). O fluxo exigia
e-mail + senha + confirmar senha (3 campos) sem oferecer motivação — o usuário já vê tudo (score,
riscos, PDF) sem conta. Este card elimina a fricção com **2 fluxos sem senha** e estabelece
**3 níveis progressivos de confiança**.

## Modelo de níveis (`users.account_level`)

| Nível | Significado | Como se chega | O que desbloqueia |
|-------|-------------|---------------|-------------------|
| **1** | Conta SEM senha | Fluxo C (link do alerta), Fluxo D (inline), `/cadastrar` só e-mail | Ver dashboard, monitorar site |
| **2** | Conta COM senha | Definir senha (nível 1→2) ou signup com senha | Alterar conta, remover site, vincular técnico, upgrade/pagamento |
| **3** | Dono verificado por controle de domínio | Meta tag / arquivo HTML / DNS TXT | Editar perfil público, exibir selo Klarim |

**Decisão do dono (pergunta respondida no início):** todas as **19 contas existentes → nível 2**
(o `ADD COLUMN account_level INTEGER DEFAULT 2` faz o backfill numa tacada). Nível 3 só se ganha
pelo novo fluxo de verificação de domínio. `account_level` é um eixo NOVO e distinto do
`access_level` do KL-82 (que filtra a VISIBILIDADE do resultado do scan) — os dois coexistem.

## Backend

### Migrations (idempotentes, em `discovery/store.py::ensure_schema`)
- `users.account_level INTEGER DEFAULT 2` (backfill: toda conta existente → 2).
- `users.source VARCHAR(20) DEFAULT 'signup'` (origem da conta: `signup`|`hmac`|`inline`).
- `users.password_hash` passa a ser **NULLABLE** (`ALTER COLUMN … DROP NOT NULL`) — conta nível 1
  não tem senha.
- **Extensão** da tabela `ownership_verifications` do KL-68 (decisão do dono: estender, não criar
  nova): `+token VARCHAR(64)`, `+domain VARCHAR(253)`; `method` passa a aceitar
  `meta_tag`|`html_file`|`dns_txt` (TTL 7 dias, setado no INSERT — o fluxo de código do KL-68 segue
  com 30 min). O fluxo de código-ao-contact_email do KL-68 fica **intacto**.
- `targets.owner_verified BOOLEAN DEFAULT FALSE`.

`create_user` agora aceita `password_hash=None` (deriva `account_level`: sem senha → 1, com → 2) e
`source`. `_create_account_record` encaminha `source` **só quando != 'signup'** — assim os
FakeStore legados dos testes seguem funcionando sem alteração (zero regressão).

Novos métodos do store: `set_user_account_level` (GREATEST — nunca rebaixa),
`create_domain_verification`, `get_pending_domain_verification`, `set_target_owner_verified`,
`delete_unconfirmed_passwordless_accounts`.

### Fluxo C — conta automática via HMAC (`GET /alert-access`)
Quem clica no link HMAC do alerta JÁ provou posse do e-mail. O handler agora:
- e-mail **tem conta** → **loga automaticamente** (cookie de sessão de usuário);
- e-mail **não tem conta** → cria conta **sem senha** (nível 1, `source='hmac'`,
  `email_confirmed=true`) e loga; **rate limit 5 auto-criações/h por IP**.
- **NÃO ativa monitoramento** — o consentimento é o botão "Sim, monitorar" no resultado.
- A sessão de alerta view-only (cookie 24h) e o registro de conversão continuam (fallback +
  analytics). Se a auto-criação estourar o rate limit, cai na sessão view-only (degradação suave).

### Fluxo D — `POST /account/signup-inline` `{email, domain}`
Cadastro orgânico sem senha no resultado do scan:
- cria conta nível 1 (`source='inline'`, `email_confirmed=false`);
- **vincula o domínio como site PENDENTE** (`link_user_site`, `is_owner=false`) — **sem** vigílias;
- envia o e-mail de confirmação (link POST-only anti pre-fetch do KL-82 S2);
- retorna `{status: confirmation_sent}` (NÃO loga) ou `{status: already_exists}`; blocklist de
  descartáveis + **rate limit 3/h por IP**.
- **A confirmação do e-mail ativa o monitoramento** (`_activate_monitoring_on_confirm` cria as
  vigílias dos sites vinculados) — assim alertas só saem para e-mails confirmados (reputação).
- A conta sem senha é **logada e vai ao dashboard** ao confirmar (senão ficaria presa — não há
  senha para o `/entrar`).

### `POST /account/signup` — senha opcional (nível 1 vs 2)
Sem senha → nível 1; com senha → nível 2. E-mail de confirmação nos dois casos.

### `POST /account/set-password` (nível 1 → 2)
Requer sessão. 400 se já tem senha (não sobrescreve), 422 se não conferem ou < 8 chars. Diferente
do `/account/change-password` (que exige a senha atual — a conta nível 1 não tem uma).

### `@require_level(n)` — gate de ações sensíveis
Helper `_require_level(user, n)` → 403 com corpo **estruturado** `{error:"insufficient_level",
required_level, current_level}` (o frontend decide o prompt certo). Gates aplicados:
- **nível ≥ 2:** `PUT /account/me`, `DELETE /account/sites/{id}`, `POST /account/technician/invite`,
  `POST /account/upgrade`.
- **nível ≥ 3:** `PUT /account/profile-confirm` (editar perfil público).

### Verificação de domínio (nível 2 → 3)
- `POST /account/sites/{id}/verify/start {method}` → gera `secrets.token_urlsafe(32)` + instruções.
- `POST /account/sites/{id}/verify/check` → confere a prova; verificado → dono + `account_level=3` +
  `targets.owner_verified`. Rate limit **10/h por IP**. Métodos:
  - **meta_tag:** `<meta name="klarim-verify" content="kl-{token}">` na home.
  - **html_file:** `/klarim-verify-{token}.html` com `klarim-verification={token}`.
  - **dns_txt:** registro TXT `klarim-verify={token}`.
  - Fetch honesto (UA do Klarim, timeout 10s); o corpo NUNCA volta ao usuário (só o boolean do
    match) → sem exfiltração. DNS via dnspython em thread.

### Cleanup
A limpeza do KL-82 (`delete_unconfirmed_inactive_accounts`, no `trial_worker`) exige ausência de
site — mas o Fluxo D vincula um site PENDENTE. Adicionei
`delete_unconfirmed_passwordless_accounts` (nível 1 + sem senha + não confirmada + >30d + sem
re-login) rodando junto, no mesmo ciclo diário. FK CASCADE limpa `user_sites`/verificações.

## Frontend (Astro 7 + React islands)

### Resultado do scan — 2 variantes por SESSÃO (não por dispositivo)
`ScanResultDetail.jsx` escolhe o bloco lateral pelo nível do KL-82 (que já reflete a sessão):
- **logado (`confirmed`, inclui quem foi auto-logado no Fluxo C)** → `MonitorConsent.jsx`
  ("Sim, monitorar" → `POST /account/sites`, sem campo de e-mail);
- **logado não confirmado (`unconfirmed`)** → banner "confirme o e-mail";
- **não logado (`anonymous`/`alert_session`)** → `InlineSignup.jsx` (só e-mail →
  `POST /account/signup-inline`).

Componentes novos: `web/src/components/scan/{InlineSignup,MonitorConsent}.jsx`. Copy pura em
`web/src/lib/scanView.js` (`inlineSignupCopy`, `monitorConsentCopy`, `MONITOR_BENEFITS`) — a
`ctaCopy` obsoleta (e o `AccountCTA`/`AlertSignup` de e-mail+senha) foram removidos.

### `/cadastrar` simplificado
`SignupForm.jsx` reescrito: **1 campo (e-mail)** → `POST /account/signup` sem senha → nível 1 +
confirmação. Sem senha/confirmar-senha. Preserva `url/role/invite/plan` (KL-68/KL-44).

### Prompts de nível no dashboard (`web/src/components/dashboard-v2/LevelPrompt.jsx`)
- `useLevelGate(user)` intercepta ações: se falta nível, abre o modal certo e **executa a ação
  original automaticamente** ao concluir.
- `SetPasswordModal` (nível 1→2, "Defina uma senha para continuar").
- `VerifyDomainModal` (nível 2→3, 3 métodos com instruções + "Verificar agora").
- `LevelBadge` (card 3c): "Conta básica" / "Conta verificada" / "Dono verificado".
- Fios: remover site (L2), vincular técnico (L2), selo (L3, `SealSection` gated), indicador de nível.

Testes: `web/src/lib/scanView.test.js` atualizado (copy nova) — **98 `node --test` verdes**.

## Testes e validação

- **Backend pytest: 1555 passed, 1 skipped** (novo `tests/test_kl99_levels.py`: **27 testes** —
  signup sem senha, Fluxo C/D, set-password, require_level, verificação por método, `_check_domain_
  control` puro, segurança sem vazamento). Ajuste em `tests/test_kl44_p6_payment.py` (FakeStore
  ganhou o método de cleanup novo).
- **Frontend: 98 `node --test` + `npm run build` verde.**
- **Stack dev local (`docker-compose.dev.yml`)** — migrations aplicaram limpo no Postgres 16 real,
  seed OK, e **todos os fluxos validados via HTTP**: passwordless signup→nível 1 auto-logado; 403
  estruturado no gate; set-password→nível 2; signup-inline `confirmation_sent`/`already_exists` +
  site pendente sem vigília; alert-access auto-cria+loga (source hmac); verify/start devolve o
  snippet correto, verify/check → `not_found` sem prova; profile-confirm 403 no nível 2 e 200 no
  nível 3 (dono3). Contas no banco com `account_level`/`source`/`password_hash` corretos por tipo.
- **Seed dev** (`scripts/seed_dev.py`): `nivel1@teste.com` (sem senha, nível 1, source hmac);
  `dono3@teste.com`/`dev123456` (dono verificado nível 3 + site próprio + `ownership_verification`
  verified); 1 verificação PENDENTE (dono@exemplo no 1º site) para testar o fluxo no UI.

## Security review (regra inviolável de 2026-07-15)

- **Superfície nova = conta sem senha.** Mitigação: nível 1 **não** faz ações sensíveis (troca de
  conta, remover site, técnico, upgrade, editar perfil, selo) — 403 até definir senha. Conta sem
  senha só nasce com prova de posse do e-mail (HMAC do alerta) ou vira útil só após confirmar.
- **⚠️ Fluxo C — auto-login de conta EXISTENTE pelo link do alerta:** o link HMAC agora loga em
  sessão COMPLETA, inclusive contas com senha (nível 2/3), sem senha. Racional: posse do e-mail ==
  posse do reset de senha (o sistema já permite reset por e-mail); magic-link é padrão de indústria.
  **Mitigação aplicada (2ª iteração):** o TTL do `alert_access` foi **reduzido de 30 → 7 dias**
  (`_ALERT_ACCESS_TTL` em `notifier/email_client.py` e `api/main.py`, em sincronia) para limitar a
  janela de link vazado / inbox compartilhado. Melhoria futura opcional: re-auth por senha em ações
  sensíveis de sessões auto-logadas.
- **Verificação de domínio (SSRF):** o fetch vai a `https://{domain}/…` onde `domain` vem de
  `targets` (site público já escaneado), não de input cru; o corpo **nunca** volta ao usuário (só o
  match) → sem exfiltração; UA honesto, timeout 10s, ≤3 redirects; token 256-bit inadivinhável;
  escopo (user, target) com o vínculo exigido.
- **set-password:** recusa se já há senha (não rouba conta alheia); a sessão já prova identidade.
- **Rate limits (via `CF-Connecting-IP`):** signup 3/h+5/dia · signup-inline 3/h · alert
  auto-create 5/h · verify-check 10/h · alert-access view 30/h.
- **PII:** `contact_email`/cnpj/whatsapp nunca em nenhum endpoint novo; signup-inline devolve só o
  e-mail **mascarado**. Corpo do 403 expõe só o `current_level` do próprio caller (não sensível).
- **Reputação de e-mail:** monitoramento só ativa na CONFIRMAÇÃO (Fluxo D) — nada de alerta para
  e-mail não confirmado.

## Decisões e gotchas

- Estender `ownership_verifications` (não criar tabela nova) — evita colisão de nome e reusa
  `mark_ownership_verified`/`status`/`expires_at`. TTL do desafio de domínio (7d) é setado no
  INSERT, não no DEFAULT do schema (que segue 30 min p/ o KL-68).
- Layout do resultado: mantive o hero + coluna-lateral do KL-89 (tuning de conversão já validado)
  em vez do "score-à-esquerda / CTA-à-direita" literal do card — a substância (variantes
  passwordless + detecção por sessão) foi entregue; deixo a critério do dono ajustar o layout.
- Wiring do gate de nível: cobri as ações que o `DashboardV2` controla diretamente (remover,
  técnico, selo) + o `LevelBadge` proativo. `PlanSection` (upgrade) e `AccountSettings` (/conta,
  página separada) **não** têm o auto-modal — o backend já os protege (403) e o `LevelBadge`
  antecipa a definição de senha. Melhoria futura opcional.
- Docker exec ficou intermitente no host de dev ("failed to change user ID") — validei tudo por
  HTTP; não afeta o código.

## Fix crítico pós-deploy (2026-07-22, 2ª rodada — 4 bugs)

Bugs relatados em produção + correções:

- **Bug 1 — HMAC não deve criar conta automaticamente.** Revertido: `GET /alert-access` volta ao
  comportamento KL-82 (só **sessão de visualização** `alert_session`, view-only; **NÃO** cria conta
  nem loga). A criação de conta foi movida para o **consentimento**: novo `POST /account/monitor-
  from-alert` — clicar "Sim, monitorar" cria a conta SEM senha (nível 1, hmac, confirmada) + vincula
  o site + **ativa o monitoramento** (vigílias) + loga; e-mail já com conta → `{existing_account}`
  (não auto-loga conta com senha). `MonitorConsent.jsx` ganhou `mode="alert"` (cria) vs
  `mode="account"` (usuário logado adiciona site). Rate limit 5/h/IP.
- **Bug 2 — conta sem senha ficava presa.** Novo **magic link**: `POST /account/magic-link {email}`
  (token HMAC TTL **1h**, RL 3/h/e-mail + 10/h/IP; e-mail ausente → `{not_found}`) +
  `GET /account/magic-access?token=` (valida → sessão real → `/dashboard`; inválido/expirado →
  `/entrar?magic=expired`). `send_magic_link` no mailer (texto puro, transacional). `/entrar` ganhou
  **"Enviar link de acesso"** + trata `?magic=expired`; **"Esqueci minha senha"** (`/recuperar-senha`)
  já existia — link mantido.
- **Bug 3+4 — layout.** `ScanResultDetail` reorganizado: **grid `md:grid-cols-2`** com score
  **COMPACTO** (anel `h-28`, score `text-4xl`) à esquerda + CTA à direita, **ambos acima do fold**
  no desktop/tablet; empilham no mobile (<768px: 1 coluna, score compacto → CTA); relatório completo
  abaixo, largura total. Validado no navegador (desktop 2 colunas confirmado, zero erro no console).

**Testes:** `test_kl99_levels.py` atualizado (alert-access não cria conta; monitor-from-alert;
magic-link/magic-access) → **1562 backend + 98 frontend verdes**. Validado no stack dev por HTTP:
alert-access só seta cookie de alerta (sem sessão/sem conta); monitor-from-alert cria+loga+monitora;
magic-link sent/not_found/rate-limit; magic-access loga → /dashboard.

## Deploy em produção (2026-07-22)

- **Commit** `9ea8927` na `main` → GitHub Actions **4/4 verde** (Build web · Nginx config check ·
  Test · Deploy to GCP VM). As migrations do KL-99 rodaram no boot da API (`ensure_schema`).
- **Validação pós-deploy (klarim.net):** `/api/health` 200 `{ok}` · `/` 200 · **`/cadastrar` 200
  com 1 campo (sem senha)** · `/dashboard` 302 · workers **discovery/alert/scan/rescan alive** +
  deps (postgres/redis/ct_logs/resend/abacatepay) ok · **score klarim.net = 100 🟢 (0 FAIL)**.
- **Endpoints novos live (probes sem efeito colateral):** `signup-inline` com e-mail descartável →
  400; `set-password` / `verify/start` sem auth → 401.
- **TTL do HMAC = 7 dias** confirmado (token `exp = now + 604800s`).
- **Redis:** **não** precisou flush — `dashboard-summary` não é cacheado (KL-90) e nenhum
  check/scoring mudou (`scan:*` intacto).
- **Jira:** não transicionei o KL-99 para Done automaticamente — a critério do dono.
