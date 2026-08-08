# KL-154 — Security Gate importa os checks de superfície SPF/DKIM/DMARC do scanner

## Objetivo

O Security Gate (scanner de exposição/configuração pós-deploy) já reusava parcialmente o
`scanner/`:

- `security_gate/checks/headers.py` → importa `HSTS_MAX_AGE_RECOMMENDED` de `scanner.checks.check_hsts`
- `security_gate/checks/ssl.py` → importa `get_tls_info`/`WEAK_PROTOCOLS` de `scanner.tls_analyzer`

Faltavam os checks de **segurança de e-mail** (SPF/DKIM/DMARC), que existem no scanner (checks
21/22/23) mas não no Gate. É verificação essencial pós-deploy: o dev precisa saber se o domínio
dele está protegido contra spoofing/phishing de e-mail.

## Regra do card (comentário de 08/08, substitui a descrição original)

- **NÃO** criar `engine/` unificado — o Gate **importa** do scanner (dependência de via ÚNICA).
- **NÃO** mover nem alterar arquivos do scanner.
- **NÃO** alterar o scan público (regressão ZERO).
- Imports **lazy** (dentro do `try`) — degrada sem crash.

## O que foi feito

### 1. Adaptador de interface — `security_gate/checks/scanner_adapter.py` (novo)

O scanner e o Gate têm modelos DIFERENTES de resultado:

| | Scanner (`CheckResult`) | Gate (`Result`) |
|---|---|---|
| Status | `PASS` / `FAIL` / `INCONCLUSO` (str) | `Status.PASS` / `FAIL` / `ERROR` / `SKIP` (enum) |
| Severidade | `CRITICA` / `ALTA` / `MEDIA` / `BAIXA` (str PT-BR) | `Severity.CRITICAL` / `HIGH` / `MEDIUM` / `LOW` / `INFO` (enum) |
| Detalhe | `evidence` | `detail` |

`adapt_check_result(check_result, check_name, category="surface") -> Result`:

- **Status:** `PASS→PASS`, `FAIL→FAIL`, `INCONCLUSO→ERROR` (neutro no score, como o INCONCLUSO é
  neutro no scanner); desconhecido → `ERROR`.
- **Severidade:** `CRITICA→CRITICAL`, `ALTA→HIGH`, `MEDIA→MEDIUM`, `BAIXA→LOW`; default `MEDIUM`.
- **Detalhe:** `evidence` → fallback `name` → fallback `check_name`.
- Traduz **por VALOR de string** (não por identidade de enum) → não acopla o Gate à implementação
  interna do scanner. Acesso a campo **defensivo** (`getattr`): se o scanner mudar a interface,
  degrada para um default em vez de quebrar.

### 2. Check de e-mail — `security_gate/checks/email_security.py` (novo)

`check_email_security(client, base_url, config=None) -> list[Result]` roda os **mesmos** checks
21/22/23 do scanner (SPF/DKIM/DMARC) e adapta o resultado. Os imports do scanner são **LAZY**
(dentro do `try`, via `importlib.import_module`) — se o scanner sumir/mudar ou a dependência de DNS
faltar, cada check vira um `Result` ERROR (INFO) **isolado**, nunca derruba o gate inteiro. Passivo
(só DNS TXT). Categoria `surface`. `client`/`config` entram só por uniformidade da assinatura do
engine — os checks do scanner fazem o próprio DNS.

### 3. Registro no engine — `security_gate/engine.py`

- `_CHECKS["email_security"] = check_email_security`
- `_DEFAULT_ORDER` ganhou `"email_security"` **após `"dns"`** (18 → **19 checks**).
- Como `ALL_CHECK_NAMES` (api/gate.py) deriva de `_DEFAULT_ORDER`, o novo check entra
  automaticamente nos planos `["all"]` (Team/Enterprise).

### 4. Config e CLI

- `security_gate/config.py` — `GateConfig.checks` default ganhou `email_security` (senão a CLI, que
  usa `config.checks`, não rodaria o check).
- `security-gate.yml` — entrada `email_security: {enabled: true}`.
- `scripts/security_gate.py` — `email_security` na lista de checks do `--help`.

### 5. Plano — checks permitidos

`discovery/store.py::_GATE_SEED_PLANS` — o plano **Pro** ganhou `email_security` (9 → 10 checks).
Free (4 checks) inalterado; Team/Enterprise (`["all"]`) já incluem. ⚠️ O seed é `ON CONFLICT DO
NOTHING`: em bancos **já existentes** (produção) as contas Pro precisam do check adicionado via
**admin de planos** (KL-151 P3). O CI/CLI (dogfooding da própria Klarim) usa a **config**, não os
planos, então já roda `email_security` sem depender do banco.

### 6. Formatters — camadas Surface vs Deep — `security_gate/formatters/terminal.py`

- **Terminal:** os resultados são agrupados em **Surface (servidor + DNS)** — categorias
  `surface`/`headers`/`ssl`/`dns`/`https` — e **Deep (exposição + código)** — todo o resto.
- **JSON:** ganhou `summary.{surface,deep}` com `{pass, fail, checks}` por camada. Campos antigos
  preservados (o teste de compat usa `>=` no set de chaves).

## Testes

`tests/test_kl154_email_security.py` — **22 testes**, hermético (checks do scanner **sempre**
mockados; deixar algum real bateria em DNS de verdade e travaria — lição aprendida no 1º draft):

- **Adaptador:** PASS/FAIL/INCONCLUSO → status; mapa de severidade (CRITICA/ALTA/MEDIA/BAIXA);
  default de severidade/status desconhecidos; fallback do detalhe; categoria custom.
- **email_security:** SPF/DMARC/DKIM presente→PASS e ausente→FAIL (severidade certa); retorna 3
  resultados na ordem spf/dkim/dmarc, categoria `surface`; check que estoura → ERROR isolado (os
  outros seguem); **falha de import lazy → 3 ERROR gracioso, sem crash**.
- **Engine + plano:** `run_all(checks=["email_security"])` inclui spf/dkim/dmarc; `email_security`
  em `_DEFAULT_ORDER`; `get_allowed_checks` — Free **não** roda, Pro roda, `["all"]` inclui.
- **Formatters:** terminal agrupa Surface/Deep (ordem e posicionamento dos checks); JSON tem
  `summary.surface`/`summary.deep` com contagens; compat dos campos antigos.

Contadores de checks (18→19) ajustados nos testes existentes: `test_kl141_gate_engine`
(2), `test_kl151_gate_product`, `test_kl151_p3_portal_admin` (2), `test_kl151_p2_scan_cli`
(blocked 14→15). **Suíte completa: 2238 passed, 1 skipped, 0 failed.**

## Validação real (3 alvos)

| Alvo | E-mail (SPF/DKIM/DMARC) | Full |
|---|---|---|
| `klarim.net` | SPF ✅ · DKIM ✅ (resend) · DMARC ✅ (p=quarantine) → **100/100** | **90/100 🟢** (só rate_limit HIGH; Surface 15 · Deep 28; CI segue verde, critical=0) |
| `sistema.igoove.com.br` | SPF ✅ · DKIM ❌ MEDIUM · DMARC ❌ HIGH (p=none) → **85/100 🟡** | — |
| Cloud Run `igoove-leads-api-…run.app` | SPF ✅ · DKIM ❌ MEDIUM · DMARC ✅ (p=reject) → **95/100 🟢** | — |

O adaptador mapeou corretamente FAIL→severidade certa e o agrupamento Surface saiu como esperado.
JSON `summary`: `{"surface":{"pass":15,"fail":0,"checks":15},"deep":{"pass":27,"fail":1,"checks":28}}`.

**Scan público inalterado:** o `scanner/` não foi tocado — o `email_security` só **importa** os
checks (leitura). Nenhuma mudança em `/api/scan/*`.

## Arquivos

**Novos:** `security_gate/checks/scanner_adapter.py`, `security_gate/checks/email_security.py`,
`tests/test_kl154_email_security.py`.
**Editados:** `security_gate/engine.py`, `security_gate/config.py`,
`security_gate/formatters/terminal.py`, `security-gate.yml`, `scripts/security_gate.py`,
`discovery/store.py` (seed Pro), `docs/ARCHITECTURE.md` (§11 — diagrama Gate→scanner), `CLAUDE.md`,
e 4 testes existentes (ajuste de contador 18→19).
