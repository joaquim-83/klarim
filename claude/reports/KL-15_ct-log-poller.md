# KL-15 — Fonte alternativa de CT logs para o Discovery Worker (poller direto)

- **Card Jira:** KL-15
- **Data:** 2026-07-07
- **Executor:** Claude CLI (Opus 4.8)
- **Depende de:** KL-11 (Discovery Worker / crt.sh), KL-14 (auth JWT do painel)
- **Commit:** `feat(KL-15): replace crt.sh with Certstream for real-time CT log discovery`

---

## Resumo executivo (mudança de rota vs. o card)

O card pedia **Certstream** (`wss://certstream.calidog.io/`) como fonte. Ao validar
a conectividade **antes de implementar**, constatei que o servidor público da
calidog **está morto**: aceita a conexão WebSocket mas **não envia nenhum frame**
(0 certs, nem heartbeats, em 20s) — confirmado tanto localmente quanto **da própria
VM de produção**. Levei a decisão ao operador, que escolheu a alternativa
recomendada: **ler os CT logs públicos direto**, sem agregador de terceiros.

Resultado: fonte de CT **funcionando de verdade** (o Certstream e o crt.sh
falhavam), sem infra nova e usando `cryptography` (já é dependência).

## Parte 1 — Poller de CT logs (`discovery/ct_poller.py`)

`CTLogPoller` — mesma interface que o worker consome (`start_listener` /
`flush_buffer` / `get_stats`), mas backed por leitura direta de CT logs (RFC 6962):

- **Descoberta de logs:** baixa a lista oficial do Google (`CT_LOG_LIST_URL`) e
  filtra os logs **`usable`** cujo intervalo temporal cobre agora — **auto-adapta à
  rotação de shards por ano** (hoje: argon/xenon 2026h2, nimbus2026, digicert…).
- **Amostragem:** numa thread daemon, a cada `CT_POLL_INTERVAL_SECONDS` (20) chama
  `get-sth` (tamanho da árvore) e `get-entries` para o **topo** de cada log
  (`CT_POLL_BATCH`=256 entradas). O buffer (set) deduplica a sobreposição entre
  polls — sempre amostra os certs mais recentes.
- **Parsing:** decodifica o `MerkleTreeLeaf` (version/leaf_type/timestamp/entry_type),
  pega o cert DER (x509_entry no próprio leaf; precert no início do `extra_data`),
  carrega com `cryptography` e extrai os domínios do **SAN + CN**.
- **Filtro:** `normalize_domain` (compartilhado com o crt.sh) — wildcards, infra
  (`mail./api./cdn.`…), não-`.com.br`, reduz ao domínio registrável.
- **Buffer cap** (`CT_MAX_BUFFER`=5000) evita crescimento sem limite.

## Parte 2 — Discovery Worker contínuo (`discovery/worker.py`)

Modelo batch (6h) → **contínuo**: `start()` sobe o poller (thread) + um heartbeat
de status; `run_cycle()` a cada `DISCOVERY_INTERVAL_MINUTES` (30) drena o buffer e
processa cada domínio novo (dedup vs DB → fetch → fingerprint → e-mail → setor →
registra → enfileira). **Fallback:** buffer vazio ⇒ tenta o crt.sh; nada ⇒ espera
o próximo ciclo. `DISCOVERY_INTERVAL_HOURS` (KL-11) → `DISCOVERY_INTERVAL_MINUTES`.

## Parte 3 — Logging

`[ct-poll] conectado — amostrando N CT logs: …`; a cada 1000 certs o total no
buffer; erros por log (não derrubam o poller). No worker:
`[discovery] buffer: N domínios .com.br → processando…` e
`[discovery] ciclo completo: X processados, Y com email, Z sem contato, W já registrados`.

## Parte 4 — API de status

`GET /api/discovery/status` (JWT, prefixo `/discovery` protegido). O worker e a API
rodam em **containers diferentes** — a ponte é o **Redis**: o worker publica o
status (`discovery:status`, heartbeat a cada 20s) e a API o lê, somando
`targets_discovered_today` (do banco). Payload: `source{connected, last_event_at,
total_seen, total_matched, buffer_size}`, `cycles_completed`, `last_cycle_at`,
`next_cycle_at`, `last_cycle_stats`, `targets_discovered_today`.

## Parte 5 — Dependências

`cryptography` (já era dependência do `check_ssl`) faz o parsing dos certs — **sem
lib nova**. A `certstream` foi removida do `requirements.txt` (servidor morto).

## Validação

- **Testes** (`tests/test_ct_poller.py`, 7 casos): `normalize_domain`; **parsing
  real** (gera um cert com SANs via `cryptography`, monta um `MerkleTreeLeaf`
  x509_entry e confirma a extração dos domínios); pipeline de ingestão + filtro
  (`mail.`/`.com` fora), buffer cap, shape do `get_stats`. `tests/test_auth.py`
  cobre `/discovery/status` (JWT). **Suíte total: 89 passed, 1 skipped.**
- **Certstream morto — comprovado:** raw WebSocket a `certstream.calidog.io` →
  **0 frames em 20s**, local e na VM.
- **Poller ao vivo (local):** contra 4 CT logs reais, em 40s: **855 entradas
  processadas, 6 `.com.br`**, buffer com domínios **reais** (`agoraeuvivo.com.br`,
  `vallesoftware.com.br`). Prova o parsing + extração ponta-a-ponta.
- **Produção (VM):** _pós-deploy + teste de 1h — ver seção abaixo._

## Validação em produção (pós-deploy)

- [ ] `[ct-poll] conectado` nos logs do container `discovery`.
- [ ] `GET /api/discovery/status` (com JWT) → `connected:true`, `total_seen` e
      `total_matched` subindo, `buffer_size > 0`.
- [ ] Após um ciclo (30min ou disparo), logs mostram domínios processados e
      `GET /api/targets/stats` cresce (novos `discovered`/`sem_contato`/`scanned`).
- [ ] **Teste de 1h:** reportar total_seen, total_matched, alvos registrados (com
      e-mail) e scans executados.

## Critérios de aceite

- [x] Fonte de CT alternativa implementada (poller direto — Certstream calidog está
      morto; decisão do operador registrada).
- [x] Discovery Worker usa o poller como fonte primária.
- [x] crt.sh mantido como fallback.
- [x] Buffer drenado a cada 30 min (configurável).
- [x] Filtro de `.com.br`, wildcards, infra, dedup (compartilhado).
- [x] Reconexão/robustez: thread com retry por log; heartbeat de status.
- [x] Logging detalhado.
- [x] `GET /api/discovery/status` (JWT, via Redis).
- [ ] Worker roda 1h na VM e descobre alvos reais (pós-deploy).
- [x] Documentação (`claude.md` §15, `README.md`).
- [x] Relatório em PT-BR.

## Follow-ups

- **Yield de `.com.br`:** amostrar o topo é simples e sempre fresco, mas em logs
  muito rápidos pode "pular" entradas entre polls. Se precisar de mais volume:
  aumentar `CT_MAX_LOGS`/`CT_POLL_BATCH` ou crawlar posição por log (com risco de
  ficar pra trás). Hoje o funil não precisa de todos os certos — só de fluxo.
- **KL-16** (dashboard operacional) consumirá `/api/discovery/status`.
- Dívida do KL-3 (stores por `POSTGRES_*`) segue de pé.
