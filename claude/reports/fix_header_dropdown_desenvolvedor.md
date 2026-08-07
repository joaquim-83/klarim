# Fix — Reorganizar header: dropdown "Desenvolvedor" + Docs dentro do Gate

## Problema

O header tinha 6 itens (Blog · Security Gate · Docs · Planos · Entrar · Cadastrar). "Security Gate" e
"Docs" são dev-facing e competiam por espaço com Blog/Planos na landing, cujo público é dono de PME.
Além disso, "Docs" é documentação DO Security Gate, não um item independente.

## O que mudou

### 1. Header — 6 itens → 4 + dropdown (`web/src/components/Header.astro`)

**Depois:** `Blog · Desenvolvedor ▼ · Planos · Entrar · [Cadastrar]`.

- Removidos os links diretos **"Security Gate"** e **"Docs"**.
- Novo dropdown **"Desenvolvedor ▼"** (`<details>/<summary>`, **CSP-safe** — sem JS inline, padrão do
  projeto) com **🔒 Security Gate** (`/security-gate`) e **📖 Documentação** (`/docs/gate/github-actions`).
  Chevron gira ao abrir (`group-open:rotate-180`); marcador nativo removido. Aplicado nos **dois**
  estados de auth (deslogado e logado, revelado pelo `header.js`).
- **Docs deixou de ser item de topo** — vive agrupado sob "Desenvolvedor".

### 2. Docs acessível DENTRO do Security Gate

- **Landing `/security-gate`:** "Ver documentação" no hero (já do KL-152 P2) **+** novo link
  **"Documentação completa →"** ao fim da seção "Como funciona".
- **Dashboard `/dashboard/gate`:** link **"Ver documentação →"** no header da seção "Integração no
  CI/CD" (`GatePortal.jsx`).
- Sidebar das páginas `/docs/gate/*` já navega entre as 7 páginas (KL-152 P2). **Nenhuma página de
  docs removida** — só o link do header principal.

### 3. Footer em seções (`web/src/components/Footer.astro`)

De um `flex-wrap` plano para **3 grupos rotulados** (responsivo `grid-cols-1 sm:grid-cols-3`):
- **Produto:** Pesquisar site · Setores · Melhores · Estatísticas · Blog
- **Desenvolvedores:** Security Gate · Documentação
- **Empresa & Legal:** Sobre · Contato · Metodologia · Privacidade · Termos · Cookies · Preferências
  de cookies

"Security Gate" e "Documentação" ficam **agrupados** em Desenvolvedores.

## Responsivido / mobile

O dropdown segue o **mesmo breakpoint do Blog** (`hidden sm:block`, visível ≥640px) e é
**touch-funcional** (é um `<details>`, abre no toque) em tablets/desktop. Em telas <640px o header só
comporta os CTAs primários (Planos/Entrar/Cadastrar) — como já era antes (Blog/Security Gate/Docs
sempre foram `hidden sm:*`). **No celular, Security Gate + Documentação são acessíveis pela seção
"Desenvolvedores" do footer** (que é visível no mobile). Forçar "Desenvolvedor" na barra de 375px
estouraria o header; um menu hambúrguer completo seria o caminho, mas está fora do escopo deste fix.

## Validação (browser, `docker-compose.dev.yml`)

- Header desktop: **4 itens** (Blog · Desenvolvedor ▼ · Planos · Entrar) + Cadastrar. ✓
- "Desenvolvedor" (click) abre o submenu com Security Gate + Documentação; "Security Gate" navega para
  `/security-gate`. ✓
- **"Docs" não aparece** como item de topo. ✓
- Footer com os 3 grupos; Security Gate + Documentação juntos. ✓
- Páginas `/docs/gate/*` seguem **200**. ✓
- Landing tem "Documentação completa →". ✓
- **Zero erro no console**; `npm run build` OK; `test:unit` 166.

## Arquivos

`web/src/components/Header.astro`, `Footer.astro`, `web/src/pages/security-gate.astro`,
`web/src/components/dashboard-v2/GatePortal.jsx`. Nenhuma mudança de backend/nginx (rotas já na
allowlist).
