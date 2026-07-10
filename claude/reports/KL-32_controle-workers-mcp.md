# KL-32 — Controle completo dos workers via MCP

**Card:** KL-32 · **Prioridade:** URGENTE.
**Contexto:** 673 alertas/dia pelo domínio principal, bounce **3,85%** (70/1817) —
acima do limite seguro (2%), danificando a reputação. Precisávamos pausar os workers
proativos **já** e ter controle granular por worker.

---

## Ação imediata (antes do deploy)

Antes de qualquer código, usei o kill-switch **já existente** (KL-27) para estancar o
sangramento: `touch /opt/klarim/STOP_ALERTS` → `alerts_stopped()=True` no container →
o alert **e** o rescan pararam de enviar e-mail no ciclo seguinte. Zero espera.

## Estado centralizado — `worker_control.json`

`discovery/worker_control.py`: um único JSON (`WORKER_CONTROL_FILE`, padrão
`/klarim-control/worker_control.json` = host `/opt/klarim/worker_control.json`) com o
estado de cada worker:

```json
{"alert": {"enabled": false, "paused_at": "…", "paused_by": "mcp", "max_per_hour": null, "batch_size": null}, …}
```

- **Fail-open:** arquivo ausente/corrompido/incompleto ⇒ `enabled: true` (nunca trava).
- **Escrita atômica** (tmp + `os.replace`) — o leitor nunca vê JSON pela metade.
- API: `load`, `is_enabled`, `worker_config`, `save`, `pause`/`resume` (incl. `"all"`),
  `set_config`.

## Mounts (docker-compose)

- **api:** `./:/klarim-control` (**rw** — o MCP/painel grava) + `WORKER_CONTROL_FILE`.
- **discovery:** `./:/klarim-control:ro` (já existia do KL-27) + var.
- **worker (scan):** `./:/klarim-control:ro` (adicionado) + var.

O arquivo vive no host → **persiste entre restarts**. `worker_control.json` e
`STOP_ALERTS` no `.gitignore`.

## Integração nos 4 workers

Cada worker lê o estado **no início de cada ciclo** (não só no startup):

- **discovery** (`worker.py`): `run_cycle` pula se desabilitado; overrides
  `cycle_minutes` (intervalo do loop) e `max_targets_per_cycle` (tamanho do ciclo).
- **alert** (`alert_worker.py`): `run_cycle` pula se desabilitado — **aditivo** ao
  `STOP_ALERTS` (respeita ambos); overrides `max_per_hour` (teto por ciclo =
  `max_per_hour × intervalo/60`) e `batch_size`.
- **rescan** (`rescan_worker.py`): `run_cycle` **e** `_monitor_cycle` pulam se
  desabilitado.
- **scan** (`scanner/main.py --worker`): quando desabilitado **não consome a fila**
  (itens ficam enfileirados) mas mantém o heartbeat; override `max_per_hour` (rate limit).

## MCP tools (6)

`mcp_server/tools/workers.py`: **`pause_worker(worker)`**, **`resume_worker(worker)`**
(`discovery|alert|rescan|scan|all`), **`get_worker_control()`** (estado de controle +
alive/dead do heartbeat Redis por worker), **`set_alert_throttle(max_per_hour,
batch_size?)`**, **`set_discovery_config(cycle_minutes?, max_targets_per_cycle?)`**,
**`set_scan_config(max_per_hour?)`**.

## `get_system_status` + REST admin

`get_system_status` (MCP + `GET /api/system/status`) agora inclui
`enabled`/`paused_at`/`paused_by` em cada worker. Endpoints REST (JWT, para o painel):
`POST /admin/workers/pause`, `POST /admin/workers/resume`, `GET /admin/workers/control`.

## Retrocompatibilidade

O `STOP_ALERTS` (KL-27) continua valendo: o alert só envia se **`STOP_ALERTS` ausente
E `alert.enabled=true`**. A camada nova é **aditiva**, não substitui.

## Testes

`tests/test_worker_control.py` (10): default fail-open, pause/resume, all, persistência,
worker inválido, arquivo corrompido/chave ausente (fail-open), set_config, endpoints
admin (JWT + pause/resume/422). `tests/test_alert_worker.py` (+2): `run_cycle` pulado
quando desabilitado, throttle `max_per_hour` capando o ciclo. Suíte completa verde.

## Ação pós-deploy

Via MCP (ou REST/arquivo): **`pause_worker("alert")`** + **`pause_worker("rescan")`**;
discovery e scan seguem **ativos** (continua descobrindo/escaneando, sem enviar e-mail).

## Arquivos

**Novos:** `discovery/worker_control.py`, `mcp_server/tools/workers.py`,
`tests/test_worker_control.py`, este relatório.
**Alterados:** `docker-compose.yml` (mounts+var), `discovery/worker.py`,
`discovery/alert_worker.py`, `discovery/rescan_worker.py`, `scanner/main.py`,
`api/main.py` (system_status + REST + import), `mcp_server/tools/__init__.py`,
`.gitignore`, `tests/test_alert_worker.py`, `claude.md`, `README.md`.
