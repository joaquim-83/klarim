# KL-92 Prompt 4 (final) — concluir todas as pendências

**Card:** KL-92 · **Status:** Implementado (aguardando deploy verde + flush `scan:*`) · **Data:** 2026-07-21

Fecha os 5 itens pendentes da validação. **2 já estavam entregues** nos prompts anteriores (parser
Nginx no P3, tendência com zeros no P2) — confirmados em produção. Os outros 3 são novos.

---

## 1. Cloudflare Web Analytics → Google Analytics 4 (+ SRI allowlist)

**Problema:** o `beacon.min.js` do Cloudflare era o **único script externo sem SRI** → o self-scan
do klarim.net reprovava o check 13 e não dava 100.

- **Removido** o `<script beacon.min.js>` do `web/src/layouts/Base.astro` + `static.cloudflareinsights.com`/
  `cloudflareinsights.com` da CSP.
- **GA4** (`G-7WPZN66JTB`) no `<head>`: loader externo `www.googletagmanager.com/gtag/js` + init
  **inline** (hash SHA-256 na CSP: `qzH7zDtLe593g3bHtjaiMTvw04nqU/2iiMJnv9osNzA=` — validado em teste
  que recomputa o hash do conteúdo exato).
- **CSP** (`security_headers.conf`): `script-src` += `www.googletagmanager.com` + hash;
  `connect-src` += `www.google-analytics.com analytics.google.com region1.google-analytics.com`;
  `img-src` += `www.google-analytics.com`. Validado com `nginx -t` local (HTTP+HTTPS).
- **Check 13 (SRI) allowlist** (`SRI_ALLOWLIST_DOMAINS`): googletagmanager / google-analytics /
  cloudflareinsights **não contam como FAIL** (SRI inviável em CDN que atualiza o bundle sem aviso).
  Testado: página só-gtag → PASS; script de terceiro sem SRI → segue FAIL. → **klarim.net volta a 100**.

## 2. Classificação de pre-fetchers de e-mail

`api/bot_classifier.py`: `_EMAIL_PREFETCH_CIDRS` (66.102.0.0/20 Gmail · 66.249.64.0/19 Googlebot ·
40.94.0.0/16 + 40.92.0.0/15 Outlook · 104.47.0.0/17 EOP) + regra **>20 domínios distintos/hora**
(set Redis `access_domains:{ip}`, TTL 1h, alimentado no middleware). Ambos → `email_prefetch`
(checado **antes** de datacenter, em `classify_bot` e `classify_bot_simple` — o parser Nginx usa o
`_simple`). Resultado: os IPs `66.x`/`104.47` que "visitam" centenas de sites deixam de contar como
humanos (`bots_filtered` sobe, `visitors_br` fica real).

## 3. Parsing do Nginx access_log — **já entregue no Prompt 3** ✅

Confirmado em produção: a tabela `access_log` tem **40.124 linhas `source='nginx'`** (vs 25.384 do
middleware), capturando `/`, `/cadastrar`, `/site/*`, `/privacidade`, etc. `visitors_br` subiu de
~26 para **56**. **Decisão mantida (hybrid):** NÃO desliguei o middleware — o parser pula `/api`/`/mcp`
(disjunto, sem duplicata) e o middleware preserva `user_id` + retroatividade. Nenhuma mudança neste
prompt além da classificação `email_prefetch` que o parser agora aplica.

## 4. LGPD — anonimização IPv6

`anonymize_old_access_logs` agora trunca **IPv4 → /24 e IPv6 → /48** para IPs >90d (idempotente, só
máscara cheia). Validado contra Postgres 16 real: `2804:14c:1:2:3:4:5:6` (100d) → `2804:14c:1::/48`;
IPv4 `189.28.100.42` (100d) → `189.28.100.0/24`; registro recente intacto.

## 5. Tendência com zeros — **já entregue no Prompt 2** ✅

`assemble_daily_series` (api/admin_analytics.py) já densifica: `[by_day.get(d, zero) for d in day_list]`
→ "7 dias" mostra 7 pontos (0 nos dias sem dado). Teste explícito adicionado.

---

## Testes (+16, `tests/test_kl92_p4.py`)

CSP GA4/CF, hash do init GA4 (recomputado), allowlist de SRI (PASS gtag / FAIL terceiro), ranges de
email_prefetch + regra >20-domínios (em `classify_bot` e `classify_bot_simple`), contrato IPv6 da
anonimização, densificação da série. Suíte: **1503 backend passed** · **96 node --test** · Astro
build OK (GA4 presente, beacon.min.js ausente).

## Security review

- CSP é a peça crítica: GA4 não funciona se `script-src`/`connect-src` estiverem errados → validei o
  `nginx -t` e o hash do inline (teste que recomputa). Sem `unsafe-inline`.
- SRI allowlist é restrita a provedores de analytics (não é isenção genérica; scripts de terceiros
  seguem exigindo SRI).
- Anonimização LGPD agora cobre IPv6.

## Arquivos

**Novos:** `tests/test_kl92_p4.py`.
**Alterados:** `web/src/layouts/Base.astro`, `frontend/nginx/security_headers.conf`,
`scanner/checks/check_sri.py`, `api/bot_classifier.py`, `api/access_log_middleware.py`,
`discovery/store.py` (IPv6), `CLAUDE.md`, `docs/SECURITY.md`.

## Pós-deploy (VM) — obrigatório

```bash
# check 13 (SRI allowlist) muda o score → flush dos scans cacheados:
sudo docker exec klarim-redis-1 sh -c "redis-cli --scan --pattern 'scan:*' | xargs redis-cli DEL"
# klarim.net deve dar 100:
curl -s 'https://klarim.net/api/scan/result?url=klarim.net'
# GA4 carrega, beacon.min.js some (DevTools → Network); console sem erro de CSP.
```
