# KL-51 Fase 5 — Enriquecimento de perfil em TODO scan

**Data:** 2026-07-13
**Card:** KL-51 (fase 5)
**Status:** entregue, deploy verde

---

## 1. Problema

Scans feitos **manualmente** no site (via `GET /scan/summary`) — e também o fluxo
admin — **não geravam `site_profile` nem classificação CNAE** (`target_classifications`).
Só o **scan worker** automático (descoberta) enriquecia o alvo, e **nem ele** gravava os
CNAEs: até aqui, apenas o script `scripts/enrich_all.py` escrevia `target_classifications`.

Consequência direta: o perfil público SEO `/site/{dominio}` (entregue no KL-51 f4) saía
**vazio** (sem descrição, sem tags, sem CNAEs) para qualquer site escaneado pelo caminho
público/manual — exatamente o caminho que gera a maior parte do tráfego orgânico.

**Requisito:** todo scan — do worker, do `/scan/summary` ou de qualquer outro caminho —
deve gerar o perfil **completo**:

1. Profiler (crawl multi-page + `build_profile`, KL-50) → `site_profile`.
2. Enriquecimento por IA (setor + descrição + tags + `business_type`, KL-47A/54).
3. Gravação dos CNAEs (`target_classifications`, KL-55).

**Restrição crítica:** o `/scan/summary` é **síncrono** (~60s). Profiler + IA somam
~10–20s. Não pode estourar o timeout de 180s.

---

## 2. Solução

### 2.1. Módulo compartilhado — `scanner/enrichment.py` (novo)

Extraí a lógica de enriquecimento (que vivia acoplada ao scan worker em `scanner/main.py`)
para um módulo **compartilhado**, para que o worker e a API chamem a **mesma** função:

```python
async def enrich_profile(store, target_id, url, security_score=None) -> None
```

- Faz o GET da homepage, resolve MX/NS (`dns_util`, em `asyncio.to_thread`), chama
  `profiler.build_profile` (crawl multi-page + parsers do KL-50).
- Chama `_ai_enrich`, que roda a IA (GPT-4o mini, KL-47A) **só quando** há
  `OPENAI_API_KEY` e homepage_html:
  - complementa o perfil (`merge_ai_into_profile`, só campos vazios — a regra de ouro
    do KL-47A);
  - refina o setor via `store.ai_update_classification` só em alvo forte
    (`sector != 'outro' and conf > 0.7`), preservando `manual`/`ai` (KL-54);
  - **grava os CNAEs da IA** em `target_classifications` com `source='ai'` — o pedaço
    que faltava no worker. A Receita (`source='receita'`) **nunca** é sobrescrita
    (garantido no `ON CONFLICT` do `upsert_target_classifications`, KL-55).
- Grava `site_profile` (`upsert_site_profile`).
- **Best-effort:** qualquer erro é só logado, nunca derruba scan/worker/request.
- Imports **lazy** (evita ciclo com `api`/`discovery` e não pesa no boot).

### 2.2. Três chamadores, um só ponto de verdade

| Caminho | Onde | Execução |
|---------|------|----------|
| **Scan worker** (descoberta) | `scanner/main.py` (`_worker_loop`) | inline, após salvar o scan |
| **`/scan/summary`** (público + logado) | `api/main.py::_ingest_scan_bg` | **background** (`_spawn`, após a resposta) |
| **`/admin/scan-and-report`** | `api/main.py::api_admin_scan_and_report` | **background** (`_spawn`) |

O caminho **pago / re-verificação** ingere pelo mesmo `_ingest_scan_bg`, então também
fica coberto.

**Timeout resolvido pela arquitetura, não por gambiarra:** o `_ingest_scan_bg` **já**
roda fora do caminho síncrono do `/scan/summary` — ele é disparado por `_spawn`
(fire-and-forget) **depois** que o scan já respondeu ao usuário (o gancho existente do
KL-17 que registra o scan no banco só na *cache miss*). Logo, os ~10–20s do profiler+IA
**não** entram no tempo de resposta nem chegam perto do timeout de 180s. Nada de rodar o
scan mais lento.

### 2.3. Limpeza em `scanner/main.py`

Removidas as funções locais `_enrich_profile` / `_ai_enrich_profile` (−61 linhas) — a
versão local da IA **não** gravava CNAE. Agora o worker importa e chama
`enrich_profile` do módulo compartilhado (+2 linhas). Nenhum teste referenciava as
funções removidas.

---

## 3. Arquivos alterados

| Arquivo | Δ | O quê |
|---------|---|-------|
| `scanner/enrichment.py` | **novo** | `enrich_profile` + `_ai_enrich` (com gravação de CNAE) compartilhados |
| `scanner/main.py` | +2 / −61 | importa/chama o módulo compartilhado; remove as funções locais |
| `api/main.py` | +14 / −1 | `_ingest_scan_bg` e admin chamam `enrich_profile` após o ingest |
| `claude.md` | +28 | seção "Fase 5" documentando o fluxo |
| `tests/test_kl51_f5_enrichment.py` | **novo** | 3 testes offline (mocks) do módulo compartilhado |

---

## 4. Testes

`tests/test_kl51_f5_enrichment.py` (offline, mocks de `fetch`/`dns_util`/`build_profile`/
`ai_enrich`, sem rede nem IA):

- `test_enrich_writes_profile_sector_and_cnaes` — grava `site_profile`, refina setor
  (`hotel`, 0.9) e grava CNAE (`55.10-8`, `source='ai'`, `rank=1`).
- `test_enrich_no_cnae_when_ai_returns_none` — IA sem CNAE ⇒ nenhuma classificação
  gravada; setor fraco (`outro`) ⇒ não atualiza.
- `test_enrich_is_best_effort` — store que explode no upsert **não** propaga o erro.

```
tests/test_kl51_f5_enrichment.py .................. 3 passed
suítes relacionadas (ai_enrichment, enrich_all, kl51_f4, kl55_cnae, profiler,
  f5) ............................................... 82 passed in 149s
```

Build local do Astro não afetado (nenhuma mudança em `web/`).

---

## 5. Verificação em produção

Scan manual de um site novo no `klarim.net` → conferir que `/site/{dominio}` mostra o
perfil completo (descrição em linguagem natural, tags, CNAEs). Como o enriquecimento é
**best-effort/background**, o perfil aparece alguns segundos após o scan (o `_spawn`
roda após a resposta). Depurar dados de teste eventuais.

---

## 6. Regras invioláveis respeitadas

- Enriquecimento **best-effort** e **fora do caminho síncrono** do `/scan/summary`
  (background) — nunca atrasa a resposta nem estoura o timeout.
- Perfil comercial **não altera o score de segurança** (KL-50).
- CNAE da IA **nunca** sobrescreve a Receita (KL-55); classificação de setor preserva
  `manual`/`ai` (KL-54).
- 100% passivo: só GET público + DNS público + a chamada opcional à IA (que já era
  usada pelo worker).
