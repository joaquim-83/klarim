# Feat — Páginas Sobre e Parceiros

**Tipo:** Conteúdo do site (sem card Jira)
**Data:** 2026-07-08

## Problema

Os links "Sobre" e "Parceiros" no footer apontavam para `#` (placeholder).
Precisavam de páginas reais.

## O que foi feito

### Páginas (texto aprovado, usado exatamente)

- **`pages/Sobre.jsx`** (rota `/sobre`): H1 "Sobre o Klarim", parágrafos do texto
  aprovado, os rótulos **"Como funciona:"**, **"O que entregamos:"**, **"Para quem
  é:"** em `<strong>` inline (não como headings), e a frase final "Segurança
  acessível…" em **destaque** (maior + itálico, cor de alerta).
- **`pages/Parceiros.jsx`** (rota `/parceiros`): H1 "Programa de Parceiros", intro,
  "Buscamos parceiros em:" e as **4 categorias** (título em bold + descrição em
  texto normal, numa lista), "Como funciona:" e "Quer ser parceiro?" em bold.
- Ambas usam o `Layout` padrão (Header + conteúdo `max-w` + Footer), paleta dark do
  Klarim, e são responsivas.

### E-mail que abre o modal (não `mailto`)

- **`components/ContactEmail.jsx`** (novo): botão `📧 scan@klarim.net` que abre o
  `ContactModal` (reuso do fix anterior). Usado no corpo das duas páginas — clicar
  no e-mail abre o modal inline, **sem sair do site**.

### Links do footer + relatório

- **`Footer.jsx`**: "Sobre" → `<Link to="/sobre">`, "Parceiros" →
  `<Link to="/parceiros">` (React Router, sem reload). Nenhum `href="#"` restante
  no site público (verificado por grep).
- **`Report.jsx`**: o CTA "Conheça nossos parceiros" (era `href="#"`) agora é
  `<Link to="/parceiros">`.

### Rotas

- **`App.jsx`**: `/sobre` → `<Sobre/>`, `/parceiros` → `<Parceiros/>` (site público,
  não lazy). O Nginx já faz o catch-all SPA (`try_files … /index.html`), então o
  acesso direto por URL funciona.

## Validação

- `npm run build` OK (sem erros de JSX).
- Sem backend novo (páginas estáticas) — nenhum teste pytest necessário; suíte
  existente intacta.
- Pós-deploy: `/sobre` e `/parceiros` carregam com o texto completo; footer navega
  sem `#`/reload; e-mail abre o modal de contato; CTA do relatório aponta para
  `/parceiros`.

## Arquivos

- `frontend/src/pages/Sobre.jsx`, `frontend/src/pages/Parceiros.jsx` (novos)
- `frontend/src/components/ContactEmail.jsx` (novo)
- `frontend/src/App.jsx`, `frontend/src/components/Footer.jsx`,
  `frontend/src/pages/Report.jsx`
- `claude.md`
