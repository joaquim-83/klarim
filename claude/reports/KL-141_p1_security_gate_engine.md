# KL-141 Prompt 1/4 — Security Gate: engine + models + checks (exposição/headers/SSL)

**Data:** 2026-08-03

Primeiro dos 4 prompts do **Security Gate** — um scanner de **exposição/configuração
pós-deploy** (NÃO é DAST — não envia payloads de ataque; verifica o que ficou exposto após o
deploy). Roda em ~7s contra `klarim.net` (alvo < 30s do card). Fase 1 = dogfooding no próprio
CI/CD. Módulo **novo e SEPARADO** do `scanner/` (portável — futuro pacote pip).

## Entregue (Prompt 1)

```
security_gate/
├── __init__.py          # run_all + modelos exportados, __version__=1.0
├── models.py            # Severity, Status, Result, GateReport (score/passed/counts), Config
├── engine.py            # run_all(url, timeout, checks, deploy_ts) → GateReport
├── checks/
│   ├── __init__.py
│   ├── exposure.py      # NOVO: 11 grupos (KL-139 checks 1-3, 5-12)
│   ├── headers.py       # reusa o threshold HSTS do scanner; validação local (1 request)
│   └── ssl.py           # reusa scanner.tls_analyzer.get_tls_info + WEAK_PROTOCOLS
└── formatters/__init__.py   # placeholder (Prompt 3)
```

- **models:** `score` = 100 − penalidades (CRITICAL −20, HIGH −10, MEDIUM −5, LOW −2, piso 0);
  `passed` = sem nenhum FAIL crítico (o CI usa p/ o exit code); `critical/high/medium_count`.
- **engine:** só orquestra (zero lógica de check). Headers **anti-cache em TODOS os requests**
  (`Cache-Control: no-store`, `Pragma`, `If-None-Match`), **User-Agent honesto** `Klarim Security
  Gate/1.0`. Um check que estoure vira `Result` ERROR isolado (não derruba o gate). `deploy_ts`
  opcional → avisa (log) se a raiz responde `Last-Modified` anterior ao deploy (cache-busting).
- **exposure (novo):** HEAD primeiro (não baixa body — princípio KL-139); um path 200 no grupo já
  reprova (`break`). `directory_listing` faz um GET limitado (2000 chars, não armazenado) p/
  distinguir listagem real de fallback de SPA. Grupos: env/git/cms-config (CRÍTICO), admin/api-docs/
  debug/backup (ALTA), source-maps/dir-listing/htaccess/server-info (MÉDIA).
- **headers:** reusa `HSTS_MAX_AGE_RECOMMENDED` do `scanner.checks.check_hsts` (não diverge o
  threshold); a validação é local porque os checks do scanner são coroutines acopladas ao próprio
  `fetch()` (rate-limit por domínio) — não são validadores puros, e o Gate usa 1 response
  compartilhado. Valida HSTS/CSP/X-Frame/X-Content-Type(nosniff)/Referrer/Permissions/Server(versão).
- **ssl:** reusa `scanner.tls_analyzer.get_tls_info` (1 handshake, cacheado, já testado) +
  `WEAK_PROTOCOLS`. Expiração: ≤0d EXPIRADO (CRÍTICO), ≤7d (CRÍTICO), ≤14d (ALTA), senão PASS;
  cert não-verificado → inválido (CRÍTICO); protocolo em WEAK_PROTOCOLS → inseguro (CRÍTICO).

## Reuso do scanner (rule 2 — não duplica)
- **SSL:** importa `get_tls_info` + `WEAK_PROTOCOLS` — reuso real (o handshake inteiro).
- **Headers:** importa o threshold `HSTS_MAX_AGE_RECOMMENDED`. O scanner **não tem validadores
  puros** de header (a lógica vive dentro de coroutines que fazem o próprio fetch), então a
  extração/validação do Gate é local — no modelo de 1-request dele. Documentado no módulo.

## Testes — `tests/test_kl141_gate_engine.py` (41)
Models (score/passed/counts), engine (agrega, filtra por `checks`, isola erro de check, respeita
`timeout` + UA honesto), exposure (200→FAIL por severidade, 403/404→PASS, timeout→ERROR sem crash,
HEAD-only nos paths simples, GET só no dir-listing, SPA vs listagem real), headers (HSTS forte/curto/
ausente, CSP, nosniff, Server com/sem versão), SSL (`get_tls_info` mockado: 87d PASS, 7d CRÍTICO,
14d ALTA, expirado/ inválido CRÍTICO, TLS1.3 PASS, TLS1.0 CRÍTICO, erro→ERROR sem crash).
**1997 pytest passed** (suite completa; +41).

## ⚠️ Achado ao rodar contra o alvo real (`klarim.net`)
`run_all` real: **score 65, passed=True (0 CRÍTICO), 7s**. Flagou 4 HIGH/MEDIUM:
`/adminer`, `/docs`, `/_profiler` (HIGH) e `/main.js.map` (MEDIUM). São **falsos positivos de
SPA**: o Astro/Vite devolve **200 + HTML da app** para paths desconhecidos que **não** estão no
blocklist do nginx (KL-138). O card **reconhece** isso (a nota do `directory_listing` fala de "SPA
retornando 200 para tudo") e mitiga só p/ o `directory_listing` (via GET do corpo); os demais checks
tratam 200 como FAIL por design do Prompt 1.

- **Impacto:** não bloqueia o gate (`passed` só olha CRÍTICO; esses são HIGH/MEDIUM) — é RUÍDO, não
  bloqueio.
- **Correção adequada = Prompt 3 (config YAML):** um **allowlist** de paths esperados (ou a
  supressão de 200-que-devolve-o-shell-da-SPA) resolve sem redesenhar os checks agora. Alternativa
  operacional: estender o blocklist do nginx (KL-138) p/ 404 esses paths também.
- **NÃO** foi alterado o design dos checks neste prompt (fidelidade ao Prompt 1 + princípio
  HEAD-first/sem-body); o achado fica registrado p/ o Prompt 3.

## Não entregue (Prompts 2-4, por design)
Formatters, CLI, config YAML/allowlist, checks de credenciais, API security, integração GitHub
Actions. `formatters/__init__.py` e `models.Config` ficam como sementes.
