# KL-156 — Fixes pós-teste manual KL-153 (dropdown, KYC, upgrade, plano no painel)

## Contexto
5 bugs encontrados no teste manual do Security Gate após o KL-153. Resolvidos num passe.

## Fix 1 — Dropdowns do header não fechavam
Os dropdowns "Para empresas"/"Para devs" (`<details>` CSP-safe) não fechavam ao clicar fora nem ao
abrir o outro. **Solução:** classe `nav-dropdown` nos `<details>` (`NavDropdown.astro` + hambúrguer
mobile do `Header.astro`) + lógica no `web/public/header.js` (script EXTERNO já allowlisted, CSP
`'self'`) — fecha ao clicar fora e fecha os OUTROS ao abrir um. `?v=3`→`?v=4`. Lógica pura testável
`otherDropdowns` (`web/src/lib/nav.js`). **Validado no browser (dev):** abrir "devs" fecha "empresas"
(`emp:false, devs:true`); clicar fora fecha tudo (`anyOpen:false`); zero erro no console.

## Fix 2 — KYC agora exige e-mail confirmado (não phone_verified)
`phone_verified` era auto-setado ao preencher o telefone → placeholder de SMS não pode ser gate de
identidade. **Solução:** helper puro `api/gate.py::_kyc_complete(cpf, address, phone, email_confirmed)`
— `kyc_completed = CPF válido + endereço (≥10) + telefone + **email_confirmed**`. O `email_confirmed`
é a ÚNICA verificação de identidade REAL (código no signup); `phone_verified` fica no schema (SMS
futuro) mas NÃO gateia. O endpoint já devolvia 403 sem e-mail confirmado — a condição reforça
(defesa-em-profundidade).

## Fix 3+4 — Fluxo de upgrade Gate end-to-end
**Diagnóstico:** o backend gerava o PIX corretamente (br_code/br_code_base64), mas o front abria
`checkout_url` (= `/dashboard/gate?upgrade=…`) em nova aba → só reabria o dashboard ("não leva a lugar
nenhum"). **Solução:**
- **Frontend** (`GatePortal.jsx`): o botão Upgrade agora abre um **modal PIX** (QR `br_code_base64` +
  copia-e-cola `br_code`) e faz **polling** de `/api/account/upgrade/status?charge_id=` (reusa o
  `_confirm_subscription_payment` que ativa o `gate_plan_id` no prefixo `gate:`); confirmado → ativa o
  plano e fecha. Reusa o padrão de QR do `PlanSection` (upgrade de site). 409 (já no plano)/429/erro →
  mensagem inline. **Nunca loading silencioso.**
- **Backend** (`api/gate.py::gate_upgrade`): sem AbacatePay configurado (`_payments_enabled()` False),
  em vez de 503 cru, responde **200 com `{fallback: true, contact_email: suporte@klarim.net, message}`**
  → o front mostra a mensagem acionável (e-mail clicável). Com pagamento OK, retorna o PIX.

## Fix 5 — Plano Gate visível no painel
**`GatePortal.jsx` — bloco "Seu plano"** (na status bar): plano + scans/hora + cooldown por domínio
(KL-155) + nível de acesso + próximo plano com preço. **`AccountSettings.jsx`** (`/dashboard/conta`)
ganhou uma seção **Security Gate** ("Plano X" + link "Abrir dashboard →", ou "Ativar" se ainda owner),
via `GET /api/account/gate/status`. Helpers puros `planName`/`planDetails`/`canUpgrade` (`ux.js`).

## Testes
**Backend** (`test_kl153_backend.py`, +3): `_kyc_complete` requer e-mail confirmado (unit) · endpoint
KYC sem e-mail confirmado → 403 · upgrade com pagamento desligado → 200 fallback (não erro silencioso).
**Frontend** (`nav.test.js` + `ux.test.js`, +4): `otherDropdowns` · `planName` · `planDetails` ·
`canUpgrade`. **2286 pytest passed · 191 node --test pass · `npm run build` OK.**

## Regras respeitadas
Engine/scanner público **inalterados**. Fallback claro no upgrade (nunca loading silencioso). CSP-safe
(header.js externo `'self'`, sem inline). Docs: `docs/SECURITY.md` §12, `claude.md`.

## Arquivos
**Editados:** `api/gate.py` (`_kyc_complete`, KYC, upgrade fallback), `web/public/header.js` (dropdown
close), `web/src/components/NavDropdown.astro` + `Header.astro` (`nav-dropdown`, `?v=4`),
`web/src/components/dashboard-v2/GatePortal.jsx` (PIX modal, plano, fallback),
`web/src/components/account/AccountSettings.jsx` (seção Gate), `web/src/lib/gate/ux.js` (planName/
planDetails/canUpgrade), `web/src/lib/nav.js` (otherDropdowns), `tests/test_kl153_backend.py`,
`web/src/lib/nav.test.js`, `web/src/lib/gate/ux.test.js`, `docs/SECURITY.md`, `claude.md`.
