# KL-66 — Fix exibição de contato nos perfis públicos

**Card:** KL-66 · **Prioridade:** ALTA · **Data:** 2026-07-15
**Escopo:** só frontend Astro (`web/src/pages/site/[domain].astro`). **Zero backend.**

## Parte 1 — Diagnóstico

**A API já retorna todos os campos de contato públicos.** `_PUBLIC_PROFILE_FIELDS`
(`api/main.py`) expõe no objeto `profile`: `description, business_type, company_name, tags,
maturity_score, phone, address, instagram, facebook, linkedin, youtube, tiktok,
google_maps_url, logo_url`. E **corretamente NÃO expõe** `contact_email`, `cnpj`,
`whatsapp`, `commercial_email` (regra de privacidade §39 / KL-51 f4) — confirmado ao vivo.

**O problema era 100% frontend:** a página `/site/{domain}` renderizava só um bloco
minúsculo, **no rodapé**, com emojis e **sem links**, mostrando apenas `phone/address/
instagram` — **ignorando** facebook, linkedin, youtube, tiktok, google_maps_url, logo_url e
company_name.

**Volume de dados (6.298 perfis) — o fix tem alto impacto:** phone 4.313 (68%), instagram
3.166 (50%), address 2.513 (40%), facebook 1.885 (30%), linkedin 1.018 (16%), youtube 997
(16%), logo 1.413 (22%), google_maps 1.385 (22%), tiktok 313 (5%). (whatsapp 2.893 —
extraído, mas **não** exposto no público.)

**Formatos armazenados** (profiler): `instagram/tiktok` = handle sem `@`; `facebook` = nome
da página; `linkedin` = slug (o profiler perde a distinção `company`/`in`); `youtube` =
caminho com prefixo (`c/`, `@`, `channel/`, `user/`); `phone` = string formatada;
`google_maps_url`/`logo_url` = URLs completas.

## Parte 2 — API pública

**Nada a corrigir.** Todos os campos públicos de contato já são retornados. WhatsApp/CNPJ/
e-mail **permanecem fora** (privacidade + a regra do card "não expor WhatsApp pessoal").

## Parte 3 — Nova seção de contato

Card **"Contato"** com destaque, **acima** do "Sobre este site" (a primeira coisa que o
dono vê depois do score):
- **Ícones SVG inline** (Feather-style, `currentColor` em laranja) — sem emojis; injetados
  por `set:html` de **strings estáticas** (o label/href do perfil passam pela interpolação
  do Astro, que escapa — sem XSS).
- **Links clicáveis:** telefone → `tel:{só dígitos}`; endereço → Google Maps (se
  `google_maps_url`, senão texto); redes → URL da rede montada do handle. **Social/maps/site
  abrem em nova aba** (`target="_blank" rel="noopener noreferrer"`).
- **Grid 2 colunas** no desktop, empilha no mobile.
- **Só campos com valor** — sem "Telefone: —". **Se não há NENHUM contato, o card não
  aparece** (`hasContact`).
- **Guard anti-lixo no endereço:** alguns `address` vieram com **classes CSS raspadas por
  engano** (ex.: `av navbar-nav d-flex …`). Só exibe se parecer endereço (tem dígito ou
  vírgula). É um bug de extração do **profiler** — diagnóstico da Parte 4, **fix é card
  futuro** (não mexi no backend).

**Monogram (logo):** a inicial de `company_name`/domínio num círculo laranja, ao lado do
domínio no topo, + `company_name` como subtítulo. **⚠️ O logo real (`logo_url`) não pode
ser exibido só no frontend:** a CSP dos perfis é `img-src 'self' data:` (confirmado), então
uma `<img>` externa é **bloqueada**. Exibir a logo real exigiria um **proxy de imagem no
backend** (fora do escopo "só frontend") — fica como card futuro. O monogram é a solução
CSP-safe, local e consistente com o dark mode.

## Parte 4 — Profiler (só diagnóstico)

O profiler extrai contato de `tel:`, `wa.me`/`data-phone`, links de redes sociais, JSON-LD
(`telephone`/`address`/`logo`) e Google Maps. Cobertura razoável (68% com telefone), mas:
(1) o `address` às vezes pega classes CSS (extração frágil); (2) o `linkedin` perde
`company`/`in`. **Não corrigido aqui** (card futuro), conforme o prompt.

## Parte 5 — Validação (pós-deploy)

- Perfil com dados (ex.: `casadoconstrutor.com.br`) mostra o card **Contato** com telefone,
  Instagram, Facebook, LinkedIn, YouTube e o site, com links.
- Perfil sem contato (ex.: `igoove.com`) **não** mostra o card (nada de seção vazia).
- `grep target=_blank` presente para social/site.

## Segurança

Só são exibidos os campos que a API já devolve no perfil **público** — que **exclui**
`contact_email`, `cnpj` e `whatsapp` (privacidade §39). Telefone/endereço/redes são dados
que o **próprio site publica**. Todos os `href` são construídos de handles restritos
(`[A-Za-z0-9._-]`), `tel:` só com dígitos, e URLs externas passam por `isSafeUrl` (só
`http(s)://`) — sem `javascript:`/`data:`. Links externos com `rel="noopener noreferrer"`.

**Regra do card:** nenhum backend Python alterado; nenhum dado sensível novo exposto.
