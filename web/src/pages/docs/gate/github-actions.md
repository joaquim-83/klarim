---
layout: ../../../layouts/DocsLayout.astro
title: GitHub Actions
description: Guia completo para integrar o Klarim Security Gate no GitHub Actions. 86 checks de segurança no seu pipeline, reprovando o deploy quando algo grave fica exposto.
slug: github-actions
---

# Integração com GitHub Actions

O Security Gate roda **86 checks de segurança** contra a URL do seu site logo após o deploy e
reprova o job se algo grave ficou exposto. Nenhum payload de ataque é enviado — é um scan 100% passivo.

## Pré-requisitos

- Conta na Klarim com o **Security Gate ativo**
- **API key** gerada (dashboard → Security Gate → API Key)
- **Domínio verificado** (dashboard → Security Gate → seu projeto → Verificar)

## 1. Adicione o secret no repositório

Em **Settings → Secrets and variables → Actions → New repository secret**:

- **Nome:** `KLARIM_KEY`
- **Valor:** sua API key (começa com `KLM_`)

Nunca cole a key direto no YAML — ela deve vir do secret.

## 2. Adicione o step ao workflow

### Opção A — Após o deploy (recomendado)

```yaml
name: Deploy & Security Gate

on:
  push:
    branches: [main]

jobs:
  deploy:
    # ... seu deploy aqui ...
    runs-on: ubuntu-latest
    steps:
      - run: echo "deploy"

  security-gate:
    needs: deploy
    runs-on: ubuntu-latest
    steps:
      - name: Klarim Security Gate
        run: |
          pip install httpx
          python -c "
          import httpx, sys, json
          r = httpx.post('https://klarim.net/api/gate/scan',
              headers={'X-API-Key': '${{ secrets.KLARIM_KEY }}'},
              json={'url': 'https://meusite.com.br', 'fail_on': 'critical'},
              timeout=120)
          data = r.json()
          print(json.dumps(data, indent=2))
          sys.exit(0 if data['passed'] else 1)"
```

### Opção B — Agendado (sem deploy)

```yaml
name: Security Gate

on:
  schedule:
    - cron: '0 8 * * 1'  # toda segunda, 8h UTC

jobs:
  gate:
    runs-on: ubuntu-latest
    steps:
      - name: Klarim Security Gate
        run: |
          pip install httpx
          python -c "
          import httpx, sys, json
          r = httpx.post('https://klarim.net/api/gate/scan',
              headers={'X-API-Key': '${{ secrets.KLARIM_KEY }}'},
              json={'url': 'https://meusite.com.br', 'fail_on': 'high'},
              timeout=120)
          data = r.json()
          print(json.dumps(data, indent=2))
          sys.exit(0 if data['passed'] else 1)"
```

## 3. Configuração avançada

### Nível de reprovação (`fail_on`)

- `critical` (default): reprova só findings **críticos**
- `high`: reprova **críticos + altos**
- `medium`: reprova **críticos + altos + médios**

### Timeout

Aumente para sites lentos: `timeout=180` (default: 120s).

### Metadata (rastreabilidade)

Inclua informação do CI para achar o run depois no dashboard:

```json
"metadata": {
    "commit": "${{ github.sha }}",
    "branch": "${{ github.ref_name }}",
    "ci": "github-actions",
    "run_id": "${{ github.run_id }}"
}
```

## 4. Exemplo de output

### PASS

```
Score: 90/100 🟢
Critical: 0 | High: 1 | Medium: 0
✅ PASSED
```

### FAIL

```
Score: 45/100 🔴
Critical: 2 | High: 3 | Medium: 1
❌ FAILED
  ❌ [CRITICAL] Credencial exposta em script.js
  ❌ [CRITICAL] .env acessível
  ❌ [HIGH] CORS reflete origin arbitrário
```

## FAQ

**Quanto demora o scan?** ~15–30 segundos.

**Posso rodar em PRs?** Sim — troque `on: push` por `on: pull_request`.

**Como vejo o histórico?** Dashboard → Security Gate → Histórico de runs.
