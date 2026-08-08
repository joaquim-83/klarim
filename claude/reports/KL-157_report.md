# KL-157 — Fix crítico: CTA /security-gate e /cadastrar não detectam sessão ativa

## Diagnóstico (investigação ANTES do fix)

### Como o auth-state é detectado nas páginas Astro hoje
- **Cookie de sessão:** `klarim_session`, **HttpOnly** → o JS do cliente NÃO lê o valor.
- **Server-side (SSR):** o `src/middleware.js` lê o cookie e valida em `${KLARIM_API_URL}/account/me`
  (`Authorization: Bearer <token>`), populando `Astro.locals.user`. **Só roda para `/dashboard/*`.**
- **Client-side:** o `web/public/header.js` chama `/api/account/me` (cookie same-origin) para revelar o
  menu logado (avatar). As ilhas React (`GateLandingCTA`, `AccountSettings`) fazem o próprio `fetch`.

### Por que NÃO funcionava
1. **`/cadastrar` (SSR) NUNCA checava sessão** → qualquer usuário LOGADO via o formulário de cadastro.
   Root cause do "vê formulário de signup".
2. **Os CTAs da seção Planos da `/security-gate` são `<a>` estáticos** para `/cadastrar?type=developer`
   (sem auth) → logado clicava → caía no form (por causa do item 1).
3. **`GateLandingCTA` mostrava o link de cadastro DURANTE o loading** (`state==='loading' ||
   'logged_out'` renderizavam o mesmo `<a>`). Antes do `fetch` resolver, o usuário logado via/clicava
   "Começar grátis" → cadastro. Contributing cause.
4. **`/security-gate` é CDN-cacheada** (`Cache-Control: max-age=300` no nginx) → detecção server-side ali
   é NÃO confiável (HTML anônimo servido a logado). Por isso o herói fica no CLIENTE (ilha que re-checa
   após hydration). `/cadastrar` e `/entrar` são **`no-store`** → SSR ali é confiável.

Os testes unitários passavam porque testavam `ctaState`/`ctaLabel` (lógica pura) com dados mockados — o
bug era na INTEGRAÇÃO (cadastro sem guard + CTA de loading + CTAs estáticos), não na lógica.

## Fixes

### Fix 2 (PRIMÁRIO, catch-all) — `/cadastrar` redireciona o logado
`web/src/lib/serverAuth.js` (novo): `fetchSessionUser(cookies)` (lê `klarim_session` + `GET /account/me`)
e `loggedInRedirect(isDev, fallback)`. `cadastrar.astro` (SSR, **no-store** → confiável): se há sessão →
`Astro.redirect('/dashboard/gate'` se `type=developer`, senão o `redirect`/`/dashboard`). **Nenhum usuário
logado vê o form**, independente de qual CTA clicou (herói, planos, etc.).

### Fix 1 — herói auth-aware sem flash
`GateLandingCTA.jsx`: o estado `loading` agora mostra um **skeleton "Carregando…"** (`pointer-events-none`);
só `logged_out` (após o fetch confirmar que NÃO há sessão) mostra o link de cadastro (rótulo **"Criar
conta →"**). Logado nunca vê o CTA de anônimo. Mantido no cliente (a página é cacheada).

### Fix 3 — seção Gate no `/dashboard/conta`
A `GateSection` do `AccountSettings.jsx` (KL-156) já busca `/api/account/gate/status` e renderiza. Validada
no browser (aparece com "Plano … (Security Gate) · Abrir dashboard →").

### Fix 4 — login volta ao contexto certo
O `LoginForm` já honra `?redirect=` (`nextUrl`→`window.location.href`). O `SignupForm` agora inclui
`redirect=/dashboard/gate` no link "Faça login" quando `source=security-gate` (fluxo dev).

## Testes
**Backend** (`test_kl153_backend.py`, +1): `GET /account/gate/status` sem sessão → **401** (com sessão →
200 já coberto por `test_status_shape`). **Frontend** (`nav.test.js`, +1): `loggedInRedirect` (dev →
`/dashboard/gate`; senão fallback). `ctaState`/`ctaLabel` (owner→activate · dev→dashboard · anon→signup)
já cobertos. **2287 pytest · 192 node --test pass · build OK.**

## Validação no browser (docker-compose.dev.yml) — OBRIGATÓRIA
Sessão simulada **sem digitar senha**: JWT do seed `dono@exemplo.com.br` gerado DENTRO do container `api`
(`auth_users.create_user_token`, mesmo `JWT_SECRET`) e setado como cookie `klarim_session`.
- **Item 7** — `/security-gate` logado → herói **"Abrir dashboard →"** (NÃO "Criar conta"). ✅
- **Item 8** — `/cadastrar?type=developer` logado → **redireciona p/ `/dashboard/gate`**, sem form. ✅
- **Item 9** — `/dashboard/conta` logado → seção **Security Gate** com "Plano … (Security Gate)". ✅
- **Deslogado** (cookie limpo): `/security-gate` mostra "Criar conta →"; `/cadastrar` mostra o form (200).
- **Zero erro no console** em todas.

## Regras
Backend de scan/engine/rate limiting **inalterados**. NUNCA "Criar conta" para logado (validado). Docs:
`claude.md` (§ auth-state nas páginas Astro).

## Arquivos
**Novo:** `web/src/lib/serverAuth.js`. **Editados:** `web/src/pages/cadastrar.astro`,
`web/src/components/security-gate/GateLandingCTA.jsx`, `web/src/components/account/SignupForm.jsx`,
`tests/test_kl153_backend.py`, `web/src/lib/nav.test.js`, `claude.md`.
