# KL-141 Prompt 3/4 — CLI + config YAML + check de API security + formatters + allowlist

**Data:** 2026-08-03

A interface do Security Gate: CLI executável, config YAML, check de API security, formatação do
output (terminal + JSON) e a resolução dos falsos positivos de SPA da exposição.

## Entregue
- **CLI (`scripts/security_gate.py`):** `python scripts/security_gate.py <url> [--fail-on
  critical|high|medium|low] [--timeout N] [--checks a,b] [--config f.yml] [--json] [--quiet]`.
  **Exit codes:** 0 passou · 1 falhou (FAIL com severidade ≥ `--fail-on`, por **rank**, não string
  — o snippet do card comparava `.value` lexicograficamente, bug corrigido) · 2 erro (offline/config
  inválida/`report.error`). Adiciona a raiz do repo ao `sys.path` (roda de qualquer CWD).
- **Config (`security_gate/config.py` + `security-gate.yml`):** `GateConfig` (dataclass) + `load_config`
  (YAML → sobrescrito pelos args da CLI; YAML ausente → defaults). **`import yaml` lazy** — o núcleo do
  Gate continua importável sem `pyyaml` (só o loader precisa). `pyyaml>=6.0,<7.0` adicionado ao
  `requirements.txt`. `checks.<nome>.enabled: false` desabilita um check. `security-gate.yml` commitado
  com a config da Klarim (allowlist, endpoints protegidos, thresholds).
- **API security (`security_gate/checks/api_security.py`):** (1) a raiz da API (`/api/`) não pode
  listar endpoints (marcadores `endpoints`/`routes`/`/webhooks`/`scanner_version` → FAIL HIGH; senão
  PASS — valida o hardening do KL-138) e (2) cada `protected_endpoint` deve responder 401/403, nunca
  200 sem auth (200 → **FAIL CRITICAL**). Não é DAST: só GET sem credencial, observando o status.
- **Formatters (`security_gate/formatters/terminal.py`):** `format_terminal` (por categoria, ícones
  ✅/❌/⚠️, score+semáforo, veredito; `--quiet` omite PASS) e `format_json` (parseable, `ensure_ascii=
  False`). Exportados em `formatters/__init__.py`.
- **Engine:** `run_all(url, timeout, checks, config, deploy_ts)` — `config` (GateConfig) é criado por
  default se ausente e passado a TODOS os checks (assinatura uniforme `check(client, url, config=None)`).
  `api` entrou no `_CHECKS`/default order. Backward-compat: chamadas antigas `check(client, url)`
  seguem funcionando (config=None).

## Falsos positivos de SPA — resolvidos de fato (não só o allowlist do card)
O card propunha um **allowlist** para os 4 FPs de SPA do Prompt 1. O dogfooding revelou que o
allowlist sozinho é **whack-a-mole**: o SPA da Klarim devolve 200 p/ TODO path fora do blocklist do
nginx (KL-138), então allowlistar 4 paths só revela os próximos (`/cpanel`, `/openapi.json`,
`/elmah.axd`, `/app.js.map`) — e o grupo `source_maps` tem **5** paths SPA-200 (allowlistar todos
neutralizaria o check). Solução em 2 camadas:
1. **Content-Type guard (novo, HEAD-only) no `check_exposure`:** um path cujo recurso REAL nunca é
   HTML (`.map`/`.sql`/`.json`/`.yml`/`.env`/`.config`/`.bak`/`.old`) mas responde `text/html` =
   fallback de SPA/app → não é exposição. **Zero risco de falso NEGATIVO** (esses tipos nunca são HTML
   quando reais; um source map/`.env` real vaza como json/text → ainda vira FAIL). `.php`/`.axd` ficam
   DE FORA (phpinfo/elmah reais SÃO HTML — suprimi-los seria falso negativo). Só lê o Content-Type do
   HEAD (sem body — fiel ao HEAD-first). Resolve `source_maps` inteiro + `/openapi.json` + configs.
2. **Allowlist (config, por-alvo):** para os paths HTML-capazes (painéis/UI/debug: `/docs`, `/adminer`,
   `/cpanel`, `/_profiler`, `/elmah.axd`, `/trace.axd`) onde o Content-Type não desambigua.

**Resultado do dogfooding (`python scripts/security_gate.py https://klarim.net`): score 100/100 🟢,
0 findings, ~16s.** Headers 7/7 ✅, SSL ✅ (69d, TLS1.3), API security ✅ (raiz limpa; /api/admin/,
/api/system/, /mcp/ → 401; /api/account/ → 404), credenciais ✅, exposição ✅.

## Testes — `tests/test_kl141_cli_config.py` (35) + engine/credentials atualizados
CLI (exit 0/1/2, `--fail-on` por rank, `--checks`, `--json`, `--quiet`); config (allowlist, disable-
check, protected_endpoints, YAML-ausente→defaults, CLI-override, ssl_min_days/hsts_min_age); API
security (root leaks HIGH, root clean PASS, endpoint 401 PASS, endpoint 200 FAIL CRITICAL); allowlist
(path allowlistado 200 → PASS; não-allowlistado → FAIL); Content-Type guard (.map text/html → PASS;
.map json → FAIL; .env text/html → PASS; .env text/plain → FAIL); formatters (ícones/score/veredito,
quiet omite PASS, JSON com todos os campos). Os testes de engine do P1/P2 atualizados p/ a assinatura
`(client, url, config)` e 5 checks. **2054 pytest passed.**

## Notas
- **KL-139 (catálogo de checks):** todos os checks do catálogo estão implementados (exposição 1-3,5-12
  + credenciais 4 + headers/ssl/api). O card sugere fechar KL-139 junto com KL-141 — **deferido para o
  Prompt 4** (KL-141 ainda tem o Prompt 4 = GitHub Actions; fecho os dois juntos ao concluir).
- **Não entregue (Prompt 4):** integração GitHub Actions, notificação por e-mail/webhook (os campos já
  estão no `GateConfig`/`security-gate.yml`, prontos).
