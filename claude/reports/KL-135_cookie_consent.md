# KL-135 — Banner de consentimento de cookies (LGPD) + GA4 condicional

**Data:** 2026-07-31 · **Status:** ✅ implementado; `npm run build` OK, **154 node --test** verdes
(+12), `nginx -t` OK (HTTP+HTTPS), pytest inalterado (nenhum arquivo backend tocado).
**Deploy:** via commit/push (CI/CD).

## Problema (bloqueador LGPD)
O Google Analytics 4 (`G-7WPZN66JTB`) era carregado **incondicionalmente** no `<head>` de toda
página pública (`Base.astro`, desde o KL-92 P4) — ou seja, **opt-out**: o cookie `_ga` e a
requisição ao `googletagmanager.com` disparavam ANTES de qualquer escolha do visitante. Isso viola
a LGPD (Art. 7º/8º — tratamento não essencial exige consentimento). Além disso, a
`/privacidade` §8 afirmava, **incorretamente**, que "o Klarim usa apenas cookies essenciais... não
usamos cookies de rastreamento de terceiros", e **não existia** a página `/cookies`.

## Solução
GA4 vira **opt-in**: só carrega com consentimento de analytics. Um banner de 1ª visita coleta a
escolha e a persiste no cookie `klarim_consent`.

### 1. Carregamento condicional do GA4 (`web/public/cookie-consent.js`, NOVO)
IIFE vanilla **externo** (passa na CSP `script-src 'self'` sem hash). Regra central:
- `loadGA4()` injeta o `gtag.js` sob demanda — **idempotente** (`window.__klarimGA4` + guarda de
  `querySelector('script[src*="googletagmanager"]')`).
- `init()`: lê o cookie → `all`/`analytics` ⇒ carrega GA4; `essential` ⇒ nada; **sem cookie ⇒
  mostra o banner** (GA4 NÃO carrega).
- `apply(value)`: grava o cookie (`Path=/; SameSite=Lax; Max-Age=31536000` + `Secure` em https),
  carrega GA4 se `all`/`analytics`, fecha o banner.
- Delegação de clique em `[data-cc]`: `accept`→all · `reject`→essential · `configure`→abre painel ·
  `save`→lê `#cc-analytics` (analytics|essential) · `reopen`→reabre (previne default do link).
- `window.klarimReopenConsent` exposto para reabrir programaticamente.

### 2. Banner (`web/src/components/CookieBanner.astro`, NOVO) no `Base.astro`
HTML estático (sem inline script → CSP-safe), `hidden` por padrão (o JS revela). Fixo no rodapé,
`z-[60]`, backdrop, **theme-aware** (utilitários slate invertem juntos no KL-87 → bg escuro + texto
claro funcionam nos 2 temas), **mobile 375px** (botões empilham, alvos ≥44px), slide-up via
`@keyframes`. Botões: **Aceitar todos** / **Configurar** / **Recusar**; painel expansível com
Essenciais (fixo) · Analytics (`#cc-analytics`) · Marketing (inativo) + **Salvar preferências**.

### 3. `Base.astro` — remoção do GA4 incondicional
Removidas as 2 tags GA4 do `<head>` (o `<script async src=googletagmanager>` + o inline
`gtag('config')`). Adicionados `<CookieBanner />` + `<script src="/cookie-consent.js?v=1">` no body.
**CSP inalterada:** o loader do GA4 já estava liberado (`script-src https://www.googletagmanager.com`);
o consent JS é externo (`'self'`). O hash SHA-256 do antigo inline GA4 continua na CSP mas **ocioso**
(inofensivo) — não removido para não mexer no arquivo de headers neste card.

### 4. Páginas
- **`/cookies` (NOVA):** política completa — o que são, essenciais (sessão/tema/consentimento),
  analytics (GA4, só com opt-in), marketing (nenhum), como gerenciar (botão `data-cc="reopen"`),
  link p/ `/privacidade`. Contato `scan@klarim.net`.
- **`/privacidade` (atualizada):** §5 agora lista os **operadores** (Resend, Reoon, AbacatePay,
  Google Analytics — este só com consentimento); §8 reescrita (essenciais + analytics opt-in +
  banner + link p/ `/cookies`).
- **`Footer.astro`:** link "Cookies" + botão "Preferências de cookies" (`data-cc="reopen"`).

### 5. Nginx (2 allowlists)
`cookies` (página) + `cookie-consent\.js` (asset) adicionados ao `location ~ ^/(…)` de
`https.conf.template` (linha 128, junto de privacidade e dos outros `.js`) **e** `http.conf`
(linha 50). Sem os 2, o `.js` cairia no fallback SPA do Vite (HTML → bloqueado por `nosniff`) e a
página daria 404. `?v=1` no `<script>` (cache-busting do Cloudflare). Ambos validados com `nginx -t`.

## Testes (`web/src/lib/cookieConsent.test.js`, +12)
Carrega o IIFE em `node:vm` com DOM/cookie mockados. Cobre a **regra crítica** (GA4 nunca sem
consentimento): 1ª visita (banner aberto, sem GA4); aceitar (all+GA4); recusar (essential, sem GA4);
configurar+salvar marcado (analytics+GA4) e desmarcado (essential); retorno com cada cookie;
reopen; flags do cookie (Path/SameSite/Max-Age/Secure em https, sem Secure em http); idempotência
do GA4. Registrado no `test:unit` (CI).

## Validação pós-deploy
1. **DevTools › Network, aba anônima:** carregar `klarim.net` → **nenhum** request a
   `googletagmanager.com`; banner aparece.
2. Clicar **Aceitar** → request ao `googletagmanager.com` dispara; recarregar → carrega direto
   (cookie `klarim_consent=all`), banner some.
3. Aba anônima nova → **Recusar** → sem request ao GA; `klarim_consent=essential`.
4. `/cookies` e `/privacidade` 200; link "Preferências de cookies" no rodapé reabre o banner.
5. `curl -sI https://klarim.net/cookie-consent.js?v=1` → `content-type: ...javascript` (não HTML).

## Arquivos
- **Novos:** `web/public/cookie-consent.js`, `web/src/components/CookieBanner.astro`,
  `web/src/pages/cookies.astro`, `web/src/lib/cookieConsent.test.js`, este relatório.
- **Editados:** `web/src/layouts/Base.astro`, `web/src/pages/privacidade.astro`,
  `web/src/components/Footer.astro`, `web/package.json`,
  `frontend/nginx/https.conf.template`, `frontend/nginx/http.conf`, `CLAUDE.md`.
