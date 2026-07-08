# Fix — Classificação de setor em cascata + reclassificação de alvos

**Tipo:** Melhoria de qualidade (sem card Jira — refino do KL-11)
**Data:** 2026-07-08

## Problema

O `classifier.py` antigo contava keywords no **HTML cru** (incluindo scripts,
footers, menus e boilerplate). Resultado: hotéis viravam "escola", e-commerces
viravam "hotel". Como o **preço do relatório (R$ 19–49) depende do setor**, uma
classificação errada = preço errado + análise poluída no painel.

## Parte 1 — `classifier.py` reescrito (cascata de 3 camadas)

`classify_sector(html, url) -> (setor, price_tier, confiança)`. Tenta cada camada
em ordem; a primeira que passa o corte de confiança vence:

1. **Domínio** (`classify_by_domain`, conf **0.9**; 2 padrões do mesmo setor →
   **0.95**). O dono batizou o site deliberadamente — pista mais forte. Ex.:
   `hotelverdegreen` → hotel; `clinicaodonto` → clinica (0.95).
2. **Cabeçalho** (`classify_by_head`, conf **0.7–0.8**). `<title>` + todos os
   `<h1>` + meta description/og:description, **peso 5×**. ≥2 matches → 0.8; 1 → 0.7.
3. **Conteúdo limpo** (`classify_by_content`, conf **≥0.5**). `extract_visible_text`
   remove `nav/footer/header/script/style/svg/noscript` **e** as tags, depois conta
   keywords com **peso 1×** (combinado com head 5×).

Sem pista ⇒ `('outro', 0.0)`.

**Robustez:**
- **Keywords ambíguas** (`reserva`, `produto`, `entrega`) só contam com
  **co-ocorrência** de uma âncora do mesmo setor. Assim "todos os direitos
  reservados" **não** vira hotel.
- Casamento **sem acento** (`_fold` via NFKD): "clínica" e "clinica" batem igual.
- `classify_sector` é **síncrono** (CPU puro, sem I/O) — segue a regra do projeto
  de reservar `async` para I/O.
- **11 setores** (adicionados juridico, condominio, imobiliaria, automotivo) +
  `outro`. `PRICE_TIERS` cobre todos (teste garante).

## Parte 2 — Confiança persistida

`ALTER TABLE targets ADD COLUMN IF NOT EXISTS classification_confidence REAL
DEFAULT 0.0` (no `_SCHEMA`, padrão idempotente). `register_target` grava a
confiança (INSERT + `ON CONFLICT UPDATE`). `worker.py` e `ingest.py` passam a
confiança da cascata (inclusive sites fora do ar, que ainda classificam pelo
domínio). `list_targets` ganhou filtro `low_confidence` (`< 0.5`).

## Parte 3 — Reclassificação de alvos existentes (JWT, `/admin/*`)

- **`POST /admin/reclassify-domains`** — **instantâneo**, só pela pista do domínio,
  **sem HTTP**. Atualiza em lote (`bulk_update_classification`, uma conexão). **Só
  altera quando o domínio dá pista confiável (≥0.9) — nunca rebaixa uma
  classificação existente para `outro`** (decisão de design: não regredir dados
  bons de scans anteriores). Retorna `{processed, updated, changed, by_sector}`.
- **`POST /admin/reclassify-all`** — **background** (`_spawn`), refaz **fetch** do
  HTML de cada alvo (rate limit **1/s**), reclassifica pela cascata completa e
  atualiza. Loga progresso a cada 50 (`[reclassify] 100/553 processados, 45
  alterados`). Idempotente (não reinicia se já rodando).
- **`GET /admin/reclassify-status`** — `{running, processed, changed, total}`.

**Recomendação seguida:** começar pelo domínio-only (instantâneo) e depois o
fetch-based para refinar os sem pista.

## Parte 4 — Painel (Alvos)

- **Badge com indicador de confiança:** ≥0.8 badge normal · 0.5–0.79 borda
  **pontilhada** (provável) · <0.5 **cinza com "?"** (incerto). Tooltip com a %.
- **Filtro "Classificação incerta"** (toggle) → `low_confidence=true`.
- **Botão "Reclassificar domínios"** no header → chama o endpoint e mostra
  `N de M alvos alterados`.
- Novos setores adicionados ao filtro de setor.

## Parte 5 — Testes

Novo **`tests/test_classifier.py`** (16 testes): camada de domínio (inclui os
casos de validação exatos), cabeçalho, conteúdo (footer ignorado, script
removido), ambíguos (co-ocorrência), cascata (domínio ganha do conteúdo, fallback
`outro`, sem-HTML usa só domínio) e cobertura de `PRICE_TIERS`. Os testes de
classificação saíram de `test_discovery.py` (assinatura antiga). `test_ingest.py`
teve o `FakeStore.register_target` ajustado para o kwarg `confidence`.

**Suíte completa: 130 passed, 1 skipped.**

## Validação (todos ✔)

| # | Caso | Resultado |
|---|------|-----------|
| 1 | `classify_by_domain("https://hotelverdegreen.com.br")` | `("hotel", 0.9)` |
| 2 | `classify_by_domain("https://xyztech.com.br")` | `None` |
| 3 | hotel com "escola" no footer | `hotel` (não escola) |
| 4 | "Todos os direitos reservados" | não vira hotel (`None`) |
| 5 | reclassificação por domínio | endpoint executa e reporta `changed/processed` |
| 6 | dashboard | badge com confiança visual + filtro "incerta" |

## Parte 6 — Documentação

`CLAUDE.md` (seção 15) atualizado com a cascata de 3 camadas, a coluna de
confiança e os endpoints de reclassificação. Este relatório.

## Arquivos alterados

- `discovery/classifier.py` (reescrito), `discovery/__init__.py` (exports)
- `discovery/store.py` (coluna + `register_target` + `all_targets_for_reclassify`,
  `update_classification`, `bulk_update_classification`, filtro `low_confidence`)
- `discovery/worker.py`, `discovery/ingest.py` (call sites + confiança)
- `api/main.py` (imports, filtro, `targets/add`, 3 endpoints de reclassificação)
- `frontend/src/lib/adminApi.js`, `frontend/src/pages/admin/Alvos.jsx`
- `tests/test_classifier.py` (novo), `tests/test_discovery.py`, `tests/test_ingest.py`
- `CLAUDE.md`
