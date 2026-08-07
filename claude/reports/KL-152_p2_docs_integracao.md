# KL-152 (Prompt 2/3) — Páginas de documentação de integração por CI/CD

## Contexto

O wizard (P1) guia o dev no primeiro scan; as docs são a referência permanente. Este prompt entrega
7 páginas públicas (sem login) em `/docs/gate/`, com layout de sidebar compartilhado, SEO e snippets
copiáveis por plataforma.

## As 7 páginas (Astro `.md` renderizado)

`web/src/pages/docs/gate/{github-actions,gitlab-ci,bitbucket,jenkins,manual,api,troubleshooting}.md`

Cada página é **markdown puro** (regra do card: conteúdo em markdown, não HTML hardcoded), com
frontmatter `layout`/`title`/`description`/`slug`. Astro renderiza o markdown dentro do
`DocsLayout.astro`. As 5 primeiras são guias por plataforma (pré-requisitos → secret → snippet
YAML/bash → config avançada → output PASS/FAIL → FAQ); `api` é a referência REST (endpoints, request/
response, tabela de erros); `troubleshooting` é a tabela de problemas/causa/solução.

**Sem Shiki** (`markdown.syntaxHighlight: false` no `astro.config.mjs`): blocos de código simples,
estilizados por `.docs-prose pre` (dark constante, igual ao wizard) — evita `<span style>` por token
e mantém consistência. Nenhum outro `.md` existia, então a mudança é segura.

## Layout + navegação

- **`web/src/layouts/DocsLayout.astro`** — Base + Header + **sidebar** + `<slot>` (markdown) + Footer.
  Sidebar sticky no desktop (coluna à esquerda), rolagem horizontal no mobile; item ativo destacado
  (`aria-current="page"`). SEO por página (title/description/canonical) + **JSON-LD `TechArticle`**.
- **`web/src/lib/gate/docsNav.js`** (PURO) — fonte única da sidebar (3 grupos: Plataformas /
  Referência / Ajuda) e dos slugs. **+4 `node --test`** (`docsNav.test.js`): 7 slugs, `docHref`,
  `isActiveDoc`, forma dos grupos.
- **`.docs-prose`** em `global.css` — h1/h2/h3, listas, código inline, blocos, **tabelas**, citações
  (theme-aware, KL-87).
- **`web/public/docs-copy.js`** — botão "Copiar" em cada `<pre>` (progressive enhancement, CSP
  `script-src 'self'`; a página funciona sem JS).

## Integração no site

- **Header**: link **"Docs"** (→ `/docs/gate/github-actions`) nos dois estados de auth.
- **Footer**: link **"Documentação"**.
- **Landing `/security-gate`**: "Ver documentação" agora aponta para `/docs/gate/github-actions`
  (antes era âncora `#como-funciona`).
- **Sitemap**: as 7 URLs entram no `_SITEMAP_STATIC` (FastAPI, KL-131).
- **Nginx**: `docs` na allowlist de conteúdo + `docs-copy\.js` na allowlist de JS, nos DOIS configs
  (`http.conf` e `https.conf.template`). `nginx -t` validado local (HTTP + HTTPS renderizado).

## Segurança

- Páginas **públicas** (sem auth) — só referência; nenhuma key real. Os snippets referenciam o
  **secret do CI** (`${{ secrets.KLARIM_KEY }}` no GitHub, `$KLARIM_KEY` nos demais); a página
  `manual` usa o placeholder `KLM_sua_key_aqui`.
- `docs-copy.js` externo, coberto por `script-src 'self'` (sem inline).
- Contato de suporte no troubleshooting: `contato@klarim.net` (o `seguranca@` foi **descontinuado**
  como remetente — CLAUDE.md; usar a keyword "seguranca" eleva spam score). Desvio consciente do
  texto do card.

## Testes

- **`web/src/lib/gate/docsNav.test.js`** (+4 `node --test`) → `test:unit` **166**.
- **`tests/test_kl152_docs_sitemap.py`** (+2 pytest): as 7 URLs no `sitemap-static.xml` + contagem.
- `npm run build` OK (7 `.md` compilam); `nginx -t` OK nos 2 configs.
- **Validado no browser** (`docker-compose.dev.yml`): 7 páginas 200, sidebar com 7 links + item ativo
  destacado, navegação interna, markdown renderizado (headings/listas/**tabelas**/código não
  escapado), botão "Copiar" injetado, "Docs" no header, SEO (title/description/canonical/TechArticle),
  contraste correto no tema claro, **zero erro no console**.

## Escopo

Prompt 2/3. **Não incluso** (Prompt 3): Enterprise workflow. Nenhuma mudança de lógica de backend
(só o `_SITEMAP_STATIC`).
