# KL-65 — SEO e reputação: Schema.org + security.txt + sinais de autoridade

**Card:** KL-65 · **Prioridade:** URGENTE · **Data:** 2026-07-15

O AI Overview do Google classificava `klarim.net` como "suspeito / associado a phishing" —
uma **alucinação** (confundiu "Klarim" com "Clarim", jornal). Este card dá ao Google
sinais semânticos claros de que o Klarim é uma **empresa legítima de segurança web**.
**Só frontend Astro + config Nginx** — nenhuma lógica de backend (Python) alterada.

## Parte 1 — Schema.org (JSON-LD)

O `Base.astro` (layout público; o admin usa `AdminLayout`, **sem** isto) passou a emitir
JSON-LD estruturado em **todas** as páginas públicas:
- **Organization** + **WebSite** — sempre (landing, /sobre, /termos, /privacidade, perfis).
  `Organization` traz `knowsAbout` (Web Security, LGPD, SSL/TLS, OWASP…), `contactPoint`,
  `areaServed: BR`, `foundingDate`.
- **SoftwareApplication** (`applicationCategory: SecurityApplication`, `offers` grátis,
  `aggregateRating`) — só na **landing** (`index.astro`).
- **WebPage + Review** — em cada **perfil público** `/site/{domain}` (SSR preenche
  `{domain}`/`{score}`): o Klarim é o `author` da `Review`, `itemReviewed` é o site avaliado,
  `ratingValue` = score/100. Reforça que o Klarim **avalia** sites (não é phishing).

O `jsonLd` do `Base` passou a aceitar objeto **ou array**; a landing/perfil injetam o
específico e o `Base` sempre prepende Organization+WebSite. JSON-LD é `type=ld+json`
(**dado**, não script) → não é governado pela CSP `script-src` (sem hash).

## Parte 2 — security.txt (RFC 9116)

- `frontend/nginx/security.txt` (Contact seguranca@, Expires 2027-07-15, Preferred-Languages,
  Canonical, Policy → /termos), **COPY** no Dockerfile → `/etc/nginx/klarim_security.txt`.
- **Nginx** `location = /.well-known/security.txt { alias …; default_type text/plain; }` nos
  **3 blocos** (http.conf + os 2 server blocks 443 do https.conf.template: principal +
  painel). O `location =` (exato) **vence** o `~ /\.` que senão devolveria 404. Mesmo padrão
  do `mta-sts.txt`.

## Parte 3 — humans.txt

`frontend/public/humans.txt` (equipe, site, sobre) → servido em **`/humans.txt`** pelo
`location /` (try_files) do container web — sem regra Nginx (não está sob `/.` nem numa
rota Astro).

## Parte 4 — Meta tags + og-image

- `Base.astro`: adicionado `<meta name="author" content="Klarim">`; o **default** do
  `og:image` passou a `https://klarim.net/og-image.png` e o `twitter:card` a
  `summary_large_image`. OG/Twitter (title/description/url/image/locale/site_name) já
  existiam e foram mantidos.
- **`frontend/public/og-image.png` (1200×630)** criado com Pillow: fundo dark `#0D1117`,
  beacon laranja, wordmark **KLA**·`R`·**IM**, tagline "Segurança web para o Brasil" +
  "Scanner passivo · 48 verificações · gratuito · 100% brasileiro". Servido em `/og-image.png`.

## Parte 5 — Página /sobre

Já era completa (o que é, como funciona, para quem, o que NÃO é, contato). Reforçada com
uma seção **Diferenciais** (gratuito · em português/LGPD · sem instalação/invasão) e um link
para a `security.txt` — reforço explícito de legitimidade.

## Parte 6 — robots.txt

`web/public/robots.txt` (servido pelo Astro em `/robots.txt`): `Allow: /` + `Disallow:
/painel/`, `/api/admin/`, `/mcp/` + `Sitemap: https://klarim.net/sitemap.xml`.

## Validação

- `nginx -t` OK em `http.conf` e no `https.conf.template` renderizado (o job `nginx-check`
  do CI repete). A location da security.txt aparece 3× (http + 2 blocos https).
- og-image.png (32 KB, 1200×630) inspecionado visualmente.
- Pós-deploy (validação do card): `curl klarim.net | grep ld+json | wc -l` ≥ 3 (Org+WebSite+
  SoftwareApplication na landing); `curl klarim.net/.well-known/security.txt`;
  `curl klarim.net/robots.txt`; `curl klarim.net/site/{dominio} | grep ld+json` ≥ 3;
  `og:type`/`og:description`. Depois: reenviar o sitemap no Search Console e pedir
  reindexação da landing.

**Regra do card:** nenhum backend Python alterado (só `web/` Astro, `frontend/nginx` +
`frontend/public` + Dockerfile). Nenhum endpoint novo (arquivos estáticos + JSON-LD). O
JSON-LD dos perfis usa os dados que o SSR já busca (`/public/profile/{domain}`), sem lógica
nova.
