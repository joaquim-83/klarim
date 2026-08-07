---
layout: ../../../layouts/DocsLayout.astro
title: Bitbucket Pipelines
description: Guia completo para integrar o Klarim Security Gate no Bitbucket Pipelines. 86 checks de segurança no seu pipeline, reprovando o deploy quando algo grave fica exposto.
slug: bitbucket
---

# Integração com Bitbucket Pipelines

O Security Gate roda **86 checks de segurança** contra a URL do seu site e reprova o step se algo
grave ficou exposto. Scan 100% passivo.

## Pré-requisitos

- Conta na Klarim com o **Security Gate ativo**
- **API key** gerada (dashboard → Security Gate → API Key)
- **Domínio verificado**

## 1. Adicione a variável do repositório

Em **Repository settings → Pipelines → Repository variables**:

- **Name:** `KLARIM_KEY`
- **Value:** sua API key (começa com `KLM_`)
- Marque **Secured**

## 2. Adicione o step ao `bitbucket-pipelines.yml`

```yaml
pipelines:
  branches:
    main:
      - step:
          name: Deploy
          script:
            - echo "deploy"
      - step:
          name: Klarim Security Gate
          image: python:3.12-slim
          script:
            - pip install httpx
            - |
              python -c "
              import httpx, sys, json
              r = httpx.post('https://klarim.net/api/gate/scan',
                  headers={'X-API-Key': '$KLARIM_KEY'},
                  json={'url': 'https://meusite.com.br', 'fail_on': 'critical'},
                  timeout=120)
              data = r.json()
              print(json.dumps(data, indent=2))
              sys.exit(0 if data['passed'] else 1)"
```

A referência à variável no Bitbucket é `$KLARIM_KEY`.

## 3. Configuração avançada

### Nível de reprovação (`fail_on`)

- `critical` (default): só findings **críticos**
- `high`: **críticos + altos**
- `medium`: **críticos + altos + médios**

### Timeout

Sites lentos: `timeout=180` (default 120s).

### Metadata (rastreabilidade)

```json
"metadata": {
    "commit": "$BITBUCKET_COMMIT",
    "branch": "$BITBUCKET_BRANCH",
    "ci": "bitbucket"
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
```

## FAQ

**Rodar em pull requests?** Use a seção `pull-requests:` no lugar de `branches:`.

**Como vejo o histórico?** Dashboard → Security Gate → Histórico de runs.
