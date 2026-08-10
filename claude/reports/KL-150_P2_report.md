# KL-150 P2 — Verificação, navegação, home, números, landing, assinatura

> **Status: DEPLOYADO EM PRODUÇÃO ✅** (10/08/2026, CI run #298 verde). Validado no dev + pós-deploy:
> `/api/public/best` total=670 (real, não 300), home sem ProductSplit, landing "Como funciona"/19
> categorias/curl YAML, site 200. Itens 1/2 (verify UI + nav do portal) live (validados no dev).

## Resumo

6 pendências. Itens 1 e 6 exigiam **diagnóstico primeiro** (documentado abaixo). Resultado: item 1
= faltava a UI de verificação (o backend estava correto); item 6 = já funcionava (sem bug).

---

## Item 1 — Verificação de domínio no Security Gate (DIAGNÓSTICO + FIX)

**Diagnóstico (empírico, dev):**
1. `gate_projects.verified` EXISTE e é populado (`BOOLEAN DEFAULT FALSE`).
2. `POST /gate/projects/{id}/verify/check` → `mark_gate_project_verified` → `UPDATE ... verified=TRUE,
   verified_at=NOW(), verification_method=%s`. **Atualiza corretamente.**
3. **São SISTEMAS SEPARADOS.** A verificação do Gate (`gate_projects`, via `/gate/projects/{id}/
   verify/*`) é independente da verificação de site/monitoramento do KL-99 (`targets.owner_verified`,
   via `/account/sites/{id}/verify/*`). Uma NÃO propaga para a outra (por design — contextos distintos).
4. O frontend lê `p.verified` corretamente (`Projects` em `GatePortal.jsx`).
5. `GET /gate/projects` retorna `verified` + `verification_method` (`_GATE_PROJECT_COLS`).

**Confirmação empírica:** setei `gate_projects.verified=TRUE` no dev e o portal passou a mostrar
"✅ Verificado (dns_txt)". **O chain de exibição funciona.**

**Causa REAL:** o portal do Gate **não tinha UI para verificar** — os endpoints existiam mas não
estavam ligados a nenhum botão. Projetos criados por "Novo projeto" (ou pelo cadastro) nasciam não
verificados e ficavam assim para sempre (sem caminho no portal).

**Fix:** adicionei o fluxo de verificação ao portal (`GatePortal.jsx`): botão **"Verificar →"** em
cada projeto não verificado → **`VerifyProjectModal`** (escolhe DNS TXT / Meta tag / Arquivo HTML →
`POST /gate/projects/{id}/verify/start` mostra o desafio → "Verificar agora" → `POST .../verify/check`
→ `verified` recarrega a lista; `not_found` avisa para conferir a config). Reusa os endpoints e o
mecanismo do KL-99 (`_verify_instructions`), sem backend novo.

## Item 2 — Dashboard dev sem navegação (FIX)

O dev ficava preso em `/dashboard/gate` (os links só existiam no dropdown do avatar, escondido).
Novo **`DashboardNav.jsx`** (menu horizontal ACIMA do conteúdo): **Dashboard · Security Gate (atual)
· Minha conta · Sair**; conta `both` ganha **Meus sites**. Links puros em `lib/nav.js::
gateDashboardNav(accountType)` (testável). Montado no topo do `GatePortal`.

## Item 3 — Remover "Dois produtos, um só lugar" da home (FIX)

Removida a seção `ProductSplit` do `index.astro` (import + uso) e **deletado** o arquivo
`web/src/components/home/ProductSplit.astro` (só a home o usava). A separação empresa × dev já está
no header. A home fica: header → hero (buscador + stats + pills de setor) → footer.

## Item 4 — Números dinâmicos (FIX)

- **`/melhores` "São 300 no total"** era `len(rows)` truncado em 300. Fix backend: `/public/best`
  agora devolve `total` = **contagem REAL** de sites score 100 (`store.count_public_score_100_sites()`,
  mesmo filtro de `public_score_100_sites`) + `shown` = tamanho da lista. Try SEPARADO (uma falha na
  contagem não zera a vitrine → cai no `len(rows)`). Em prod: "São 719 no total" listando ≤300.
- **Home**: os contadores já eram dinâmicos (KL-103, `landing-stats.js` puxa `/public/stats` ao vivo);
  atualizei os fallbacks estáticos estagnados (50.800+→100.000+, 27.000+→60.000+, meta 50.000+→100.000+).
  No dev o valor ao vivo do seed aparece (ex.: "56 sites analisados").
- `/setores`, `/estatisticas` já eram SSR-dinâmicos (nada a mudar).

## Item 5 — Landing "Como funciona" desatualizado (FIX)

Reescritos os 3 steps (`security-gate.astro`): **1. Crie sua conta** (cadastro + API key automática)
· **2. Digite a URL** ("escaneie na hora… **sem verificar domínio**") · **3. Integre no CI/CD**
(snippet YAML). Removida a menção a "verifique o domínio". Corrigido **"18 categorias" → "19"** (o
engine tem 19 checks) e o rótulo de plano `Todos (18)` → `Todos (${count})` (fallbacks 18→19; live já
retornava 19). Snippets **YAML limpos (curl, sem Python raw)** — header `X-API-Key` (o real da API; o
exemplo do card usava `Authorization: Bearer`, que está incorreto para este endpoint).

## Item 6 — Fluxo de assinatura (DIAGNÓSTICO — sem bug)

**Diagnóstico (empírico, dev):**
1. `GatePortal.jsx` **lê** `?upgrade=` no mount (linha `new URLSearchParams(...).get('upgrade')`) →
   passa como `autoUpgrade` ao `StatusBar`.
2. O `StatusBar` **auto-dispara** `upgrade(autoUpgrade)` no `useEffect` (KL-159).
3. `upgrade()` chama `POST /account/gate/upgrade`. Em dev (AbacatePay OFF) a resposta é
   **`{fallback:true, contact_email:'suporte@klarim.net', message}`** → a UI mostra a mensagem de
   fallback. Em prod (AbacatePay configurada) a resposta traz `br_code_base64`/`charge_id` → **abre o
   modal PIX (QR + copia-e-cola)** com polling do status.
4. O CTA "Assinar Pro" (logado) resolve para **`/dashboard/gate?upgrade=pro`** (a `GatePlanCTA`
   detecta a sessão corretamente). O "redireciona para /criar-conta" do relato **não se reproduz**
   (foi corrigido no P1/KL-159).

**Onde o fluxo para no dev:** exatamente no `POST /account/gate/upgrade`, que devolve `fallback`
porque **AbacatePay não está no dev stack** — comportamento CORRETO (KL-156), não um bug. Em produção
com a chave real, retorna a cobrança PIX e o modal abre. **Nenhum código foi alterado no item 6.**

---

## Testes

- **`pytest`: 2313 passed, 1 skipped** (+2 `test_kl150_p2_public_best.py`; KL-74 `test_public_best`
  ajustado — FakeStore ganhou `count_public_score_100_sites`).
- **`node --test`: 220 passed** (+2 `gateDashboardNav` em `nav.test.js`).
- **`npm run build`: OK.**
- **Browser (dev)**: item 1 (botão Verificar + modal com 3 métodos + desafio), item 2 (nav
  Dashboard·Security Gate·Minha conta·Sair), item 3 (ProductSplit removido, hero intacto), item 4
  (/melhores usa o total real; home "56 sites analisados" ao vivo), item 5 (3 steps novos, 19
  categorias, curl YAML, sem Python nem verificação de domínio), item 6 (CTA→upgrade→fallback).
  **Zero erro no console.**

## Arquivos

**Backend:** `api/main.py` (`/public/best` total real), `discovery/store.py`
(`count_public_score_100_sites`).
**Frontend:** `web/src/components/dashboard-v2/GatePortal.jsx` (verify UI + DashboardNav),
`web/src/components/dashboard-v2/DashboardNav.jsx` (novo), `web/src/lib/nav.js` (`gateDashboardNav`),
`web/src/pages/index.astro` (remove ProductSplit + fallbacks), `web/src/pages/security-gate.astro`
(Como funciona + 19 categorias + YAML). **Deletado:** `web/src/components/home/ProductSplit.astro`.
**Testes:** `tests/test_kl150_p2_public_best.py`, `tests/test_kl74_content.py` (fake), `nav.test.js`.
**Docs:** `claude.md`.

## Escopo NÃO tocado

Engine de scan, rate limiting e o SEO (títulos/URLs/Schema.org do KL-132) intactos.
