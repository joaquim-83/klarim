# Fix urgente — persistência de login (header) + fluxo do técnico

**Data:** 2026-07-22 · **Prioridade:** CRÍTICA (produção, todos os usuários)
**Contexto:** pós-swap do Dashboard v2, o header mostrava "Entrar" em todas as páginas mesmo logado.

---

## Bug 1 — Login não persiste no header (RAIZ ENCONTRADA)

### Não era cookie nem auth — era **cache do Cloudflare servindo o script errado**.

Investigação (descartando hipóteses):
- Cookie `klarim_session`: `path=/; secure; samesite=lax` → correto (enviado em todas as rotas). ✅
- `/api/account/me`: `cf-cache-status: DYNAMIC` (não cacheado) → 401 sem auth, correto. ✅
- Landing: `no-store` (HTML sempre fresco) e contém o markup novo do header + `/header.js`. ✅
- **`/header.js` em produção → `content-type: text/html`** (o HTML de fallback do SSR), **não** o
  meu JavaScript. Com `X-Content-Type-Options: nosniff`, o browser **bloqueia a execução** do
  script (MIME text/html + nosniff) → o `header.js` nunca roda → o toggle de login nunca acontece
  → o header fica em "Entrar" **em todas as páginas** (inclusive a que "parece logada").

### Causa RAIZ (2 camadas)
1. **nginx — o culpado principal.** O `web` (nginx) serve o root `/usr/share/nginx/html` (build **Vite**
   do /painel) e tem um **allowlist explícito** de paths que são proxiados ao container `astro`
   (`https.conf.template`): a regex inclui `favicon.svg|robots.txt|track.js|theme.js` — mas **NÃO**
   `header.js` nem `planos-auth.js`. Então `/header.js` cai no `location / { try_files $uri /index.html }`
   → não existe no dir do Vite → serve o **`index.html` do Vite (text/html)**. `theme.js`/`track.js`
   funcionam porque **estão** no allowlist. (Confirmado: o astro serve o JS certo; o nginx é que não
   proxiava.) Com `nosniff` + text/html, o browser bloqueia a execução → `header.js` nunca roda.
2. **Cloudflare** cacheou esse `text/html` de `/header.js` (`cf-cache-status: HIT`, 4h), amplificando.

### Fix
- **nginx (raiz):** adicionei `header\.js|planos-auth\.js` ao allowlist do astro em
  **`https.conf.template`** e **`http.conf`** (mesmo mecanismo do theme.js). `nginx -t` ✅ nos dois.
- **`Header.astro`/`planos.astro`:** `?v=2` nos scripts — chave de cache nova no Cloudflare (o
  `/header.js` sem versão segue com o HTML envenenado por ~4h; a URL `?v=2` é fresca → busca do
  origin, agora corretamente proxiado ao astro → JS). Convenção: bump da versão a cada alteração.

> **Fragilidade anotada:** todo novo `.js` público na raiz precisa entrar no allowlist do nginx
> (como theme.js/track.js/header.js). Um follow-up seria uma regra `location ~ ^/[\w-]+\.js$` →
> astro (anchorada na raiz p/ não colidir com `/assets/` do Vite).

---

## Bug 2 — Técnico convidado não vê os sites (RAIZ ENCONTRADA)

Além do sintoma do header (= Bug 1), havia 2 gaps reais (pré-existentes ao KL-90):

1. **Backend:** `auto_link_technician_by_email` (que ativa convites pendentes) só rodava no
   **SIGNUP**. Uma conta que **já existia** ao ser convidada ficava com o vínculo **`pending` para
   sempre** — o e-mail de convite leva ao **laudo**, não a uma página de aceite (que não existe).
   → **Fix:** o endpoint `GET /account/technician/clients` agora chama `auto_link_technician_by_email`
   (idempotente, best-effort) **antes** de listar → o técnico vê os sites assim que abre o dashboard.
2. **Frontend:** `TechnicianClients` só renderizava se `user.role` fosse technician. Mas um **dono**
   convidado como técnico mantém `role='owner'` → a seção ficava escondida mesmo com vínculos ativos.
   → **Fix:** a seção agora aparece se **há clientes** (ou se é técnico declarado). Passa `isTech` e
   se auto-esconde só para o dono comum sem vínculos.

### Validado end-to-end (curl, dev)
```
dono convida tecnico@agencia.com.br → invited: True (pending)
tecnico login → GET /technician/clients → auto-linka → 1 cliente:
  hotel-exemplo.com.br 83 | dono: d***o@exemplo.com.br | status: active
```
E-mail do dono **mascarado** (regra inviolável). O badge "🔧 Profissional de TI" fica na própria seção.

---

## Arquivos
- `web/src/components/Header.astro` — `/header.js?v=2`
- `web/src/pages/planos.astro` — `/planos-auth.js?v=2`
- `api/main.py` — auto-link no `GET /account/technician/clients`
- `web/src/components/dashboard-v2/TechnicianClients.jsx` — prop `isTech` + self-hide por vínculos
- `web/src/components/dashboard-v2/DashboardV2.jsx` — sempre renderiza `TechnicianClients`

## Resolução final (produção)
- Deploys: `1d8730f` (?v=2 + técnico) → `eaf736c` (**nginx allowlist**, a raiz) → `9090d06` (**?v=3**).
  O `?v=2` do header.js ficou envenenado no Cloudflare porque foi requisitado DURANTE o diagnóstico
  (antes do fix do nginx subir) → precisou de uma chave nova (`?v=3`). Lição: **não requisitar a
  URL versionada antes do fix do origin estar no ar**, senão o CF cacheia o erro naquela chave.
- **Prova em produção** (network trace em `/planos`, deslogado):
  - `/header.js?v=3` → **200 text/javascript** (BYPASS) e **disparou `GET /api/account/me` (401)** →
    o script EXECUTOU (antes era bloqueado por MIME/nosniff e nunca rodava).
  - `/planos-auth.js?v=2` → 200 JS e disparou `GET /api/account/subscription` (401) → executou.
  - Zero erro de MIME/CSP no console. Logado, o `/account/me` volta 200 → o avatar aparece → **login persiste**.
- Fluxo do técnico validado end-to-end (curl, dev): convite → tecnico abre dashboard → auto-link → vê o site.

## Validação
- CI 4/4 verde (3 deploys) · workers 4/4 alive · **score klarim.net = 100 🟢** · públicas 200 · health ok.

## Regras
- ✅ Deploy imediato · ✅ relatório PT-BR · ✅ e-mail do dono nunca exposto cru.
