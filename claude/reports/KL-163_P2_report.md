# KL-163 (Prompt 2/2) — Endereço estruturado (CEP + ViaCEP) + KYC polish

**Status:** ✅ implementado e validado no `docker-compose.dev.yml`. **SEM DEPLOY** — aguarda a
revisão visual do dono.

**Card:** KL-163 — Security Gate: relatório PDF, endereço estruturado, KYC. Este prompt substitui o
`address` TEXT livre por endereço **estruturado** (campos + CEP com auto-preenchimento ViaCEP),
grava em JSONB, e adiciona o polish de KYC (telefone "não verificado", cidade/UF no PDF).

---

## 1. O que foi entregue

### Schema — `address_data JSONB` (backward compat)
`ALTER TABLE users ADD COLUMN IF NOT EXISTS address_data JSONB` (idempotente no `ensure_schema`).
Formato: `{cep, street, number, complement, neighborhood, city, state}`. A coluna **`address` TEXT
NÃO foi removida** (legado — contas antigas com texto livre seguem válidas). Regra de leitura:
`address_data` primeiro; se NULL, cai no `address` TEXT.

### Store
- `update_user_kyc(..., address_data=None)` — grava um dos dois: estruturado → `address_data`
  (limpa o TEXT); legado → `address` (limpa o JSONB). Evita divergência entre os dois.
- `get_account_gate_fields` retorna `address_data`.
- `list_gate_dev_accounts` passa a expor `phone` + `phone_verified` (admin).

### `POST /account/kyc` — aceita objeto OU string (backward compat)
- `KYCBody.address: Optional[Union[str, Dict[str, Any]]]`.
- **Objeto** → `_validate_and_normalize_address` (`api/gate.py`): campos obrigatórios
  `cep/street/number/neighborhood/city/state` (**422** se faltar), CEP normalizado para `00000-000`
  (**422** se não tiver 8 dígitos), UF ∈ 27 UFs (**422** se inválida), `complement` opcional →
  gravado em `address_data`.
- **String** → comportamento legado (texto livre em `address` TEXT).
- `_kyc_complete` aceita os dois via `_address_ok` (dict com todos os campos OU string ≥10 chars) +
  CPF + telefone + **e-mail confirmado** (regra KL-156 inalterada).

### PDF — cidade/UF no cabeçalho (ajuste do P1)
`build_gate_run_context(city_state=…)` + o endpoint deriva `city_state` do `address_data` via
`_city_state_from_address` (`'Cidade/UF'`). O template mostra a cidade/UF **abaixo do CPF**. **Nunca
o endereço completo** (rua/número) — só cidade/UF como contexto. Endereço legado (texto livre) não é
mostrado (não é confiável).

### Frontend
- **`web/src/lib/gate/address.js`** (PURO/testável): `maskCep`, `isValidCep`, `parseViaCepResponse`
  (extrai `logradouro/bairro/localidade/uf`, `null` se `erro`), `isAddressComplete`, `UF_LIST` (27),
  `ADDRESS_REQUIRED`, `emptyAddress`.
- **`web/src/components/dashboard-v2/AddressFields.jsx`** (componente controlado, reutilizável):
  CEP (mascarado) + Rua + Número + Complemento (opcional) + Bairro + Cidade + UF (`<select>` 27 UFs).
  **Auto-preenchimento ViaCEP:** ao digitar 8 dígitos → `fetch https://viacep.com.br/ws/{cep}/json/`
  com **debounce 500ms** + spinner de loading; sucesso → preenche rua/bairro/cidade/UF (editáveis);
  `erro` → "CEP não encontrado. Verifique e tente novamente."; ViaCEP fora do ar → "Preencha
  manualmente." (**nunca bloqueia** o form). Validação inline: campo obrigatório vazio → borda
  vermelha ao `blur`.
- Usado no **`KycBanner.jsx`** (modal do resultado) E no **`GateOnboarding.jsx`** (wizard step 4) —
  ambos com o mesmo componente (sem duplicar a lógica ViaCEP).
- `canSubmitKyc` (`ux.js`) atualizado para aceitar `address` como **objeto** (`isAddressComplete`)
  ou string legada.
- **Telefone:** nota "Seu telefone será verificado por SMS em breve. Por enquanto, os dados são
  validados pelo CPF e e-mail." no modal + badge **"(não verificado)"** no admin (`GatePlansPage`,
  com tooltip "Verificação por SMS será implementada em breve").

### CSP / rede (ViaCEP)
A chamada ao ViaCEP é **client-side**. A CSP pública estrita (`frontend/nginx/security_headers.conf`)
ganhou `https://viacep.com.br` em `connect-src` (validado com `nginx -t` renderizando o
`https.conf.template`). O dev stack (`dev.conf`) **não tem CSP** → a validação no navegador funcionou
sem depender disso. (O "allowlist de rede do container" citado no card não se aplica: o fetch sai do
navegador, não do servidor.)

---

## 2. Segurança / privacidade

- **CPF sempre mascarado** no PDF (inalterado do P1); **endereço nunca completo** no PDF (só
  cidade/UF).
- Validação server-side de CEP/UF/campos (não confia no cliente) — o front é feedback imediato.
- `_validate_and_normalize_address` limita cada campo a 200 chars (defensivo).
- ViaCEP é fail-open no front (não bloqueia o cadastro se cair) e a decisão de completude é
  server-side.

---

## 3. Testes

**Backend — `tests/test_kl163_p2_address.py` (+12):**
1. `POST /account/kyc` com address objeto → grava em `address_data` (CEP/UF normalizados), `address`
   TEXT fica None.
2. address string (legado) → grava em `address` TEXT, `address_data` None.
3. CEP inválido → **422**; 4. UF inválida → **422**; 5. `number` faltando → **422**;
   6. CEP sem traço (8 dígitos) → normalizado.
7. `_kyc_complete` com `address_data` válido → **True**; com texto legado ≥10 → **True**; vazio/`{}`/
   dict incompleto/sem e-mail → **False**.
8. `_validate_and_normalize_address` (normaliza CEP/UF, complemento opcional).
9. `_city_state_from_address` (dict/JSON string/None/inválido; nunca vaza rua/número).
10. PDF: `city_state` no HTML, sem rua/complemento; sem city_state → sem a linha.
Além disso, o FakeStore de `test_kl153_backend.py` foi atualizado para o novo `update_user_kyc`.

**Frontend — `web/src/lib/gate/address.test.js` (+12) + `ux.test.js` (+1):**
`maskCep`, `isValidCep`, `parseViaCepResponse` (válido/erro/UF inválida), `isAddressComplete`
(completo/sem número/complemento opcional/CEP-UF inválidos), `UF_LIST`=27, `ADDRESS_REQUIRED`,
`emptyAddress`, e `canSubmitKyc` com endereço objeto.

**Resultado:** **2349 pytest passed, 1 skipped** · **236 node --test pass** · `npm run build` OK ·
`nginx -t` OK.

---

## 4. Validação no navegador (dev — OBRIGATÓRIA)

Conta dev `gatedev@teste.com` (KYC resetado). Scan avulso real (example.com → 43/100) para abrir o
modal de KYC via "Completar cadastro →".

1. **ViaCEP autofill:** digitei o CEP `80010-000` → `GET viacep.com.br/ws/80010000/json/` (200) →
   Rua "**Rua José Loureiro**", Bairro "**Centro**", Cidade "**Curitiba**", UF "**PR**" preencheram
   automaticamente (editáveis). ✅
2. **CEP inválido:** `99999-999` → borda vermelha + "**CEP não encontrado. Verifique e tente
   novamente.**"; campos de endereço ficam vazios para preenchimento manual. ✅
3. **Submit completo** (CPF `111.444.777-35`, número 100, telefone) → `POST /account/kyc` **200**;
   no banco: `kyc_completed=t`, `address_data={"cep":"80010-000","city":"Curitiba","state":"PR",
   "number":"100","street":"Rua José Loureiro","neighborhood":"Centro"}`, `address` TEXT vazio. ✅
   (Um CPF já usado por outra conta deu **409** com a mensagem certa — validação OK.)
4. **Telefone "(não verificado, SMS em breve)":** nota visível abaixo do campo no modal. ✅
5. **PDF:** relatório do run com o cabeçalho **"Desenvolvedor: CPF \*\*\*.\*\*\*.777-35"** seguido de
   "**Curitiba/PR**" (só cidade/UF, sem rua/número). ✅ (render inspecionado visualmente)
6. **Validação impede submit incompleto:** o botão Confirmar usa `canSubmitKyc` (desabilitado sem
   endereço completo) — coberto no unit test + observado na UI.

---

## 5. Arquivos

**Novos:** `web/src/lib/gate/address.js`, `web/src/lib/gate/address.test.js`,
`web/src/components/dashboard-v2/AddressFields.jsx`, `tests/test_kl163_p2_address.py`.
**Alterados:** `discovery/store.py` (schema + `update_user_kyc` + `get_account_gate_fields` +
`list_gate_dev_accounts`), `api/gate.py` (`KYCBody`/`account_kyc`/`_validate_and_normalize_address`/
`_address_ok`/`_kyc_complete`/`_city_state_from_address`/endpoint do report),
`reporter/gate_run_report.py` (`city_state` no contexto + template), `web/src/lib/gate/ux.js`
(`canSubmitKyc`), `web/src/components/dashboard-v2/KycBanner.jsx`,
`web/src/components/dashboard-v2/GateOnboarding.jsx`,
`web/src/components/admin/GatePlansPage.jsx` (telefone + badge),
`web/src/lib/gate/ux.test.js`, `tests/test_kl153_backend.py` (FakeStore),
`frontend/nginx/security_headers.conf` (CSP viacep), `web/package.json` (test:unit),
`docs/API.md`, `CLAUDE.md`.

**Não alterado (regra do card):** a engine de scan, o rate limiting, e a coluna `address` TEXT
(preservada para backward compat).
