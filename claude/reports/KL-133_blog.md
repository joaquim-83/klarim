# KL-133 — Blog com conteúdo no banco e publicação via MCP

**Data:** 2026-07-31 · **Status:** ✅ INFRA completa + 1 draft de teste; **1926 pytest / 142 node**,
`nginx -t` OK (http+https), build OK. **Deploy:** via commit/push (CI/CD). **Sem escrever artigos**
(o card pede só a infraestrutura + 1 draft).

---

## O quê

Blog editorial com o conteúdo (markdown) no banco, publicável via **MCP** (Claude) ou API admin —
para capturar busca informacional ("meu site é seguro?", "o que é HTTPS") usando o dado proprietário
de 74k sites. Sem painel visual (MCP + API bastam, rule 5).

## Backend
- **Tabela `blog_posts`** (`discovery/store.py::ensure_schema`): slug único, title/subtitle/content
  (markdown)/meta_description/og_image_url/category/tags[]/status(draft·published·archived)/author/
  data_snapshot(JSONB)/reading_time_min/published_at/created_at/updated_at + 3 índices.
- **Helpers puros:** `_blog_slugify` (NFKD sem acento, `[a-z0-9-]`, ≤200) · `_blog_reading_time`
  (`ceil(palavras/200)`, mín 1).
- **Store CRUD:** `create_blog_post` (gera slug + reading_time; `status='published'`→`published_at=NOW()`)
  · `update_blog_post` (partial, whitelist; publicar seta `published_at` se nulo; conteúdo → recalcula
  reading_time) · `archive_blog_post` · `get_blog_post_by_id` · `get_blog_post_by_slug(published_only)`
  · `list_published_blog_posts` · `list_all_blog_posts`.
- **Endpoints** (`api/main.py`):
  - Público: `GET /blog/posts` (paginado, **sem** o corpo), `GET /blog/posts/{slug}` (**404 se draft**),
    `GET /blog/rss.xml` (RSS 2.0, 20 últimos, `application/rss+xml`, RFC 2822). RL **30/min/IP**.
  - Admin (JWT via prefixo `/admin` — middleware existente): `POST` (201; 409 slug duplicado; 422 sem
    title/content), `PUT` (partial), `DELETE` (arquiva), `GET` (lista todos os status). RL **10/min/IP**.
  - `GET /sitemap-blog.xml` (cache Redis 1h) + adicionado ao **sitemapindex** do KL-131.

## MCP (`mcp_server/tools/blog.py` — 5 tools)
`create_blog_post` · `update_blog_post` · `list_blog_posts` · `get_blog_post` (por id/slug) ·
`archive_blog_post`. Registradas em `tools/__init__.py`. Datetimes → ISO no retorno.
**⚠️ Pós-deploy: reconectar o MCP** (Settings) p/ as tools aparecerem.

## Frontend (Astro SSR)
- `/blog` (`web/src/pages/blog/index.astro`): listagem paginada (card: categoria/título/subtítulo/data/
  tempo de leitura/autor), link RSS.
- `/blog/{slug}` (`web/src/pages/blog/[slug].astro`): artigo com **`web/src/lib/blog.js::renderMarkdown`**
  (`marked` + `sanitize-html`, allowlist estrita → remove `<script>`/`<iframe>`/`on*=`/`javascript:`
  — **XSS, rule 7**; suporta tabelas, código, imagens, headings, listas, links). **Schema.org Article**,
  **OG `article`**, **CTA de scan**, **sidebar por categoria**, **breadcrumb**, **canonical** (Base.astro).
  404 (noindex) se o post não existe/publicado. Estilos do markdown em `<style>` scoped (bundlado, CSP-safe).
- **Deps novas:** `marked` + `sanitize-html` (rodam no SSR/Node; não incham o bundle do cliente).

## Nginx
- `blog` na allowlist Astro (`http.conf` + `https.conf.template`) → `/blog`, `/blog/{slug}` ao Astro.
- `location = /blog/rss.xml` → **FastAPI** (exato, vence a regex do blog). `/sitemap-blog.xml` já cai
  na regex `^/sitemap[...]\.xml$` do KL-131. `nginx -t` valida http + https renderizado.

## Segurança (regra de 2026-07-15)
- Admin CRUD gateado pelo middleware `/admin` (JWT `typ=admin`); público read-only. Rate limits.
- **Sanitização do markdown** (sanitize-html, allowlist) + **CSP estrita** (2ª camada: inline script sem
  hash é bloqueado). data_snapshot é JSONB parametrizado; slug/tags parametrizados (sem injeção).
- `contact_email`/PII não entram no blog.

## Testes
- **Backend** (`tests/test_kl133_blog.py`, +14): slugify/reading_time; fluxo create draft→(não público)→
  publish→(público+RSS+sitemap); 409 slug duplicado; 422 sem campos; 401 sem auth; archive some do público;
  RSS válido (`<channel>`/`<item>`/pubDate); sitemap-blog; paginação/categoria.
- **Frontend** (`web/src/lib/blog.test.js`, +10): markdown (headings/tabelas/código); **remove
  script/iframe/on-handler/javascript:**; rel de segurança nos links; categoria/data/sidebar.
- `nginx -t` OK; `npm run build` OK (blog SSR + marked/sanitize-html); `pytest` **1926**.

## Validação pós-deploy
1. `docker compose exec api python -m scripts.seed_blog_draft` → cria o draft (NÃO publica).
2. Publicar o draft via MCP (`update_blog_post(id, status='published')`) ou `PUT /admin/blog/posts/{id}`.
3. `https://klarim.net/blog` lista o post; `/blog/{slug}` renderiza o markdown (tabela+código);
   view-source tem `Article` JSON-LD + OG `article` + canonical; `/blog/rss.xml` (`application/rss+xml`);
   `/sitemap-blog.xml` + o sitemapindex referencia-o.
4. Reconectar o MCP p/ as 5 tools aparecerem. Fechar KL-133 no Jira.

## Arquivos
- Backend: `discovery/store.py` (tabela + helpers + 7 métodos), `api/main.py` (endpoints + RSS + sitemap-blog),
  `mcp_server/tools/blog.py` (+ `__init__`), `scripts/seed_blog_draft.py`, `tests/test_kl133_blog.py`.
- Frontend: `web/src/lib/blog.js` (+ `.test.js`), `web/src/pages/blog/index.astro`,
  `web/src/pages/blog/[slug].astro`, `web/package.json` (marked + sanitize-html).
- Nginx/docs: `frontend/nginx/{http.conf,https.conf.template}`, `CLAUDE.md`, `docs/API.md`, este relatório.
