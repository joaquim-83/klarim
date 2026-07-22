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

### Causa
- Build local **inclui** `dist/client/header.js` corretamente. Via SSH, o **container astro em
  produção serve `/header.js` como `text/javascript` 200 no ORIGIN**. Ou seja: o origin está certo.
- O problema é **só o cache do Cloudflare**: durante a janela do deploy (o rebuild na VM leva 10-50
  min; o `header.js` é um arquivo NOVO), uma requisição a `/header.js` pegou o HTML de fallback do
  SSR (o astro ainda não tinha o arquivo) → o **Cloudflare cacheou esse `text/html`** (`cf-cache-status:
  HIT`, `max-age=14400` = 4h) → passou a servir HTML para todos.
- `theme.js`/`track.js` **não** sofrem isso porque (a) já existiam no origin há semanas e (b) são
  referenciados com **versão** (`?v=2`, `?v=65`) — o padrão de cache-busting do projeto. Meus
  `header.js`/`planos-auth.js` estavam **sem versão**.

### Fix
- **`Header.astro`**: `/header.js` → **`/header.js?v=2`** (chave de cache nova no Cloudflare → busca
  do origin, que serve JS correto).
- **`planos.astro`**: `/planos-auth.js` → **`/planos-auth.js?v=2`**.
- Convenção documentada no código: **bump da versão a cada alteração** desses arquivos (como o theme.js).

> Não dá para purgar o cache do Cloudflare daqui (sem token da API). A versão nova sidestepa o cache
> envenenado — a URL `?v=2` nunca foi cacheada, então é buscada fresca do origin (já correto).

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

## Validação
- Build ✅ · pytest (CI) ✅ · fluxo do técnico end-to-end ✅ (curl)
- Bug 1 (cache) valida-se **pós-deploy** em produção (a URL `?v=2` é fresca no Cloudflare).

## Regras
- ✅ Deploy imediato · ✅ relatório PT-BR · ✅ e-mail do dono nunca exposto cru.
