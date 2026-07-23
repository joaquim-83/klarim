# Hotfix de produção — 2026-07-23 (4 problemas simultâneos)

**Prioridade:** CRÍTICA. Deploy imediato. Origem: relatório de 4 falhas em produção após o deploy KL-96.

---

## Diagnóstico (dados reais da VM antes de tocar no código)

| # | Alegação do card | Realidade | Ação |
|---|---|---|---|
| 1 | Scanner parado, fila vazia, último scan 17:29 | **Falso alarme parcial + bug real**. A fila `scan_queue` estava 0 porque a chave REAL é **`klarim:scan_queue`** (=600). O worker ESTAVA escaneando (heartbeat 21:20, target 48489), mas **não persistia no Postgres desde 20:29** | Corrigido (raiz) |
| 2 | nginx_parser em loop de erro | **Confirmado** — `OSError('telling position disabled by next() call')` a cada ciclo | Corrigido |
| 3 | CT poller com JSONDecodeError | **Transitório** — sem erros nos últimos 8 min; mas o código estourava a cada instabilidade do Google | Endurecido |
| 4 | `ALERT_DAILY_LIMIT` duplicado no `.env` | Já limpo (`grep -c` = 1) | Nenhuma |

### Causa-raiz do #1 (a mais grave)
Log de startup do `klarim-worker-1`:
```
[klarim-worker] targets/scans indisponível (DeadlockDetected('deadlock detected ...
Process A waits for AccessExclusiveLock on relation 16627 ... blocked by Process B ...'))
```
No deploy KL-96, **api + discovery + worker reiniciaram juntos e rodaram `ensure_schema()`
CONCORRENTEMENTE**. Os `ALTER TABLE`/`CREATE INDEX` disputaram `AccessExclusiveLock` → o
Postgres matou um lado com **DeadlockDetected**. O worker perdeu; o código fazia
`store = None` **permanente** → daí em diante ele **escaneava, cacheava no Redis e logava
`-> score X`, mas NUNCA gravava no Postgres** (o `print` do score fica FORA do `if store is
not None`, por isso "parecia" que scaneava). Ficaria assim até um restart manual.

**Não é bug do código KL-96** (que passou nos testes e no CI) — é uma condição de corrida de
DDL exposta pelo restart simultâneo do deploy, somada a um tratamento de erro frágil no worker.

---

## Correções

### #1 — resiliência de schema no deploy concorrente (2 camadas)
- **`store.ensure_schema` agora RETENTA em erro TRANSITÓRIO de DDL** (DeadlockDetected /
  LockNotAvailable / "concurrently updated" / "could not obtain lock") — 6 tentativas com
  backoff exponencial (`_is_transient_ddl`, puro/testável). Erro REAL (sintaxe/permissão/coluna
  inexistente) propaga na hora. Protege TODOS os containers (api/discovery/worker) e o próprio
  deploy desta correção.
- **`scanner/main.py`: o worker NÃO zera mais o `store`** se `ensure_schema` falhar. As tabelas
  já existem (criadas pela API/discovery no boot); segue COM persistência — cada `save_scan` tem
  `try/except` no loop e volta a gravar quando o DB estabiliza (antes: `store=None` = escaneava
  sem persistir até restart manual).

### #2 — nginx_parser (`api/nginx_log_parser.py`)
`for line in f` usa o protocolo de iterador (readahead) que **desabilita `f.tell()`** →
`OSError`. Trocado por **`readline()`** (compatível com `tell`). Bônus: uma linha SEM `\n`
final (Nginx escrevendo no meio) é **deixada para o próximo ciclo** (não avança o offset por
uma linha parcial).

### #3 — ct_poller (`discovery/ct_poller.py`)
`.json()` cru estourava `JSONDecodeError` a cada ciclo quando o Google CT devolvia corpo
vazio/429/HTML. Novo **`_get_json`** com retry/backoff (`CT_RETRY_ATTEMPTS=3`,
`CT_RETRY_BACKOFF=1.0`): 200-vazio / 429 / 5xx → retenta; 4xx (não-429) → desiste; JSON
inválido → engolido. Retorna **None** (sem exceção) quando o CT está instável → o poller pula
o log. **WARN throttled** no `_run` (1ª falha + a cada 10 ciclos sem sucesso, não a cada
ciclo). O discovery segue com o buffer/crt.sh.

### #4 — `.env`
Já limpo (`grep -c ALERT_DAILY_LIMIT /opt/klarim/.env` = 1). Nenhuma ação.

---

## Testes
`tests/test_prod_hotfix.py` (+10): `_is_transient_ddl` (só concorrência), retry do
`ensure_schema` (transitório→retenta / real→propaga), nginx_parser (readline + linha parcial +
incremental), `_get_json` (200/empty+429/bad-json/4xx-early) e `_poll_log` (False no instável /
ingesta no ok). **Suite: 1603 passed, 1 skipped.**

## Validação em produção (pós-deploy)
1. `redis-cli LLEN klarim:scan_queue` > 0 (chave certa).
2. `targets.last_scan_at` e a tabela `scans` voltando a AVANÇAR (persistência OK).
3. `[nginx_parser]` sem `OSError`; `access_log` recebendo linhas do Nginx.
4. `[ct-poll]` sem erro a cada ciclo (WARN throttled, se houver).
5. Workers 4/4 alive; health ok; score klarim.net = 100.

## Segurança
Sem novos endpoints/inputs. `_is_transient_ddl` e `_get_json` só classificam erros/respostas.
O retry de DDL só reexecuta o MESMO schema idempotente. O ct_poller já roda com timeout e
User-Agent honesto. Nenhuma credencial tocada.
