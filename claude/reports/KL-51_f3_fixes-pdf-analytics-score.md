# KL-51 f3 — 3 correções urgentes: PDF 402, analytics vazio, score 100

## 1. PDF retornava 402 com `PAYWALL_ENABLED=false`

**Causa:** `_require_paid` só liberava o PDF em **modo dev** ou **sem chave AbacatePay**
(`_free_access`). Em produção a chave está configurada → 402, ignorando o `PAYWALL_ENABLED`
(o próprio docstring do `_paywall_enabled` diz "o PDF é sempre gratuito", mas o código não
cumpria). **Fix:** `if not _paywall_enabled() or _free_access(): return` — com o paywall
desligado (default, freemium) o PDF (`/report/executive|technical`) é gratuito para
qualquer scan. Com `PAYWALL_ENABLED=true` volta a exigir a cobrança.

## 2. Eventos recentes vazios no Analytics do painel

**Causa:** o tracker do KL-21 (`site_events` via `POST /api/events`) só existia no frontend
**Vite** (`frontend/`). Com a migração do site público para **Astro** (KL-51), a landing/scan
pararam de disparar eventos → `site_events` sem atividade nova → "Eventos recentes" vazio.
**Fix:** tracker para o Astro:
- `web/public/track.js` — asset **externo** (não inline → passa na CSP `script-src 'self'`),
  com `session_id`/UTM, dispara `page_view` em cada página pública e expõe `window.klarimTrack`.
- `web/src/layouts/Base.astro` inclui `<script src="/track.js" is:inline>`.
- `ScanFlow.jsx` emite o funil: `scan_started`, `code_requested`, `code_verified`,
  `scan_limit_reached`, `scan_completed`, `result_viewed`.
- Nginx: `/track.js` entra na regex das rotas Astro.

## 3. Score 100/100 — headers do Nginx

Scan real de klarim.net = **84/100, 11 FAILs**. O dono faz os **4 de DNS** (DNSSEC, CAA,
MTA-STS, BIMI). A CLI fez os **6 de header** + o de OCSP:

Snippet compartilhado **`frontend/nginx/security_headers.conf`** (via `include` no server
block e em cada `location` com `add_header` próprio — um `add_header` no location quebra a
herança; o job `nginx-check` do CI monta o arquivo):

| Check | Fix |
|-------|-----|
| **05 CSP** | `script-src` **sem** `'unsafe-inline'` — os **3 scripts inline** do Astro (toggle de auth do Header + 2 do runtime de island) entram por **hash SHA-256**. Todo script novo é **externo** (track.js, chunks das islands) → `'self'`. `style-src` mantém `'unsafe-inline'` (só script-src/default-src reprovam; React/Recharts usam estilo inline). + `object-src 'none'`, `base-uri 'self'`, `frame-ancestors 'none'`, `form-action 'self'`. |
| **31 Permissions-Policy** | `camera=(), microphone=(), geolocation=(), payment=(), usb=()…` |
| **32 COOP** | `Cross-Origin-Opener-Policy: same-origin` |
| **33 COEP** | `Cross-Origin-Embedder-Policy: require-corp` (seguro: site 100% same-origin, todo recurso servido leva CORP) |
| **34 CORP** | `Cross-Origin-Resource-Policy: same-origin` |
| **36 Cache-Control** | `no-store` na landing (`location = /`, tem o form de scan) + páginas com formulário (regex Astro) |
| **43 OCSP** | ⚠️ `ssl_stapling` **não** resolve: o check lê o **OCSP URI do certificado** (AIA), e a **Let's Encrypt removeu o OCSP URI** em 2025 (OCSP descontinuado). O check foi ajustado — ausência de OCSP URI é o novo normal ⇒ **INCONCLUSO** (neutro), não FAIL. |

**Estabilidade dos hashes CSP:** os 3 hashes vêm dos scripts inline do Astro, que **não
mudam** (não mexemos no Header nem na versão do Astro — `npm ci` fixa a versão pelo
package-lock). Ao alterar o script do Header ou subir o Astro, **recalcular** (curl das
páginas + sha256) — senão os scripts são bloqueados. Verificação pós-deploy: comparar os
hashes inline das páginas servidas com os da CSP (determinístico, sem browser).

## Testes

- `test_payments.py` → +1 (`test_require_paid_free_when_paywall_off`); os 2 testes de 402
  agora setam `PAYWALL_ENABLED=true` (o 402 só faz sentido com o paywall ligado).
- `test_kl37_tls.py` → `test_ocsp_no_uri_inconcluso` (era `_fail_baixa`).
- Nginx validado pelo `nginx-check` do CI (`nginx -t` com o include montado); JS por esbuild.

## Verificação pós-deploy

Re-scan de klarim.net (05/31/32/33/34/36 PASS, 43 INCONCLUSO); PDF baixa sem 402;
`POST /api/events` popula `site_events` (page_view + funil); as páginas Astro **carregam e
as islands hidratam** (hashes CSP conferem). Score sobe para ~100 quando os 4 DNS forem
configurados pelo dono.
