# Fix painel admin — scan com HTML raw + analytics "inflado por bots"

> **Status: DEPLOYADO EM PRODUÇÃO ✅** (10/08/2026, CI run #302 verde). Pós-deploy: site 200, `visitors_br=422`
> (pós-filtro de UA, era 469), `bots_filtered≈1,58M` (rotulado). P1 validado no dev (504 → mensagem limpa).

## Problema 1 — HTML cru na "Segurança da plataforma" (DIAGNÓSTICO + FIX)

### Diagnóstico
1. **O scan grava HTML como evidence?** **NÃO.** Inspecionei a `platform_security_scans` em produção:
   os 4 scans têm score 90/100, `passed=true`, `error IS NULL` e **nenhum** `results` com
   `<html>/<style>/<script>`. Os `detail` dos checks são strings curtas ("nenhum path exposto", etc.).
   **O evidence armazenado está limpo.**
2. **Então de onde vem o HTML?** É **TRANSITÓRIO**: quando uma chamada de API do painel (o polling
   `securityScanStatus` / o detalhe `securityScanDetail`) pega um **502/504** (Cloudflare/nginx quando
   a origem oscila), o `web/src/lib/admin/adminApi.js::req()` fazia
   `throw new Error(\`Erro ${status}. ${await resp.text()}\`)` — e `resp.text()` de um erro 502 é a
   **PÁGINA HTML inteira** (`<html>/<head>/<style>/<script>…`). Essa mensagem ia para `setMsg`/
   `expanded.error` e era renderizada como TEXTO no painel (React escapa → aparece o HTML "cru" como
   texto, com o CSS inline e tudo). **Causa raiz: o tratamento de erro do `adminApi`, não o evidence.**

### Fix
- **`adminApi.js::req()`** — no `!resp.ok` **nunca** joga o body cru na mensagem. Se o content-type é
  JSON, extrai só o `detail` (erro estruturado da API); senão (página de erro HTML) usa uma mensagem
  **genérica por status** ("Serviço temporariamente indisponível…" p/ 5xx). **Nunca inclui HTML.**
  Corrige TODOS os componentes do painel (não só a Segurança).
- **Defesa (PlatformSecurityCard.jsx)** — `msg`/`expanded.error`/`c.detail` passam por `clampText`
  (corta em 300 chars + "…", `lib/admin/securityScan.js`, puro/testável) + `break-words`. Mesmo que
  um texto longo escape, o painel não quebra.

### Validado no browser
Parei o container `api` → `/api/admin/security-scan/status` devolve **504 text/html** (o cenário
exato). Carreguei `/painel/sistema`: o painel mostra **"Serviço temporariamente indisponível. Tente
novamente em instantes."** e **NENHUM HTML cru** aparece como texto (`page_hasRawHtmlAsText=false`).
Com o `api` no ar, a Segurança renderiza normal (checks como texto escapado).

## Problema 2 — "milhões de bots na Visão Geral" (DIAGNÓSTICO + melhoria)

### Diagnóstico (números reais de produção)
1. **A Visão Geral usa a fonte certa?** **SIM** — o `KpiGrid` já lê `server-metrics` (badge
   "📡 server") para Visitantes BR/Scans/Contas/Bots (não o tracker inflado).
2. **`al_server_metrics` filtra bots?** **SIM** — `visitors_br/total` já contam
   `COUNT(DISTINCT ip_address) WHERE is_bot = false`.
3. **Números reais (prod, período default 7d):**
   - **Visitantes BR = 469** (distinct IP, is_bot=false, BR) — **plausível, NÃO milhões.**
   - **Bots filtrados = 1.452.022 (1,45 MILHÃO)** — `is_bot=true`. **É ISTO que aparecia como
     "milhões"** — e está **corretamente rotulado "Bots filtrados"** (não "visitantes"). 24h: 517k
     linhas totais, 452k bots (87%), 65k humanos, 396 visitantes BR.

**Conclusão:** não há bug de "milhões de visitantes" — a contagem de visitantes já é correta (469) e
usa a fonte autoritativa. O número "milhões" é o card **"Bots filtrados"** (accurate + rotulado).

### Melhoria (o filtro de UA que o card pediu)
Entre os `is_bot=false` havia bots que **escapam do classificador**: as **nossas próprias tools**
(`Klarim Security Gate/1.0`, `KlarimScanner/1.0`, `node`) + scanners de `wp-admin` + crawlers.
Adicionei um filtro por **user-agent** (`discovery/store.py::_BOT_UA_RE`, regex POSIX hardcoded) ao
`al_server_metrics`: tira esses UAs dos **visitantes** (e os conta em `bots_filtered`). Impacto em
prod: **Visitantes BR 469 → 421** (7d) — mais preciso, ainda centenas (nunca milhões).

### Validado no browser
`/painel/analytics` → Visão geral: **Visitantes BR 0** (dev seed), **Scans 11**, **Contas 8**,
**Bots filtrados 757** — todos plausíveis, cada card com badge de fonte "server". Zero "milhões".

## Testes

- **`pytest`: 2317 passed, 1 skipped** (+2 `test_kl150_bot_ua_filter.py`: `_BOT_UA_RE` casa bots —
  incl. as nossas tools — e NÃO casa Chrome/Safari/Firefox/iPhone).
- **`node --test`: 221 passed** (+1 `clampText`).
- **`npm run build`: OK.** SQL do regex validado contra o Postgres da VM (469→421).
- **Browser**: P1 (504 → mensagem limpa, sem HTML), P2 (KPIs plausíveis, fonte server). **Zero erro
  no console.**

## Arquivos

**Frontend:** `web/src/lib/admin/adminApi.js` (erro sem body cru),
`web/src/components/admin/PlatformSecurityCard.jsx` (clampText + break-words),
`web/src/lib/admin/securityScan.js` (+`clampText`) e `.test.js`.
**Backend:** `discovery/store.py` (`_BOT_UA_RE` + filtro de UA no `al_server_metrics`).
**Testes:** `tests/test_kl150_bot_ua_filter.py` (novo). **Docs:** `claude.md`.

## Escopo NÃO tocado

Engine de scan público, rate limiting e o site público intactos. A app do painel é o Astro em
`/painel` (não app separada); os componentes ficam em `web/src/components/admin/`.
