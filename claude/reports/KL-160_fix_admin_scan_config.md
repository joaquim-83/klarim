# Fix KL-160 — Admin security scan usa a config do `security-gate.yml`

## Problema

O botão "Executar varredura" do painel admin (`POST /admin/security-scan`) dava **90/100** enquanto
o CLI (`scripts/security_gate.py`) dava **100/100**. Causa: o admin criava um `GateConfig` com
**defaults** — `rate_limit_endpoints = ["/", "/api/"]` — em vez de ler o `security-gate.yml`, que
inclui `/api/scan/` (zona restrita 2r/s que dispara o 429 na rajada concorrente). Sem o
`/api/scan/`, o check de rate limit não tripava e o `rate_limit` ficava FAIL (HIGH) → 90.

## Fix

Em `api/gate.py::_run_platform_security_scan`, passei a carregar a MESMA config do CLI via
`load_config` (que já existia em `security_gate/config.py` e é o que o CLI usa):

```python
yml = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "security-gate.yml")
config = load_config(yml)          # lê o security-gate.yml (rate_limit_endpoints, allowlist, checks…)
config.target = url; config.fail_on = "critical"; config.timeout = 60
checks = config.checks or list(_ENGINE_ORDER)   # fail-safe: YAML sem checks → roda todos
report = await run_all(url=url, timeout=config.timeout, checks=checks, config=config)
```

- **Caminho absoluto** (raiz do repo, relativo a `api/gate.py` → `/app/security-gate.yml` no
  container) — não depende do CWD.
- **Fail-safe**: se o YAML não trouxer `checks`, roda todos (`_ENGINE_ORDER`).
- Admin e CLI passam a rodar com a config IDÊNTICA (mesmos endpoints de rate limit, mesma allowlist
  de exposição, mesmos checks).

`load_config` já existia (o card previa extraí-la se não existisse — não foi preciso).

## Validação

- **`pytest`: 2311 passed, 1 skipped** (`test_kl160_security_scan.py` 6/6 — `run_all` mockado, não
  afetado; `load_config` lê o YAML real do repo).
- **Dev (`docker-compose.dev.yml`)**: botão do admin → **score=100, passed=True, critical=0, high=0**
  — igual ao CLI (que dá 100/100 com `rate_limit ativo em /api/scan/ (4/10 → 429)`).

## Arquivos

`api/gate.py` (import `load_config` + `_run_platform_security_scan`), `claude.md`.

## Deploy

Direto (fix pequeno). `ensure_schema`/Redis inalterados.
