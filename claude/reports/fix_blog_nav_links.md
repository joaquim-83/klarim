# Fix — Links de navegação para o Blog (complemento KL-133)

**Data:** 2026-07-31 · **Status:** ✅ implementado, `npm run build` OK. **Deploy:** via commit/push (CI/CD).

## Problema
O blog (KL-133) estava deployado e funcional, mas sem nenhum link de acesso na UI — o visitante só
chegava digitando a URL. Adicionados os links nos 3 pontos de navegação.

## Mudanças (frontend apenas)
1. **Header público** (`web/src/components/Header.astro`): link **"Blog"** antes de "Planos", nos dois
   estados (deslogado `data-auth="out"` e logado `data-auth="in"`). Aparece em toda página pública que
   usa o Header. Escondido no mobile muito estreito (`hidden sm:inline-flex`) para não estourar o header
   em 375px (o Blog também está no footer, sempre acessível).
2. **Footer público** (`web/src/components/Footer.astro`): `{ href: '/blog', label: 'Blog' }` no início
   da lista de links (junto de Setores/Melhores/Metodologia).
3. **Sidebar do admin** (`web/src/components/admin/AdminShell.jsx`): item **"Blog"** (ícone de documento
   + "↗") no fim do menu, com `target="_blank" rel="noopener noreferrer"` → abre o site público `/blog`
   em nova aba. Sem painel visual de edição (publicação é via MCP/API, regra do KL-133).

## Regras atendidas
- **Não** criou painel admin de blog (só o link).
- Frontend puro; nenhuma mudança de backend/lógica. Build OK.

## Arquivos
- `web/src/components/Header.astro`, `web/src/components/Footer.astro`,
  `web/src/components/admin/AdminShell.jsx`, este relatório.
