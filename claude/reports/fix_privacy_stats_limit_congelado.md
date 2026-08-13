# Fix — `privacy_indicator_stats` congelado por `LIMIT 20000` sem `ORDER BY`

**Data:** 2026-08-13 · **Deploy:** direto (fix pequeno).

## Problema
`discovery/store.py::privacy_indicator_stats()` usava `LIMIT 20000` **sem `ORDER BY`**. Em
PostgreSQL, `LIMIT` sem ordenação devolve os registros em **ordem de heap** (fisicamente estável)
→ sempre o MESMO subconjunto. Com a base já em ~51.431 scans com privacidade (14.302 novos nos
últimos 7 dias), a estatística ficou **congelada** há ~5 dias: `get_privacy_stats` (MCP) e o bloco
`privacy` do `/api/tools/stats` sempre retornavam `scanned=19.846` e os mesmos números, ignorando
todos os scans novos.

## Fix (Opção A — remover o `LIMIT`)
```python
# antes: ... AND scanned_at > NOW() - INTERVAL '90 days' LIMIT 20000
# depois:... AND scanned_at > NOW() - INTERVAL '90 days'
```
A query **já** filtra por 90 dias — a janela limita o volume naturalmente (atinge um *plateau* no
regime permanente, quando a entrada ≈ saída após 90 dias). Sem `LIMIT`, lê **todos** os scans da
janela → números completos e sempre atualizados. Off-hot-path: o MCP é chamada admin ocasional e o
`/api/tools/stats` cacheia 24h (Redis `tools:stats`).

**Por que não a Opção B agora:** para uma estatística de inteligência ("X% dos sites BR sem política
de privacidade"), a amostra COMPLETA é mais fiel que uma fatia dos 30k mais recentes (que
super-representa alvos re-escaneados). A leitura atual (~51k linhas JSONB) fica bem abaixo de 5s.
**Follow-up documentado no código:** se um dia esta leitura passar de ~5s, trocar por
`ORDER BY scanned_at DESC LIMIT N` — o índice `idx_scans_date` (em `scans.scanned_at`) já cobre,
tornando a variante barata e sempre fresca.

## Testes
Nenhum teste exercita o SQL real (os testes de `privacy_indicator_stats` usam stubs de `FakeStore`).
Sanidade: `test_kl44_p5_privacy` + `test_kl134_tools` + `test_kl164_privacy_multipage` = **57 passed**;
`import discovery.store` OK.

## Deploy + validação — 2026-08-13 ✅
Commit `56f0ac6` → `main`. CI/CD **run #31694104624 — success** (Test 1m54s · Build · Nginx ·
Deploy 4m14s · Security Gate).

- **MCP `get_privacy_stats`** (live, não cacheado): `scanned` **19.846 → 52.258** ✅ (descongelou);
  `avg_privacy_score` 3.4; indicadores refletindo a base completa (ex.: `privacy_policy` fail 39.368,
  `dsar_channel` fail 51.577). A leitura de ~52k linhas JSONB rodou sem timeout.
- **Flush do cache público** na VM: `sudo docker exec klarim-redis-1 redis-cli DEL tools:stats` → `1`
  (chave removida).
- **`/api/tools/stats`** (após flush, recomputou): `privacy.scanned` **19.846 → 52.260** ✅;
  `privacy_policy_fail_pct` 74,5→**75,3**; `dsar_fail_pct` 99,1→**98,7**; `dpo_fail_pct` 77,4→**77,6**;
  `cached_at` novo.

Nenhum passo falhou.
