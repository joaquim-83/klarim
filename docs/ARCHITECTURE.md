# Klarim — Arquitetura

> Referência detalhada extraída do antigo `claude.md`. Histórico íntegro das 50
> entregas em `docs/HISTORY.md`; endpoints em `docs/API.md`; segurança em `docs/SECURITY.md`.

## 1. Visão geral

O Klarim é um **scanner passivo de segurança web** (Security Rating, não pentest) para
PMEs brasileiras, operado como plataforma **freemium** ("Guardião Digital"). Fluxo de
alto nível:

```
Descoberta (CT logs) → fila Redis → Scan (48 checks passivos) → score/semáforo
   → Enriquecimento (perfil comercial + IA + CNAE) → Perfil público /site/{dominio}
   → Notificação (alerta proativo) → Conta/monitoramento → Vigílias contínuas
```

Cada peça é **best-effort/fail-open**: uma API externa fora do ar degrada para
`INCONCLUSO`/regex-only, nunca derruba o scan nem o worker.

## 2. Containers (Docker Compose)

| Serviço | Papel | Porta |
|---|---|---|
| `postgres` | PostgreSQL 16 — toda a persistência | interno |
| `redis` | fila de scan, cache por tier, heartbeat, rate limit, OAuth MCP | interno |
| `api` | FastAPI (endpoints públicos + admin + `/mcp` montado) | `127.0.0.1:8000` |
| `worker` | scan worker async (consome a fila, escaneia, enriquece) | — |
| `discovery` | Discovery + Alert + Rescan + Vigília via `asyncio.gather` | — |
| `astro` | Astro 7 SSR (Node standalone) — site público | `127.0.0.1:4321` |
| `web` | **Nginx** — único público; TLS + roteamento + segurança | `80` / `443` |

`api` monta `./:/klarim-control` **rw** (MCP grava `worker_control.json`); `discovery`
e `worker` montam `:ro`. Imagem Python (`build: .`) é compartilhada por api/worker/
discovery; `.dockerignore` exclui `frontend/`/`web/` dela.

## 3. Nginx — front único de TLS/segurança

- **TLS Let's Encrypt self-healing:** entrypoint escolhe `http.conf` (sem cert) ou
  `https.conf.template` (com cert, via envsubst) em runtime → deploy nunca quebra por
  falta de cert. Redirect 80→443, security headers com `always`.
- **Roteamento:** rotas públicas (`/`, `/scan`, `/site`, `/ranking`, `/dashboard`,
  `/cadastrar`, …) → **Astro** (resolver dinâmico `astro:4321`); `/painel*` + `/assets/`
  → build **Vite**; `/api/` → `api:8000` (rewrite tira o prefixo); `/mcp/` → `api:8000`
  (identidade, SSE com buffering off).
- **Resolver dinâmico:** `resolver 127.0.0.11 valid=10s` + upstream em variável (`set
  $klarim_api api:8000`) → re-resolve o IP por request (o container recriado ganha IP
  novo; sem isso, 502).
- **Subdomínios:** `painel.` (mesmo build, redireciona raiz→`/painel/login`), `mta-sts.`
  (policy MTA-STS via Cloudflare).
- **Hardening:** bloqueia dotfiles/paths sensíveis (regex → 404), security headers no
  `server` com `always`. ⚠️ Um `add_header` num `location` **quebra a herança** — repetir
  os headers de segurança ao adicionar `location`. Snippet compartilhado
  `frontend/nginx/security_headers.conf`. Valide com `nginx -t` (job de CI).

## 4. Frontend — Astro (público) + React islands + Vite (/painel)

**Decisão KL-51 (menor risco):** em vez de substituir o `frontend/` Vite, foi
**adicionado** o serviço `astro`; o Nginx proxeia as rotas públicas novas → Astro e
mantém o build Vite em `/painel*`. O painel admin não mudou.

- **`web/` (Astro 7):** `output: 'server'` + `@astrojs/node` standalone; páginas
  públicas com `prerender=true` (SSG) e o fluxo de scan/contas com `prerender=false`
  (SSR). Tailwind v4 CSS-first. Ilhas React (`components/**`) para interatividade
  (`ScanFlow`, `Dashboard`, `SignupForm`…), falando com `/api/*`.
- **`frontend/` (Vite):** build React do `/painel` admin, `lazy()` code-split (Recharts
  num chunk separado); ilhas admin usam `client:only="react"`.
- **CSP:** público estrito (scripts inline por hash SHA-256); `/painel` relaxado
  (`script-src 'unsafe-inline'`, painel é noindex/operator-only). Ver `docs/SECURITY.md`.

## 5. Scanner engine (`scanner/`)

- **`runner.py`** roda os 48 checks em **paralelo** (`asyncio.gather` +
  `Semaphore(SCAN_MAX_CONCURRENCY=12)`), preservando ordem; carimba OWASP/CWE/LGPD por
  `check_id`. Seguro porque o rate limit de `base.fetch` é **por-domínio** (`asyncio.Lock`,
  1 req/s) — só domínios distintos se sobrepõem.
- **48 checks** (`checks/check_*.py`, descoberta dinâmica): 15 grátis (ORDER≤15) + 33
  pagos. Categorias: headers, HTTPS/HSTS/TLS/cert (+ TLS profundo 41–44 via
  `tls_analyzer` compartilhando 1 handshake), supply-chain (SRI/fontes), CORS/cookies,
  DNS/e-mail (SPF/DKIM/DMARC/DNSSEC/CAA/MTA-STS/BIMI via `dns_util`), CVE (`cve_db`
  Retire.js, check_30), content analysis passivo (45–48).
- **`scoring.py`** — score 0–100 + semáforo: 🟢 ≥90 **E** zero FAIL Alta/Crítica · 🟡
  ≥50 · 🔴 <50.
- **Cache por tier** (`cache.py`): `scan:free:<hash>` / `scan:full:<hash>` (ambos casam
  `scan:*` no flush), TTL 1h, com fallback no banco (`scans`).
- **Enriquecimento** (`profiler.py` + `ai_enrichment.py` + `enrichment.py`): crawl
  multi-page → dados comerciais (contatos, JSON-LD, tecnologias, CNPJ) + IA (setor,
  descrição, tags, CNAEs). Best-effort, **fora do caminho síncrono** do scan, **não
  altera o score**.

## 6. Workers (container `discovery`)

`discovery/worker.py::main()` roda `asyncio.gather(DiscoveryWorker, AlertWorker,
RescanWorker, VigiliaWorker)`. Cada um relê config por ciclo (`get_setting`) e checa
`worker_control` (`is_enabled`) — pausa/retoma via MCP sem redeploy.

| Worker | Arquivo | Ciclo | Função |
|---|---|---|---|
| Discovery | `ct_poller.py` + `worker.py` | 30 min | CT log poller → filtra `.com.br` → fingerprint + e-mail + setor → registra + enfileira **todo site acessível** (KL-60) |
| Alert | `alert_worker.py` | 30 min | alerta proativo em batch (50, Resend Batch API), teto `ALERT_DAILY_LIMIT`/cota mensal, anti-bounce, kill-switch `STOP_ALERTS` |
| Rescan | `rescan_worker.py` | 24 h | reescaneia alvos ≥30 dias + e-mail de evolução; monitora sites 100 |
| Vigília | `vigilia_worker.py` | 6 h | 5 vigílias (SSL, domínio, score, e-mail, reputação) p/ contas Pro/Agency; enforcement de plano; **começa pausada** |
| Scan | `scanner/main.py --worker` | contínuo | `blpop` da fila → escaneia → cacheia → salva + enriquece inline |

**Resiliência:** heartbeat no Redis (`worker:<name>:status`, TTL 600s) → painel mostra
🔴 se expira; watchdog em thread (`os._exit(1)` se o loop trava) + `restart:
unless-stopped`. Motivo: incidente de 08/07 (domínio travado congelou tudo por 7,5h).

## 7. Dados (PostgreSQL — `discovery/store.py`)

Schema criado idempotente no `ensure_schema` (sem Alembic). Principais tabelas:

- **Núcleo:** `targets` (alvos + setor + `last_scan_*`), `scans` (histórico, `source`,
  `scanned_by_email`), `site_profile` (perfil comercial, `public_visible`,
  `edited_by_admin`), `target_classifications` (CNAE multi-setor).
- **Funil/e-mail:** `payments`, `alert_log`, `rescan_log`, `email_log` (rastreabilidade
  unificada, KL-62), `email_blocklist`, `recovery_tokens`, `scan_verifications`,
  `scan_credits`, `site_events` (tracking), `scan_leads` (PQL).
- **Contas:** `users`, `user_sites`, `password_resets`, `vigilias`, `vigilia_alerts`,
  planos/assinaturas (KL-44).
- **Operação:** `monitored_sites`, `inbox_messages`, `admin_settings` (config ao vivo).

## 8. Integrações externas

| Serviço | Uso |
|---|---|
| **Resend** | e-mail — 2 domínios: `klarim.net` (transacional) + `klarimscan.com` (proativo). Batch API + webhook Svix (bounce/complaint) |
| **AbacatePay** | PIX (R$ 19 avulso); webhook query-secret + HMAC |
| **OpenAI** | GPT-4o mini (setor/descrição/tags/CNAE; ~US$0,001/site; fail-open) |
| **APIs públicas de leitura** | crt.sh (CT/subdomínios), HIBP, Google Safe Browsing, IBGE (CNAE), BrasilAPI/ReceitaWS (CNPJ), RDAP (domínio) |

## 9. MCP Server (`mcp_server/`)

Wrapper fino sobre a API/store — operar o Klarim por linguagem natural no Claude.
SSE puro (Starlette montado no FastAPI em `/mcp`) + **OAuth 2.1 + PKCE** (KL-63,
Klarim é o próprio authorization server) + **`MCP_API_KEY` estático** como fallback.
Auth própria (`MCPAuthMiddleware`, fail-closed, constant-time) — fora do JWT admin.
49 tools por domínio (ver `docs/API.md`), todas via `_guard` (nunca derrubam a sessão).

## 10. Fluxo de dados end-to-end

1. **Discovery** lê CT logs → domínio `.com.br` → fetch + fingerprint + e-mail + setor
   → `register_target` (UPSERT) → enfileira scan.
2. **Scan worker** escaneia (48 checks) → `scoring` → cacheia → salva em `scans` +
   atualiza `targets` → **enriquece** perfil + IA + CNAE inline.
3. **Alert worker** pega alvos escaneados com falhas (ou score 100) → alerta proativo.
4. **Perfil público** `/site/{dominio}` fica indexável (sitemap, og:image, Schema.org).
5. Visitante escaneia (verificação por e-mail, KL-25) → vira **lead** (PQL, fire-and-
   forget) → **conta** (signup vincula histórico) → **monitoramento** (vigílias por plano).
6. Todo e-mail passa por `KlarimMailer._send` → `email_log` (rastreabilidade + blocklist).
