# KL-134 (Prompt 1/2) — Backend das micro-ferramentas SEO

**Data:** 2026-08-12 · **Escopo:** 5 ferramentas públicas + rate limiter + timeout + stats.
**Sem deploy** (validado no `docker-compose.dev.yml`). **PRONTO PARA REVISÃO VISUAL** (o Prompt 2 é o frontend).

## Princípio seguido
Cada ferramenta é um **wrapper** que CHAMA um check/analisador já existente da engine, formata e
devolve JSON PT-BR simplificado. **Nenhum check foi alterado** (`ssl`/`headers`/`privacy_checks`/
`tech_detector`/DNS) — só chamados. Toda a lógica de montagem vive em **funções puras**
(`build_*_response`), testáveis sem rede; os endpoints fazem só o I/O (com timeout) e delegam.

## Entregáveis

### `api/tools.py` (novo) — infraestrutura + 6 endpoints
- **`validate_tool_url`** / **`validate_tool_domain`** — aceitam `example.com` / `https://…/path`;
  normalizam com scheme; validam o hostname por regex de domínio (`ValueError` em vazio/lixo).
- **`check_tools_rate_limit(redis, ip)`** — `tools:rl:{ip}`, INCR + EXPIRE 60, **10/min por IP**,
  429 com `Retry-After`; **fail-open** (sem Redis → aceita). Reusa o cliente Redis do app.
- **`run_check_with_timeout(fn, *args, timeout=15)`** — `asyncio.wait_for`; estouro → `ToolTimeout`
  → **504** "O site não respondeu em 15 segundos."
- **Builders puros:** `build_ssl_response`, `build_headers_response`, `build_lgpd_response`,
  `build_tech_response`, `build_email_response` + helpers (`_ssl_grade`, `_lgpd_grade`,
  `_friendly_issuer`, mapas de nome/categoria de tech).

### Endpoints (registrados via `app.include_router(_tools.router)` em `api/main.py`)
Externamente sob `/api/tools/*` (o nginx faz `rewrite ^/api/(.*) /$1` → o FastAPI vê `/tools/*`).

| Endpoint | Fonte reusada | Observação |
|---|---|---|
| `GET /api/tools/ssl?url=` | `scanner.tls_analyzer.get_tls_info` | grade A/B/C/F; issuer amigável (LE, GTS, DigiCert…); expirado/autoassinado → `valid:false` |
| `GET /api/tools/headers?url=` | `scanner.checks.base.fetch` | 7 headers de segurança, `score N/7`, explicação PT por header |
| `GET /api/tools/lgpd?url=` | `scanner.privacy_checks.scan_privacy` | **8 indicadores reais**, `score N/8`, grade Adequado/Parcial/Atenção/Inadequado + disclaimer |
| `GET /api/tools/tech?url=` | `scanner.tech_detector.detect_tech_stack` | nomes/categorias amigáveis; DNS (MX/NS/TXT) para email/dns provider; vazio → `message` |
| `GET /api/tools/email?domain=` | `dns_util` + seletores DKIM do check 22 | SPF/DKIM/DMARC/MX, `score N/4`; DKIM sondado em paralelo; recebe `domain=` (opera sobre DNS) |
| `GET /api/tools/stats` | `dashboard_summary` + `privacy_indicator_stats` + `get_tech_adoption` | agregados; **cache Redis 24h** (`tools:stats`) |

### Testes — `tests/test_kl134_tools.py` (29, todos passando)
Cobrem os 16 casos do card: URL validator (4), rate limiter (unit + endpoint 429 com Retry-After),
timeout (unit + endpoint 504), os 5 endpoints happy-path (I/O mockado), 400 (sem/ inválido), 502
(inacessível), stats (dados agregados + **uso do cache**: 2º request não recomputa), builders puros
(DMARC múltiplo, cert autoassinado, bandas de grade LGPD, mapa de issuer). `python3 -m pytest
tests/test_kl134_tools.py` → **29 passed**. Suíte de gate (regressão): 75 passed. Collection global
sem erro de import (2378 testes).

## Validação no dev stack (curl real contra `klarim.net`)
- **ssl** → `valid:true`, TLSv1.3, grade **A**, issuer "Google Trust Services", 3 checks pass.
- **headers** → `6/7` (X-XSS-Protection ausente, corretamente `baixa`).
- **lgpd** → `6/8`, "Parcialmente adequado", 8 indicadores.
- **tech** → 5 techs (Cloudflare×2, Open Graph, Astro 7.0.7, Hostinger e-mail).
- **email** → `4/4` (SPF/DKIM/DMARC/MX pass).
- **stats** → shape correto (dev tem seed pequeno; em prod virão os números reais).
- **erros** → sem param 400, URL inválida 400.
- **rate limit** (redis vivo no dev) → 9×200 depois **429** com `Retry-After: 43`.
- **roteamento nginx** → `GET :3000/api/tools/stats` → **200**.

## Decisões e desvios (documentados)
1. **Nomes reais dos arquivos:** a spec citou `scanner/checks/ssl.py` e `security_gate/checks/
   surface.py` (inexistentes). Usei os reais: `scanner/tls_analyzer.py` (SSL), `scanner/
   privacy_checks.py` (LGPD), `scanner/tech_detector.py` (tech) e os checks 21/22/23 + `dns_util`
   (e-mail). Nenhum foi alterado.
2. **LGPD = 8 indicadores (não 7):** a engine (`privacy_checks`) tem 8 indicadores técnicos reais.
   Retornei os 8 (score `N/8`) com o grade proporcional — **sem fabricar** o "DMARC" que a spec
   misturou ao LGPD (DMARC é do tool de e-mail). Nenhum dado inventado.
3. **`context` = copy verificada:** os números do bloco `context` (74,5%/83,6%/99,1%; WordPress
   20,2%; Cloudflare 30,8%; base 115.849) são os REAIS informados no card, como constantes de copy.
   O `/api/tools/stats` calcula os agregados **ao vivo** do banco (fonte de verdade separada).
4. **Segurança:** endpoints públicos sem auth (por design de aquisição) — a única proteção é o
   rate limit 10/min/IP; timeout 15s; validação de domínio antes de qualquer I/O; sem SSRF (só
   GET/HEAD passivos via `base.fetch`/DNS, nunca sobre input não-validado).

## Arquivos
- **Novos:** `api/tools.py`, `tests/test_kl134_tools.py`, este relatório.
- **Alterados:** `api/main.py` (+2 linhas: import + `include_router`), `docs/API.md` (seção nova),
  `CLAUDE.md` (§9 subsistemas + índice de cards).

## Pendente (Prompt 2)
Frontend: 5 landing pages Astro consumindo estes endpoints (copy de aquisição, mobile-first,
CTA para o scanner completo).
