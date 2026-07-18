# KL-59 — Ativar Google Safe Browsing API

**Sem card Jira** — config na VM (sem código) · **Data:** 2026-07-18

---

## O que foi feito

O check `check_29_safe_browsing.py` já existia (`scanner/checks/`), gated em
`GOOGLE_SAFE_BROWSING_KEY` — sem a key, retornava INCONCLUSO. Faltava só configurar a key.

1. **`.env` da VM** (`/opt/klarim/.env`): adicionada a linha `GOOGLE_SAFE_BROWSING_KEY=…`
   (append idempotente — guardado por `grep`, 1 linha, sem duplicata). A key vive **só** no
   `.env` (gitignored) — **nunca** no código/git.
2. **Recreação dos containers que rodam scans:** `docker compose up -d api worker discovery`
   (não `restart` — `restart` NÃO relê o `env_file`; só `up -d` recria com a nova env var).
   Os três serviços usam `env_file: .env`, então a key chega ao ambiente do container.
   Confirmado: `printenv GOOGLE_SAFE_BROWSING_KEY` presente no `klarim-api-1`.

## Validação

`check_29` executado direto no `klarim-api-1` (bypassa o cache de scan):

| URL | Antes | Depois |
|---|---|---|
| https://klarim.net | INCONCLUSO (key não configurada) | **PASS** |
| https://www.google.com | INCONCLUSO | **PASS** |

→ KL-59 funcional.

## Decisões

- **Não flushei `scan:*`** no Redis. O código do check não mudou (só a env var), e um flush
  dispararia uma onda de re-scans (contra o princípio "não mudar carga operacional" do KL-77).
  Scans em cache seguem INCONCLUSO no Safe Browsing até o rescan natural (worker 24h / ≥30d);
  scans novos já pontuam o check.
- **Correção do runbook:** o card sugeria `docker compose restart scan` — mas (a) não há
  serviço `scan` (o worker de scan é `worker`); (b) `restart` não relê o `env_file`. Usei
  `docker compose up -d api worker discovery`.

## Regra respeitada

A API key **não** foi commitada — vive só no `.env` da VM. `claude.md` atualizado com a nota
"Google Safe Browsing API ativa (KL-59, check_29 funcional)" (sem o valor da key).
