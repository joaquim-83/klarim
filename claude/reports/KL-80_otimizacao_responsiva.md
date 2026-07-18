# KL-80 — Otimização de experiência: desktop + mobile responsivo

**Card:** KL-80 · **Prioridade:** High · **Data:** 2026-07-18
**Dependências:** KL-74 ✅, KL-20 ✅

---

## Contexto

68% do tráfego é mobile. Desktop e mobile com peso igual. As páginas de conteúdo do KL-74
(`/setores`, `/setor/{slug}`, `/melhores`, `/estatisticas`) e o perfil (`/site/{domain}`) já
foram construídos **mobile-first** (KL-74/78/20). A auditoria focou nas superfícies mais antigas
(landing, fluxo de scan, auth, dashboard, planos, laudo).

## Auditoria (3 varreduras paralelas por página)

Muitas superfícies já estavam corretas: os tokens `field`/`btn` (`text-base` 16px + `py-3.5` ~48px),
os formulários de auth (`flex-col sm:flex-row`), a landing, `ScanInput`, os grids das seções.
**Defeitos reais encontrados** (ignorados os falsos positivos como "grid sem `grid-cols-1` explícito"
— o Tailwind já é 1-col por default — e badges decorativos):

| Superfície | Problema | Correção |
|---|---|---|
| `Header.astro` (todas as páginas) | links de nav `py-2` (~36px, < 44px) | `inline-flex min-h-[44px] items-center px-3 py-2` (mantém o nav de 3–4 links cabendo em 375px) + `active:scale-95` no Cadastrar |
| `ScanFlow.jsx` — resultado | dropdown "Baixar PDF" `w-64` **fixo estourava 375px** (scroll horizontal) | `w-full sm:w-64` + wrapper `w-full sm:w-auto` |
| `ScanFlow.jsx` — resultado | botões secundários (Enviar/Ver perfil) sem `w-full` → overflow no mobile | `w-full sm:w-auto` + `active:scale-[0.98]` |
| `ScanFlow.jsx` — código | "Trocar e-mail"/"Reenviar código" links-texto (< 44px) | `min-h-[44px]` + padding |
| `SignupForm.jsx` / `ForgotForm.jsx` | "Reenviar código"/"Trocar e-mail" links-texto (< 44px) | `min-h-[44px]` + padding + transição |
| `Dashboard.jsx` | sites em lista vertical; card queria densidade | **grade 2-col no `lg:`** (`grid-cols-1 lg:grid-cols-2 lg:items-start`) |
| `Dashboard.jsx` | 5 botões de ação do card `py-2` (< 44px) | `inline-flex min-h-[44px] items-center` em todos |
| `Dashboard.jsx` | histórico: "Ver"/"✕" alvos minúsculos | `min-h-[44px]`/`min-w-[44px]` |
| `OwnershipVerification.jsx` | input código `text-sm`/`w-32`/`py-2`; "Reenviar" `text-xs` | `h-12 w-full text-base sm:w-40`, botões `min-h-[44px]`, empilha no mobile |
| `TechnicianSection.jsx` | input convite `text-sm` (**zoom iOS**) + botão `py-2` | `h-12 text-base` + botão `min-h-[44px]` |
| `laudo/[code].astro` | círculo de score `h-28 w-28` fixo | `h-24 w-24 sm:h-28 sm:w-28`, texto `text-3xl sm:text-4xl` |
| `planos.astro` | grade `lg:grid-cols-3` sem passo intermediário | `grid-cols-1 md:grid-cols-2 lg:grid-cols-3` |

## Padrões adotados (documentados em `claude.md`)

1. **Alvos de toque ≥ 44px:** links/botões tapáveis usam `min-h-[44px]` (+ `min-w-[44px]` p/ ícones)
   ou `py-3`. Links-texto pequenos (reenviar/voltar) ganham `inline-flex min-h-[44px] items-center px-1`.
2. **Inputs ≥ 16px:** `text-base` sempre (nunca `text-sm` em `<input>`/`<textarea>` — evita o zoom do iOS).
   Altura `h-12`.
3. **Botões full-width no mobile:** `w-full sm:w-auto` (empilham no mobile, inline no desktop).
4. **Nada de largura fixa que estoure 375px:** dropdowns/menus `w-full sm:w-64`.
5. **Grades responsivas:** `grid-cols-1` base → `md:`/`lg:` (cards de site 2-col no `lg:`; planos 1→2→3).
6. **Feedback tátil:** `active:scale-95`/`active:scale-[0.98]` em botões/cards do mobile.
7. **Breakpoints Tailwind padrão** (sm 640 / md 768 / lg 1024 / xl 1280); hover-states com `transition`.

## Decisões / desvios

- **Sem menu hambúrguer** no Header: o nav tem só 3–4 links curtos e cabe em 375px com os alvos
  ampliados — um hambúrguer seria over-engineering. Apenas ampliei os alvos de toque.
- **Sem CTA sticky no bottom** (padrão opcional do card): o perfil `/site/{domain}` já tem múltiplos
  CTAs (cross-linking, "Verificar outro site", ClaimSite) e não é excessivamente longo; uma barra fixa
  é UX debatível (pode incomodar/sobrepor) e não é um defeito — deixado como melhoria futura opcional.
- Falsos positivos da auditoria (grids sem `grid-cols-1` explícito, badges decorativos `text-xs`,
  botões `py-3.5` já ~48px) **não** foram alterados — evita churn sem valor.

## Validação

- `npm run build` (Astro) → **verde** (corrigi um erro de comentário JSX `{/* */}` num slot de ternário).
- `pytest` → **1024 passed, 1 skipped** (mudanças são só de frontend).
- As páginas do KL-74 (setores/setor/melhores/estatisticas) e o perfil já eram mobile-first —
  mantidas.
