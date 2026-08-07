---
layout: ../../../layouts/DocsLayout.astro
title: Referência da API
description: Referência da API REST do Klarim Security Gate — endpoints, autenticação por API key, formato de request/response e códigos de erro.
slug: api
---

# Referência da API REST

- **Base URL:** `https://klarim.net/api/gate`
- **Autenticação:** header `X-API-Key: KLM_xxxx`
- **Formato:** JSON (request e response)

Todos os endpoints exigem o header `X-API-Key`. A engine roda **no servidor** da Klarim — o
cliente só envia a URL a escanear.

## POST /gate/scan

Roda o scan de segurança contra a URL informada (síncrono, tipicamente < 60s).

**Request:**

```json
{
    "url": "https://meusite.com.br",
    "fail_on": "critical",
    "timeout": 60,
    "metadata": {"commit": "abc123", "ci": "github-actions"}
}
```

- `url` (obrigatório): domínio registrado como projeto e **verificado**.
- `fail_on` (opcional, default `critical`): `critical` | `high` | `medium`.
- `timeout` (opcional, default 60, máx 180): segundos.
- `metadata` (opcional): objeto livre para rastreabilidade (aparece no histórico).

**Response (200):**

```json
{
    "run_id": 42,
    "url": "https://meusite.com.br",
    "score": 90,
    "passed": true,
    "duration_ms": 14500,
    "critical": 0,
    "high": 1,
    "medium": 0,
    "results": [
        {"check": "headers", "status": "pass", "severity": "medium", "detail": "..."}
    ],
    "checks_run": ["headers", "ssl", "exposure", "credentials"],
    "checks_blocked": ["jwt", "tls_ciphers"],
    "plan": "Pro",
    "dashboard_url": "https://klarim.net/dashboard/gate/runs/42"
}
```

- `passed` já respeita o `fail_on` enviado.
- `checks_blocked`: checks fora do seu plano (faça upgrade para incluí-los).

## GET /gate/projects

Lista os projetos da conta (com o plano efetivo e os checks incluídos).

## POST /gate/projects

Cria um projeto. Nasce **não verificado**.

```json
{"name": "Meu App", "url": "https://meuapp.com.br"}
```

## GET /gate/runs

Lista os runs (sumário, sem `results`). Query: `?project_id=1&limit=20`.

## GET /gate/runs/{id}

Detalhe de um run (inclui `results`). 404 se o run não é da conta.

## POST /gate/projects/{id}/verify/start

Inicia a verificação de domínio. Body: `{"method": "dns_txt"}` (ou `meta_tag` | `html_file`).
Retorna o desafio + as instruções.

## POST /gate/projects/{id}/verify/check

Confere o desafio no site. Se comprovado, o projeto fica `verified` e pode ser escaneado.

## Códigos de erro

| Código | Significado |
|---|---|
| 401 | API key inválida, ausente ou revogada |
| 403 | Domínio não verificado, ou limite de domínios do plano atingido |
| 429 | Limite de scans/dia OU de requisições/minuto excedido |
| 422 | Parâmetro inválido (ex.: URL malformada) |

## Limites por plano

Scans/dia, domínios, requisições/minuto e quais checks rodam variam por plano
(Free / Pro / Team / Enterprise). Veja a tabela em
[klarim.net/security-gate](/security-gate#planos).
