# KL-163 — Relatório de deploy (P1 + P2)

**Data:** 11/08/2026 · **Autorizado pelo dono** após aprovação da validação visual.
**Escopo:** P1 (relatório PDF do run do Security Gate) + P2 (endereço estruturado CEP/ViaCEP + KYC
polish), deployados juntos.

---

## 1. Push

- Commit: **`2a121af`** — `feat(KL-163): PDF do run do Security Gate + endereço estruturado
  (CEP/ViaCEP) no KYC`
- Push: `bd5a011..2a121af  main -> main`
- 22 arquivos (inclui o fix do FakeStore em `tests/test_kl153_backend.py`, necessário para o job de
  teste passar com a nova assinatura de `update_user_kyc`).

## 2. CI/CD — GitHub Actions (run **#31512717649**, `2a121af`)

| Job | Status | Duração |
|---|---|---|
| Test | ✅ success | 1m40s |
| Build web (Astro) | ✅ success | 14s |
| Nginx config check | ✅ success | 14s |
| Deploy to GCP VM | ✅ success | 3m56s |
| Security Gate (live, pós-deploy) | ✅ success | 27s |

**Run: completed / success.** O job Security Gate roda o scanner contra o `klarim.net` LIVE depois do
deploy — passou (site no ar, sem falha crítica; o CSP novo não quebrou nada).

## 3. Verificação pós-deploy na VM (`klarim-prod`)

**(1) Schema — `address_data JSONB` existe** (SSH + `\d users`):
```
address_data          | jsonb
```
✅ presente.

**(2) Endpoint PDF responde sem auth = 401** (esperado — `_resolve_gate_account` exige API key/sessão):
```
$ curl -s -o /dev/null -w "%{http_code}" https://klarim.net/api/gate/runs/1/report
401
```
✅ rota ativa e protegida.

**(3) CSP — `viacep.com.br` em `connect-src`**:
```
$ curl -sI https://klarim.net/ | grep -i content-security-policy | grep -o "connect-src[^;]*"
... https://viacep.com.br ...
```
✅ presente (o auto-preenchimento por CEP funciona no site público).

**(4) Containers (sanidade extra)** — 7/7 up; **db/redis preservados** (Up 2 semanas, healthy — decisão
KL-124: `--force-recreate` escopado aos 5 apps), os 5 apps recriados (Up 2 min):
```
klarim-api-1        Up 2 minutes
klarim-astro-1      Up 2 minutes
klarim-db-1         Up 2 weeks (healthy)
klarim-discovery-1  Up 2 minutes (healthy)
klarim-redis-1      Up 2 weeks (healthy)
klarim-web-1        Up 2 minutes
klarim-worker-1     Up 2 minutes
```

## 4. Resultado

**Deploy 100% verde e verificado.** Nenhum step falhou. Sem necessidade de flush de Redis (o
`dashboard-summary`/report não são cacheados; nenhuma mudança em `scoring.py` ou check).

**Observação:** o telefone do KYC aparece "(não verificado)" no admin por design (verificação por SMS
é escopo futuro); a coluna `address` TEXT legada foi preservada (backward compat) e não é usada por
novos KYC.
