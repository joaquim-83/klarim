# KL-149 — Security Gate: 14 checks novos (CORS, cookies, JWT, rate limit, open redirect, DNSSEC, dependencies, infra URLs)

## Contexto

O Security Gate tinha 5 categorias (headers, SSL, exposure, credentials, API). Para virar
ferramenta séria de CI/CD que devs/CTOs integram no pipeline, faltavam checks que a comunidade
espera. **Todos passivos** — o Gate continua "não é DAST" (GET/HEAD/DNS/handshake TLS; **nenhum
payload de ataque**). Alvos apenas **autorizados** (a Klarim no CI).

## Os 13 módulos novos (14 checks lógicos) — `security_gate/checks/`

| Módulo | Check | Sev. máx |
|---|---|---|
| `cors.py` | reflexão de Origin arbitrário / wildcard+credentials | CRITICAL |
| `cookies.py` | HttpOnly/Secure/SameSite nos cookies de SESSÃO (ignora `_ga`/consent/theme) | CRITICAL |
| `redirect.py` | open redirect via `?redirect=/?next=…` (sonda benigna, **não navega** ao destino) | HIGH |
| `rate_limit.py` | mini-burst de **10** GETs → algum 429? (roda por ÚLTIMO) | HIGH |
| `error_disclosure.py` | stack trace/caminho interno em 404/5xx | HIGH |
| `https_redirect.py` | HTTP → HTTPS (301/302) | CRITICAL |
| `jwt_analysis.py` | `alg:none` / sem `exp` / PII no payload (só DECODIFICA) | CRITICAL |
| `form_security.py` | `<form action>` para domínio externo | CRITICAL |
| `dns_security.py` | DNSSEC + CAA (2 checks) | MEDIUM |
| `dependencies.py` | libs JS com CVE conhecida (base LOCAL, sem API externa) | HIGH |
| `tls_ciphers.py` | ciphers fracos RC4/DES/3DES/NULL/EXPORT | CRITICAL |
| `subdomain.py` | subdomain takeover (CNAME p/ PaaS desativado, por fingerprint) | CRITICAL |
| `infrastructure_urls.py` | URLs de backend/PaaS/túnel/localhost/IP-privado/k8s no HTML+JS | CRITICAL |

## Decisões de engenharia (segurança e correção)

1. **Passividade preservada — `error_disclosure` mais conservador que o card.** O snippet do card
   sugeria disparar `?id='+OR+1=1--` e `?%00` (payloads de injeção). A Klarim tem regra INVIOLÁVEL
   "NUNCA payloads de injeção (SQLi/XSS)" e o Gate se posiciona como "não envia payload de ataque".
   Implementei o teste de 5xx com **inputs malformados benignos** (percent-encoding inválido, valor
   muito longo) — disparam erro de parsing sem serem ataque. O 404 é um path aleatório (100% passivo).
2. **`jwt` só decodifica** (base64) tokens já emitidos — **nunca forja/assina** (regra do card).
3. **`tls_ciphers` — anti falso-positivo do TLS 1.3 (bug pego na validação real):** `set_ciphers()`
   NÃO controla os ciphersuites do TLS 1.3, então um handshake "passava" negociando um cipher FORTE
   (`TLS_AES_256_GCM_SHA384`) e o código o reportava como fraco → **3 falsos CRÍTICOS na klarim.net**.
   Fix: forço `maximum_version = TLSv1_2` **e** só reporto se o cipher NEGOCIADO contém um marcador de
   fraqueza real (RC4/DES/NULL/EXPORT/MD5/ADH…). Sem isso, o job de CI iria vermelho por engano.
4. **`infrastructure_urls` — regex multi-label (bug pego na validação real):** o Cloud Run novo é
   `SERVICE-PROJNUM.REGION.run.app` (multi-label). O regex `[a-z0-9-]+\.run\.app` (single-label) NÃO
   pegava o backend da Igoove. Fix: `[a-z0-9.-]+` no prefixo de todos os patterns de PaaS. Agora
   detecta `ig-backend-…southamerica-east1.run.app`. **Nunca alerta o próprio domínio.**
5. **`rate_limit` roda por ÚLTIMO** no `_DEFAULT_ORDER` — seu mini-burst (possível 429 no IP do gate)
   não contamina os outros checks.
6. **Blocking (DNS/socket) via `asyncio.to_thread`** com helpers mockáveis (`_query_dnskey`/`_query_caa`/
   `_accepts_cipher`/`_resolve_cname`) — não trava o event loop e é testável offline.
7. **`_extract_js_urls`/`_origin`/`_host` movidos p/ `security_gate/utils.py`** (compartilhados por
   credentials + infrastructure, como o card pediu).

## Integração

Engine: `_CHECKS` + `_DEFAULT_ORDER` (5 → **18**), preservando o loop + o roteamento de
`spa_fingerprint` do KL-147 (só exposure/api são spa-aware; os 13 novos usam `(client,url,config)`).
`GateConfig.checks` default com os 18; `rate_limit_endpoints` configurável. `security-gate.yml`,
formatter (categorias) e CLI `--checks` atualizados.

## Validação contra os 3 alvos (obrigatória)

| Alvo | Score | Achados | Nota |
|---|---|---|---|
| `klarim.net` | **90/100 🟢** | rate_limit (HIGH) | Critical **0** → CI verde. Sem falsos positivos. |
| `sistema.igoove.com.br` (SPA) | **55/100 🟡** | **infra: Cloud Run `ig-backend-…run.app` + `127.0.0.1` no JS**, HSTS, DNSSEC, CAA, rate_limit | O **achado real do card** confirmado ✅ |
| Traka Cloud Run API | **43/100 🔴** | headers ausentes, DNSSEC, CAA, rate_limit | Critical **0** (API bare sem hardening) |

**Nenhum FAIL crítico falso** nos 3 → o job `security-gate` da CI (`--fail-on critical`) segue verde
(um score baixo não reprova; só CRÍTICO reprova). Os findings HIGH/MEDIUM são honestos e acionáveis.

## Testes

- **Novo `tests/test_kl149_new_checks.py`** (+38): ≥1 PASS + ≥1 FAIL por check + casos extras
  (CORS wildcard/creds, JWT alg-none/no-exp/PII, versão de dependência, cipher fraco mockado, CNAME
  de takeover mockado, Cloud Run multi-label, k8s CRITICAL, header de dev, próprio-domínio ignorado).
- Engine tests do KL-141 ajustados para 18 checks (isolamento de erro, contagem).
- **`2101 pytest passed, 1 skipped`.** Os testes de credentials/exposure/api/KL-147 seguem verdes
  (o refactor de `_extract_js_urls` não regrediu).

> ⚠️ Docker não estava disponível no ambiente local para o `docker-compose.dev.yml`; a validação foi
> a suíte offline completa + a execução REAL do CLI contra os 3 alvos autorizados (acima). A CI roda
> pytest no push e o job `security-gate` roda o Gate contra `klarim.net` pós-deploy.

## Docs

- `docs/SECURITY.md` §10 (novo) — tabela dos 18 checks + as regras invioláveis (passividade, teto de
  10 requests, zero forja de token, zero injeção).
- `CLAUDE.md` — entrada do card KL-149 em §9.

## Pós-deploy

Fechar o **KL-149 no Jira** após a validação (feita acima). O job `security-gate` da CI passará a
rodar os 18 checks contra `klarim.net` no próximo deploy — esperado **90/100 🟢, Critical 0** (verde).
