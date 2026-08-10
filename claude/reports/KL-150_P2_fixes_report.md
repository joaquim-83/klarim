# KL-150 P2 — Ajustes pós-validação: espaçamento, verificação, container, números

> **Status: PRONTO PARA REVISÃO VISUAL** — implementado e validado no `docker-compose.dev.yml`.
> **NENHUM push/deploy foi feito.** Aguarda a validação do Cidinei.

## Resumo

4 ajustes identificados na revisão pós-deploy do P2. Itens 2 e 4 exigiram diagnóstico de dados.

## Item 1 — Home com espaço excessivo, footer abaixo do fold (FIX)

**Causa:** o `min-h-[100dvh]` estava no `<main>` → o hero sozinho já ocupava a tela inteira e
empurrava o footer para baixo do fold.

**Fix (sticky-footer real, `index.astro`):** movi o `min-h-[100dvh]` para o WRAPPER e o `<main>`
virou `flex-1` (cresce no espaço restante, hero centralizado), com o footer no fundo da MESMA tela.

**Validado no browser:**
- **1920×1080**: sem scroll; footer, pills e contadores **visíveis** (`docScrollable=false`).
- Altura NATURAL do conteúdo = **528px** (hero 344 + padding 64 + footer 120) → cabe em **768 e até
  720**. Em qualquer laptop, contadores + pills + footer aparecem sem rolar.

## Item 2 — Domínio verificado no scanner aparece "Não verificado" no Gate (FIX — Opção A)

**Diagnóstico:** Gate-verify (`gate_projects.verified`) e owner-verify do scanner
(`targets.owner_verified` + `user_sites.is_owner`, KL-99) são tabelas SEPARADAS e não propagavam.
Quem verificou o domínio pelo scanner via o Gate ainda como "Não verificado".

**Fix (Opção A — propagar):** novo `store.propagate_scanner_verification(account_id)` — um `UPDATE`
que marca `verified=TRUE, verification_method='scanner'` nos projetos Gate NÃO verificados cujo
domínio a MESMA conta já provou possuir no scanner (`user_sites.is_owner` + `targets.owner_verified`,
match por `lower(domain)`). Idempotente. Chamado em **`GET /gate/projects`** (lazy, cobre projetos
existentes + reconcilia a cada load) e em **`POST /gate/projects`** (o projeto nasce já verificado se
aplicável). Ambos best-effort (falha não quebra a listagem/criação).

**Validado:** SQL contra o Postgres do dev (verified f→t, method=scanner) + **browser**: criei um
target `devpuro.com.br` owner-verified + vínculo de posse da conta → o portal passou a mostrar
**"✅ Verificado (scanner)"** no projeto (antes "Não verificado").

## Item 3 — Container do dashboard Gate muito estreito (FIX)

`/dashboard/gate` estava em `md:max-w-5xl` (margens largas); o dashboard principal usa
`lg:max-w-7xl`. Alinhei o `<main>` de `gate.astro` a **`lg:max-w-7xl`** (mesmo padrão).
**Validado no browser:** o `<main>` passou a **1280px** (era ~1024) — ocupa a largura da tela.

## Item 4 — Números do score 100 não batem (FIX)

**Diagnóstico:** `public_platform_stats.score_100_count` (home/estatísticas = 730 em prod) conta
**domínios distintos com `last_scan_score=100`** (sem filtro). Mas `count_public_score_100_sites`
(usado por /melhores = 670) adicionava `status IN ('scanned','alerted')` + `public_visible` → número
MENOR (só perfis públicos). Superfícies diferentes mostravam números diferentes.

**Fix:** renomeei para **`count_score_100_sites`** e usei a MESMA query de
`public_platform_stats.score_100_count` (distinct domains, score 100, sem filtro de perfil/status) →
**todas as superfícies mostram o mesmo número**. Em prod: /melhores, home e /estatísticas passam a
bater com `get_dashboard_stats`.
**Validado no browser (dev):** `/public/best.total` === `/public/stats.score_100_count` (**MATCH**).

## Testes

- **`pytest`: 2315 passed, 1 skipped** (+2 `test_kl150_p2_propagation.py`; `test_kl150_p2_public_best`
  e `test_kl74_content` ajustados ao rename `count_score_100_sites`).
- **`node --test`: 220 passed** · **`npm run build`: OK.**
- **Browser (dev)**: item 1 (footer/pills sem scroll @1920/1080; conteúdo 528px cabe em 768), item 2
  ("✅ Verificado (scanner)"), item 3 (main 1280px), item 4 (best.total === stats.score_100). **Zero
  erro no console.**

## Arquivos

**Backend:** `discovery/store.py` (`propagate_scanner_verification` + `count_score_100_sites`),
`api/gate.py` (propagação em GET/POST /gate/projects), `api/main.py` (usa `count_score_100_sites`).
**Frontend:** `web/src/pages/index.astro` (sticky footer), `web/src/pages/dashboard/gate.astro`
(container max-w-7xl). **Testes:** `tests/test_kl150_p2_propagation.py` (novo),
`tests/test_kl150_p2_public_best.py`, `tests/test_kl74_content.py`. **Docs:** `claude.md`.

## Escopo NÃO tocado

Engine de scan, rate limiting e SEO (títulos/URLs/Schema.org do KL-132) intactos.
