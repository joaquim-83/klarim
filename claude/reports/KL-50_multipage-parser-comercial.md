# KL-50 (Camadas 1+2) — Multi-page crawl + parser expandido de dados comerciais

**Card:** KL-50 · **Prioridade:** CRÍTICA (desbloqueia a plataforma pública).
**Problema:** de 10.359 alvos, 39% em `sem_contato` e 77,5% com plataforma `unknown`.
O crawler fazia 1 GET na homepage e só buscava `mailto:`. A maioria do contato de PME
está em páginas internas e formatos não parseados (JSON-LD, `tel:`, `wa.me`, CNPJ).

---

## Camada 1 — Multi-page crawl

`discovery/contact.py`: o `extract_email` **já** caía para páginas de contato; expandi
`_CONTACT_PATHS` de 4 para **8** (`contato, contact, sobre, about, quem-somos,
sobre-nos, fale-conosco, atendimento`). Isso aumenta a taxa de e-mail na **descoberta**
(tira alvos de `sem_contato` já no pipeline). `scanner/profiler.crawl_contact_pages(url)`
busca homepage + as 8 internas (HTTP 200, segue 1 redirect, rate limit 1 req/s por
domínio — reusa `scanner.checks.base.fetch`).

## Camada 2 — Parser expandido (`scanner/profiler.py`, puro + testável)

- **A. Contatos** (`extract_contacts`): e-mail (reusa a extração/ranking hardened do
  discovery, MX-validada, KL-19/24), telefone (`tel:` formatado `(DD) 9999-9999` +
  regex no texto visível), whatsapp (`wa.me`/`api.whatsapp.com`/`data-phone`),
  endereço (JSON-LD `PostalAddress` > regex BR no texto visível — script/style
  removidos antes do regex), **CNPJ com validação de dígitos** (`validate_cnpj`).
- **B. JSON-LD** (`extract_structured_data`): parseia todos os `<script type=
  application/ld+json>` (incl. `@graph`), `json.loads` com try/except; extrai
  name/telephone/email/address/openingHours/sameAs/logo/description; **@type →
  setor** (Hotel→hotel, LegalService→juridico, …) — mais confiável que regex.
- **C. Redes sociais** (`extract_social_links`): handles de Instagram/Facebook/
  LinkedIn/YouTube/TikTok (ignora paths reservados: sharer, tr, p, login…) + Google
  Maps + `has_blog` (RSS ou `/blog`) + `has_app` (App Store/Play).
- **D. Tecnologias** (`extract_technologies`): ~30 fingerprints **case-insensitive**
  (GA4, GTM, Hotjar, JivoChat, Tidio, PagSeguro, Mercado Pago, WooCommerce, Nuvemshop,
  VTEX, RD Station, FB Pixel, Omnibees, Cookiebot…) agrupados por categoria em JSONB.
- **E. Infraestrutura** (`extract_infrastructure`): provedor de e-mail (MX→google_
  workspace/microsoft_365/locaweb/…), DNS (NS→cloudflare/route53/registro_br/…), CDN
  (headers cf-ray/x-amz-cf-id/fastly), CA. `dns_util.resolve_mx`/`resolve_ns` (novos,
  mockáveis).
- **F. Maturidade** (`calculate_maturity_score`): 0–10 dos sinais (HTTPS+HSTS,
  analytics, ≥2 redes, chat/whatsapp, pagamento, blog, cookie consent, e-mail
  profissional, responsivo, security≥80).

`build_profile(url, homepage_html?, headers?, mx?, ns?, ca?, security_score?)`
orquestra tudo e devolve o dict pronto para `site_profile`. Nunca levanta (degrada).

## Tabela `site_profile` + store

Migration em `_SCHEMA` (SERIAL/INTEGER — o schema **não** usa UUID; a spec do card
assumia UUID, adaptei). 1 perfil por `target_id` (UNIQUE, ON DELETE CASCADE), índices
por CNPJ e maturidade. `upsert_site_profile`/`get_site_profile` no store.

## Integração no pipeline

- **Scan worker** (`scanner/main.py`): após salvar o scan, `_enrich_profile` busca
  headers da homepage + MX/NS + roda `build_profile` → `upsert_site_profile`.
  Best-effort (erro só loga, não afeta o scan). +~9 GETs por scan (rate-limited; o
  worker já espera 72s entre scans).
- **Discovery** (`contact.py`): os 8 paths aumentam o e-mail encontrado na descoberta.
- **API/MCP:** `GET /targets/{id}` anexa `profile`; `GET /targets/{id}/profile`;
  MCP `get_site_profile(target_id)`.

## Reprocessamento — `scripts/enrich_batch.py`

`docker compose exec api python scripts/enrich_batch.py --limit 500`: itera
`sem_contato`, crawl + extract_email (MX) + build_profile; achou e-mail →
`update_target_email` (volta a `discovered`) + enfileira scan; sempre grava o perfil.
Log em `enrichment_batch.log`. 500/dia (~8 dias para os ~4k). Idempotente.

## Testes (`tests/test_profiler.py`, 20)

Contatos (email same-domain, tel, whatsapp, CNPJ, endereço), `validate_cnpj` (válido/
inválido/todos-iguais), JSON-LD (Hotel/LegalService/@graph/malformado), redes (5 +
maps + blog + app + paths reservados), tecnologias (GA4/PagSeguro/JivoChat/Woo, case-
insensitive, headers), infra (MX/NS/CDN/CloudFront/sem-match), maturidade (7 sinais→7,
free email→0), crawl (coleta 200s, fallback all-404, reusa homepage dada), edge cases
(HTML vazio/malformado), build_profile end-to-end. Sem dependências externas.

## Meta

`sem_contato` 39%→<15% e `unknown` 77,5%→<40% (via mais e-mails na descoberta + o
`enrich_batch` nos ~4k existentes + o perfil rico que alimenta perfis públicos).

## Arquivos

**Novos:** `scanner/profiler.py`, `scripts/enrich_batch.py`, `tests/test_profiler.py`,
este relatório. **Alterados:** `discovery/contact.py` (8 paths), `scanner/checks/
dns_util.py` (MX/NS), `discovery/store.py` (tabela + métodos), `scanner/main.py`
(enrich), `api/main.py` (endpoints), `mcp_server/tools/targets.py` (tool), `claude.md`,
`README.md`.
