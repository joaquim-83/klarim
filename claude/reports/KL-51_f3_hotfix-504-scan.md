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

## Correções (todas de baixo risco)

1. **`auth_users.optional_user` captura QUALQUER exceção → `None`** (antes só
   `HTTPException`). Auth opcional **nunca** pode derrubar o scan — se o token é inválido
   ou o banco falha, trata como anônimo. Atende diretamente à diretriz do card.
2. **`proxy_read_timeout`/`proxy_send_timeout` do `/api/`: 120s → 180s** (nos 3 blocks:
   `http.conf` + os 2 `/api/` do `https.conf.template`). Dá folga para o scan frio
   terminar **antes** do desconecta — o que também elimina o `AssertionError` na maioria
   dos casos.
3. **`scan.astro`**: o fetch SSR de `/account/me` ganhou timeout (`AbortSignal.timeout(4000)`)
   — se a API estiver ocupada, o render da página `/scan` não trava (cai para deslogado; o
   fluxo com e-mail/código ainda funciona).
4. **`ScanFlow.runScan` re-tenta 1×** após uma pausa de 20s em caso de falha/timeout: o
   scan lento **termina e cacheia** no servidor mesmo quando o cliente recebe 504, então a
   re-tentativa pega o **cache quente** e devolve o resultado.

**Não** paralelizei o `runner` (seria o ganho real de latência) por ser arriscado num
hotfix: os checks 41-44 compartilham um handshake TLS (dependente de ordem sequencial), há
contenção possível no rate limiter (1 req/s por domínio) e init concorrente dos caches
CVE/CNAE, com risco de mudar score. Fica como **otimização futura com testes**.

## Testes

`tests/test_accounts.py` → **25** (novo `test_optional_user_never_raises`: sem token → None;
store que explode → None, nunca levanta). SQL/JS validados. O comportamento do scan real
(80s < 180s) é verificado **em produção** após o deploy.

## Verificação pós-deploy

Anônimo (`auth_required` rápido) + logado (scan real < 180s, 200) + um scan completo fresco
retornando 200 dentro do novo timeout. `nginx -t` no CI (job `nginx-check`) valida os
timeouts novos.
