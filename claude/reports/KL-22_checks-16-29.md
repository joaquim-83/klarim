# KL-22 — Expansão do scanner de 15 para 29 checks passivos

**Card:** KL-22
**Objetivo:** ampliar o motor de varredura passiva do Klarim de 15 para **29
checks**, organizados em cinco blocos (web, DNS/e-mail, conteúdo, infra passiva e
OSINT), sem violar a regra inviolável de **varredura 100% passiva**.

---

## 1. Resumo

Foram adicionados **14 checks novos (16–29)**. Todos seguem a interface existente
(`async def check(url) -> CheckResult`, constantes de módulo `ORDER`/`CHECK_ID`/
`NAME`) e são descobertos automaticamente por `discover_checks()` — **nenhuma
lista hardcoded** foi tocada e o `scoring.py` continua dinâmico (funciona com
qualquer número de checks). O conjunto passou de 15 → **29**.

Cada check é **estritamente passivo**: só faz `GET`/`HEAD` a URLs públicas,
consultas DNS públicas e chamadas a **APIs públicas gratuitas de leitura**.
Nenhum envia payload de ataque, faz brute-force ou acessa área autenticada.

Checks que dependem de recurso externo (DNS, crt.sh, HIBP, Safe Browsing)
**degradam para `INCONCLUSO`** (neutro no score) quando o recurso está fora, sob
rate limit, ou sem chave — **nunca lançam erro** que derrube o scan.

---

## 2. Os 14 checks novos

### Bloco web original (16–20)

| # | Check | Sev. | PASS / FAIL |
|---|-------|------|-------------|
| 16 | Documentação de API exposta | Alta | FAIL se `/docs`, `/swagger`, `/openapi.json`, `/graphql`… respondem 200 **com marcadores fortes** de doc (swagger-ui, redoc, graphiql). O gate por marcador evita falso positivo do catch-all de SPA. |
| 17 | Cookies sem flags de segurança | Média | FAIL se um cookie de sessão (`session`/`sid`/`token`/`auth`/`csrf`/`jwt`) vem sem `Secure`+`HttpOnly`+`SameSite`. Sem cookies = PASS. |
| 18 | CORS permissivo | Alta | Preflight `OPTIONS`/`GET` com `Origin` forjado; FAIL se `Access-Control-Allow-Origin: *` ou reflete a origem forjada. Sem ACAO = PASS. |
| 19 | Redirect para domínio diferente | Média | GET sem seguir redirect; FAIL se a raiz (3xx) aponta para um **domínio registrável diferente** (risco de domínio abandonado/sequestrado). www→apex, http→https = PASS. |
| 20 | 403/404 em paths sensíveis | Baixa | HEAD (fallback GET) em `.git/config`, `.env`, `wp-admin/`, `.htaccess`; **403 confirma existência** (information disclosure) → FAIL. 404 = PASS. |

### Bloco A — DNS / e-mail (21–23)

Via `scanner/checks/dns_util.py` (dnspython encapsulado em helpers **síncronos e
mockáveis**, chamados por `asyncio.to_thread`). Convenção: `[]` = ausência
definitiva (NXDOMAIN/NoAnswer) → **FAIL**; `None` = erro de DNS → **INCONCLUSO**.

| # | Check | Sev. | PASS / FAIL |
|---|-------|------|-------------|
| 21 | SPF ausente/fraco | Alta | Sem `v=spf1` → FAIL; `+all` → FAIL; `-all`/`~all` → PASS; sem `all` restritivo → FAIL. |
| 22 | DKIM ausente | Média | Tenta seletores comuns (`default`, `google`, `selector1/2`, `k1`…). Algum com `v=DKIM1`/`p=` → PASS; todos ausentes → FAIL; todos com erro DNS → INCONCLUSO. |
| 23 | DMARC ausente/permissivo | Alta | `_dmarc.{domínio}`: ausente → FAIL; `p=none` → FAIL; `p=quarantine`/`p=reject` → PASS. |

### Bloco B — conteúdo web (24–25)

Parse do HTML servido (via `html.parser` da stdlib).

| # | Check | Sev. | PASS / FAIL |
|---|-------|------|-------------|
| 24 | Mixed content | Média | Recurso (`script/img/iframe/link[stylesheet]/…`) carregado via `http://` numa página HTTPS → FAIL. Ignora `localhost`/`127.0.0.1`. |
| 25 | Formulários inseguros | Alta | `<form method=post>` com `action` `http://` ou para domínio registrável diferente → FAIL. Action relativo/ausente = mesma origem HTTPS → PASS. |

### Bloco C — infra passiva (26–27)

| # | Check | Sev. | PASS / FAIL |
|---|-------|------|-------------|
| 26 | Subdomínios expostos (CT logs) | Média | crt.sh (`?q=%.{domínio}&output=json`); FAIL se **>20 subdomínios E** algum com nome sensível (admin/staging/dev/api/…). crt.sh lento/fora → INCONCLUSO. |
| 27 | Dangling CNAME (subdomain takeover) | Crítica | Para subdomínios comuns, resolve o CNAME; se aponta para serviço propenso a takeover (heroku/azure/s3/github.io/…) **E** o alvo não existe mais (NXDOMAIN) → FAIL. Sem DNS = INCONCLUSO. |

### Bloco D — OSINT (28–29)

APIs públicas gratuitas.

| # | Check | Sev. | PASS / FAIL |
|---|-------|------|-------------|
| 28 | Vazamentos de dados (HIBP) | Média | `GET /api/v3/breaches?domain=` (sem chave). 404 = sem vazamentos → PASS; lista não-vazia → FAIL; rate limit/erro → INCONCLUSO. |
| 29 | Google Safe Browsing | Crítica | Sem `GOOGLE_SAFE_BROWSING_KEY` → **INCONCLUSO** com nota. Com chave: `threatMatches:find`; match (malware/phishing) → FAIL; vazio → PASS; erro → INCONCLUSO. |

---

## 3. Detalhes de implementação

- **Passividade preservada.** `checks/base.fetch` recusa métodos que não sejam
  `GET`/`HEAD`. Os dois checks que precisam de outro método (18 CORS → `OPTIONS`,
  29 Safe Browsing → `POST` de consulta a uma API do Google, **não ao alvo**)
  usam `httpx.AsyncClient` diretamente, num seam isolado e mockável. Nenhum
  request adversarial é enviado **ao site do alvo**.
- **Seams mockáveis** (mantêm o CI hermético): `dns_util.resolve_txt/resolve_cname/
  host_exists` (21–23, 27), `check_26._crtsh`, `check_28._breaches`,
  `check_29._query`. Os testes monkeypatcham esses pontos — **zero rede em CI**.
- **`reporter/__init__.py` continua lazy** (PEP 562): importar `reporter.
  risk_messages` não puxa o WeasyPrint nos containers do worker.
- **Score dinâmico intacto.** `scoring.py` e `discover_checks()` não precisaram
  de mudança — o produto cresce só com o drop de novos `check_*.py`.

## 4. Conteúdo de relatório/mensagens

Para os 14 checks novos foram acrescentadas entradas em:

- **`reporter/risk_messages.py`** — `RISK_MESSAGES` (headline + risco concreto +
  ícone) e as categorias de resumo (`_CAT_VAZAMENTO`/`_CAT_GOLPES`/`_CAT_INVASAO`/
  `_CAT_SUPPLY`). Mesmos riscos aparecem no PDF executivo, e-mails de alerta/
  evolução, `/result` e a tela admin **Escanear**.
- **`reporter/generator.py`** — `ACCESSIBLE` (frase de negócio) e `TECHNICAL`
  (impacto + correção + exemplo de código) para os `check_16`…`check_29`.

Três testes-guarda garantem que **todo check registrado** tem entrada em
`RISK_MESSAGES`, `ACCESSIBLE` e `TECHNICAL` (falham se um check novo for esquecido).

## 5. Testes

`tests/test_checks_16_29.py` — **51 testes offline** (rede mockada), cobrindo
PASS/FAIL/INCONCLUSO de cada um dos 14 checks + os 3 testes-guarda de cobertura
(29 registrados, RISK_MESSAGES completo, ACCESSIBLE/TECHNICAL completos).

`tests/test_risk_messages.py::test_all_15_checks_mapped` foi generalizado para
`test_all_checks_mapped` (asserção no **contrato**, não num número fixo).

```
pytest                → 282 passed, 1 skipped   (suite inteira, offline)
pytest tests/test_checks_16_29.py → 51 passed   (~1s, hermético)
```

## 6. Validação de ponta a ponta

**Self-scan real de `www.klarim.net` (29 checks, 109s):**

- **24 PASS · 2 FAIL · 3 INCONCLUSO · score 93 (🟡 amarelo).**
- INCONCLUSO **degradaram com elegância, sem erro**: check 04 TLS (OpenSSL local
  não negocia TLS 1.0/1.1 — pré-existente), check 26 crt.sh (indisponível no
  momento), check 29 Safe Browsing (sem chave configurada).
- **Os 2 FAIL são achados legítimos no DNS do próprio `klarim.net`:**
  - **check 22 (DKIM)** — nenhum registro DKIM nos seletores comuns.
  - **check 23 (DMARC)** — política `p=none` (permissiva, só monitora).
  - (check 21 SPF **passa** — SPF presente e restritivo.)

O score caiu de 100 → 93 por **achados reais**, não por bug. Isso é o scanner
funcionando: os novos checks de e-mail encontraram lacunas verdadeiras no domínio
do Klarim. Coerente com o princípio "o Klarim pratica o que prega" — a correção
(publicar DKIM e subir o DMARC para `quarantine`/`reject` na Hostinger) é uma ação
de operação separada, fora do escopo de código deste card.

**PDFs:** executivo e técnico renderizam com os 29 checks (headers `%PDF-`
válidos); o técnico exercita o detalhamento de FALHA dos checks novos (22/23).

## 7. Deploy

- CI (`pytest`) verde bloqueia/libera o deploy como de costume.
- **Pós-deploy na VM:** limpar o cache Redis de scans (`scan:*`) para que scans
  cacheados com 15 checks sejam refeitos com 29 — ver a memória
  *"Scan cache stale after scoring change"*. Depois, um self-scan de `klarim.net`
  para confirmar os 29 checks em produção.

## 8. Arquivos

**Novos:** `scanner/checks/check_16..29_*.py` (14), `scanner/checks/dns_util.py`,
`tests/test_checks_16_29.py`, este relatório.
**Alterados:** `reporter/risk_messages.py`, `reporter/generator.py`,
`tests/test_risk_messages.py`, `requirements.txt` (comentário do dnspython),
`.env.example` (`GOOGLE_SAFE_BROWSING_KEY`), `README.md`, `CLAUDE.md`.
