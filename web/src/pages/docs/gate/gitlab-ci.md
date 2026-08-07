---
layout: ../../../layouts/DocsLayout.astro
title: GitLab CI
description: Guia completo para integrar o Klarim Security Gate no GitLab CI. 86 checks de segurança no seu pipeline, reprovando o deploy quando algo grave fica exposto.
slug: gitlab-ci
---

# Integração com GitLab CI

O Security Gate roda **86 checks de segurança** contra a URL do seu site e reprova o job se algo
grave ficou exposto. Scan 100% passivo — nenhum payload de ataque.

## Pré-requisitos

- Conta na Klarim com o **Security Gate ativo**
- **API key** gerada (dashboard → Security Gate → API Key)
- **Domínio verificado**

## 1. Adicione a variável de CI/CD

Em **Settings → CI/CD → Variables → Add variable**:

- **Key:** `KLARIM_KEY`
- **Value:** sua API key (começa com `KLM_`)
- Marque **Protected** e **Masked**

## 2. Adicione o job ao `.gitlab-ci.yml`

```yaml
stages:
  - deploy
  - post-deploy

security_gate:
  stage: post-deploy
  image: python:3.12-slim
  rules:
    - if: '$CI_COMMIT_BRANCH == "main"'
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

A referência à variável no GitLab é `$KLARIM_KEY` (sem `${{ }}`).

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
    "commit": "$CI_COMMIT_SHA",
    "branch": "$CI_COMMIT_REF_NAME",
    "ci": "gitlab-ci"
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

**Rodar só em merge requests?** Use `rules: - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'`.

**Como vejo o histórico?** Dashboard → Security Gate → Histórico de runs.
