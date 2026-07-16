# KL-44 P5 — Indicadores técnicos de privacidade + selo "Monitorado por Klarim" + benchmark setorial

**Card:** KL-44 (fase P5) · **Prioridade:** Highest
**Dependências:** P1–P4 ✅, KL-67 ✅, KL-71 ✅
**Status:** ✅ Concluído — 939+ testes passando, deploy pendente de push.

Reposicionamento legal: o Klarim **não** afirma conformidade LGPD nem certifica. Entrega
**fatos técnicos** (diagnóstico), com **disclaimer obrigatório** em toda superfície.

---

## Bloco 1 — 8 indicadores técnicos de privacidade

**`scanner/privacy_checks.py`** (novo, puro/testável): 8 indicadores passivos sobre um
**único GET** (mesmo caminho dos checks de segurança, zero requests extras por indicador):

1. Política de Privacidade (Art. 9°) · 2. Banner de Cookies/CMP (Art. 7°/8°) ·
3. Cookies de terceiros pré-consentimento — *negativo* (Art. 7°) · 4. Canal de direitos
do titular / DSAR (Art. 18°) · 5. Identificação do Encarregado/DPO (Art. 41°) ·
6. Política de Cookies dedicada (Guia ANPD) · 7. HTTPS em formulários (Art. 46°) ·
8. Headers de segurança em formulários (Art. 46°).

- Cada indicador cita o artigo **como referência**, não como atestado.
- **`privacy_score` (0–8) SEPARADO** do score de segurança (0–100) — nunca se combinam.
  Integrado ao `run_scan` (`asyncio.gather` junto com os checks), persistido em
  `scans.checks_json->'privacy'` (via `ScanReport.to_dict`/`from_dict`). Fail-open: erro →
  `privacy=None`, nunca derruba o scan de segurança.
- Exposto por: `_summary_payload` (scan/summary), `/public/profile`, `/public/laudo`,
  `/account/sites/{id}` e o selo.

**1º scan de validação** (exemplo do card, HTML sintético com CookieYes + form HTTPS +
política + direitos + headers, com `_ga`): **5/8** — PASS em política, banner, DSAR, HTTPS,
headers; FAIL em cookies de terceiros (`_ga`), DPO e política de cookies. Bate com o
esperado.

## Bloco 2 — Selo "Monitorado por Klarim"

- **`GET /seal/{domain}`** (público, factual `seal_type="monitored"`): score + semáforo +
  `privacy_score`/`privacy_total` + `profile_url`. **CORS `*`**, cache Redis 1h, rate limit
  60/h/IP. Nunca PII, nunca "certificado"/"aprovado".
- **`web/public/seal/widget.js`** (estático, <5KB, sem dependências): busca `/api/seal/…`,
  renderiza badge discreto (auto dark/light, compact/full), abre o perfil público em nova
  aba. **Sem tracking** — só 1 GET de leitura; fallback estático se a API cair.
- **Dashboard (dono verificado):** seção "Selo de monitoramento" no `SiteDetail` — preview
  das opções (tema/tamanho) + snippet copiável. Só aparece se `is_owner` (quem só monitora
  não instala selo).

## Bloco 3 — Benchmark setorial

- **`store.sector_benchmark(sector, min_count=10)`**: média/mediana/min/max +
  distribuição por semáforo (🟢≥90 / 🟡50–89 / 🔴<50), **anônima** (só agregados, nunca
  nomeia sites); None se < 10 scans. **`all_sector_benchmarks`** para o `/benchmark/all`.
- **`GET /benchmark/{sector}`** enriquecido (mediana + distribuição), **cache Redis 24h**,
  fallback ao geral se amostra < 10. **`GET /benchmark/all`** novo. Retrocompatível
  (`avg_score`/`count` preservados p/ o Dashboard).
- Exposto no **perfil público** (distribuição do setor) e no **boletim** (Seu score vs.
  média + "Acima/Abaixo da média").

## Bloco 4 — Disclaimer + UI + admin/MCP

- **Disclaimer obrigatório** (`PRIVACY_DISCLAIMER`, fonte única) em: perfil público, laudo,
  dashboard e boletim. O selo não leva (tamanho), mas o perfil que ele linka leva.
- **UI:** seção "Indicadores de privacidade: N/8" com ✅/❌ + referência LGPD por indicador
  no perfil público (`/site/{domain}`), no laudo (`/laudo/{code}`) e no dashboard
  (`SiteDetail` com "O que fazer" por FAIL — texto genérico, não assessoria).
- **Admin/MCP:** `GET /admin/privacy-stats` + MCP `get_privacy_stats` — distribuição
  PASS/FAIL por indicador (inteligência comercial: "X% do setor sem banner de cookies").

## Correção latente encontrada

O `checks_json` persistido é o **dict completo** do report (`{results, score, privacy}`),
mas laudo/boletim o liam como **lista** de checks. Tornei a leitura **defensiva** (dict →
`results`/`checks` + extrai `privacy`; lista → usa direto) no `/public/laudo`,
`account_site_detail` e `bulletin_worker` — corrige o acesso e habilita a privacidade.

## Testes

- **`tests/test_kl44_p5_privacy.py`** (17): os 8 indicadores puros (PASS/FAIL, score,
  disclaimer), negativo de cookies de terceiros, HTTPS/headers em formulários, ausência de
  linguagem de conformidade; endpoints selo (CORS + cache + factual), benchmark rico +
  fallback + `/all`, admin privacy-stats (auth), `/seal` público.
- `test_mcp_server`: `get_privacy_stats` registrada. `test_runner_concurrency`: fixture que
  neutraliza o GET de privacidade (mede concorrência dos checks). FakeStores de
  `public_profile` (kl51_f4, kl42) ganharam `get_latest_scan_full`/`sector_benchmark`.
- **Suite: 939 passed, 1 skipped.**

## Regras invioláveis respeitadas

Nunca "compliant/certificado/aprovado" (teste `test_no_compliance_language`); disclaimer em
toda superfície; privacy_score separado; benchmark anônimo; selo factual; widget sem
tracking; scanner passivo (1 GET, sem payloads); `contact_email` nunca exposto.

## Deploy

Sem migration (privacy vive no `checks_json`). **Flush `scan:*` no Redis não é necessário**
(o score de segurança não mudou; a privacidade só aparece em scans novos — os antigos
mostram a seção quando reescaneados). O widget é estático (Astro `public/`).
