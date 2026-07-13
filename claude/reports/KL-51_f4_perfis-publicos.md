# KL-51 Fase 4 — Perfis públicos SEO + og:image + sitemap + notificação

> Transforma os ~18k sites em landing pages indexáveis (tráfego orgânico + viralidade).
> **Regra de linguagem:** o Klarim avalia a segurança do **SITE**, não do negócio.

## Parte 1 — Página `/site/{dominio}` (Astro SSR)

`web/src/pages/site/[domain].astro` (`prerender=false`) faz **uma** chamada ao backend
agregado **`GET /public/profile/{domain}`** e renderiza: score + semáforo, "Sobre este
site" (descrição da IA, tipo, CNAEs, tags, maturidade, plataforma), benchmark do setor,
CTA "É o seu site? Reivindicar", contatos públicos e disclaimer. Estados:
- `ok` → perfil completo (indexável).
- `not_found` / `not_scanned` → "ainda não analisado" + CTA `/scan?url={domain}` (noindex).
- `discarded` → "não disponível" (noindex).

**Endpoint `GET /public/profile/{domain}`** (público — `/targets` é admin, então a rota é
`/public/profile/`): retorna alvo (score/semáforo/plataforma/setor, **sem
contact_email**), perfil (`_PUBLIC_PROFILE_FIELDS` — **sem cnpj/commercial_email/whatsapp**),
CNAEs e benchmark. O semáforo vem do último scan (fallback por score).

`/score/{dominio}` → **301** para `/site/{dominio}` (URL curta de compartilhamento).

## Parte 2 — og:image dinâmico

**`GET /og/{dominio}.png`** (1200×630): `_og_svg` monta um card SVG (logo beacon, domínio,
score grande na cor do semáforo, descrição) → PNG via **cairosvg** (reusa o cairo do
WeasyPrint; import **lazy** para o CI/suite não exigir libcairo). Cache **em processo 24h**
+ `Cache-Control: public, max-age=86400`. **Fail-open:** alvo sem score / render falho →
302 para o favicon. Servido via `/api/og/` (location `/api/` já existente). Escolhi
cairosvg (Python) em vez de Satori (Node) porque o cairo já está na imagem — zero infra nova.

## Parte 3 — Sitemap XML

`web/src/pages/sitemap.xml.js` (SSR): páginas estáticas + 1 URL por perfil público, com
`lastmod` do último scan. Domínios de **`GET /public/sitemap-domains`**
(`store.list_public_profile_domains`: `status IN ('scanned','alerted')` + `site_profile`;
exclui descartado/sem_contato). `robots.txt` já referencia `Sitemap:
https://klarim.net/sitemap.xml`. **Passo manual do dono:** submeter no Google Search Console.

## Parte 4 — Notificação ao dono

**`POST /notify/profile-view {domain}`** (fire-and-forget): envia o aviso "alguém verificou
a segurança do seu site" via Resend (`send_profile_view` + `profile_view.html`). **Rate
limit 1/domínio/24h** (Redis `SET notify:{domain} NX EX 86400`). **Não** notifica: sem
e-mail, `descartado`, `unsubscribed`, ou e-mail que já é de **usuário registrado**. Opt-out
reusa `/api/unsubscribe` (KL-12). O `[domain].astro` chama `/notify` no SSR (best-effort).

## Parte 5-6 — Compartilhamento + Nginx

`/score/` (301). Nginx: `location ~ ^/(site|score|sitemap\.xml)(/|$)` → Astro, com os
security headers (include) + `Cache-Control: public, max-age=300` (perfis são cacheáveis,
sem formulário). og via `/api/og/`.

## Parte 7 — Engenharia de dados

`track.js` dispara **`profile_view`** (com o domínio) nas páginas `/site/` — mede tráfego
dos perfis. As notificações e CTAs (`data-cta="claim"|"verify-other"`) ficam disponíveis
para eventos.

## Base.astro + CSP

`Base.astro` ganhou `ogImage`/`ogType`/`twitterCard`/`fullTitleOverride`/`jsonLd`. O
**structured data** é `<script type="application/ld+json">` — **dado**, não script
executável, então **não** é governado pela CSP `script-src` (não precisa de hash). O perfil
não tem islands React → o único script inline continua sendo o do Header (hash já na CSP).

## Testes

`tests/test_kl51_f4_profiles.py` (offline, TestClient + FakeStore): og SVG válido; perfil
nos 4 estados; **privacidade** (sem cnpj/commercial_email/whatsapp/contact_email no
payload); normalização de `www.`; sitemap-domains (lastmod); notify ok; og fail-open (302).
O render real do PNG precisa do libcairo (ausente no CI) — testado o SVG + o fail-open.
As páginas `.astro` são validadas pelo `build-web` do CI.

## Verificação pós-deploy

`GET /site/{dominio}` (200, SEO/og completos), `GET /api/og/{dominio}.png` (image/png),
`GET /sitemap.xml` (XML com perfis), `/score/x` (301), `/public/profile/x` sem dados
privados, e o `profile_view` em `site_events`.
