# KL-50 (extensão) — Reprocessamento completo de perfis com IA (`enrich_all.py`)

**Card:** extensão do **KL-50** (perfil comercial) + **KL-47A** (IA) — sem card Jira
dedicado. **Prioridade:** CRÍTICA — desbloqueia a plataforma pública (Astro).
**Problema:** a maioria dos ~13.202 alvos não tem `site_profile` nem classificação por
IA. O `enrich_batch.py` só reprocessa `sem_contato`; os alvos escaneados **antes** do
profiler (KL-50) e da IA (KL-47A) — 1.705 `alerted` + 1.186 `scanned` — e os 7.548 em
setor `outro` (regex errou) ficavam de fora.

---

## O que foi feito

Novo script **`scripts/enrich_all.py`** (o `enrich_batch.py` foi **mantido** intacto).
Cobre **todos** os alvos acessíveis (não `descartado`) que ainda precisam de
enriquecimento, selecionando por prioridade em **3 grupos disjuntos**:

| Grupo | Quem | O que ganha |
|------|------|-------------|
| **G1** | sem `site_profile` (prioridade `alerted` > `scanned` > `sem_contato` > `discovered`) | perfil (crawl + profiler) + IA |
| **G2** | com perfil mas classificação **fraca** e não-IA (`sector='outro'` ou confiança < 0.5) | IA acerta o setor |
| **G3** | com perfil + setor por IA mas **sem descrição** | IA gera a descrição |

Os grupos são mutuamente exclusivos (G1 = sem perfil; G2 = `source ≠ 'ai'`; G3 =
`source = 'ai'`), então não há dupla contagem nem retrabalho.

### Seleção no banco (`discovery/store.py`)

Dois métodos novos, com a SQL centralizada no store (padrão do projeto) e um
LEFT JOIN `site_profile` para evitar N+1:

- **`list_enrichment_candidates(limit, mode)`** — os candidatos ordenados por
  prioridade de grupo, depois por status (`alerted`→`scanned`→`sem_contato`→
  `discovered`), depois por `id`. Traz `profile_id`/`profile_description`/
  `profile_sources` do JOIN.
- **`count_enrichment_groups(mode)`** — panorama do backlog (G1/G2/G3 + total) via
  `COUNT(*) FILTER (WHERE …)`, sem `limit`.
- **`_enrichment_where(mode)`** — a cláusula WHERE compartilhada. `mode`: `all` |
  `only_ai` (só G2/G3, pula o crawl) | `sem_contato` (comportamento antigo). Sempre
  exclui `descartado`.

### Processamento por alvo (`process_target`)

1. **Crawl + profiler** (se `needs_crawl`): `crawl_contact_pages` (8 páginas) →
   `extract_email` (MX-validado) → `build_profile` (headers + MX/NS). Uma única
   baixada da homepage é reusada (`crawl_contact_pages(homepage_html=…)`).
2. **IA** (se `needs_ai`): `ai_enrich` numa única chamada GPT-4o mini →
   `merge_ai_into_profile` (só campos vazios) + setor via `ai_update_classification`
   (que já protege `manual`/regex forte). E-mail achado pela IA passa pela mesma
   validação de MX (KL-24) antes de reativar um `sem_contato`.
3. **Grava o perfil** (`upsert_site_profile`).
4. **E-mail novo em `sem_contato`** → `update_target_email` (volta a `discovered`) +
   enfileira na `klarim:scan_queue`.

### Helpers puros (testáveis offline)

`enrichment_group`, `needs_crawl`, `needs_ai`, `should_update_sector` — funções puras
que **espelham** o SQL da seleção e as regras de decisão. Permitem testar a lógica de
seleção/decisão sem banco (a suíte de CI é hermética).

---

## Flags (CLI)

```
--limit N            # máx. de alvos por execução (padrão 500)
--no-limit           # processa todo o backlog
--only-sem-contato   # só sem_contato (comportamento do enrich_batch)
--only-ai            # só IA, pula o crawl multi-page (assume perfil existente)
--dry-run            # mostra o que faria, sem crawl/IA/gravação
--ai-delay S         # segundos entre chamadas OpenAI (padrão 1.0)
```

Uso na VM (container `api`, tem banco + funções + `OPENAI_API_KEY` do `.env`):

```bash
docker compose exec api python scripts/enrich_all.py --limit 500
docker compose exec api python scripts/enrich_all.py --no-limit
docker compose exec api python scripts/enrich_all.py --dry-run
```

Saída detalhada por alvo + resumo (processados, grupos, crawls, chamadas IA, setores
reclassificados, e-mails novos, custo IA estimado, tempo) em `enrichment_all.log`.

---

## Automação (cron na VM)

```cron
# 2×/dia (06:00 e 18:00 UTC), 500 alvos/execução
0 6,18 * * * cd /opt/klarim && docker compose exec -T api python scripts/enrich_all.py --limit 500 >> /opt/klarim/enrichment_cron.log 2>&1
```

500 × 2/dia = 1.000/dia → ~6 dias para drenar os ~5.500 pendentes. Custo IA ~US$1/dia
(~US$5,50 total). Um worker dedicado contínuo fica para quando o volume justificar.

---

## Garantias / regras invioláveis

- **Idempotente:** a seleção nunca traz um alvo já completo (perfil + setor IA +
  descrição). Rodar N vezes não duplica dados (`upsert_site_profile` é UPSERT).
- **Fail-open:** sem `OPENAI_API_KEY`, a IA é silenciosamente desligada (só
  crawl/profiler). Erro de crawl/IA/banco **por alvo** é logado e **não aborta o
  batch** — resiliência para uma varredura de milhares (mesmo padrão do
  `enrich_batch.py`). *Desvio consciente da spec, que sugeria fail-fast no erro de
  banco: preferi a resiliência por alvo para não perder um lote inteiro por um blip
  transitório.*
- **A IA complementa, nunca sobrescreve:** só preenche campo vazio do perfil; o setor
  só muda em alvo fraco (`ai_update_classification` filtra no SQL) e **nunca** um
  `classification_source='manual'`.
- **E-mail da IA passa por MX** antes de tirar um alvo de `sem_contato` (corta bounce,
  KL-24).
- Tudo **passivo** (GET público + DNS público + 1 chamada OpenAI); o perfil é dado
  **comercial** e **não** altera o score de segurança.

---

## Testes (`tests/test_enrich_all.py`, 18 casos — offline)

Cobre os 10 cenários pedidos + extras:

1–3. Seleção G1 (sem perfil) / G2 (setor `outro`) / G3 (sem descrição).
4. Não seleciona `descartado`. 5. Não seleciona alvo completo. 6. Idempotência.
7. E-mail achado → reativa `sem_contato` + enfileira (store/redis falsos).
8. A IA respeita `manual` (e regex forte / confiança baixa).
9. `--dry-run` não grava nada. 10. `--limit 10` é propagado e respeitado (+ `--no-limit`).
Extras: modos `only_ai`/`only_sem_contato` mapeiam para o store; `needs_crawl`/
`needs_ai` em perfil incompleto / IA desligada.

`pytest tests/test_enrich_all.py` → **18 passed** (~1s). Suíte completa: **493 passed,
1 skipped**. A SQL gerada foi validada no dialeto Postgres (`sqlglot`).

---

## Arquivos

- **Novo:** `scripts/enrich_all.py`, `tests/test_enrich_all.py`,
  `claude/reports/KL-50_enrich-all-reprocessamento.md`.
- **Editado:** `discovery/store.py` (2 métodos + `_enrichment_where`), `README.md`,
  `CLAUDE.md` (seção 30).

## Como deployar / operar

1. Merge → o CI roda `pytest` e faz deploy (o script já fica no container `api`).
2. `--dry-run` primeiro para conferir o backlog e a seleção.
3. Rodar em lotes (`--limit 500`) ou agendar o cron acima.
4. Sem alteração de score → **flush do Redis não é necessário** (o script não mexe em
   `scans`/scoring).
