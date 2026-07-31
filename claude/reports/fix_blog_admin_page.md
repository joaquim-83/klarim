# Fix — Página de gestão de Blog no painel admin (complemento KL-133)

**Data:** 2026-07-31 · **Status:** ✅ implementado, `npm run build` OK, 142 node --test verdes.
**Deploy:** via commit/push (CI/CD).

## Problema
O link "Blog" do admin abria o site público. O operador precisa ver/gerir os posts (draft/published/
archived) dentro do painel. O backend (CRUD `/admin/blog/posts`) e as MCP tools já existiam (KL-133).

## Mudanças (frontend admin apenas)
- **`web/src/lib/admin/adminApi.js`:** helper `del` + métodos `blogList`/`blogCreate`/`blogUpdate`/
  `blogDelete` (Bearer JWT, mesmo cliente admin). A lista admin já traz o **corpo markdown** (o
  `_blog_admin` do backend inclui `content`) → a edição pré-preenche do estado local, sem GET extra.
- **`web/src/components/admin/BlogPage.jsx`:** ilha React (`client:only`, padrão do painel). Reusa
  `AdminShell`/`Card`/`Button`/`Badge`/`Loading`/`ErrorBox`/`useAsync`/`formatDate`. Lista com **filtro
  por status** (Todos/Rascunhos/Publicados/Arquivados), badge colorido (🟡/🟢/⚫), título, categoria,
  data (`published_at`→`created_at`), tempo de leitura. **Ações** por linha: 👁️ ver público (nova aba,
  só published), ✏️ editar (modal), 📤 publicar (draft→published), 📥 despublicar (published→draft),
  🗑️ arquivar (DELETE), ↩️ restaurar (archived→draft). **Modal** (mesmo padrão do `EditPlanModal`):
  título/subtítulo/categoria(select)/tags(vírgula)/meta_description/og_image + **textarea de markdown**
  (min-h 300px, mono). Salvar = `blogUpdate` (edição) ou `blogCreate` (novo, via "+ Novo post"). Toast
  de feedback. Sem WYSIWYG (markdown bruto, fora do escopo — o operador pode usar as MCP tools).
- **`web/src/pages/painel/blog.astro`:** monta `<BlogPage client:only="react" />` em `AdminLayout`.
  A rota `/painel/blog` já cai no bloco nginx `^/painel(/|$)` → Astro (sem mudança de nginx).
- **`web/src/components/admin/AdminShell.jsx`:** o item "Blog" da sidebar passou de `/blog` (público,
  nova aba) → **`/painel/blog`** (admin, mesma aba).

## Regras atendidas
- Reusa os componentes do admin (ui.jsx, AdminShell, padrão de modal). Backend/MCP já prontos (KL-133).
- Edição = textarea de markdown (não WYSIWYG). Frontend puro; sem novo endpoint.

## Validação pós-deploy
1. `/painel/blog` (logado) lista os posts (incl. o draft de teste); filtro por status funciona.
2. Publicar/Despublicar/Arquivar/Restaurar mudam o status (reload); "Ver público" abre `/blog/{slug}`.
3. Modal edita e salva; "+ Novo post" cria draft.

## Arquivos
- `web/src/lib/admin/adminApi.js`, `web/src/components/admin/BlogPage.jsx`,
  `web/src/pages/painel/blog.astro`, `web/src/components/admin/AdminShell.jsx`, este relatório.
