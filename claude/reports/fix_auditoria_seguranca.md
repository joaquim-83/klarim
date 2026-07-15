# Correção dos findings da auditoria de segurança + limpeza de contas de teste

**Data:** 2026-07-15
**Base:** `claude/reports/auditoria_seguranca_2026-07-15.md` (pentest passivo, read-only)
**Escopo:** limpeza das contas de teste do pentest + correção dos findings **F-02** e
**F-03**, hardening de verificação de e-mail no signup, e verificação (report-only) de
**F-04** e **F-06**. Cada correção foi testada antes de avançar.

> **Regra do card:** "Cada correção testada antes de avançar." Parts 4 e 5 são **apenas
> reporte** — nada foi alterado na infra sem confirmação.

---

## Part 0 — Limpeza das contas de teste (URGENTE)

Durante a auditoria (pentest via browser) foram criadas 6 contas de teste. Removidas com
**guarda de timestamp** (`created_at > '2026-07-15T10:00:00Z'`) para **não** tocar em
contas legítimas anteriores:

| e-mail | id |
|---|---|
| bot0@spam.com | 20 |
| bot1@spam.com | 21 |
| bot2@spam.com | 22 |
| bot3@spam.com | 23 |
| bot4@spam.com | 24 |
| reservas@verdegreen.com.br | 25 |

- **Antes:** 14 users. **Depois:** 8 users (`test_remaining = 0`).
- `DELETE FROM users WHERE created_at > '2026-07-15T10:00:00Z'` → CASCADE apagou
  `user_sites`/`subscriptions` dessas contas; `targets`/`scans`/`site_profile`
  **permaneceram** (dados do sistema, não da conta).

---

## Part 1 — F-03 [BAIXO]: `target.id` (PK interna) exposto no perfil público

`GET /api/public/profile/{domain}` devolvia `target.id` (a PK interna), facilitando
enumeração de alvos. O frontend usa o **domínio**, nunca o id — então o campo foi
**removido** do payload público (`api/main.py`, dict `target`):

```python
# KL-44 fix (auditoria F-03): NÃO expor o `target.id` (PK interna) no perfil público —
# o frontend usa o `domain`, não o id; expor ajudava enumeração.
"target": {"url": ..., "domain": domain, "sector": ..., "platform": ...,
           "score": ..., "semaphore": ..., "last_scan_at": ...},   # sem "id"
```

**Teste:** `tests/test_kl51_f4_profiles.py::test_profile_ok_and_privacy` ganhou
`assert "id" not in body["target"]`. ✅ 12 passed.

---

## Part 2 — F-02 [BAIXO]: rate limiting migrado para Redis (com fallback in-memory)

O rate limit era 100% in-memory (`dict`): não é distribuído (não vale entre múltiplos
workers) e **reseta a cada deploy** (a janela some no restart). Migrado para **Redis**
(fixed-window `INCR`+`EXPIRE`), preservando **os limites e as chaves atuais** (decisão do
usuário — mudar só o mecanismo):

- Novo helper `_redis_allow(namespace, key, max_hits, window, fallback_bucket)`:
  incrementa `rate:{namespace}:{key}` no Redis, seta `EXPIRE` na 1ª ocorrência, e é
  **fail-safe** — se o Redis estiver indisponível, degrada para o `_ip_rate_limit`
  in-memory (o mesmo `dict` de antes). Retorna `(allowed, retry_after)`.
- Migrados: **login admin** (`admin_login`, 5/min/IP), **login de usuário** (`user_login`,
  10/min/IP), **forgot** (`forgot`, 3/h por e-mail), **signup** (`signup`, 5/h/IP) e
  **signup verify** (`signup_verify`, 10/10min/IP). Os limites são **idênticos** aos de
  antes; só o backing store mudou.
- Os `dict` (`_login_attempts`, `_signup_attempts`, `_forgot_attempts`, `_reset_attempts`)
  continuam existindo como **fallback** e são usados pela suíte de testes (que roda sem
  Redis) — zero dependência nova de infra para o CI.

**Teste:** toda a suíte de contas/login exercita o caminho de fallback in-memory sem
Redis. ✅ `tests/test_accounts.py` 35 passed.

---

## Part 3 — Hardening: verificação de e-mail no signup (direcionado)

**Gap:** era possível criar conta via API direta (`POST /account/signup`) com o e-mail de
um terceiro — o signup confiava que o e-mail já fora provado no fluxo de scan (KL-25), mas
uma chamada direta pula o scan. **Decisão do usuário: abordagem direcionada** — exigir
código **só quando o e-mail NÃO foi verificado** (nem no scan KL-25, nem na sessão),
**sem** quebrar o fluxo scan→cadastro nem duplicar a verificação do KL-25.

- **`store.email_has_verified_scan(email)`** (novo, `discovery/store.py`): `True` se o
  e-mail já tem uma verificação de scan **confirmada** (`scan_verifications.verified`).
- **`account_signup` reestruturado:**
  - e-mail **já verificado** no scan → cria a conta **direto** (fluxo intacto, sem código).
  - e-mail **não verificado** → gera um código CSPRNG de 6 dígitos, guarda o signup
    **pendente** (senha já com hash + url + tentativas) no Redis (TTL 15 min, fallback
    in-memory), envia por e-mail e responde `{"status": "verification_sent", "email":
    <mascarado>}` — **sem criar a conta ainda**.
- **`POST /account/verify` (novo):** valida o código (constant-time, máx **3 tentativas**,
  TTL 15 min) e só então cria a conta + sessão. Sem serviço de e-mail configurado, o
  caminho não-verificado responde **503** (nunca cria conta silenciosamente).
- **E-mail dedicado** `send_signup_verification_code` (reusa o template
  `verification_code.html`, agora com `purpose`/`expires_label` genéricos — a linha "Use-o
  para escanear" só aparece no fluxo de scan).
- **Frontend** (`web/src/components/account/SignupForm.jsx`): fluxo de 2 passos — quando o
  backend responde `verification_sent`, a UI pede o código de 6 dígitos (com reenviar). O
  cadastro pós-scan (e-mail já verificado) continua **1 passo** (só senha).

**Testes** (novos em `tests/test_accounts.py`): não-verificado → `verification_sent` +
conta não criada; verify com código certo → cria conta + cookie; código errado → 400 e não
cria; verify sem pendência → 400; e-mail já verificado no scan → cria direto sem código;
sem serviço de e-mail → 503. ✅ 35 passed.

---

## Part 4 — F-04 [BAIXO]: senha do Postgres (APENAS REPORTE)

O finding F-04 apontava o **default `change-me`** no `DATABASE_URL` do `docker-compose.yml`
(placeholder do repo). Verificado o `.env` de **produção** na VM (o valor **nunca** foi
impresso — só medida a força):

| Propriedade | Resultado |
|---|---|
| Comprimento | **32 caracteres** |
| Maiúscula / minúscula / dígito / símbolo | **todos presentes** |
| Valor default/fraco (`postgres`/`change-me`/`admin`/…) | **não** |
| Exposição de porta | `klarim-db-1` → **`127.0.0.1:5432`** (localhost-only) |
| Listener 5432/6379 | só `docker-proxy` em **127.0.0.1** (não em `0.0.0.0`) |

**Conclusão:** produção usa senha **forte** (não o default do compose) e o Postgres/Redis
**não estão expostos** externamente (bind em `127.0.0.1`, sem regra de firewall para
5432/6379). **Nenhuma alteração necessária.** *(Sugestão opcional, não aplicada: trocar o
placeholder `change-me` do `docker-compose.yml` por uma nota `# defina no .env` para evitar
que um dev suba local com o default.)*

---

## Part 5 — F-06 [INFO]: firewall do GCP (APENAS REPORTE)

Regras de firewall do projeto `project-b08050df-fa4e-49ac-919`:

| Regra | Porta | Origem | Avaliação |
|---|---|---|---|
| `klarim-allow-http` | tcp:80 | 0.0.0.0/0 | ✅ esperado (web público) |
| `klarim-allow-https` | tcp:443 | 0.0.0.0/0 | ✅ esperado (web público) |
| `default-allow-ssh` | tcp:22 | 0.0.0.0/0 | ⚠️ mundo (mitigado por chave SSH + IAM do `gcloud`) |
| `default-allow-rdp` | tcp:3389 | 0.0.0.0/0 | ⚠️ **regra inútil** — VM Linux, sem serviço RDP |
| `default-allow-icmp` | icmp | 0.0.0.0/0 | ℹ️ baixo risco (ping) |
| `default-allow-internal` | tcp/udp/icmp | 10.128.0.0/9 | ✅ tráfego interno |

**Não há** regra expondo **5432 / 6379 / 8000** — Postgres, Redis e a API só respondem no
host interno / atrás do Nginx. **Recomendações (report-only, não aplicadas — aguardando
confirmação):**
1. **Deletar `default-allow-rdp`** (nenhum serviço RDP na VM Linux; a regra é superfície
   morta).
2. **Restringir `default-allow-ssh`** ao **IAP** (`35.235.240.0/20`) ou a faixas conhecidas,
   em vez de `0.0.0.0/0` — o `gcloud compute ssh` continua funcionando via IAP.

Nenhuma dessas é urgente (SSH está protegido por chave/IAM; RDP não tem serviço atrás), mas
ambas reduzem a superfície de ataque a custo zero.

---

## Part 6 — Testes + deploy

- **Suíte local:** `pytest` — verde (contas 35 passed, perfis 12 passed, + suíte completa).
- **Deploy:** commit dos arquivos da correção + push → CI (Test + Build web + Nginx check +
  Deploy GCP) → verificação de que `/api/public/profile/{domain}` não traz mais `id`.

## Arquivos alterados

- `api/main.py` — `_redis_allow` (F-02) + remoção do `target.id` (F-03) + signup
  direcionado / `/account/verify` / pending-signup helpers (Part 3).
- `discovery/store.py` — `email_has_verified_scan` (Part 3).
- `notifier/email_client.py` + `notifier/templates/verification_code.html` —
  `send_signup_verification_code` + template com `purpose`/`expires_label` (Part 3).
- `web/src/components/account/SignupForm.jsx` — fluxo de 2 passos (Part 3).
- `tests/test_accounts.py`, `tests/conftest.py`, `tests/test_kl51_f4_profiles.py` — testes.
- `claude/reports/fix_auditoria_seguranca.md` — este relatório.

## Findings — status final

| ID | Sev | Finding | Status |
|---|---|---|---|
| F-02 | Baixo | Rate limit in-memory | ✅ **Corrigido** — Redis + fallback in-memory |
| F-03 | Baixo | `target.id` exposto no perfil público | ✅ **Corrigido** — removido do payload |
| F-04 | Baixo | Default `change-me` no compose | ✅ **Verificado** — prod usa senha forte 32c, DB localhost-only |
| F-06 | Info | SSH/RDP em 0.0.0.0 na VM | ✅ **Verificado** — DB não exposto; recomendado remover RDP + IAP no SSH (report-only) |
| — | — | Signup com e-mail de terceiro (gap) | ✅ **Hardening** — verificação direcionada por código |
| F-01 | Info | CSP relaxada no painel | Aceito (decisão documentada) |
| F-05 | Info | SPA `index.html` em paths desconhecidos | Aceito (sem dado sensível) |

**Nenhum finding crítico ou alto — antes ou depois.**
