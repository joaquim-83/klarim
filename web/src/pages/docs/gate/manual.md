---
layout: ../../../layouts/DocsLayout.astro
title: Manual / Terminal
description: Rode o Klarim Security Gate manualmente no terminal com Python (httpx) ou curl. 86 checks de segurança sob demanda.
slug: manual
---

# Uso manual (terminal)

Rode o Security Gate direto do seu terminal — útil para testar antes de integrar ao CI/CD.

## Pré-requisitos

- **API key** gerada (dashboard → Security Gate → API Key)
- **Domínio verificado**

## Instalação

```bash
pip install httpx
```

## Scan rápido (Python)

```bash
python -c "
import httpx, sys, json
r = httpx.post('https://klarim.net/api/gate/scan',
    headers={'X-API-Key': 'KLM_sua_key_aqui'},
    json={'url': 'https://meusite.com.br', 'fail_on': 'critical'},
    timeout=120)
data = r.json()
print(json.dumps(data, indent=2))
sys.exit(0 if data['passed'] else 1)"
```

Substitua `KLM_sua_key_aqui` pela sua API key e `https://meusite.com.br` pelo domínio verificado.

## Alternativa com curl

```bash
curl -s https://klarim.net/api/gate/scan \
  -H "X-API-Key: KLM_sua_key_aqui" \
  -H "Content-Type: application/json" \
  -d '{"url":"https://meusite.com.br","fail_on":"critical"}' | jq .
```

## Guardar a key numa variável

Evite deixar a key no histórico do shell:

```bash
export KLARIM_KEY="KLM_sua_key_aqui"

curl -s https://klarim.net/api/gate/scan \
  -H "X-API-Key: $KLARIM_KEY" \
  -H "Content-Type: application/json" \
  -d '{"url":"https://meusite.com.br","fail_on":"critical"}' | jq .
```

## Níveis de reprovação

- `critical` (default): só findings **críticos**
- `high`: **críticos + altos**
- `medium`: **críticos + altos + médios**

## FAQ

**Site lento?** Aumente o `timeout` para `180`.

**Preciso instalar algo além de `httpx`/`curl`?** Não. A engine roda no servidor da Klarim; o
cliente só envia a URL e a key.
