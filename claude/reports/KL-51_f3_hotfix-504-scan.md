# Hotfix — 504 no /api/scan/summary (KL-51 f3)

> **Urgente:** scans começaram a dar **504 Gateway Timeout** após o último deploy.
> Console: 401 em recurso + 504 em `/api/scan/summary`.

## Diagnóstico

A hipótese inicial (optional_user/middleware rejeitando requests) **não** se confirmou —
a auth do endpoint já é opcional. Medições em produção:

| Teste | Resultado |
|-------|-----------|
| `/api/scan/summary` anônimo, URL nunca escaneada | **200 `auth_required` em 0,5s** (rápido, correto) |
| `/api/account/me` anônimo | 401 em 0,46s (correto — é o que o Header usa) |
| `/api/health` | 200 em 0,48s |
| **Scan real completo (gov.br, logado)** | **200 em 80,8s** (score 78) |

**Causa raiz:** o scan roda **inline e sequencial** no request (`scanner/runner.py`:
`for check: await check_fn`). 48 checks serializados levam ~80s num site grande — **perto
do `proxy_read_timeout` de 120s** do `location /api/`. Sites mais lentos, ou a **janela de
cache frio logo após o deploy** (o `docker compose down/up` limpa o Redis + os caches de
CVE/CNAE em `/tmp` do container, então os primeiros scans re-baixam tudo), ultrapassam
120s. Aí o Nginx **desconecta** o cliente (504), e o handler — que roda atrás do
`_admin_auth_mw` (um `BaseHTTPMiddleware`) — termina o scan e tenta responder a um cliente
já desconectado, gerando o **`AssertionError`** visto nos logs (ruído; o worker se
recupera e os scans seguintes voltam 200).

O "401 em recurso" do console é o `/api/account/me` do Header (esperado quando deslogado),
não o scan — pista secundária.

## Correções

**Descoberta na verificação:** só bumpar o timeout **não bastou** — um scan **frio** de
site grande (`correios.com.br`) deu **504 aos 180,6s**. O gargalo é o scan **sequencial**.
Então a correção principal virou **paralelizar o runner**.

1. **`scanner/runner.py` — checks em paralelo** (`asyncio.gather` + `Semaphore
   (SCAN_MAX_CONCURRENCY=12)`). Seguro: o rate limiter de `base.fetch` é **por-domínio**
   (`asyncio.Lock` segurado durante todo o request), então requests ao MESMO domínio
   continuam **serializados em 1 req/s** (regra do scanner passivo preservada); só os
   checks de **domínios distintos** (crt.sh, HIBP, DNS, TLS, CVE…) passam a se sobrepor.
   `gather` devolve os resultados **na ordem dos checks** (relatório inalterado). Medido:
   example.com 48 checks, score 75, ordem preservada, sem erro.
2. **`auth_users.optional_user` captura QUALQUER exceção → `None`** (antes só
   `HTTPException`). Auth opcional **nunca** pode derrubar o scan. Atende à diretriz do card.
3. **`proxy_read_timeout`/`proxy_send_timeout` do `/api/`: 120s → 180s** (3 blocks) — folga
   extra além da paralelização.
4. **`scan.astro`**: fetch SSR de `/account/me` com timeout (`AbortSignal.timeout(4000)`) —
   API ocupada não trava o render da `/scan`.
5. **`ScanFlow.runScan` re-tenta 1×** após 20s em falha/timeout: o scan lento **cacheia** no
   servidor mesmo com 504 no cliente → a re-tentativa pega o cache quente.

Sobre a paralelização: os checks 41-44 compartilham um handshake TLS por host (cache ~2min);
em paralelo podem fazer alguns handshakes a mais (mais requests, ainda passivo) — sem
impacto no score. O rate limiter garante a política de 1 req/s por domínio.

## Testes

- `tests/test_accounts.py` → **25** (`test_optional_user_never_raises`).
- `tests/test_runner_concurrency.py` (novo): `run_scan` com checks mockados roda em
  **paralelo** (duração < soma dos delays) e **preserva a ordem** + carimba `check_id`.
- Scan real de example.com (48 checks, score coerente, ordem ok) rodado localmente.

## Verificação pós-deploy

Anônimo (`auth_required` rápido) + o mesmo `correios.com.br` **frio** agora retornando
**200 bem abaixo de 180s**. `nginx -t` no CI valida os timeouts.
