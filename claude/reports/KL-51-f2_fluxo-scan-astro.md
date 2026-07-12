# KL-51 (fase 2) — Fluxo de scan no Astro + paywall aberto + correções

**Card:** KL-51 (fase 2 de ~4) · **Prioridade:** CRÍTICA (core do produto).
Entrega o fluxo de scan na plataforma Astro (e-mail → código → progresso →
resultado) + 2 correções da fase 1 (logo, contato).

---

## Decisões (com o dono)

- **Paywall aberto por flag.** O dono definiu: abrir **todos os 48 checks** na web,
  via `PAYWALL_ENABLED` (default `false`), com o **código do gate limpo e funcional**
  (nada comentado — reativa trocando a flag). O PDF continua gratuito. *(48 checks =
  15 free + 33 pago; a "29" em docstrings antigas é do KL-27.)*
- **Resultado inline no island** (não `/resultado/{scan_id}` SSR): o backend faz scan
  **bloqueante** (`/scan/summary`, ~30s, sem id pollável), então o resultado renderiza
  no próprio island após o scan. Uma página de resultado SSR com SEO/og:image fica para
  a fase de perfis públicos.

## Backend (`api/main.py`, `discovery/store.py`)

- **`_paywall_enabled()`** (default `false`). Gateia:
  - `POST /scan/request-code`: pula `limit_reached`/`already_scanned` quando aberto →
    **sem limite de 1 scan/e-mail** (a verificação de e-mail continua = captura de lead +
    anti-bot por rate limit).
  - `GET /scan/summary`: força `full=True` para todo scan autorizado, **preservando** o
    ingest público (KL-17) via `is_public_free` (capturado antes de abrir o paywall).
  - Com `PAYWALL_ENABLED=true`, volta o gate KL-27 (15 + 33 🔒, 1 scan/e-mail).
- **Benchmark:** `GET /benchmark` (global) e `GET /benchmark/{sector}` (cai para global
  se amostra < 5). `store.global_avg_score`/`sector_avg_score` (via
  `targets.last_scan_score`). Públicos.

## Frontend (`web/`)

- **`components/scan/ScanFlow.jsx`** (React island, `client:load`) + `checks.js`
  (agrupa os 48 em 6 categorias). Etapas: **e-mail** → `request-code`; **código** (6
  dígitos, e-mail mascarado, reenviar 45s) → `verify-code` (scan token); **progresso**
  simulado (barra + categorias + dicas rotativas) durante o `summary` bloqueante;
  **resultado** inline (score animado 0→N, semáforo, frase contextual, benchmark, 48
  checks por categoria com FAILs expansíveis mostrando evidência/impacto/correção +
  OWASP/CWE/LGPD, CTA de PDF executivo/técnico).
- **`pages/scan.astro`** (SSR, `prerender=false`) lê `?url=` e passa ao island;
  `<noscript>` avisa que a análise precisa de JS.
- **Correções f1:** `Logo.astro` = **logo real** (beacon laranja + `KLA`**`R`**`IM`,
  réplica do `Logo.jsx`) + **favicon** beacon laranja. **`/contato`** (Astro SSR,
  `prerender=false`): form no-JS (honeypot + `X-Real-IP` propagado) que **reusa** o
  endpoint existente **`POST /contact`** — o footer aponta pra `/contato` (sem `mailto`).
  `KLARIM_API_URL` (`http://api:8000`) no serviço `astro` para os fetches SSR.

## Nginx

`/scan` e `/contato` entram na regex das rotas Astro. `/api/scan/*` **não** conflita
(casa o prefixo `/api/`, não a âncora `^/scan`). O fluxo Vite de `/scan` fica sombreado.

## Testes

- Backend: `PAYWALL_ENABLED` — testes KL-27/31 que validam o gate passam a setar
  `PAYWALL_ENABLED=true`; novos testes cobrem o **default aberto** (`request-code` sem
  limite; `summary` completo com token básico). Suíte: **517 passed, 1 skipped**.
- Frontend: `npm run build` OK (SSR `scan`/`contato` + island).
- Nginx: validado no CI (`nginx -t`).

## Notas / pendências

- **Sem JS:** o fluxo de scan exige JS (island). Landing/legais/contato funcionam sem JS.
- **"Entrar"** e criação de conta continuam para a fase 5.
- **Batch `enrich_all`** segue pendente de re-execução (foi interrompido pelo deploy da
  fase 1); é idempotente — rodar `docker compose exec api python scripts/enrich_all.py
  --limit 500` quando quiser.

## Arquivos

- **Novos:** `web/src/components/scan/{ScanFlow.jsx,checks.js}`, `web/src/pages/scan.astro`,
  `web/src/pages/contato.astro`, `claude/reports/KL-51-f2_fluxo-scan-astro.md`.
- **Editados:** `api/main.py`, `discovery/store.py`, `web/src/components/{Logo,Footer}.astro`,
  `web/public/favicon.svg`, `docker-compose.yml`, `frontend/nginx/{http.conf,https.conf.template}`,
  `tests/{test_scan_verification,test_kl31_score100}.py`, `claude.md` (§39), `README.md`.
