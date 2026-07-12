# KL-54 — Taxonomia de setores: 15 → 48 setores + 13 macro-setores

**Card Jira:** KL-54 · **Prioridade:** URGENTE (desbloqueia o batch de
`enrich_all.py` e os perfis públicos). **Sem** impacto no score de segurança,
**sem** flush de Redis, **sem** migration (a coluna `targets.sector` é TEXT).

**Problema:** o Klarim classificava em ~15 setores + `outro` e **57% dos alvos**
(7.548 de 13.202) caíam em `outro` — a taxonomia não cobria o mercado de PME
brasileiro. A IA (GPT-4o mini) já classificava inline, mas presa aos 15 setores do
prompt.

---

## Fonte da verdade única — `discovery/sector_taxonomy.py` (novo)

Módulo **puro** (zero imports internos → sem risco de ciclo; importável de qualquer
camada). Conteúdo:

- `SECTOR_TAXONOMY` — `setor → {macro, label}`. **48 setores** finos + `outro`,
  organizados em **13 macro-setores** + `outro`.
- `VALID_SECTORS`, `MACRO_SECTORS`, `MACRO_LABELS`.
- `SECTOR_ALIASES` — setores legados desmembrados (`saude → clinica`).
- `get_macro(s)` / `get_label(s)` / `normalize_sector(s)` (limpa + resolve alias +
  inválido ⇒ `outro`).

O **macro-setor é derivável** por lookup — **não** há coluna nova no banco.

> **Nota sobre a contagem:** o card fala em "~47 setores"; o dict entregue tem
> **48 setores + `outro` = 49 entradas** (48 = a lista completa que o card
> especifica). Implementei a lista fiel do card e ajustei os testes para o número
> real (o "47"/"46" do card eram aproximações).

---

## Módulos afetados (todos importam da taxonomia)

| Módulo | Mudança |
|--------|---------|
| `scanner/ai_enrichment.py` | `SYSTEM_PROMPT` lista os 48 setores **dinamicamente** (`sorted(VALID_SECTORS-{"outro"})`); `SECTORS = VALID_SECTORS`; validação via `normalize_sector` (resolve `saude→clinica`, inválido→`outro`). |
| `discovery/classifier.py` | `PRICE_TIERS = {s: "standard" for s in VALID_SECTORS}` (**preço único** R$ 19 — tier só p/ analytics); `DOMAIN_PATTERNS`/`SECTOR_KEYWORDS` **desmembrados** + padrões novos. |
| `scanner/profiler.py` | `_SCHEMA_SECTOR` (Schema.org `@type`→setor) mapeia os finos: `Dentist→odontologia`, `Bakery→padaria_confeitaria`, `Pharmacy→farmacia`, `VeterinaryCare→veterinaria`, `TravelAgency→turismo_viagens`, … |
| `api/main.py` | `GET /sectors` (público; via Nginx `GET /api/sectors`) → `sectors` (48 `{id,label,macro}`) + `macro_sectors` (13). `_VALID_SECTORS = set(PRICE_TIERS)` já cobre os 48. |
| `frontend/.../SectorEditor.jsx` | `SECTOR_OPTIONS` espelha a taxonomia (48 + outro, ordenada por macro). |
| `frontend/.../Alvos.jsx` | filtro de setor deriva de `SECTOR_OPTIONS` (cobre os 48). |

### Desmembramento do classificador regex (a decisão de design mais delicada)

`classify_sector` indexa `PRICE_TIERS[setor]` **direto** — todo setor em
`DOMAIN_PATTERNS`/`SECTOR_KEYWORDS` **tem** que estar em `VALID_SECTORS` (garantido
por teste). Como o domínio dá confiança **0.9** (que a IA **não** rebaixa, pois só
refina classificações fracas), os setores **finos** ficam **antes** dos genéricos no
dict — no empate, o específico vence:

- `odontologia`/`veterinaria`/`psicologia`/`nutricao`/`farmacia`/`hospital`/
  `laboratorio` saem de `clinica`;
- `padaria_confeitaria`/`bar_lanchonete` saem de `restaurante`;
- `faculdade`/`curso_idiomas` saem de `escola`;
- comércio ganha `loja_moda`/`otica`/`supermercado`/`petshop`/`moveis_decoracao`/
  `eletronicos`; e entram beleza, serviços, imóveis, eventos, institucional etc.

Mantive os padrões **precisos** e evitei os curtos/ambíguos (a IA cobre a cauda
longa). Ex.: `odontosorriso.com.br` → `odontologia`; `clinicaodonto.com.br` →
`clinica` (pois `clinica`+`clinic` dão 2 padrões, 0.95 > 0.9) — macro-correto e
consistente com o comportamento antigo.

---

## Retrocompatibilidade

- Os **15 setores antigos** continuam válidos (mesmo nome).
- O genérico antigo `saude` (KL-47A, ~3 alvos) foi **desmembrado**; `normalize_sector`
  o mapeia para `clinica` via `SECTOR_ALIASES` (a IA refina no batch). Nenhum valor
  legado quebra.
- **Preço único mantido:** `PRICE_TIERS` foi achatado para `standard` em todos os
  setores (o tier só serve a analytics de classificação; o preço é R$ 19 desde o
  KL-27). *Isso muda os tiers analíticos dos 15 setores antigos — decisão explícita
  do card ("Todos com tier standard").* Os testes de classificação manual foram
  atualizados.

---

## Testes

- **Novo `tests/test_sector_taxonomy.py` (19 casos):** estrutura (48+outro, 13 macros,
  macro+label em todos), helpers (`get_macro`/`get_label`/`normalize_sector` + alias
  `saude→clinica`), IA (prompt lista os 48; aceita setor novo; normaliza inválido e
  legado — `call_openai` mockado), Schema.org (`Dentist→odontologia`,
  `Bakery→padaria_confeitaria`; todos os valores de `_SCHEMA_SECTOR` válidos),
  classificador (setores novos por domínio; `PRICE_TIERS` cobre a taxonomia e é todo
  `standard`), e o endpoint `GET /sectors` (48 setores + 13 macros).
- **Atualizados:** `tests/test_classifier.py` (o desmembramento fino + tier `standard`)
  e `tests/test_manual_classify.py` (tier `standard`).
- **Suíte completa:** `pytest` → **512 passed, 1 skipped**. Frontend: `npm run build`
  ✓ (Vite, 6.3s).

---

## Arquivos

- **Novo:** `discovery/sector_taxonomy.py`, `tests/test_sector_taxonomy.py`, este
  relatório.
- **Editado:** `scanner/ai_enrichment.py`, `discovery/classifier.py`,
  `scanner/profiler.py`, `api/main.py`, `frontend/src/components/admin/SectorEditor.jsx`,
  `frontend/src/pages/admin/Alvos.jsx`, `tests/test_classifier.py`,
  `tests/test_manual_classify.py`, `claude.md` (seção 38), `README.md`.

## Operação pós-deploy

O batch `enrich_all.py` agora classifica na taxonomia completa. Rodar (opcional,
quando o dono quiser reclassificar os `outro`):
`docker compose exec api python scripts/enrich_all.py --dry-run` → `--limit 500`.
Sem flush de Redis (metadata de setor não altera o score).
