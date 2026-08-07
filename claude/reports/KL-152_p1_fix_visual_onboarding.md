# KL-152 (Prompt 1/3) — Fix visual do dashboard Gate + Onboarding wizard (5 steps)

## Contexto

O Security Gate (KL-151) está em produção, mas a primeira impressão do dev pós-ativação era de UI
inacabada: contraste fraco, snippet Python raw, inputs sem label. Este prompt corrige a UI de
`/dashboard/gate` e adiciona o wizard que guia o dev até o primeiro scan.

## PARTE A — Fix visual

**Causa raiz do contraste (KL-87):** o `GatePortal.jsx` fora escrito com classes *light-first*
(`text-slate-900`, `bg-white`, `bg-slate-50`, `text-slate-500`, `border-slate-200`). Como o KL-87
**inverte** a escala `--color-slate-*` no tema claro, essas classes resolviam para o lado errado
(texto quase branco sobre card claro) → título "desabilitado". Fix: adotar os **tokens canônicos**
theme-aware de `web/src/components/dashboard-v2/shared.js` — títulos `text-white`, subtítulos
`text-slate-300`, cards `card` (`rounded-2xl border border-slate-800 bg-slate-900/60`) — os mesmos do
dashboard principal (KL-90). **Não foi inventado design novo**; copiou-se o padrão existente.

1. **Título/subtítulo** → `text-2xl font-bold text-white` + `text-sm text-slate-300`.
2. **Cards** → token `card` de `shared.js` (idêntico ao Dashboard v2).
3. **Snippet de integração** → substituído o Python raw por **abas** (`GateIntegrationTabs.jsx`:
   GitHub Actions / GitLab CI / Bitbucket / curl), CSP-safe (estado React), com **URL do projeto
   pré-preenchida** e botão **Copiar** (`GateCodeBlock.jsx`). A key NUNCA é embutida — o YAML
   referencia o secret (`${{ secrets.KLARIM_KEY }}` no GitHub, `$KLARIM_KEY` nos demais).
4. **Inputs "Novo projeto"** → agora com `<label>` ("Nome do projeto" / "URL do site") + placeholders;
   `text-base` (16px, sem zoom no iOS — KL-80) e `h-12`.
5. **Badge de plano** (`PlanBadge`) → "Plano Pro · 9 checks incluídos" + **barra de progresso**
   (`9/18`) + CTA **"Upgrade → Team (18 checks)"** (`planProgress`, puro).

## PARTE B — Onboarding wizard (`GateOnboarding.jsx`, 5 steps)

Aparece no `/dashboard/gate` quando `gate_runs` está vazio; dismissível ("Pular wizard"), **reaparece
até o 1º scan**, some após completar (flag `localStorage['klarim_gate_onboarded']`). Estado 100% no
React (a escolha de plataforma não vai ao backend).

- **Step 1 — CI/CD:** GitHub / GitLab / Bitbucket / Jenkins / Manual (personaliza os próximos steps).
- **Step 2 — secret:** instrução específica por plataforma (`secretSteps`) + a **API key real**. A key
  crua só existe logo após a ativação → é lida do `sessionStorage['klarim_gate_new_key']` (gravada
  pelo CTA da landing e pelo "Regenerar" do portal). Sem ela, mostra o prefixo mascarado + botão
  **"Gerar nova key"** (`regenerate-key`, grace de 1h — não quebra CI).
- **Step 3 — YAML:** snippet pré-preenchido (URL do projeto + secret) em `GateCodeBlock`, zero edição.
- **Step 4 — deploy:** spinner + **polling `GET /api/gate/runs?limit=1` a cada 10s**; ao chegar o 1º
  run mostra score/semáforo/duração/checks e o botão p/ o Step 5.
- **Step 5 — pronto:** próximos passos + "Ir para o dashboard" (marca o onboarding como concluído).

## Arquitetura / arquivos

- **`web/src/lib/gate/snippets.js`** (PURO, testável): `buildSnippet(platform, url)`, `secretRef`,
  `secretSteps`, `planProgress(slug, count)`, `TOTAL_CHECKS=18`, listas de plataformas.
- **`web/src/lib/gate/snippets.test.js`** — **+8 testes** `node --test` (snippet por plataforma, URL
  pré-preenchida, secret referenciado nunca key crua, progresso/próximo plano, saturação).
- **`GatePortal.jsx`** reescrito (theme-aware + labels + badge + abas + gating do wizard).
- Novos: `GateCodeBlock.jsx` (+ `CopyBtn`), `GateIntegrationTabs.jsx`, `GateOnboarding.jsx`.
- `security-gate/GateLandingCTA.jsx` — grava a key nova no `sessionStorage` (para o wizard).

## Segurança

- **API key nunca embutida** no YAML (só referência ao secret do CI). A key crua só aparece no Step 2
  (add secret) e no card "Regenerar", sempre exibida **uma vez**; guardada em `sessionStorage`
  (não `localStorage`) — limpa ao fechar a aba e ao concluir o wizard.
- Tudo consome os endpoints existentes (`key-info`/`projects`/`runs`/`regenerate-key`) com o cookie de
  sessão; **sem endpoint novo** neste prompt.

## Testes

- `npm run test:unit` **162 passed** (154 + 8 novos); `npm run build` OK.
- Validação no browser via `docker-compose.dev.yml` (ativa o Gate pela landing → `/dashboard/gate`):
  contraste correto (claro/escuro), abas, badge/barra, wizard passo-a-passo.

## Escopo

Prompt 1/3. **Não incluso** (Prompts 2-3): webhook de notificações, convite de colega pelo portal,
editor de `security-gate.yml`. Nenhuma mudança de backend/nginx.
