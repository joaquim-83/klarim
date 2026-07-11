# KL-32 — Análise de qualidade de headers + headers modernos

**Card:** KL-32 (o card Jira **de headers** — não confundir com "worker control", que
recebeu o mesmo número por colisão). **Prioridade:** Alta.
**Objetivo:** transformar "header checker" em "header analyser" — aprofundar 4 checks de
presença para análise de eficácia e adicionar 6 checks de headers modernos ausentes.

---

## Parte 1 — Checks aprofundados (mesmo arquivo, tier/severidade inalterados)

- **check_05 CSP** — `parse_csp`/`analyze_csp`: faz parse da policy. Valores perigosos
  (`'unsafe-inline'`, `'unsafe-eval'`, `'unsafe-hashes'`, `*`, `data:`, `blob:`) em
  `script-src`/`default-src` → **FAIL** com evidência detalhada ("equivale a não ter CSP").
  Diretivas essenciais ausentes (`default-src`, `object-src`, `base-uri`,
  `frame-ancestors`) → nota (PASS). Antes um CSP cosmético passava; agora reprova.
- **check_02 HSTS** — avalia `max-age` (mín. **6 meses**, ideal **1 ano**),
  `includeSubDomains`, `preload`. `max-age` ausente/0/curto → FAIL ("proteção efêmera");
  aceitável → PASS com notas do que melhorar.
- **check_17 cookies** — `analyze_cookie` por cookie: `SameSite=None` sem `Secure`,
  `Domain` amplo (public suffix, ex.: `.com.br`), prefixo `__Secure-`/`__Host-` sem a flag
  exigida, cookie de sessão sem HttpOnly/Secure/SameSite → FAIL, listando cada problema.
- **check_18 CORS** — lê também `Access-Control-Allow-Credentials`: `*`/origem-refletida
  **+ credenciais** → **FAIL ALTA** (exfiltração cross-origin real); `*` sozinho → **FAIL
  MÉDIA**.

## Parte 2 — Checks novos (31–36, tier pago ORDER>15, todos A05:2025)

| # | Check | Sev. | CWE | Regra |
|---|-------|------|-----|-------|
| 31 | Permissions-Policy | Média | CWE-693 | ausente/feature sensível com `*` → FAIL |
| 32 | COOP | Baixa | CWE-346 | `same-origin[-allow-popups]` → PASS; ausente/`unsafe-none` → FAIL |
| 33 | COEP | Baixa | CWE-346 | `require-corp`/`credentialless` → PASS; ausente → FAIL |
| 34 | CORP | Baixa | CWE-346 | `same-site`/`same-origin`/`cross-origin` → PASS; ausente → FAIL |
| 35 | Referrer-Policy | Baixa/Média | CWE-200 | `unsafe-url` → FAIL MÉDIA; ausente → FAIL BAIXA; seguro → PASS |
| 36 | Cache-Control em `<form>`/senha | Média | CWE-524 | form + `no-store`/`no-cache`/`private` → PASS; form sem proteção → FAIL; sem form → PASS |

São **pagos** de propósito: headers modernos com adoção baixa — se fossem gratuitos, quase
todo site brasileiro ficaria vermelho.

## Parte 3 — Classificação (KL-34/35)

Os 6 novos entram em `classifications.py` (A05:2025 Security Misconfiguration + CWE-693/
346/346/346/200/524 + Art. 46), carimbados pelo runner. Novos CWE: 346 e 524.

## Parte 4 — Relatórios (identidade dual)

`RISK_MESSAGES` (executivo, informal — "CSP mal configurada é como ter um alarme instalado
mas desligado"; câmera/microfone; janelas; cache), `ACCESSIBLE` e `TECHNICAL` (com as
diretivas/valores e o fix) para os 6 novos. Categorias de risco atualizadas.

## Testes (`tests/test_kl32_headers.py`, 27 + CORS em `test_checks_16_29.py`)

CSP (unsafe-inline/eval/`*`/default-src ausente/limpo/ausente), HSTS (curto/bom/0/completo),
cookies (SameSite=None sem Secure, `__Secure-` sem Secure, ok), CORS (`*` → MÉDIA,
`*`+credenciais → ALTA), Permissions-Policy/COOP/COEP/CORP/Referrer/Cache-Control (PASS/
FAIL/severidade), classificações dos 6. Contagens ajustadas **30→36 / pagos 15→21** em
`test_kl27_funnel.py`, `test_classifications.py`, `test_checks_16_29.py`.

## Deploy

**Flush `scan:*` no Redis após deploy** — a análise de qualidade faz CSP/HSTS antes PASS
virar FAIL, mudando scores (esperado e desejado). Docs: `claude.md` §33, `README.md`.

## Arquivos

**Novos:** `check_31_permissions_policy.py`, `check_32_coop.py`, `check_33_coep.py`,
`check_34_corp.py`, `check_35_referrer_policy.py`, `check_36_cache_control_forms.py`,
`tests/test_kl32_headers.py`, este relatório. **Alterados:** `check_csp.py`,
`check_hsts.py`, `check_17_cookies.py`, `check_18_cors.py`, `classifications.py`,
`reporter/generator.py`, `reporter/risk_messages.py`, `tests/test_checks_16_29.py`,
`tests/test_kl27_funnel.py`, `tests/test_classifications.py`, `claude.md`, `README.md`.
