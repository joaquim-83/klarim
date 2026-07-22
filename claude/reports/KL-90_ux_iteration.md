# KL-90 — Iteração de UX: Dashboard v2 + navegação + monitoramento

**Data:** 2026-07-22
**Card:** KL-90 (continuação do Prompt 2)
**Ambiente:** desenvolvimento local. **Sem deploy/push/commit.**

Iteração de experiência sobre o Dashboard v2 (`/dashboard/v2`) + Header global +
páginas de Planos e Conta, a partir do feedback do dono. Tudo validado no navegador
(temas claro e escuro, zero erro no console).

---

## O que foi entregue (todos os 9 itens)

### Item 1 — Header global com perfil logado + busca persistente ✅
`web/src/components/Header.astro` + **`web/public/header.js`** (externo).
- Logado: **avatar** (inicial do usuário) com **dropdown** (nome + e-mail, "Meu dashboard",
  "Minha conta", "Sair"), no lugar de Entrar/Cadastrar.
- **Campo de busca persistente** (estilo Google, `/scan?url=`) revelado em toda página quando logado.
- **Prod-safe:** a lógica saiu do `<script>` inline (que era um dos 5 hashes da CSP) para um
  script EXTERNO (`/header.js`, coberto por `script-src 'self'`) — não precisa recomputar hash.

### Item 2 — "+ Adicionar site" funciona ✅
`AddSiteModal.jsx`: modal com campo de domínio → `POST /account/sites` → re-fetch + seleciona o novo site.

### Item 3 — Painel fixo de sites (fim do dropdown) ✅
`MonitoredSitesPanel.jsx`: coluna fixa (sticky) à esquerda com todos os sites monitorados
(domínio + score + semáforo, selecionado destacado, scroll se >5) + **histórico dos últimos
pesquisados** (`GET /account/scan-history`, link para o perfil público).

### Item 4 — Card do score consolidado ✅
`ScoreCard.jsx` reescrito: score/semáforo/tendência/benchmark + tira de status (online/SSL/
último scan) + ações (PDF/Compartilhar/Escanear) + **"Ver perfil público →"** + **"Ver landing →"**
+ CTA destacado **"🔧 Vincular Técnico"** (`TechnicianModal.jsx` → `POST /account/technician/invite`).
A segunda coluna (StatusPanel) foi **removida** — tudo no card do score.

### Item 5 — Seção de Monitoramento dedicada ✅
`MonitoringSection.jsx`: (5a) status das vigílias ativas (`GET /account/vigilias`, por site);
(5b) **o que está sendo monitorado** (6 itens, ✅ ativo / ⚪ disponível, derivado das vigílias
reais + plano) + canal (e-mail) + link de preferências; (5c) **boletim** com a frequência do
plano; (5d) gancho de preferências.
> **Honesto:** não há endpoint de *salvar preferências* de vigília (`/account/vigilias` é
> read-only) — a seção reflete o estado REAL e liga a cobertura ao plano (link p/ upgrade), sem
> toggles falsos. Persistência de preferências = follow-up de backend.

### Item 6 — Riscos e Checklist collapsible ✅
`Collapsible.jsx`: header clicável com contagem ("⚠️ Riscos [6]", "📋 O que fazer [3]"),
**recolhido por padrão**, expande no clique (▸/▾). `RisksList`/`Checklist` viraram conteúdo "puro".

### Item 7 — Página de Planos logada ✅
`planos.astro` + **`web/public/planos-auth.js`** (externo). Logado: banner **"Seu plano: Pro ·
em trial · 24 dias restantes"**, card atual com badge verde **"✓ Seu plano"** + ring, e CTAs por
posição: **Fazer downgrade** (inferior) · **Plano atual** (desabilitado) · **Fazer upgrade →** (superior).

### Item 8 — Página de Conta mais compacta ✅ (parcial)
`conta.astro`: container **`max-w-2xl` → `max-w-4xl`** (fim do espaço vazio nas laterais);
back-link → `/dashboard/v2`. O conteúdo (dados pessoais, senha, plano, excluir conta) já existia
em `AccountSettings.jsx`. **Deferido** (follow-up): lista de sites com remover, preferências de
notificação, exportar dados e técnico vinculado — nesta iteração o foco foi o layout (item 8a).

### Item 9 — Inteligência de mercado no dashboard ✅
`ExploreSection.jsx`: 4 cards compactos → `/setor/{slug}` (seu setor) · `/ranking` · `/estatisticas` · `/melhores`.

---

## Novo layout do dashboard

`DashboardV2.jsx` reescrito: **painel fixo de sites (esquerda) + coluna de conteúdo (direita)**
(`lg:flex` + `lg:w-72`/`lg:flex-1`). Empilha no mobile (painel no topo). Ordem da coluna direita:
banners (offline/score-100) → ScoreCard consolidado → Monitoramento → Categorias → Riscos/Checklist
(collapsible) → Evolução → Explore. Modais de técnico e adicionar-site. Toast global.

---

## Validação (navegador, com o seed)

| Item | Resultado |
|---|---|
| 1 · Header logado | avatar "J" + dropdown (João Silva / dono@exemplo.com.br / Meu dashboard / Minha conta / Sair) + busca visível |
| 2 · Adicionar site | modal abre (POST /account/sites) |
| 3 · Painel de sites | 5 sites listados, selecionado destacado, sticky |
| 4 · Score consolidado | ações + perfil público + landing + Vincular Técnico (modal OK, domínio correto) |
| 5 · Monitoramento | vigílias ativas (score/SSL) + "o que monitoramos" + boletim semanal |
| 6 · Collapsible | Riscos [6] / Checklist [3] recolhidos, expandem independentes |
| 7 · Planos logado | banner "Pro · trial · 24 dias" + "✓ Seu plano" + downgrade/atual/upgrade |
| 8 · Conta | max-w-4xl, dados/senha/plano/excluir |
| 9 · Explore | 4 cards de mercado |
| Tema claro/escuro | legível nos dois |
| Console | **zero erros** |
| `npm run build` | ✓ compila |
| `npm run test:unit` | ✓ (96, sem falhas) |

---

## Notas técnicas

- **Scripts externos (CSP prod-safe):** `header.js` e `planos-auth.js` substituem scripts
  inline (que exigiam hash na CSP estrita). Cobertos por `script-src 'self'`.
- **"Meu dashboard" → `/dashboard/v2`** (o que está em teste). No swap (Prompt 3), quando o v2
  virar o `/dashboard`, o link volta a `/dashboard`.
- **Gotcha do dev server (recorrente):** classes Tailwind NOVAS de arquivos recém-criados às vezes
  não entram no scan incremental do dev → **recriar/reiniciar o astro** (scan limpo) resolve; o
  `npm run build` de produção gera tudo. O restart já é robusto (o `command` do serviço `astro`
  remove `web/.astro/dev.json` no boot).

## Deferido (follow-up)
- Item 5b: persistência de preferências de monitoramento (precisa de endpoint `PUT /account/vigilias/...`).
- Item 8b: sobre a conta — lista de sites com remover, preferências de notificação, exportar dados,
  técnico vinculado (o núcleo dados/senha/plano/excluir já existe).

## Ajustes visuais pós-validação (2026-07-22)

3 correções rápidas do feedback do dono, validadas no navegador:
1. **Evolução do score sem espaço vazio** (`ScoreHistory.jsx`): removido `h-full` + **eixo Y
   auto-escalado ao intervalo dos dados** (scores 71–83 não "grudam" mais no topo deixando o
   resto vazio) + altura compacta (`h-28`). O card encerra logo após as labels de data.
2. **"Inteligência de mercado" (Explore) removida** do `DashboardV2.jsx` (aparecia solta perto do
   footer) — o dashboard encerra na Evolução do score. `ExploreSection.jsx` fica no repo p/ voltar
   num card bem posicionado no futuro.
3. **Um único link no card do score** (`ScoreCard.jsx`): removido "Ver perfil público →" (era
   redundante com a landing); mantido só **"Ver landing page →"** (`https://{domain}`).

## Regras atendidas
- ✅ Sem deploy/push/commit · ✅ tema claro/escuro · ✅ hot reload · ✅ relatório PT-BR.
