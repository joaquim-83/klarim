# KL-104 Parte 3 — Visão 360° do alvo (painel de inteligência)

**Card:** KL-104 (High) · **Parte 3 de 3 — fecha o card** · **Status:** ✅

## Objetivo
Juntar na página de detalhe do alvo (`/painel/alvos/{id}`) a inteligência que hoje exige abrir
4-5 páginas e cruzar mentalmente: **quem monitora**, **onde o alvo está no funil**, **quem
pesquisou o domínio** (comportamento por IP, KL-92) e uma **timeline unificada**. Um único
endpoint, 4 seções, degradação graciosa.

## Entregue

### Backend — `GET /admin/targets/{id}/intelligence` (JWT admin)
Uma chamada → `{monitoring, funnel, visitors, timeline}`. **Arquitetura testável:** agregações
brutas (SQL parametrizado) em `discovery/store.py` (`ti_*`, **1 conexão por método** → uma falha
não contamina as outras); **montagem PURA** em **`api/target_intelligence.py`** + orquestrador
com **degradação graciosa** em 2 níveis:
- `_try(fn, …)` engole erro de sub-query (ex.: `technician_links` ausente) → o campo vira `null`;
- `_safe_section(builder, …)` isola a seção inteira → falha vira `{"error": …}`, nunca derruba o response.

**Seção 1 · Monitoramento** — `monitors` (`user_sites`→`users`: e-mail/plano/nível/desde — o e-mail
é dado admin legítimo), `vigilias` (por `site_domain`, com status/next_check), `owner_verified`
(flag `targets.owner_verified` + método/data da `ownership_verifications`), `technician`
(`technician_links`, prioriza o não-revogado mais recente).

**Seção 2 · Lead & Conversão** — funil de **6 etapas** derivado de timestamps REAIS
(discovered=`discovered_at` · scanned=`last_scan_at` · alerted=1º e-mail de alerta ·
account_created=1º `user_sites` · monitoring=1ª vigília ativa · paid=`payments` por `target_url`);
`emails_sent` (últimos 20 do `email_log`) + `emails_summary` (total/by_type/by_status/último) +
`lead_score` (reusa `alert_quality_score`, classificação hot/warm/cold).

**Seção 3 · Visitantes** — `total_queries`/`unique_ips` (30d, humanos) + **top 10 IPs mascarados
/24** (`mask_ip(ip,3)`, LGPD KL-92 — o IP completo **nunca** deixa o backend) + **cross-site**
(outros domínios consultados pelo mesmo IP: **1 query batch** `ip_address = ANY(%s::inet[])`,
corte 5/IP no módulo, cada domínio com `target_id` resolvido p/ o DomainLink) + `traffic_sources`
(classificação por `referrer`: direto/alerta/perfil/google/interno/outros).

**Seção 4 · Timeline** — UNION lógico (merge em Python, ordem DESC) de **5 fontes**: scans,
alertas (`email_log`), perfil-consultado (`access_log` `endpoint LIKE '/site/%'`, IP mascarado),
status do site (`site_status_log`), descoberta (`targets.discovered_at`). **Paginação por cursor**
(`?before=<iso>` → cada fonte filtra `< before`; `has_more`/`next_cursor`). O `email_log.sent_at`
é TIMESTAMPTZ e as demais fontes são naive — normalizei via `AT TIME ZONE 'UTC'` no SQL p/ o merge
não misturar datetime aware/naive.

**Índices:** `access_log(domain_queried, created_at)` e `(ip_address, created_at)` — cobrem
visitantes + cross-site com filtro de data.

### Frontend — `TargetIntelligence.jsx` (montado no topo do `AlvoDetalhePage`)
1 fetch no mount → 4 seções `<details>/<summary>` (CSP-safe, padrão do P2). Funil visual (etapas
atingidas em laranja, futuras em cinza tracejado). Timeline com **"Carregar mais ↓"** (cursor).
Cross-site = **DomainLinks** (componente do P1) clicáveis → detalhe do alvo cruzado (texto puro se
sem `target_id`). Seção `null`/`{error}` → "Dados indisponíveis" (sem quebrar as outras). IPs já
chegam mascarados do backend.

## Segurança (revisão obrigatória)
- Endpoint sob **JWT admin** (prefixo `/admin`, middleware) — 401 sem token, 404 alvo inexistente.
- **IP mascarado /24** em TODA saída (top IPs + eventos de perfil na timeline); o IP completo nunca
  sai do backend. Cross-site expõe **só domínios**, nunca IPs.
- **Queries 100% parametrizadas** (o único interpolado é `INTERVAL 'N days'` com `int(days)`).
  `contact_email`/cnpj/whatsapp do alvo nunca no payload (o e-mail dos MONITORS é dado admin, ok).

## Testes
`test_kl104p3_intelligence.py` (**+18**): montagens puras (funil ativo/etapa, lead class, fontes de
tráfego, visitantes com máscara /24 + cross-site + cap por IP, timeline merge/ordem/cursor/máscara,
parse_cursor), orquestrador (4 seções, **isolamento de falha** de seção + **degradação** de
sub-tabela ausente), endpoint (401/404/200, IP nunca completo no response). **Suite: 1662 backend**
+ 107 `node --test`; build Astro OK. Os **8 padrões SQL** (join, AT TIME ZONE, `ANY(::inet[])`,
`endpoint LIKE`, cutoff, payments por target_url…) validados no **Postgres 16 da VM**.

## Validação pós-deploy
Painel → Alvos → alvo com histórico → 4 seções. Monitoramento lista quem monitora; Funil mostra as
etapas + e-mails; Visitantes traz IPs mascarados + cross-site clicável; Timeline em ordem, "Carregar
mais" pagina. Fechar o KL-104 no Jira (→ Feito).
