---
layout: ../../../layouts/DocsLayout.astro
title: Jenkins
description: Guia completo para integrar o Klarim Security Gate no Jenkins. 86 checks de segurança no seu pipeline, reprovando o build quando algo grave fica exposto.
slug: jenkins
---

# Integração com Jenkins

O Security Gate roda **86 checks de segurança** contra a URL do seu site e falha o build se algo
grave ficou exposto. Scan 100% passivo.

## Pré-requisitos

- Conta na Klarim com o **Security Gate ativo**
- **API key** gerada (dashboard → Security Gate → API Key)
- **Domínio verificado**

## 1. Adicione a credencial

Em **Manage Jenkins → Credentials → System → Global credentials → Add Credentials**:

- **Kind:** Secret text
- **Secret:** sua API key (começa com `KLM_`)
- **ID:** `klarim-key`

## 2. Adicione o stage ao `Jenkinsfile`

```groovy
pipeline {
  agent any
  stages {
    stage('Deploy') {
      steps { echo 'deploy' }
    }
    stage('Klarim Security Gate') {
      steps {
        withCredentials([string(credentialsId: 'klarim-key', variable: 'KLARIM_KEY')]) {
          sh '''
            pip install httpx
            python -c "
            import httpx, sys, json
            r = httpx.post('https://klarim.net/api/gate/scan',
                headers={'X-API-Key': '$KLARIM_KEY'},
                json={'url': 'https://meusite.com.br', 'fail_on': 'critical'},
                timeout=120)
            data = r.json()
            print(json.dumps(data, indent=2))
            sys.exit(0 if data['passed'] else 1)"
          '''
        }
      }
    }
  }
}
```

O `withCredentials` injeta a credencial `klarim-key` como a variável `$KLARIM_KEY` — ela nunca
aparece no log do build.

## 3. Configuração avançada

### Nível de reprovação (`fail_on`)

- `critical` (default): só findings **críticos**
- `high`: **críticos + altos**
- `medium`: **críticos + altos + médios**

### Timeout

Sites lentos: `timeout=180` (default 120s).

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

**Marcar o build como instável em vez de falhar?** Troque `sys.exit(1)` por uma etapa que rode
`currentBuild.result = 'UNSTABLE'` no seu `post`.

**Como vejo o histórico?** Dashboard → Security Gate → Histórico de runs.
