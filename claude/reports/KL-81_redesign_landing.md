# KL-81 — Redesign da landing: buscador "Pesquise qualquer site"

**Card:** KL-81 · **Prioridade:** High · **Data:** 2026-07-18
**Arquivo:** `web/src/pages/index.astro` (só a landing — nenhuma outra página mudou)

---

## Antes → Depois

| | Antes | Depois |
|---|---|---|
| **Título** | "Seu site é seguro?" | **"Pesquise qualquer site."** |
| **Subtítulo** | "Descubra em 30 segundos." (laranja) | mantido (laranja) |
| **Botão** | "Verificar" | **"Pesquisar →"** |
| **Ícone no input** | — | **lupa (SVG) à esquerda** |
| **Placeholder** | "https://seusite.com.br" | **"digite um domínio..."** |
| **Linha abaixo** | "100% gratuito. Sem cadastro." | **"Relatório completo. 100% gratuito."** |
| **Sub do hero** | "48 verificações de segurança. Relatório completo. 100% gratuito." | removido |
| **Selo** | "🛡 13.000+ sites brasileiros analisados" | removido |
| **Abaixo do hero** | Como funciona · Checks · Benchmark · Para quem | **removido — só hero + footer** |

Verificado no HTML prerenderizado (`dist/client/index.html`): o novo hero está presente e
"Seu site é seguro", "Como funciona", "Sem cadastro", "13.000+" e "48 verificações de
segurança" **não aparecem** mais.

## Layout

`Base.astro` mantém o `<body class="min-h-screen">` (compartilhado por todas as páginas), então
envolvi a landing num `<div class="flex min-h-screen flex-col">`:
- **Header** é `position: absolute` (flutua no topo, não ocupa espaço no fluxo) → sem alteração.
- **`<main class="flex flex-1 items-center justify-center px-5 py-16">`** centraliza o hero
  verticalmente no espaço entre nav e footer.
- **Footer** no fundo → sem alteração.

A página cabe numa tela sem scroll (nav flutuante + hero centralizado + footer). O `py-16` no
main garante respiro em telas curtas (o hero não encosta no header/footer).

## Mobile-first

- Input + botão **empilham no mobile** (`flex-col`), **inline em `sm:`** (`sm:flex-row`).
- Input `h-14` (56px, destaque) com `pl-11` (espaço da lupa) + `text-base` (16px, sem zoom iOS).
- Botão `h-14`, `w-full sm:w-auto`, `active:scale-95` (feedback tátil).
- Título fluido `text-4xl sm:text-5xl lg:text-6xl`.
- `autocapitalize=none autocorrect=off autocomplete=off spellcheck=false` no input de domínio.

## O que NÃO mudou

Identidade visual (dark + laranja + tipografia), Logo, Header, Footer, o **fluxo de scan**
(o form ainda é `GET /scan?url=` — só mudou texto/botão/ícone), e **todas as outras páginas**.
SEO: `title`/JSON-LD `SoftwareApplication` mantidos; a `meta description` foi alinhada ao novo
posicionamento ("Pesquise a segurança de qualquer site…").

## Decisões

- **Form inline** (não o componente `ScanInput`) — a landing precisa da lupa + textos próprios;
  inline evita mexer no `ScanInput` (que segue disponível). Os componentes de seção removidos
  (`HowItWorks`/`WhatWeCheck`/`Benchmark`/`ForWhom`) ficaram no repositório mas **sem uso**
  (nenhum import quebrado) — não deletei para não tocar em arquivos além do necessário.
- **Lupa em SVG** (não emoji 🔍) para renderização consistente entre plataformas.

## Validação

- `npm run build` (Astro) → **verde**; HTML prerenderizado conferido (hero novo presente,
  conteúdo antigo ausente, footer com Setores/Melhores/Estatísticas/Termos, `action="/scan"`).
- `pytest` → **1024 passed, 1 skipped** (mudança só de frontend).
- Faltou (honesto): inspeção visual real em DevTools 375px/1440px — as classes são responsivas
  padrão e o build valida a compilação, mas uma passada visual fecharia 100%.
