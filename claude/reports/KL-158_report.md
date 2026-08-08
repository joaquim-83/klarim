# KL-158 — Fix: Pro trial sem pagamento + banner KYC sem link clicável

## Contexto
Dois bugs de produção no Security Gate: (1) toda conta dev nascia no **Pro** (trial 14d) sem pagar →
o plano Free era inútil; (2) o banner de KYC no resultado do scan era **texto puro** sem ação → o
usuário sabia que precisava do KYC mas não chegava ao formulário.

## Fix 1 — Remover o trial Pro automático (todo dev começa no Free)

**Diagnóstico:** o `provision_gate_developer` (e o `/gate/register` e o `/account/gate/activate`)
setava `set_account_gate_plan(..., now, now+14d)` — plano Free MAS com `gate_trial_ends_at` no futuro.
O `get_effective_gate_plan` devolve **Pro** enquanto há trial ativo → o efetivo era Pro para todos.

**Fix (`api/gate.py`):** os 3 pontos agora setam **Free SEM trial** (`set_account_gate_plan(..., None,
None)`):
- `provision_gate_developer` (usado pelo signup `source=security-gate`);
- `POST /gate/register`;
- `POST /account/gate/activate` (só para conta nova — sem plano/trial).

`_TRIAL_DAYS` removido (código morto). **Contas com trial LEGADO NÃO são alteradas retroativamente**
(o `if not gate_plan_id and not trial_ends` do activate as pula; o trial expira naturalmente). Com Free
como padrão, os limites do novo dev passam a ser: 5 scans/h, cooldown 30 min/domínio, 5 min entre
domínios (KL-155) — intencional, incentiva upgrade. **Rate limiting NÃO foi alterado** (os limites do
Free já estavam corretos).

## Fix 2 — Banner KYC clicável (Opção A: modal inline)

**`web/src/components/dashboard-v2/KycBanner.jsx`** (novo, reutilizável): banner com botão **"Completar
cadastro →"** → modal com CPF (mascarado/validado), endereço e telefone → `POST /api/account/kyc` →
`onCompleted`. Reusa a lógica do wizard (`maskCPF`/`isValidCPF`/`canSubmitKyc`). Erros inline (CPF
inválido/duplicado, e-mail não confirmado). Usado no **`GatePortal.jsx::ScanResultCard`** (o "Novo scan"
avulso / último resultado — onde estava o texto puro). Após o KYC, `loadFullResult` busca o run
persistido (`GET /gate/runs/{id}`, **não re-escaneia** — o rate limit por domínio barraria), mostra os
checks detalhados por categoria, o banner some, e re-busca o status (Nível de acesso → Completo).
O wizard (step 3→4) já era acionável (botão "Ver detalhes completos"), então não foi tocado.

## Testes
**Backend** (`test_kl153_backend.py`): `provision_gate_developer` → Free, sem trial, efetivo Free
(5 scans/dia) [cobre registro source=security-gate]; `activate` owner → Free, `trial_ends_at` None,
efetivo Free. **Frontend** (`ux.test.js`, +1): `canSubmitKyc` (CPF válido + endereço ≥10 + telefone).
`kycBannerVisible`/`planName` (free→"Free") já cobertos. **2287 pytest · 193 node --test pass · build OK.**

## Validação no BROWSER (docker-compose.dev.yml) — obrigatória, feita
- **Item 8** — conta dev nova (signup `source=security-gate`) → `plan="Free"`, `plan_slug="free"`,
  `scans_limit_hour=5`. Screenshot: "Plano Free · 1 de 5 scans usados · Cooldown 30 minutos · Upgrade →
  Pro". **NÃO** é Pro. ✅
- **Item 9** — scan no dashboard (80/100) → banner com botão **"Completar cadastro →"** → clique abre o
  modal "Confirme sua identidade" (3 campos). ✅
- **Item 10** — CPF `529.982.247-25` + endereço + telefone → Confirmar → `kyc_completed=true`,
  `access_level="complete"`, **banner some** e o resultado passa a mostrar os checks detalhados. ✅
- Zero erro no console.

## Regras
Engine de scan / rate limiting / scanner público **inalterados**. Docs: `claude.md` (Free como default,
sem trial Pro).

## Arquivos
**Novo:** `web/src/components/dashboard-v2/KycBanner.jsx`. **Editados:** `api/gate.py` (3 pontos + remove
`_TRIAL_DAYS`), `web/src/components/dashboard-v2/GatePortal.jsx` (KycBanner + detalhes + refresh),
`web/src/lib/gate/ux.js` (`canSubmitKyc`), `tests/test_kl153_backend.py`, `web/src/lib/gate/ux.test.js`,
`claude.md`.
