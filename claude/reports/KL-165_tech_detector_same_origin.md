# KL-165 — Tech detector: same-origin + múltiplos sinais (fix falso positivo WordPress)

**Status:** implementado e validado ao vivo · pronto para deploy
**Prioridade:** Highest · **Tipo:** Tarefa
**Arquivos:** `scanner/tech_detector.py`, `scanner/main.py`, `scripts/backfill_tech_stack.py`, `api/tools.py`, `tests/test_kl165_platform_same_origin.py`, `CLAUDE.md`, `docs/SECURITY.md`

---

## 1. Problema real

`telecomsip.com.br` foi classificado como **WordPress** pela Klarim. O dono respondeu ao
alerta apontando o erro. A investigação confirmou que ele está certo:

1. As **duas** referências `wp-content` no HTML da home são de **domínios de terceiros**:
   - `https://1000logos.net/wp-content/uploads/2017/03/Nokia-Logo.png`
   - `https://i0.wp.com/fiberhomebrasil.com.br/wp-content/uploads/2022/01/logo.png`
   (`i0.wp.com` = Jetpack Photon, um CDN de imagens usado por qualquer site — não implica WordPress.)
2. O `/wp-admin/` que retorna 200 é um **honeypot de segurança**, não um painel WP real.

### Causa raiz

O detector procurava `wp-content|wp-includes` no **HTML cru** (regex em `SCRIPT_PATTERNS`),
**sem verificar de qual domínio a URL era**. Qualquer imagem/logo/embed de terceiro com
`wp-content` no caminho gerava classificação de WordPress no domínio errado. É um erro de
**atribuição**: evidência do domínio X contada como se fosse do domínio analisado.

> Nota: o detector é **puro/passivo** — ele **não** faz requests a `/wp-admin` ou `/wp-json`.
> Logo o honeypot `/wp-admin` nunca influenciou a classificação; a causa 100% real foram as
> refs `wp-content` **cross-origin**. Confirmado ao vivo (ver §4).

---

## 2. O que foi feito

### Fix 1 — Same-origin obrigatório para evidência de plataforma

Novo helper puro em `scanner/tech_detector.py`:

```python
def is_same_origin(found_url, target_domain) -> bool:
    # URL relativa (sem host) → same-origin. Absoluta → compara pelo domínio
    # registrável (eTLD+1): apex/www/subdomínios do alvo contam; terceiros, não.
```

- `_extract_urls(html)` extrai as URLs de recurso do HTML (`src`/`href`/`content`/`url(...)`…).
- `_same_origin_url_blob(html, domain)` mantém **só** as URLs same-origin — os marcadores de
  plataforma baseados em URL são procurados **nesse blob**, nunca no HTML cru.
- `detect_tech_stack(...)` ganhou o parâmetro **`domain`** (opcional, retrocompatível).

### Fix 2 — WordPress exige 2+ sinais same-origin

Nova função `_detect_platforms` + `_PLATFORM_SPECS` (data-driven, extensível):

| Sinal | Tipo | Basta sozinho? |
|---|---|---|
| Meta `generator` = WordPress | forte (declaração da própria origem) | ✅ sim |
| Cookie `wp_settings`/`wordpress_logged_in`/`wp-postpass` | forte (setado pelo servidor) | ✅ sim |
| `/wp-content/` em URL **same-origin** | fraco | ➖ precisa de 2 |
| `/wp-includes/` em URL **same-origin** | fraco | ➖ |
| `wp-emoji-release` / `/wp-json` same-origin | fraco | ➖ |
| classe `wp-block-*` no markup | fraco | ➖ |

Regra: classifica se **≥1 sinal forte** OU **≥2 sinais** (`min_signals=2`). Um `/wp-admin` 200
sozinho (honeypot/SPA) ou uma única ref não classificam.

> Decisão: `generator`/cookie são **fortes** e bastam sozinhos — são declarações same-origin,
> impossíveis de vir de um embed cross-origin, então não pertencem à classe de falso positivo
> que o card ataca. Isso preserva a detecção correta de WordPress real que só expõe o generator.

### Fix 3 — Shopify e demais plataformas

- **Shopify:** removido o marcador `cdn.shopify.com` de `SCRIPT_PATTERNS` — ele é carregado
  **cross-origin** por *buy-button embeds* em sites que **não** são Shopify (o exato caso do
  card: "embed Shopify externo → NÃO Shopify"). Shopify agora vem só de sinais same-origin:
  header `x-shopify-stage` + cookie **`_shopify_*`** (o padrão de cookie foi ampliado de
  `_shopify_s` para a família toda, preservando recall de lojas reais).
- **Wix:** já detectado só por header (`x-wix-request-id`) → same-origin, sem mudança.
- **WooCommerce / Nuvemshop / VTEX / Joomla / Webflow:** mantidos como estão (revisados). São
  markup same-origin (body-class) ou marcas distintas de baixo volume/risco cross-origin; gatear
  seus CDNs de marca reduziria recall sem resolver a classe de falso positivo do card (que é
  causada por **substrings genéricas** — `wp-content` — e por **CDNs de embed** — `cdn.shopify.com`).

### Threading do `domain` nos callers

- `scanner/main.py::persist_tech_detection(..., domain=None)` → o scan worker passa
  `domain=domain_of(url)`; o **backfill** reusa o `domain` do payload GCS automaticamente.
- `scripts/backfill_tech_stack.py` (dry-run) → passa `domain=payload.get("domain")`.
- `api/tools.py::_tech_io` (tool público `/api/tools/tech`) → passa `domain=host`.

---

## 3. Testes

- **Novo:** `tests/test_kl165_platform_same_origin.py` (20 casos): `is_same_origin`
  (relativa/apex/www/subdomínio/terceiro/look-alike/sem-domínio/data:), WordPress cross-origin →
  não classifica, 2 sinais same-origin → classifica, 1 sinal fraco → insuficiente, generator/cookie
  forte sozinho → classifica, blog WP real (regressão), Shopify embed → não, Shopify header/cookie
  same-origin → sim, Astro-generator ≠ WordPress, serviços de terceiros (GA4/Stripe) seguem cross-origin.
- **Suite completa:** `pytest` **2406 passed, 1 skipped**; `npm run test:unit` **246 passed**. Zero regressões.

---

## 4. Validação ao vivo (os 4 alvos do card)

Rodado o detector real (fetch honesto + DNS) contra cada alvo:

| Alvo | Esperado | Resultado |
|---|---|---|
| **telecomsip.com.br** | NÃO WordPress | ✅ `wordpress? não` (as 2 refs `wp-content` confirmadas cross-origin: `1000logos.net`, `i0.wp.com`) |
| **br.wordpress.org** (WP real) | WordPress | ✅ `wordpress? SIM` (generator 7.2 + assets same-origin) |
| **wpbeginner.com** (WP real) | WordPress | ✅ `wordpress? SIM` (generator do plugin não é "WordPress", mas os 2+ sinais same-origin `wp-content`/`wp-includes` classificam) |
| **klarim.net** | Astro / NÃO WordPress | ✅ `astro` (generator 7.0.7), `wordpress? não` |
| Site com embed Shopify externo | NÃO Shopify | ✅ coberto por teste determinístico (`cdn.shopify.com` + `ShopifyBuy` inline, sem header/cookie → não classifica) |

Confirmação da causa em `telecomsip.com.br`: o HTML **contém** `wp-content` cru (o regex antigo
casaria → WordPress), mas `is_same_origin=False` para **ambas** as URLs → o novo detector não classifica.

---

## 5. Impacto na base e reclassificação

- Sites atualmente marcados WordPress: **~12.898** (`get_tech_adoption('wordpress')`). Uma parte
  é falso positivo por cross-origin.
- **Como a base se auto-corrige:** `site_tech_stack` grava tecnologia **por `scan_id`**, e as
  leituras (`get_tech_adoption`, `tech_summary_by_domain`) usam **o scan mais recente**. Logo,
  **ao re-escanear** um site, o novo scan não gera o `wordpress` falso e a classificação vigente
  passa a ser a correta — sem DELETE manual. O worker **rescan** (ciclo 24h, alvos ≥30 dias) faz
  isso gradualmente; para acelerar, enfileirar os alvos WordPress na `klarim:scan_queue`.
- **Follow-up recomendado (pós-deploy):** enfileirar re-scan dos ~12,9k alvos WordPress (ritmo
  200/h). Não-destrutivo, reusa o caminho de scan já provado. Deixado como passo operacional para
  não misturar mutação em massa da base com o fix.

---

## 6. Segurança

- **Passivo mantido:** nenhum request novo. O detector continua PURO (sem sondar `/wp-admin`/`/wp-json`).
- **Anti-honeypot / anti-evidência forjada:** a atribuição agora é à prova de refs de terceiros —
  empresas de segurança (que estão entre os alvos e sondam a plataforma) não conseguem induzir uma
  classificação errada plantando paths de plataforma em domínios de terceiros ou honeypots.
- **Sem exposição de dado sensível.** `docs/SECURITY.md` e `CLAUDE.md` atualizados.

---

## 7. Deploy

Deploy direto após validação (regra do card). Definição de pronto: push + GitHub Actions
(test+deploy) **100% verde**. `site_tech_stack` é por-scan → **não** requer flush de `scan:*`
(não muda scoring/semáforo). Registrar o deploy em `claude/DEPLOY_HISTORY.md` quando verde.
