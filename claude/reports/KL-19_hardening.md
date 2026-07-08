# KL-19 — Blindar Discovery Worker: timeout por domínio + watchdog + filtro de e-mail

- **Card Jira:** KL-19
- **Data:** 2026-07-08
- **Executor:** Claude CLI (Opus 4.8)
- **Depende de:** KL-15 (CT poller), KL-11 (Discovery Worker)
- **Incidente que originou:** 08/07 03:20 UTC — o Discovery Worker travou por 7,5h.
  Um domínio bloqueou o event loop (compartilhado por discovery/alert/rescan via
  `asyncio.gather`), parando os três. Sem crash (sem OOM), só congelamento.
- **Commit:** `fix(KL-19): add per-domain timeout, watchdog, and email quality filters`

---

## Parte 1 — Timeout total por domínio

`run_cycle` foi refatorado: a lógica por domínio virou **`_process_domain(domain,
stats)`** e cada chamada roda sob **`asyncio.wait_for(..., timeout=
DISCOVERY_DOMAIN_TIMEOUT)`** (padrão **30s**). Se um site trava (DNS lento, servidor
que aceita mas não responde, redirect infinito, ou o `extract_email` somando vários
GETs sequenciais sob o rate limiter), o domínio é **pulado** (`stats["timeouts"]`)
e o loop **continua**. Timing por domínio: log de aviso se `> 10s` ("lento").
Resumo do ciclo agora inclui timeouts + erros:

```
[discovery] ciclo completo: 50 processados, 30 com email, 12 sem contato, 3 timeouts, 2 erros, ...
```

Os GETs internos já têm timeout de 10s (`scanner.checks.base.fetch`), mas com
`follow_redirects` o timeout é **por hop** (até ~20×) e o rate limiter serializa —
o `wait_for` por domínio é a rede de segurança que cobre todos esses casos.

## Parte 2 — Watchdog de auto-recuperação

**Nuance importante:** `HEALTHCHECK` + `restart: unless-stopped` **não** reinicia um
container "Up mas travado" no Docker Compose puro — `restart` só dispara no **exit**
do processo, e o healthcheck só muda o *status*. Então:

- **Watchdog em thread** (a recuperação de verdade): uma thread daemon marca
  `_last_progress` (atualizado pelo heartbeat de 20s e por domínio). Se não há
  progresso por `DISCOVERY_WATCHDOG_SECONDS` (600s), faz **`os._exit(1)`** → o
  processo sai → o `restart: unless-stopped` sobe um novo container → warm-up →
  workers voltam. Roda numa **thread** justamente para funcionar quando o event
  loop asyncio está 100% preso.
- **`HEALTHCHECK`** no `docker-compose` (`discovery/healthcheck.py`): checa se algum
  heartbeat (`discovery:status`/`worker:alert:status`/`worker:rescan:status`) existe
  no Redis; senão exit 1. Marca o container **unhealthy** (visível em `docker ps` e
  útil se um dia adicionarmos autoheal/Swarm). Redis inacessível ⇒ exit 0 (não
  reinicia por causa disso).

## Parte 3 — Filtro de e-mails inválidos (`contact.py`)

`_is_valid_email(email)` roda no `_best_email`, rejeitando antes de retornar:

- **nomes de arquivo** — domínio ou local terminando em `.css/.js/.png/.jpg/.svg/
  .woff/.pdf/…` (ex.: `_@astro.dwg1vcjs.css`);
- **placeholders de template** — `seuemail@`, `youremail@`, `email@email`,
  `exemplo@`, `contato@seusite`… e domínios de exemplo (`email.com.br`,
  `example.com`, `suaempresa.com.br`…);
- **local part < 2 chars** (ex.: `_@…`);
- formato inválido (regex `^[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}$`).

Sem e-mail válido ⇒ o alvo vira `sem_contato` (não recebe alerta). Protege a cota do
throttle (estava em 40/50 no dia do incidente) e a reputação do `klarim.net` no
Resend (bounces).

## Variáveis (`.env.example`)

`DISCOVERY_DOMAIN_TIMEOUT=30`, `DISCOVERY_WATCHDOG_SECONDS=600`.

## Validação

- **Testes** (`tests/test_discovery.py`, +4): `_is_valid_email` rejeita o lixo real
  do incidente (`_@astro.dwg1vcjs.css`, `seuemail@email.com.br`, `logo@site.png`,
  `x@example.com.br`) e aceita e-mails reais; `_best_email`/`extract_email` filtram
  o lixo e retornam o válido; **`run_cycle` pula um domínio que trava** (mock que
  dorme > timeout → `timeouts=1`, o domínio seguinte processa, loop continua).
  **Suíte total: 102 passed, 1 skipped.** `discovery.worker`/`healthcheck` importam
  limpo.
- **Produção (VM):** _pós-deploy — ver abaixo (o deploy também destrava o discovery
  parado desde 03:20)._

## Validação em produção (pós-deploy)

- [ ] Deploy recria o container discovery → workers voltam a 🟢 (destrava o
      incidente); `/system/status` mostra discovery/alert/rescan `alive:true`.
- [ ] Log de início mostra `timeout/domínio=30s, watchdog=600s`.
- [ ] `docker inspect` do discovery mostra `Health: healthy` após o start_period.
- [ ] Um ciclo completa com o novo resumo (com `timeouts`).

## Critérios de aceite

- [x] Processamento por domínio sob `asyncio.wait_for(30s)`.
- [x] Domínio que trava é pulado (log + `timeouts`), loop continua.
- [x] `HEALTHCHECK` no docker-compose (heartbeat Redis) + watchdog que reinicia de
      fato (o healthcheck sozinho não reinicia no Compose puro — documentado).
- [x] `_is_valid_email` rejeita extensões de arquivo e placeholders.
- [x] Log de tempo por domínio (aviso se > 10s).
- [x] Stats de timeout no resumo do ciclo.
- [x] `DISCOVERY_DOMAIN_TIMEOUT` (+ `DISCOVERY_WATCHDOG_SECONDS`) no `.env.example`.
- [x] Testes do filtro de e-mail (+ do timeout).
- [x] Documentação (`claude.md` §15, `README.md`).
- [x] Relatório em PT-BR.
- [ ] Deploy + validação + commit/push.

## Follow-ups

- Os alertas já enviados para placeholders/lixo (ex.: `seuemail@email.com.br` 3×)
  não têm como ser "des-enviados"; o filtro previne daqui pra frente.
- Se quisermos auto-restart via healthcheck sem depender do watchdog interno, dá
  para adicionar um container `autoheal` — mas o watchdog em thread já resolve.
