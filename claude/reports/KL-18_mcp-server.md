# KL-18 — Servidor MCP: cobertura de leitura e escrita para operar o Klarim via Claude

- **Card Jira:** KL-18
- **Data:** 2026-07-09
- **Executor:** Claude CLI (Opus 4.8)
- **Depende de:** toda a API existente (KL-1 a KL-24)
- **SDK:** `mcp` 1.27 (`FastMCP`), transporte **SSE** montado no FastAPI em `/mcp/sse`
- **Commit:** `feat(KL-18): add MCP server with full read/write tools for operating Klarim via Claude`

---

## Objetivo

Operar o Klarim por **linguagem natural** no Claude — reaproveitar os ~1.900 alvos
`sem_contato` (pesquisar e-mail → adicionar ao pipeline), monitorar o sistema,
disparar scans e alertas. O MCP server é um **wrapper fino** sobre a API: cada tool
chama uma função de endpoint ou um método do `store` já existente (zero lógica de
negócio duplicada).

## O que mudou

### `mcp_server/server.py` — FastMCP + 25 tools

- **17 tools de leitura:** `get_system_status`, `get_email_health`,
  `get_discovery_status`, `get_config`, `list_targets`, `get_target`,
  `get_target_stats`, `search_targets`, `list_scans`, `get_scan`, `get_scan_stats`,
  `list_alerts`, `get_alert_stats`, `list_payments`, `get_payment_stats`,
  `get_funnel`, `get_rescan_stats`.
- **8 tools de escrita:** `scan_url`, `add_target`, `update_target_email`,
  `update_target_status`, `update_target_sector`, `send_alert_to_target`,
  `send_report_to_email`, `classify_targets_batch`.
- **Reuso sem duplicação:** as tools chamam as funções de endpoint do `api.main`
  (ex.: `api_system_status`, `api_admin_scan_and_report`, `api_target_alert`) ou
  métodos do `store` (`list_targets`, `stats`, `alert_stats`…). As funções de
  `api.main` são importadas **lazily** dentro das tools para quebrar o ciclo de
  import (`api.main` importa `mount_mcp`).
- **`_guard()`** envolve toda execução e converte exceções (incl. `HTTPException`)
  num dict `{"error", "status_code"}` — a tool nunca derruba a sessão MCP.

### Montagem SSE no FastAPI (`mount_mcp`)

- Transporte SSE montado no MESMO app em **`/mcp/sse`** (+ `/mcp/messages/`),
  seguindo o padrão canônico do FastMCP (`connect_sse` + `_mcp_server.run`).
- **Detalhe crítico:** o `SseServerTransport` recebe o caminho **relativo**
  `/messages/`. Como o sub-app é montado em `/mcp`, esse vira o `root_path`, que o
  transporte **prefixa** ao anunciar o endpoint ao cliente → `/mcp/messages/`.
  Passar `/mcp/messages/` duplicava o prefixo (`/mcp/mcp/messages/`) — bug pego no
  smoke test e corrigido.
- Import em `try/except` no fim do `api.main`: se o pacote `mcp` faltar, a API sobe
  sem o MCP (degradação graciosa).

### Autenticação

- `MCP_API_KEY` no header `Authorization: Bearer <chave>`, validada em **tempo
  constante** (`hmac.compare_digest`) na conexão SSE **e** nos POSTs `/messages/`.
- **Sem `MCP_API_KEY` ⇒ MCP desligado** (todas as conexões recebem 401).
- `/mcp/*` **não** entra nos prefixos protegidos por JWT do `_admin_auth_mw` (tem
  auth própria).

### Nginx (proxy SSE)

- `location /mcp/` adicionado ao `http.conf` e aos **dois** server blocks 443 do
  `https.conf.template` (klarim.net + painel.klarim.net), com
  **`proxy_buffering off` + `proxy_cache off` + `Connection '' ` +
  `proxy_http_version 1.1`** — sem isso o Nginx bufferiza os eventos e o SSE não flui.

### Dependências (conflito resolvido)

Instalar `mcp` puxa `sse-starlette`, cujas versões novas exigem **Starlette 1.x**,
que **quebra o FastAPI 0.115** (`Router.__init__() got an unexpected keyword
'on_startup'`). Pinado no `requirements.txt` o conjunto compatível:
`mcp>=1.27,<2`, `starlette>=0.40,<0.42`, `sse-starlette>=1.6.1,<2.2`,
`fastapi>=0.115,<0.116`.

## Validação

- **Smoke test SSE (uvicorn local):**
  - `GET /mcp/sse` sem chave / com chave errada → **401**.
  - `GET /mcp/sse` com a chave → `event: endpoint` + `data: /mcp/messages/?session_id=…`
    (prefixo único, correto).
  - `POST /mcp/messages/` sem chave → **401**.
- **Testes** (`tests/test_mcp_server.py`): 25 tools registradas (17+8) com
  descrições, `_key_ok` (bom/ruim/sem-chave), 401 no endpoint SSE, `_guard`
  convertendo exceções, e execução de tools com store falso (`list_targets`,
  `search_targets`, `get_target`, `update_target_status` válido/inválido→422).
- **Suite completa: 201 passed, 1 skipped** (o skip é o scan online opt-in). O bump
  FastAPI 0.115.14 / Starlette 0.41.3 não quebrou nada.

## Ação manual na VM (após deploy)

1. Gerar e gravar a chave no `.env` da VM: `MCP_API_KEY=$(openssl rand -hex 32)`.
2. Redeploy (`sudo bash /opt/klarim/deploy/deploy.sh`).
3. Testar: `curl -N -H "Authorization: Bearer <chave>" https://klarim.net/mcp/sse`
   deve retornar `event: endpoint`.
4. Conectar no Claude Desktop (`claude_desktop_config.json`) com a URL
   `https://klarim.net/mcp/sse` + header `Authorization: Bearer <chave>`.

## Notas de design

- **Endpoint `/mcp/sse` (sem barra final):** aceitamos `/sse` e `/sse/` (duas rotas)
  para robustez com clientes que adicionam ou não a barra.
- **Auth nos dois lados (SSE + messages):** defense-in-depth — o POST de mensagens
  também exige a chave, não só a abertura do stream.
- **Tools de escrita reusam a validação existente:** ex.: `update_target_status`
  passa pelo `_VALID_STATUSES` do endpoint (status inválido → 422 → `_guard` →
  `{"error", "status_code": 422}`), sem reimplementar regra.
