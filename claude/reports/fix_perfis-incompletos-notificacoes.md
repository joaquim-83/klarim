# Fix — Perfis incompletos + notificações ausentes (igoove)

**Data:** 2026-07-14 · **Prioridade:** alta

Diagnóstico OBRIGATÓRIO feito **em produção** antes de qualquer correção. Resultado:
o problema é **site-específico** e tem causa clara.

## Diagnóstico (produção)

Rodei um script no container `api` da VM contra os alvos `igoove*`:

| Alvo | status | platform | contact_email | site_profile | fetch homepage |
|---|---|---|---|---|---|
| igoove.com (8172) | scanned | unknown | **None** | existe, **vazio** (maturidade 1, sem desc/tags/CNAE) | **HTTP 403** |
| igoove.com.br (13642) | scanned | unknown | **None** | **NÃO existe** | HTTP 200 (2506 bytes, stub, sem JSON-LD) |

- `AI_ENRICHMENT_ENABLED=True`, `OPENAI_API_KEY` setada → a IA **está ligada**.
- `USER_AGENT = KlarimScanner/0.1 (…passive security scan; GET/HEAD only)`.
- **`FETCH https://igoove.com → 403`**: o site **bloqueia o User-Agent honesto do Klarim**
  (WAF/Cloudflare anti-bot). O crawl volta vazio → o profiler não extrai nada → perfil
  esparso. A IA (`_ai_enrich`) sai cedo sem `homepage_html`.
- O **scan** mesmo assim pontuou (81): checks de header/TLS/DNS funcionam com a resposta 403.

### Causas por problema
- **Problema 1 (perfil incompleto):** o site 403a o UA honesto → sem conteúdo para o profiler.
  Por **§4.3 (não nos passamos por navegador)**, não podemos trocar o UA para burlar o WAF.
  É **limitação**, não bug. O igoove.com.br (200) é um stub sem dados úteis (SPA/redirect).
- **Problema 2 (sem notificação):** `contact_email=None` (não conseguimos extrair e-mail do
  site bloqueado) → `/notify/profile-view` **corretamente pula** (comportamento por design).
  O Resend funciona para outros sites (confirmado: chaves `notify:*` de vários domínios).

## Correções entregues

1. **Logging do bloqueio (fim da falha silenciosa).** `scanner/enrichment.py::enrich_profile`
   agora **loga** o status da homepage (`homepage HTTP 403 (anti-bot/WAF…)`), a falha de fetch
   (antes `except: pass`) e o resumo (`páginas=N homepage=STATUS`). Isso torna o diagnóstico
   instantâneo em vez de exigir investigação manual.

2. **Re-enrich forçado.** `scripts/enrich_all.py --domain <texto>` (e `--force`) roda o
   `enrich_profile` **compartilhado** (crawl + profiler + IA + CNAE) em cada alvo casado,
   **ignorando os grupos** — reprocessa mesmo alvos que já têm perfil. `store.list_targets_
   matching(pattern)` seleciona por domínio/url. Uso:
   `docker compose exec -T api python scripts/enrich_all.py --domain igoove` (ou `--dry-run`).
   (Não conserta um site que bloqueia o UA — mas conserta os que **podem** ser crawleados.)

3. **Consultas de perfil no painel Alertas.** A página **Alertas** ganhou a aba **"Consultas
   de perfil"** (Opção C): lista os eventos `profile_view` do `site_events` (KL-51 f4) — site
   consultado + origem (utm) + data. Via `GET /analytics/events?event_type=profile_view`
   (`analytics_events` ganhou o filtro). O `site_events` **não guarda IP** (só domínio/sessão),
   então a coluna IP não é exibida.

## Testes
`tests/test_fix_profile_notify.py` (6): enrich_profile **loga** o 403 + grava perfil esparso;
`_force_enrich` roda o enrich_profile compartilhado (+ dry-run não enriquece); `/analytics/
events?event_type=` filtra profile_view / sem filtro / exige admin. Full-suite + build do
painel verdes.

## Ops (pós-deploy)
- `docker compose exec -T api python scripts/enrich_all.py --domain igoove` para reprocessar
  os alvos igoove — o igoove.com continuará esparso (403), mas o log deixa isso explícito, e
  os alvos crawláveis ganham perfil.

## Conclusão honesta
`igoove.com` **não pode** ter perfil rico porque bloqueia o scanner (WAF) e não podemos
falsear o UA (§4.3). A correção real é: (a) **logar** o bloqueio (feito — some a falha
silenciosa), (b) **ferramenta de re-enrich** para os sites que **são** crawláveis (feito),
(c) **surfaçar** as consultas de perfil no painel (feito). A notificação pulada é correta
(sem e-mail extraível).
