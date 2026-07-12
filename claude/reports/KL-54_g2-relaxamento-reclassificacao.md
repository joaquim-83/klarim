# KL-54 (ajuste) — Relaxar o Grupo 2: a IA revê TODA classificação por regex

**Contexto:** extensão do KL-54 (48 setores). Ajuste rápido em `scripts/enrich_all.py`
e `discovery/store.py`.

**Problema:** a condição do Grupo 2 (reclassificação por IA) estava restritiva demais —
só revia classificações **fracas** (`sector='outro'` OU `confidence < 0.5`). Com a
expansão de 15 → 48 setores, **toda** classificação por regex precisa passar pela IA.
Sites como `agencianextweb.com.br`, que o regex deu `imobiliaria` com confiança **0.5**,
nunca eram revistos (deveria ser `agencia`).

---

## Mudança

**Antes** (G2):
```
classification_source != 'ai' AND (sector = 'outro' OR classification_confidence < 0.5)
```
**Depois** (G2):
```
classification_source NOT IN ('ai', 'manual')   -- null-safe: IS DISTINCT FROM ...
```
Removido o filtro de setor/confiança. Só preserva `manual` (operador) e `ai` (já revisto).

### `discovery/store.py` — dois pontos (seleção **e** update)

1. **`_ENRICH_G2`** (a query de seleção do batch): agora
   `sp.id IS NOT NULL AND source IS DISTINCT FROM 'ai' AND source IS DISTINCT FROM 'manual'`.
2. **`ai_update_classification`** (o UPDATE em si): o guard SQL tinha a **mesma**
   restrição de fraqueza — sem relaxá-lo, o `agencianextweb` seria *selecionado* mas
   **nunca atualizado** (imobiliaria/0.5 não é `outro` nem `<0.5`). Novo guard:
   `classification_source IS DISTINCT FROM 'manual' AND ... IS DISTINCT FROM 'ai'`.
   Vale para os dois chamadores (`enrich_all.py` **e** o scan worker KL-47A) — a IA
   passa a corrigir qualquer regex confiante, mantendo manual/ai intactos.

### `scripts/enrich_all.py` — helpers puros (espelham a nova lógica)

- **`enrichment_group`**: G2 = perfil + `source ∉ {ai, manual}`.
- **`needs_ai`**: qualquer alvo classificado por **regex** → sempre chama a IA (antes:
  só `outro`/confiança baixa/sem descrição). Alvos `ai`/`manual` só chamam a IA se
  faltar **descrição** (gera a descrição sem tocar o setor).
- **`should_update_sector`**: removida a guarda "regex já acertou com confiança"; agora
  reclassifica **qualquer** regex desde que a IA volte com setor real (≠`outro`) e
  confiança ≥ 0.7. **Preserva `manual` e `ai`**.

`scanner/main.py`: comentário do `ai_update_classification` atualizado (não muda o gate
`sector≠outro AND conf>0.7` do worker; quem mudou foi o guard do SQL).

---

## Impacto e trade-offs

- **Cobertura total:** o batch agora varre **todo** o banco (menos manual/ai) — todos
  os ~13k regex passam pela IA uma vez. Ao reclassificar, viram `source='ai'` e saem do
  G2 (idempotente daí em diante).
- **Cauda residual:** alvos que a IA **não** consegue classificar com confiança (volta
  `outro`/conf<0.7) permanecem `source='auto'` e serão re-selecionados em execuções
  futuras — mesmo comportamento da lógica antiga para o `outro`. Custo controlado por
  `--limit`/`--ai-delay` (~US$0,001/alvo).
- **Preserva o trabalho humano/IA:** `manual` nunca é tocado; `ai` não é re-sobrescrito.

---

## Testes (`tests/test_enrich_all.py`)

- Atualizados: `test_select_group2_any_regex_classification` (inclui o caso
  agencianextweb: `domain/imobiliaria/0.5` → G2), `test_group2_preserves_ai_and_manual`,
  `test_needs_ai_regex_always_reviewed`, `test_ai_updates_regex_but_preserves_ai_and_manual`.
- `pytest` → **513 passed, 1 skipped**. SQL validada no dialeto Postgres (`sqlglot`).

## Arquivos

- **Editado:** `discovery/store.py`, `scripts/enrich_all.py`, `scanner/main.py`,
  `tests/test_enrich_all.py`, `claude.md` (seções 30 e 37).
- **Novo:** este relatório.
