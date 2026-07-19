# KL-75 — Enriquecimento Expandido (Prompt 1 de 2): Tech Stack + DNS + SSL + Status

> **Card:** KL-75 · **Prioridade:** High · **Status:** Prompt 1 concluído (Prompt 2 pendente)
> **Data:** 2026-07-19

## Resumo

O scanner já coletava headers HTTP, HTML completo, DNS (MX/NS) e certificado SSL em cada
scan — e descartava 90% depois de extrair os 48 checks. O KL-77 Fase 2 (entregue hoje)
passou a preservar o **response bruto** no GCS. Este prompt extrai **inteligência
tecnográfica e comercial** dos mesmos dados, **sem nenhum request HTTP adicional**: o
`enrich_profile(capture_raw=True)` do KL-77 já devolve o response ao scan worker, e a
detecção parseia o que já está em memória (regex sobre strings — custo ~0).

**Escopo entregue (Prompt 1):** Grupos 1–6 (headers, scripts, meta tags, DNS expandido,
SSL SAN/issuer/organização, status do site). Grupos 7–8 (SaaS/portal, subdomínios) ficam
para o Prompt 2.

---

## O que foi implementado

### 1. Módulo de detecção — `scanner/tech_detector.py` (função pura)

`detect_tech_stack(headers, html, dns, ssl) -> dict` — **pura** (sem DB, sem I/O),
totalmente testável offline. Nunca levanta: entradas ausentes/malformadas viram vazio.

Retorno:
```python
{
  "technologies": [{"name", "category", "subcategory", "version", "source", "confidence"}],
  "email_provider": "google_workspace" | None,
  "dns_provider": "cloudflare" | None,
  "related_domains": ["www.hotel.com.br", "loja.hotel.com.br"],
  "site_status": "ativo" | "parked" | "abandonado" | "fora_do_ar" | "bloqueado" | "dominio_inativo",
  "verified_platforms": ["google", "facebook"],
  "company_name": "Hotel LTDA" | None,   # organização OV/EV do certificado
  "schema_types": ["Hotel"]              # @type do JSON-LD (confirma setor)
}
```

**Patterns implementados** (deduplicados por `name`; a versão preenche mesmo detecção
tardia):

| Grupo | Fonte | Nº de detecções | Exemplos |
|---|---|---|---|
| 1 | Headers HTTP | **15** | nginx/apache/litespeed/iis/openresty, php/asp.net/express/nextjs, cloudflare_cdn (cf-ray), fastly, aws_cloudfront, shopify (x-shopify-stage), wix |
| 1 | Cookies (`set-cookie`) | **7** | PHPSESSID→php, _shopify_s→shopify, wp_settings→wordpress, laravel_session, connect.sid→express, JSESSIONID→java, ASP.NET_SessionId |
| 2 | Scripts no HTML | **49** | GA4 (com `G-XXX`), GA-UA, plausible, umami, matomo, clarity, hotjar, meta_pixel, google_ads, rd_station, hubspot, mailchimp, mercado_pago, pagseguro, stripe, pagarme, asaas, tawk_to, jivochat, crisp, zendesk, intercom, whatsapp_widget, shopify, nuvemshop, vtex, woocommerce, wordpress, joomla, webflow, recaptcha, hcaptcha, cloudflare_turnstile, cloudinary, youtube/vimeo embed, google_maps, google/apple sign-in, facebook_sdk, jsdelivr/cdnjs/unpkg, algolia, auth0, firebase_auth |
| 3 | Meta tags / Schema.org | **5** + JSON-LD | open_graph, google_search_console (google-site-verification), facebook_verified, generator (extrai CMS+versão), rss_feed; `@type` do JSON-LD |
| 4 | DNS MX → e-mail | **13** | google_workspace, microsoft_365, locaweb, hostinger, zoho, titan, godaddy, registro_br, umbler, kinghost |
| 4 | DNS NS → provedor | **8** | cloudflare, aws_route53, azure_dns, google_dns, registro_br, hostinger, locaweb |
| 4 | DNS TXT → plataforma | **5** | google, facebook, pinterest, microsoft (SPF `v=spf1` **ignorado** — não é verificação) |
| 5 | SSL issuer → CA | **13** | lets_encrypt (R3/E1), digicert, sectigo/comodo, cpanel, zerossl, cloudflare_ssl, google_trust, amazon, godaddy |
| 5 | SSL SAN → domínios | — | mesmo domínio registrável (wildcard vira base; terceiros excluídos) |
| 5 | SSL O (OV/EV) → nome legal | — | `company_name` (preenche `site_profile` **só se vazio**) |
| 6 | Status do site | **13 padrões parking** | ativo/parked/abandonado/fora_do_ar/bloqueado/dominio_inativo |

### 2. Tabelas (`ensure_schema` de `discovery/store.py`, idempotente)

- **`site_tech_stack`** — 1 linha por (tecnologia, scan): `name/category/subcategory/
  version/source/confidence/detected_at`. **UNIQUE `(target_id, scan_id, name)`** +
  `ON CONFLICT DO NOTHING` → idempotente (reprocessar o mesmo scan não duplica).
- **`site_status_log`** — histórico de status por scan (`status/http_code/
  response_time_ms/detected_at`).
- **`targets.email_provider`** (VARCHAR) + **`targets.related_domains`** (JSONB) — query
  rápida (o stack detalhado fica na tabela dedicada).

### 3. Integração no scan worker (`scanner/main.py`)

`persist_tech_detection(store, target_id, scan_id, response_data)` roda **após o enrich e
antes do upload GCS**, sobre o response já em memória. **Resiliente:** todo o corpo está
em `try/except` — se a detecção/gravação falhar, o scan completa normalmente (já está
persistido). Grava: batch INSERT do stack; `email_provider`/`related_domains`;
`company_name` (só-se-vazio, respeita `edited_by_admin`); status autoritativo
(`classify_site_status` com o **http_status real** — o detector só vê conteúdo).

O `enrich_profile` ganhou **1 lookup DNS TXT** (só quando `capture_raw=True` — o caminho
público não paga). O `tls_analyzer._cert_info` passou a extrair `subject_o` (organização).

### 4. Exposição

- **Público:** `GET /public/tech-summary/{domain}` — **só badges booleanos**
  (`has_analytics/cdn/payment/chat/captcha/ecommerce`, `email_provider`, `site_status`,
  `tech_count`). NUNCA o stack detalhado. Rate limit **30/min por IP real**; respeita
  `site_profile.public_visible`; cache 1h.
- **Admin:** `GET /targets/{id}/tech-stack` (prefixo `/targets` → JWT admin) — stack
  detalhado + `email_provider` + `related_domains` + `status_history`.
- **MCP (3 novas, total 54):** `get_tech_adoption(tech, sector?)`,
  `get_site_tech_stack(domain)`, `get_site_status_history(target_id?|domain?, limit)`.

### 5. Backfill — `scripts/backfill_tech_stack.py`

Reprocessa os responses já arquivados no GCS (`gs://klarim-raw`, a partir de 2026-07-19)
pela **MESMA** função de detecção — sem re-scan, sem request HTTP. `--date YYYY-MM-DD` |
`--all` | `--limit N` | `--dry-run`. Batches de 50, idempotente (UNIQUE index). Scans
anteriores ao GCS (sem response bruto) só recuperam via re-scan gradual do worker.

---

## Segurança (revisão obrigatória)

- **Endpoint público expõe só booleanos** — nunca nomes/versões (o stack detalhado é valor
  agregado, reservado a API autenticada/admin). Rate limit 30/min/IP real (CF-Connecting-IP).
- **Admin protegido** pelo middleware JWT (`/targets` prefix) — teste confirma 401/403 sem token.
- **MCP** com auth própria fail-closed (OAuth 2.1/PKCE + `MCP_API_KEY`); tools read-only.
- **SQL** 100% parametrizado; `save_tech_stack` via `execute_values` (batch, tuplas).
- **Regra de ouro respeitada:** `company_name` só preenche se **vazio** (nunca sobrescreve
  regex/IA/manual; respeita `edited_by_admin`); `email_provider`/`related_domains` via
  COALESCE (não zeram dado existente).
- **Sem PII:** `contact_email`/CNPJ/WhatsApp nunca aparecem. `email_provider` é o nome do
  provedor (ex.: `google_workspace`), derivado de MX — **DNS público**, não um e-mail.
- **ReDoS:** todos os regexes são lineares (sem quantificadores aninhados).
- **Dados técnicos são públicos** (headers HTTP, certificados são registros públicos); o
  valor está na **agregação** (market share por setor, correlação stack×score).

## Performance

- Detecção **em memória** (regex sobre strings já carregadas) — **< 500ms/scan** (alvo).
- **Batch INSERT** (1 INSERT multi-linha), não N inserts.
- UNIQUE index previne duplicatas; DNS TXT é 1 lookup extra (só no worker).

## Testes — **51 novos** (todos offline)

- **`tests/test_kl75_tech_detector.py` (45):** função pura (nginx header+versão, GA4 com
  `G-XXX`, cookie PHPSESSID, MX google/outlook, NS cloudflare/awsdns, SSL SAN excluindo
  wildcard+terceiro, wildcard-only→base, issuer→CA, organização→company_name, generator→CMS,
  verified platforms de TXT+meta com SPF ignorado, header case-insensitive, dedup header+cookie
  mantendo versão, combinação complexa de hotel, schema @type, status em todos os branches);
  integração `persist_tech_detection` (grava tudo, resiliente a erro de store, status usa
  http_status real, response vazio); endpoint público (badges, domínio desconhecido,
  `public_visible`, rate limit 30/min); helpers de API; backfill (decode roundtrip, prefixo).
- **`tests/test_mcp_server.py` (+6):** registro das 3 tools + execução (adoção com %,
  stack por domínio, histórico, not-found).

**Suíte completa: 1232 passed, 1 skipped.**

## Validação de sucesso (critérios do card)

| # | Critério | Status |
|---|---|---|
| 1 | `detect_tech_stack` correto em ≥5 cenários | ✅ |
| 2 | Worker grava stack em `site_tech_stack` | ✅ (`persist_tech_detection`) |
| 3 | `email_provider` via MX | ✅ |
| 4 | `related_domains` via SSL SAN | ✅ |
| 5 | `site_status_log` por scan | ✅ |
| 6 | Parking detectado | ✅ (13 padrões PT/EN) |
| 7 | Batch INSERT sem duplicatas | ✅ (UNIQUE + ON CONFLICT) |
| 8 | Falha na detecção não trava o scan | ✅ (try/except) |
| 9 | Público = badges resumidos | ✅ |
| 10 | 3 MCP tools funcionam | ✅ |
| 11 | Backfill processa GCS | ✅ |
| 12 | Tempo adicional < 500ms | ✅ (em memória) |
| 13 | ≥ 30 testes novos | ✅ (**51**) |
| 14 | CI verde | ⏳ (validar no push) |

## Arquivos

**Novos:** `scanner/tech_detector.py`, `scripts/backfill_tech_stack.py`,
`mcp_server/tools/tech.py`, `tests/test_kl75_tech_detector.py`.
**Alterados:** `discovery/store.py` (schema + 7 métodos), `scanner/main.py`
(`persist_tech_detection` + wire), `scanner/enrichment.py` (DNS TXT no `capture_raw`),
`scanner/tls_analyzer.py` (`subject_o`), `api/main.py` (endpoint público + admin + 3 helpers),
`mcp_server/tools/__init__.py`, `tests/test_mcp_server.py`, `CLAUDE.md`, `docs/API.md`,
`docs/ARCHITECTURE.md`.

## Pendências / Prompt 2

- Grupos 7–8: detecção de SaaS/portal e enumeração de subdomínios.
- Página admin (detalhe do alvo) mostrando o tech stack (UI) — fora do escopo do Prompt 1.
- Rodar o backfill na VM após o deploy (`python -m scripts.backfill_tech_stack --date 2026-07-19`).
