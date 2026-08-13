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

## Pós-deploy
- **Flush do cache público** na VM (só o `/api/tools/stats` cacheia; o MCP é live):
  ```bash
  sudo docker exec klarim-redis-1 redis-cli DEL tools:stats
  ```
- **Validação** (`get_privacy_stats` via MCP — não cacheado, reflete na hora):
  `scanned` > 19.846; números dos indicadores mudam; `avg_privacy_score` pode mudar (base maior).
