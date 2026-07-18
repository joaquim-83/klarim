# FIX — Dedup de domínios duplicados em `targets` e rankings

**Sem card Jira** — fix de integridade de dados · **Data:** 2026-07-18

---

## Problema

`klarim.net` aparecia 4× no ranking do setor Tecnologia — havia múltiplos registros em
`targets` para o mesmo domínio. Causa raiz: `register_target` fazia `ON CONFLICT (url)`,
então URLs variantes do mesmo site (`https://foo`, `https://www.foo`, `http://foo`)
criavam **linhas distintas**. Isso inflava rankings, cross-linking, contagens
("30.000+ sites") e benchmarks.

---

## Fix em dois níveis

### Nível 1 — Dedup nas queries (efeito imediato)

`DISTINCT ON (domain)` (pega a linha de **maior score** por domínio) nas listagens, e
agregação sobre **1 linha por domínio** nas contagens. Métodos corrigidos em
`discovery/store.py`:

| Método | Correção |
|---|---|
| `public_sector_sites` | `DISTINCT ON (t.domain)` em subquery + reordenação externa |
| `public_related_sites` | `DISTINCT ON` no helper `_q` |
| `public_score_100_sites` | `DISTINCT ON` (subquery) |
| `list_sector_ranking` (KL-42) | `DISTINCT ON` (subquery) |
| `public_sector_top_fails` | `DISTINCT ON (t.domain)` (1 scan por domínio) |
| `public_sector_index` | agrega sobre `DISTINCT ON (t.domain)` — count/avg/mediana/distribuição consistentes |
| `public_sector_stats` | idem |
| `public_platform_stats` | `COUNT(DISTINCT domain)` + distribuição sobre 1 linha/domínio |
| `ranking_sectors_summary` (KL-42) | count/avg sobre `DISTINCT ON (t.domain)` |

> **Observação:** os métodos de benchmark cru (`sector_benchmark`, `all_sector_benchmarks`,
> `sector_avg_score`, `global_avg_score`) **não** foram alterados — ficam corretos após a
> limpeza do Nível 2 e a constraint UNIQUE impede recorrência. Também: a normalização de
> variantes `www.foo`↔`foo` como domínios de string distinta é um tema à parte (fora do
> escopo); este fix trata duplicatas de **domínio idêntico** (o caso klarim.net).

### Nível 2 — Limpeza + prevenção da raiz

1. **`register_target` agora deduplica por domínio na origem:** antes de inserir, procura
   um target com o mesmo `domain`; se existe, **atualiza e devolve o id** (preserva
   classificação manual) em vez de criar duplicata. `ON CONFLICT (url)` continua como
   backstop de corrida de URL idêntica.
2. **Endpoint admin de merge** (`api/main.py`, protegido pelo middleware JWT `/admin`):
   - `GET /admin/duplicate-domains` — diagnóstico (não altera nada).
   - `POST /admin/dedup-targets?dry_run=true|false&add_constraint=true` — mergeia.
     `store.dedup_targets(apply, add_constraint)`:
     - **Sobrevivente** = o mais recentemente escaneado (`last_scan_at DESC, id ASC`).
     - **Reaponta as FKs** dos duplicados p/ o sobrevivente. Tabelas RESTRICT (bloqueiam o
       DELETE) e as que guardam dado do usuário: `scans`, `alert_log`, `rescan_log`,
       `monitored_sites`, `site_events`, `email_log`, `shared_reports`, `bulletins`,
       `ownership_verifications`, `user_sites`, `target_classifications`,
       `typosquat_alerts`, `technician_links`. Onde há UNIQUE envolvendo `target_id`
       (`user_sites`, `target_classifications`, `typosquat_alerts`, `technician_links`),
       remove a linha do duplicado que colidiria antes de reapontar. `site_profile`
       (UNIQUE `target_id`): o sobrevivente **adota** o perfil de um duplicado só se não
       tiver; o resto é removido.
     - Recomputa `last_scan_*` do sobrevivente e **deleta** os duplicados.
     - Cria `CREATE UNIQUE INDEX idx_targets_domain_unique ON targets(domain) WHERE domain
       IS NOT NULL AND domain <> ''` — **backstop** contra novas duplicatas.
     - Tudo numa **transação** (`_run` faz commit/rollback atômico).

---

## Validação

**Offline** (`pytest`): **989 passed, 1 skipped** — inclui 4 testes novos (diagnóstico,
dry-run, apply, exigência de auth admin).

**Contra Postgres real** (cluster efêmero, cenário: `klarim.net` ×3 + 2 domínios únicos,
com scans/perfis/CNAEs/posse de usuário num duplicado):
- Nível 1: toda query devolve `klarim.net` **1×**; `sector_index`/`platform_stats`
  contam domínios distintos (3, não 5 linhas).
- Nível 2: `dry_run` não altera; `apply` mergeia 2 duplicados + cria a constraint.
- **Integridade pós-merge:** 1 target sobrevivente, **3 scans preservados**, **posse do
  usuário preservada** (o `user_sites` do duplicado migrou), 1 perfil (UNIQUE respeitado).
- `register_target` de variantes passou a devolver o **mesmo id** (dedup na origem).
- INSERT direto de domínio repetido → **bloqueado** pela UNIQUE.

---

## Execução em produção (2026-07-18)

| Passo | Resultado |
|---|---|
| Deploy do código (Nível 1 + fix `register_target` + endpoints) | ✅ CI verde (Test + Build + Nginx + Deploy) |
| Diagnóstico (`find_duplicate_domains`) | **16 domínios duplicados · 20 linhas extras** (klarim.net ×4, 2 domínios ×3, o resto ×2) |
| `dedup_targets(apply=True, add_constraint=True)` | **16 mergeados · 20 linhas deletadas · constraint criada** |
| Pós-merge: `find_duplicate_domains` | **0 duplicatas restantes** |
| Índice `idx_targets_domain_unique` | ✅ presente |
| Ranking `/setor/tecnologia`: domínios distintos | ✅ `sites` sem domínio repetido (`len == len(set)`); `klarim.net` 1× |

> **Nota:** a 1ª tentativa de `apply` abortou (rollback atômico, DB intacto) por colisão
> **loser↔loser** em `target_classifications` (dois duplicados com o mesmo `cnae_code` que
> o sobrevivente não tinha). Corrigido (commit `aec09a2`, `EXISTS` que mantém 1 linha por
> chave) e re-validado contra Postgres real antes do re-apply. Caches `public:*`/`benchmark:*`
> foram limpos no Redis (db0) pós-merge; a 1ª verificação por `grep` deu "2" — falso alarme:
> `klarim.net` (score 100) aparece **1× no ranking + 1× na vitrine score-100** da mesma
> resposta (seções distintas), não é duplicata.

---

## Ordem seguida

1. Nível 1 (queries) + fix da origem (`register_target`) — deploy imediato (para de gerar
   duplicatas e corrige a exibição).
2. Diagnóstico (`dry_run`) → apply na VM.
3. Constraint UNIQUE criada no apply (após a limpeza).
