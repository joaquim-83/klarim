# KL-87 — Tema light + fix resultado anônimo + fix espaços desktop

**Card:** KL-87 · **Prioridade:** High · **Data:** 2026-07-19
**Dependências:** KL-82 ✅, KL-86 ✅, KL-81 ✅, KL-20 ✅.

---

## Parte 1 — Tema light (padrão) + dark (toggle)

### Decisão de arquitetura (a chave do card)

O card sugeria migrar ~1000 classes hardcoded em 41 arquivos para `bg-[var(--...)]` — enorme e
arriscado. O card também oferecia a **alternativa**: *"usar `@theme` para mapear as vars nos
tokens do Tailwind"*. Como no Tailwind v4 **todo utilitário resolve `var(--color-slate-…)`**,
**sobrescrevi os tokens `--color-slate-*` e `--color-white` por tema** num único CSS — a escala
slate **invertida** no light (950→branco … 100→tinta escura), defaults do Tailwind no dark. Assim
**os 41 arquivos viraram theme-aware sem UMA edição de layout**. Verde/amarelo/vermelho (semáforo,
funcionais) e o laranja da marca ficam **constantes**.

**Exceções (as únicas edições mecânicas):**
- `text-slate-950` (texto escuro sobre botão laranja, 25 ocorrências) → `text-[var(--accent-text)]`
  (`#0f172a` constante nos 2 temas) — senão a inversão o deixaria branco (baixo contraste).
- `bg-white` do QR PIX (`PlanSection`) → `bg-[#ffffff]` (QR precisa de fundo branco sempre).
- Logo: já usa `text-white` (→ vira escuro no light) + laranja hardcoded → **funciona nos 2 sem
  mudança**.
- `.prose-klarim` (termos/privacidade/sobre): hexes fixos → vars slate (senão invisível no light).

### Toggle + anti-FOUC + admin

- **Anti-FOUC:** script **inline** no `<head>` do `Base.astro` define `data-theme` ANTES do paint
  (`localStorage` > `prefers-color-scheme`; light default). Inline obrigatório (externo → flash) →
  hash **SHA-256 adicionado à CSP** (`security_headers.conf`). Validado que o hash bate os bytes
  do build.
- **Toggle sol/lua** no `Header` (`#theme-toggle`) + `public/theme.js` **externo** (CSP `'self'`)
  que sincroniza o ícone e persiste em `localStorage`.
- **Admin** (`AdminLayout`): `<html data-theme="dark">` — **sempre dark**, sem toggle.
- **Cards com sombra no light** (`--card-shadow`; none no dark).

### Verificado (Claude-in-Chrome, build local)

Landing em **light** (padrão): fundo branco, título escuro, botão laranja com texto escuro, logo
legível, footer claro. **Toggle → dark**: fundo escuro, título branco, ícone vira ☀️.
**Persistência**: reload mantém (`localStorage=dark`, `data-theme=dark`, body `#020617`). ✅

## Parte 2 — Resultado de scan anônimo

O `CategoryBars` (barras por categoria + cadeado) e o `RisksSection` (1 de N riscos + "crie conta
para ver") **já haviam sido entregues no KL-82 Slice 1** — confirmado no código. Faltava só o
**rótulo "Compartilhe este resultado"** acima dos botões de share (2A) → adicionado no `ShareRow`.

## Parte 3 — Fix do gap no dashboard desktop

O grid do KL-86 acoplava a altura da linha de Saúde à do Checklist (mesma `row` do grid) → gap
sob a Saúde. **Fix (recomendação §3B do card):** `lg:auto-rows-min lg:items-start` + o **Checklist
ocupa 3 linhas** (`lg:row-span-3`), alinhando com Saúde+Riscos+Evolução sem forçar a altura da
linha da Saúde. Categorias (col1-2, row4) + Plano (col3, row4) fecham a base sem célula vazia. A
ordem-fonte segue a **ordem mobile** (saúde→checklist→riscos→categorias→evolução→plano); no desktop
os `row-start`/`col-start` reposicionam.

## Decisões

- **Token-override (inversão) em vez de migração por arquivo** — mesmo resultado, risco ~zero por
  arquivo (nenhum layout tocado), reversível (é 1 bloco CSS).
- **Não introduzi as vars semânticas** do card (`--bg-primary` etc.) nos componentes: a inversão
  dos tokens slate já entrega o efeito. As poucas vars novas: `--accent-text`, `--card-shadow`.
- **Laranja da marca = `#ff6b35`** (o real do Klarim/logo), não `#f97316` do card — não trocar
  branding. Constante nos 2 temas.

## Testes

- `pytest`: **1069 passed, 1 skipped** (backend intocado — Parte 1/2/3 são só frontend/nginx/CSP).
- Build Astro **verde**; hash CSP conferido contra os bytes do build; toggle+persistência
  verificados no browser (light+dark).

## Honestidade / follow-up

- Verifiquei visualmente a **landing** (light+dark) no browser local. As páginas SSR (scan,
  dashboard, perfil, planos, auth) usam os MESMOS tokens/utilitários → herdam o tema, mas uma
  passada visual em cada, **em produção após o deploy**, fecharia 100% (farei via browser).
- A inversão da escala slate cobre o uso consistente (fundo escuro + texto claro) do design; se
  algum ponto específico usar slate "ao contrário", pode precisar de ajuste pontual — corrijo
  forward se a QA visual em prod apontar.
- Sombra de card aplicada via `--card-shadow` no token; cards que não usam a var (Astro inline)
  contam com a borda para separação — suficiente no light.
