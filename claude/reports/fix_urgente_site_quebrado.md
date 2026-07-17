# FIX URGENTE — Site quebrado após deploy P6

**Data:** 2026-07-16 · **Prioridade:** Emergência · **Sem card Jira**
**Commit:** `2b33782` · **Status:** ✅ Corrigido, deploy verificado.

Os containers estavam frescos (P6 deploy, 11 min) — **não** era build velho/cache. O
diagnóstico isolou 3 causas-raiz distintas (a hipótese do card sobre `/account/me` era um
red herring).

---

## Diagnóstico (método)

1. `curl` público: `/api/account/me` → 401 (correto sem token), `/api/health` → 200,
   homepage OK, mas **`/planos` → título "O alarme que toca antes do ataque"** (página
   errada), `/dashboard` → 302 (correto sem cookie).
2. SSH na VM: **todos os containers UP e recriados há 11 min** (build novo, não velho).
3. `curl` do Astro direto (`127.0.0.1:4321/planos`) → **"Planos e preços — Klarim" ✅** —
   o Astro serve a página nova corretamente.
4. `curl` do nginx direto (HTTPS, bypass Cloudflare) → **página errada** — logo o problema
   está no **nginx**, não no Astro nem no Cloudflare (`cf-cache-status: DYNAMIC`, sem cache).
5. `grep` do título errado → `frontend/index.html` (a **SPA Vite**).

---

## Problema 1 (raiz) — Dashboard tela preta

**NÃO era o `/account/me` 401** (esse é o comportamento correto sem token; o endpoint existe
em `api/main.py:784`). A causa real: no P6 eu adicionei ao `Dashboard.jsx`:

```js
const [planUpgradeParam] = useState(() => new URLSearchParams(window.location.search)...)
```

O inicializador do `useState` roda **também no SSR** (uma ilha `client:load` é renderizada
no servidor antes de hidratar), onde **`window` não existe** → `ReferenceError: window is
not defined` → a ilha Dashboard quebra → **tela preta**. (O código antigo usava `window` só
dentro de `useEffect`, que é client-only.)

**Fix:** guarda `typeof window !== 'undefined'` (mesmo padrão já usado em `ScanFlow.jsx`).

## Problema 2 — /planos abre a SPA Vite

O nginx roteia as páginas públicas por uma **allowlist explícita** de caminhos Astro
(`location ~ ^/(termos|…|dashboard|laudo|…)`). O `/planos` (novo no P6) **não estava na
lista** → caía no `location /` (fallback `try_files $uri /index.html` da SPA **Vite**,
`frontend/index.html`, título "O alarme que toca antes do ataque").

**Fix:** adicionei `planos` à allowlist do Astro no `https.conf.template`.

## Problema 3 — Homepage antiga intermitente

Sintoma derivado do Problema 2: `/planos` (e navegações que passavam pela SPA Vite)
mostravam a landing antiga. A homepage `/` sempre foi servida pelo Astro (`location = /`,
título correto). `cf-cache-status: DYNAMIC` confirma que o Cloudflare **não** cacheia as
páginas. Resolve com os fixes 1–2 + hard-refresh (limpa cache do navegador).

## Problema 4 — CSP bloqueava o beacon do Cloudflare

O site está atrás do Cloudflare, que injeta `static.cloudflareinsights.com/beacon.min.js`.
A CSP estrita (`script-src 'self' + hashes`) bloqueava → ruído no console.

**Fix:** liberei o host específico — `https://static.cloudflareinsights.com` no `script-src`
e `https://cloudflareinsights.com` no `connect-src`. Host explícito **não enfraquece** a CSP
(sem `unsafe-inline`/wildcard); o score 100 do próprio site é preservado.

---

## Correções aplicadas

| Arquivo | Mudança |
|---|---|
| `web/src/components/account/Dashboard.jsx` | guarda `typeof window` nos `useState` de query param |
| `frontend/nginx/https.conf.template` | `planos` na allowlist de rotas Astro |
| `frontend/nginx/security_headers.conf` | CSP libera o beacon Cloudflare (script-src + connect-src) |

## Verificação pós-deploy

- `curl https://klarim.net/api/account/me` → 401 (sem token) · com token → 200.
- `https://klarim.net/planos` → "Planos e preços — Klarim" (3 cards).
- Login → dashboard renderiza (sem tela preta).
- Console sem o erro de CSP do beacon.

## Lição

`useState(() => …)` **não** é client-only — roda no SSR das ilhas Astro `client:load`.
Todo acesso a `window`/`document`/`navigator` em inicializador de `useState` (ou no corpo do
componente) precisa de guarda `typeof window !== 'undefined'`. Só `useEffect`/handlers são
garantidamente client-side.
