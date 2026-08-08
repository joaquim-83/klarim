# KL-159 — CRÍTICO: fluxo de pagamento do Gate quebrado (diagnóstico + fix)

## Diagnóstico (cada etapa testada individualmente)

### Etapa 1 — Backend (curl direto, sem frontend)
- **AbacatePay no dev stack:** `ABACATEPAY_API_KEY` **NÃO configurada** → `_payments_enabled()` = **False**.
  (Em produção está configurada — o upgrade de site funciona.) Documentado: no dev, o endpoint deve
  devolver o **fallback**, não PIX.
- **`gate_plans`:** `pro` existe com `price_brl=4900` (R$49), `team=14900`. OK — o slug está certo.
- **`POST /account/gate/upgrade {plan:pro}` com token de dev nível 1 (passwordless):**
  `HTTP 403 {"detail": {"error": "insufficient_level", "required_level": 2, "current_level": 1}}`.
  **← RAIZ DO BUG.** O dev criado por `source=security-gate` é **nível 1 (sem senha, KL-99)**, mas o
  endpoint exige `_require_level(user, 2)`. O `detail` é um **OBJETO**.
- **Mesma chamada com o dev promovido a nível 2:** `HTTP 200 {"fallback": true, "plan": "pro",
  "price_display": "R$ 49/mês", "contact_email": "suporte@klarim.net", "message": "…"}`. O endpoint e o
  fallback do KL-156 **funcionam** — o problema era o nível 1 + o front.

### Etapa 2 — Frontend dashboard (`GatePortal.jsx`, browser + Network)
- **Nível 2:** clicar "Upgrade" → `POST /api/account/gate/upgrade` → **200 fallback** → a mensagem
  aparece. **Funciona.**
- **Nível 1:** clicar "Upgrade" → `POST … upgrade` → **403** → o front mostra **NADA** (sem modal, sem
  erro, sem feedback). Causa dupla: (1) o dev é nível 1 → 403; (2) o handler fazia
  `setErr(e.data?.detail)` com `detail` sendo um **objeto** → React quebra ao renderizar um objeto como
  filho → o erro some silenciosamente. **← "nada acontece" (evidência #2 do card).**

### Etapa 3 — Página de Planos (`security-gate.astro`)
- Os CTAs "Assinar" (pro/team) eram `<a href="/cadastrar?type=developer">` **estáticos**. Logado no
  Free → `/cadastrar` → (KL-157) **redireciona p/ `/dashboard/gate`** — sem pagamento. **← evidência #1
  do card.**

## Fixes

### Fix A — backend (nenhuma mudança de regra)
O endpoint funciona (nível 2 → fallback; prod → PIX). O `_require_level(user, 2)` (pagamento exige
senha) é mantido — o front passa a tratar o 403.

### Fix B — dashboard: o botão "Upgrade" NUNCA fica em silêncio (`GatePortal.jsx`)
- **Coerção de erro** (`ux.js::errDetail`): o `detail` da API vira sempre **string** (objeto → `.error`)
  — nunca mais um objeto no `setErr` (fim do crash silencioso).
- **`upgrade(planSlug)`** trata: `fallback` (mensagem + `mailto:`), PIX (`br_code_base64` → modal QR +
  polling, KL-156), **403 `insufficient_level` → abre o modal "Defina uma senha" (KL-99 `SetPasswordModal`)
  e RE-TENTA o upgrade automaticamente ao concluir**, 409 (já no plano), 429/erro (mensagem inline).
- **Auto-upgrade:** lê `?upgrade=pro|team` na URL e dispara o fluxo no mount (para a página de Planos).

### Fix C — página de Planos leva ao pagamento (`security-gate.astro`)
Os CTAs "Assinar" (pro/team) agora apontam para **`/dashboard/gate?upgrade={slug}`** → o dashboard
dispara o fluxo de pagamento. Logado no Free → paga; anônimo → o middleware manda ao `/entrar`
preservando o `?upgrade=` (fix no `middleware.js`: `pathname + search`) e volta ao dashboard. Free →
cadastro; Enterprise → vendas.

## Testes
**Backend** (`test_kl153_backend.py`): upgrade com Free → fallback (KL-156); já no Pro → 409; **KL-159:
nível 1 → 403 `insufficient_level` (required_level 2)**. **Frontend** (`ux.test.js`, +1): `errDetail`
(string / objeto `{error}` / ausente). `upgradeTarget` já cobre free→pro, pro→team. **2288 pytest · 194
node --test pass · build OK.**

## Validação no BROWSER (docker-compose.dev.yml) — obrigatória, feita
- **Item 6a** (dev nível 1) — clicar "Upgrade" → abre o modal **"🔒 Defina uma senha para continuar"**
  (2 campos) — NÃO "nada acontece". ✅ (não completei a definição de senha por causa da regra de não
  digitar senha; a lógica de re-tentativa é `SetPasswordModal.onDone → upgrade(slug)`.)
- **Item 6b/7** (dev nível 2) — `/dashboard/gate?upgrade=pro` → **auto-upgrade no mount** →
  mensagem "Para assinar o plano Pro, entre em contato pelo e-mail **suporte@klarim.net**" com link
  `mailto:` clicável. (Em prod, com AbacatePay, o mesmo caminho abre o **modal PIX** — `br_code_base64`.) ✅
- **Item 8** — DevTools Network: `POST /api/account/gate/upgrade` é disparado e processado (403 → modal
  de senha · 200 → fallback). **Zero erro no console** (fim do `[object Object]`). ✅

## Regras
Engine de scan / rate limiting / scanner público **inalterados**. Nunca mais silêncio no upgrade. Docs:
`claude.md`. **AbacatePay não está no dev stack** → documentado; o fallback visível cobre esse caso.

## Arquivos
**Editados:** `web/src/components/dashboard-v2/GatePortal.jsx` (errDetail, upgrade(slug), 403→senha,
auto-upgrade), `web/src/lib/gate/ux.js` (`errDetail`), `web/src/pages/security-gate.astro` (CTAs),
`web/src/middleware.js` (preserva `?query`), `tests/test_kl153_backend.py`, `web/src/lib/gate/ux.test.js`,
`claude.md`.
