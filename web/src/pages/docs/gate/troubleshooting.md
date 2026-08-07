---
layout: ../../../layouts/DocsLayout.astro
title: Troubleshooting
description: Soluções para os erros mais comuns ao integrar o Klarim Security Gate — 401, 403, 429, timeout, checks faltando e domínio não verificado.
slug: troubleshooting
---

# Troubleshooting

Os problemas mais comuns e como resolvê-los.

| Problema | Causa | Solução |
|---|---|---|
| **401 Unauthorized** | API key inválida, revogada ou ausente | Confira o secret no CI; se preciso, regenere a key no dashboard |
| **403 Forbidden** | Domínio não verificado | Verifique o domínio (meta tag, DNS TXT ou arquivo HTML) no dashboard |
| **403 "Limite de domínios"** | O plano atingiu o `max_domains` | Faça upgrade do plano |
| **429 Too Many Requests** | Limite de scans/dia ou de requisições/minuto | Aguarde o reset (meia-noite UTC) ou faça upgrade |
| **Score inesperado** | Cache de CDN servindo conteúdo antigo | Adicione um `sleep 30` antes do scan |
| **Timeout** | Site lento ou checks pesados | Aumente para `timeout=180` |
| **Checks faltando** | Plano Free (4 checks) | Upgrade para Pro (9) ou Team (18) |
| **"Domínio não verificado"** | Verificação pendente | Publique a meta tag / DNS TXT / arquivo no site e rode a verificação |
| **Primeiro scan não aparece** | Secret não configurado no CI | Confira o **nome** do secret (`KLARIM_KEY`) |

## Entendendo o `passed`

O campo `passed` respeita o `fail_on` enviado:

- `fail_on: critical` → reprova só com findings **críticos**
- `fail_on: high` → reprova com **críticos + altos**
- `fail_on: medium` → reprova com **críticos + altos + médios**

Se o job "passa" quando você esperava reprovar, confira o `fail_on`.

## Ainda com problema?

- **Dashboard:** [klarim.net/dashboard/gate](/dashboard/gate) — histórico de runs e estado da key
- **Contato:** [contato@klarim.net](mailto:contato@klarim.net)
- **Documentação da API:** [Referência da API](/docs/gate/api)
