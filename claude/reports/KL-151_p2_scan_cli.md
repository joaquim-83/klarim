# KL-151 (Prompt 2/4) — API REST de scan + CLI publicável + MCP tools

## Contexto

O backend core (Prompt 1) já provê contas dev, API keys (SHA-256), planos, projetos (verificação
de domínio), runs, convites, trial Pro 14d e enforcement no servidor. Este prompt entrega a **API
REST de scan** (o dev roda no pipeline dele via API key), o **CLI** publicável e as **MCP tools**.

**Princípio fundamental:** o scan roda no **SERVIDOR** da Klarim. O client só envia URL + API key;
a API roda a engine (86 checks) e devolve o resultado. O client nunca executa checks.

## O que foi entregue

### API REST (`api/gate.py`)

- **`POST /gate/scan`** — autentica por API key → valida que o domínio da URL é um projeto
  **REGISTRADO** e **VERIFICADO** (exceto planos com `scan_third_party`, ex.: Enterprise) →
  `enforce_scan_limit` → roda `run_all` com **só os checks do plano** → persiste em `gate_runs` →
  devolve `{run_id, score, passed, duration_ms, critical/high/medium, results, checks_run,
  checks_blocked, plan, dashboard_url}`. O `passed` **respeita o `fail_on` do dev** (`_passed_for`
  usa o `SEVERITY_RANK`, não só o "crítico" que a engine olha por padrão). Os `checks_blocked` vêm
  no response (CTA de upgrade para o Free).
- **`GET /gate/runs`** (sumário, sem o `results` pesado; filtro `project_id`/`limit`) e
  **`GET /gate/runs/{id}`** (detalhe com `results`; ownership check → 404 se não é da conta).

**Segurança do scan:** só escaneia domínio que o dev **provou controlar** (verificado) — o domínio é
extraído da URL e casado com um projeto da conta, então não dá para usar a Klarim como proxy de scan
contra terceiros (userinfo `@` no URL cai no host real, que não bate projeto → 403).

### Rate limit por API key

`enforce_scan_limit` virou um **contador atômico no Redis** por dia-calendário UTC
(`gate_scans:{account}:{YYYY-MM-DD}`, `INCR` + `EXPIRE 86400`), consumindo 1 crédito por scan e
bloqueando (429) acima do teto do plano. **Fallback** para `count_gate_runs_today` (banco) se o
Redis cair — nunca desliga o limite por falha de infra. `-1` = ilimitado (Enterprise).

### CLI publicável (`scripts/klarim_gate_cli.py`)

Standalone (só depende de `httpx`), roda como `python scripts/klarim_gate_cli.py`. Subcomandos
`scan` (com `--fail-on`/`--json`/`--quiet`/`--timeout`/`--metadata`; key via `--api-key` ou env
`KLARIM_API_KEY`), `projects`, `runs`. Saída em **PT-BR**. **Exit codes:** 0 passou · 1 reprovou
(finding ≥ `--fail-on`) · 2 erro (API fora, key inválida, domínio não verificado, limite atingido —
com mensagem amigável para 401/403/429).

### MCP tools (`mcp_server/tools/gate.py`, visão admin)

`list_gate_projects` (todos/por conta), `get_gate_project`, `create_gate_project` (extrai o domínio
da URL), `list_gate_runs`, `get_gate_run`. Registradas no `tools/__init__.py` (**MCP → 80 tools**;
reconectar o MCP pós-deploy para aparecerem). Store: `list_gate_runs`/`get_gate_run` com `account_id`
opcional (None = admin), `admin_list_gate_projects`/`get_gate_project_by_id`.

### Nginx

O `location /api/` **já proxia** `/api/gate/*` com `proxy_read_timeout 180s` — mais generoso que os
120s pedidos e suficiente para um scan de <60s. **Nenhuma location nova** foi adicionada: uma
`location /api/gate/` mais específica exigiria re-declarar todos os security headers (o footgun
documentado no `claude.md`) para zero benefício. Decisão documentada.

## Testes

- **`tests/test_kl151_p2_scan_cli.py`** (+19): `POST /gate/scan` (ok com score/results; sem key→401;
  key inválida→401; domínio não registrado/não verificado→403; `scan_third_party` escaneia sem
  verificar; `fail_on` inverte o `passed`; 5 scans Free OK + 6º→429; `checks_run`/`checks_blocked`;
  run + metadata persistidos), `GET /gate/runs`+`/{id}` (sumário vs detalhe, 404 de outra conta),
  `enforce_scan_limit` ilimitado, **CLI** (passed→0, failed→1, `--json`, `--quiet`, sem key→2, API
  fora→2, projects/runs), **MCP** (5 tools registradas). A engine (`run_all`) é mockada para não
  fazer rede.
- **Store P2 validada contra Postgres 16 real** (container throwaway): `create_gate_run` (JSONB
  results/metadata), `list_gate_runs` (sumário), `get_gate_run` (full + ownership), `count_gate_runs_
  today`, `admin_list_gate_projects`, `get_gate_project_by_id`.
- **Suíte completa: 2151 passed, 1 skipped.**

## Docs

- `CLAUDE.md` §9 (continuação do card, Prompt 2/4), `docs/API.md` (endpoints scan/runs + gate.py nas
  tools MCP, contagem 80).

## Uso (exemplos)

```bash
# CLI
KLARIM_API_KEY=KLM_xxxx python scripts/klarim_gate_cli.py scan https://meuapp.com.br --fail-on critical

# curl
curl -s https://klarim.net/api/gate/scan -H "X-API-Key: KLM_xxxx" \
  -H "Content-Type: application/json" -d '{"url":"https://meuapp.com.br","fail_on":"critical"}' | jq .

# GitHub Actions (exit 1 reprova o job)
- run: pip install httpx && python scripts/klarim_gate_cli.py scan $SITE_URL \
    --api-key ${{ secrets.KLARIM_API_KEY }} --fail-on critical \
    --metadata '{"commit":"${{ github.sha }}","ci":"github-actions"}'
```

## Próximos prompts

3/4 = frontend (landing do produto, portal do dev, dashboard de runs). 4/4 = admin de planos +
publicação do CLI no PyPI.
