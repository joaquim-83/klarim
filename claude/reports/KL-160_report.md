# KL-160 — Rate limiting + fix falsos positivos SPA + botão de varredura no admin

> **Status: PRONTO PARA REVISÃO VISUAL** — implementado e validado no `docker-compose.dev.yml` +
> `nginx -t` + Gate real contra klarim.net. **NENHUM push/deploy foi feito.** Aguarda autorização
> do Cidinei (as mudanças de nginx SSL/rate-limit têm risco de produção — ver "Risco" abaixo).

## Resumo

4 entregas: (A/urgente) bloquear `/adminer` e `/_profiler` no nginx; (B) o Gate deixa de dar falso
positivo de SPA fallback quando o Cloudflare remove o ETag; (C) rate limiting na aplicação +
bloqueio de acesso por IP direto; (D) botão de varredura de segurança no painel admin.

## Fix A (URGENTE) — Nginx bloqueia painéis de admin/debug

O bloco de exploit-paths do KL-138 cobria `/admin(/|$)` mas deixava `/adminer` passar (virava SPA
fallback → o Gate reportava `admin_panel_exposed`); `/_profiler` não estava em lista nenhuma. Novo
`location ~* ^/(adminer|_profiler|phpMyAdmin|pma|dbadmin|sql|mysql|cpanel|webmail|roundcube|
squirrelmail|horde|wp-content/debug) { return 404; }` em **http.conf E https.conf.template**, antes
do fallback. Nenhuma rota pública/API começa por esses tokens.

## Fix B — Gate: fingerprint SPA por `last-modified` quando o ETag some

O guard do KL-147 usa o ETag como comparador primário; o Cloudflare **remove** o ETag →
`fingerprint` sem comparador → `/adminer`/`/_profiler` (200 + index.html) eram reportados como
exposição. `security_gate/engine.py::_detect_spa_fallback` passa a capturar `last_modified`; e
`security_gate/utils.py::matches_spa_fingerprint` ganhou o fallback: **mesmo `Last-Modified` +
`Content-Type` HTML ⇒ é o fallback de SPA** (não exposição). Ordem dos comparadores:
ETag → Last-Modified+HTML → Content-Type+Content-Length.
**Validado contra o klarim.net REAL** (antes do deploy do nginx): `admin_panel_exposed` e
`debug_exposed` agora **PASS** — a correção é Gate-side, não precisa do nginx para funcionar.

## Parte C — Rate limiting + bloqueio de IP direto

**Zonas** (topo de http.conf e https.conf.template — no MESMO arquivo, porque o `nginx -t` da CI
monta cada config sozinho; só um config é carregado por vez → sem zona duplicada):
`general 30r/s` · `api 10r/s` · `scan 2r/s` + `limit_req_status 429`.
⚠️ **Chave = IP REAL do cliente** (`CF-Connecting-IP` via `map`), **NÃO** `$binary_remote_addr`:
atrás do Cloudflare o `$remote_addr` é a edge do CF → todos os usuários cairiam num único bucket
(throttle global do site). O `map` cai no `$remote_addr` quando não há CF (acesso direto). Aplicado:
`/` + páginas Astro → `general` (burst 50); `/api/` → `api` (burst 20); `/api/scan/` → `scan`
(burst 5, prefixo mais longo). Assets `_astro`/`/assets`, MCP/SSE e OAuth ficam SEM limite (evita
throttlar bursts de assets e o SSE). **Não** apliquei zona `auth` nas PÁGINAS `/entrar`/`/cadastrar`
(throttlar o GET da página prejudica o usuário legítimo; o brute-force real é no POST de login, que
já tem rate limit no backend + cai na zona `api`).
**Bloqueio de IP direto** (só no https.conf.template): `server { listen 443 ssl default_server;
ssl_reject_handshake on; }` (recusa TLS de SNI desconhecido, sem cert) + `server { listen 80
default_server; return 444; }`. O bloco do klarim.net perdeu o `default_server` (o CF conecta com
SNI=klarim.net → casa o server real; IP direto → reject/444).

## Parte D — Botão "Segurança da plataforma" no painel admin

- Tabela **DEDICADA** `platform_security_scans` (não `gate_runs`, que exige `account_id`/`project_id`
  NOT NULL de um cliente — poluiria os stats). Guarda score/counts + os `results` (JSONB).
- **`POST /admin/security-scan`** roda o Gate completo contra klarim.net **assíncrono**
  (`asyncio.create_task`) — devolve `{status:started}` na hora; `{running}` se já roda; **429
  cooldown 5 min**. **`GET /admin/security-scan/status`** (running + último + histórico) para polling;
  **`GET /admin/security-scan/{id}`** (detalhe completo). Admin-only (middleware `_admin_auth_mw`).
  Alerta best-effort ao operador se score < 80 ou surgir CRÍTICO.
- **UI** (`components/admin/PlatformSecurityCard.jsx` na página Sistema): último score + semáforo +
  findings, botão "Executar varredura completa", histórico com **detalhe expandível** (checks
  ordenados FAIL-primeiro), destaque vermelho se score < 80 ou crítico. Lógica pura em
  `web/src/lib/admin/securityScan.js`.

## Parte 2 — DNSSEC

**Já resolvido.** O Gate real contra klarim.net retorna `✅ Dnssec Ok — DNSSEC configurado (zona
assinada)`. O finding do card (NoNameservers) não se reproduz mais — o DNSSEC foi ativado no
Cloudflare. Documentado no `docs/SECURITY.md`.

## Validação

- **`pytest`: 2311 passed, 1 skipped** (+11: `test_kl160_security_scan.py` 6 + `test_kl147_spa_fingerprint.py` +5 do last-modified).
- **`node --test`: 218 passed** (+7: `securityScan.test.js`).
- **`npm run build`: OK.**
- **`nginx -t`: OK** nos dois configs (rate-limit + IP-block + admin/debug), exatamente como a CI.
- **Rate limit — comportamento real** (nginx standalone): 20 requests concorrentes com o MESMO
  CF-IP → **4×200 + 16×429**; 20 CF-IPs DIFERENTES → **20×200** (cada cliente no seu bucket, sem
  throttle global). O 429 sai via `limit_req_status 429`.
- **Gate real contra klarim.net**: `admin_panel_exposed` PASS · `debug_exposed` PASS · `dnssec` PASS ·
  score **90/100 🟢** (único finding: `rate_limit_missing`, que o deploy do nginx resolve → 100).
- **Painel admin (browser, dev)**: card renderiza (90/100 + 0 Critical|1 High|0 Medium), botão dispara
  o scan (started → running → completa em ~36s → persiste), cooldown 429, histórico com detalhe
  expandível (39 checks, FAIL-primeiro). **Zero erro no console.**

## Risco (LER antes de deployar)

O `nginx -t` valida SINTAXE, não o roteamento SSL/SNI em runtime; o dev stack é HTTP-only. As peças
de MAIOR risco (não testáveis 100% fora de produção):
1. **Bloqueio de IP direto** (`ssl_reject_handshake` no 443 default_server + remoção do
   `default_server` do bloco klarim.net). Se o Cloudflare conectar com SNI diferente de
   `klarim.net`/`www`, cairia no reject. O CF envia SNI = hostname de origem (klarim.net) → deve
   casar o server real. Recomendo revisar este bloco e ter o rollback à mão (VM fallback do CLAUDE.md).
2. **Rate limiting**: mitigado pela chave CF-Connecting-IP (validada). Assets/SSE/OAuth ficam de fora.

**Sugestão:** se preferir, deployar primeiro só Fix A + Fix B + Parte D (baixo risco) e a Parte C
(rate-limit + IP-block) num segundo passo com observação. Aguardo sua decisão.

## Deploy (pós-autorização)

- `ensure_schema` cria `platform_security_scans` no boot; **sem flush Redis**.
- **Após o deploy**, o job **Security Gate** do CI (contra klarim.net LIVE) deve ficar **verde com
  score ~100** (admin_panel/debug/dnssec PASS por Fix B + rate_limit PASS por Parte C).
- `docs/SECURITY.md` (§ rate limiting / IP-block / self-scan / DNSSEC) e `docs/DEPLOY.md` atualizados.

## Deploy (09/08/2026) + refinamento do rate-limit check

Commit `cf791a8` deployado (CI run #295 verde, incl. nginx-check + Deploy + Security Gate). Site UP
(páginas 200), `/adminer`/`/_profiler`/`/pma` → 404, admin_panel/debug/DNSSEC PASS.

**Refinamento (commit seguinte):** o `rate_limit` continuava FAIL no Gate — o check enviava 10 GETs
**SEQUENCIAIS** e o leaky bucket do nginx "refilla" entre um e outro (RTT ~400ms ≈ o refill de 2r/s),
então nunca disparava o 429 mesmo com o limite ativo (comprovado: 12 requests CONCORRENTES → 4×307 +
**8×429**). Dois ajustes: (1) `security_gate/checks/rate_limit.py` faz a rajada **CONCORRENTE**
(`asyncio.gather`, ≤10 requests, passivo) — corrige o falso negativo p/ todos os clientes do Gate;
(2) `security-gate.yml` inclui `/api/scan/` (zona `scan` 2r/s/burst 5, que a rajada dispara; `/` e
`/api/` têm limite generoso de propósito e não tripam com 10 requests). **Resultado contra prod:
`Rate limit ativo em /api/scan/ (4/10 → 429)` → score 100/100 🟢, 0 findings.**

## Arquivos

**Nginx:** `frontend/nginx/http.conf`, `frontend/nginx/https.conf.template`.
**Gate:** `security_gate/engine.py`, `security_gate/utils.py`.
**Backend:** `discovery/store.py` (tabela + 3 métodos), `api/gate.py` (3 endpoints + runner + alerta).
**Frontend:** `web/src/lib/admin/securityScan.js` (+ `.test.js`),
`web/src/components/admin/PlatformSecurityCard.jsx`, `web/src/components/admin/SistemaPage.jsx`,
`web/src/lib/admin/adminApi.js`, `web/package.json`.
**Testes:** `tests/test_kl160_security_scan.py`, `tests/test_kl147_spa_fingerprint.py`.
**Docs:** `claude.md`, `docs/SECURITY.md`, `docs/DEPLOY.md`.
