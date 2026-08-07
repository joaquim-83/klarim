# KL-151 (Prompt 4/4) — Segurança avançada, Enterprise, testes completos + fechamento

## Contexto

Backend (P1), API+CLI+MCP (P2) e Frontend (P3) deployados e verdes. Este último prompt adiciona as
camadas de segurança empresarial, Enterprise (CNPJ + scan de terceiro redigido), audit log, rotação
de key com grace period, rate limit por minuto, e a suíte de integração. **Fecha o KL-151.**

## Schema (P4)

- **`gate_audit_log`** (account_id, key_id, action, target_domain, detail JSONB, ip_address,
  user_agent, created_at) + índices por (account, data) e (action, data).
- **`gate_api_keys.grace_expires_at`** — grace period da rotação.
- **`users.company_cnpj / company_contract_url / enterprise_notes`** — Enterprise.

## 1. Audit log

`api/gate.py::log_gate_audit(account_id, action, request, key_id, domain, detail)` — **fail-safe**
(nunca derruba a ação). Integrado em TODOS os endpoints: `scan`, `scan_blocked`, `key_created`,
`key_regenerated`, `project_created`, `project_verified`, `invite_sent`, `invite_accepted`,
`invite_revoked`, `plan_changed`, `enterprise_updated`. **Regra inviolável: NUNCA guarda o VALOR da
API key — só o prefixo** (`KLM_xxxx`). Leitura: `GET /admin/gate/audit` (todas as contas + filtros
`account_id`/`action`) e `GET /account/gate/audit` (a PRÓPRIA conta, ownership enforcado).

## 2. API key rotation — grace period de 1h

`regenerate-key` agora revoga a antiga com `grace_expires_at = NOW()+1h`
(`revoke_gate_api_keys_with_grace`) e devolve `grace_period_minutes: 60`. `authenticate_api_key`
aceita uma key revogada **dentro do grace** (o CI/CD em andamento não quebra); depois → 401. Uma
revogação hard (sem grace, ex.: bounce/segurança) continua barrando na hora.

## 3. Rate limit por minuto por key

`_enforce_rpm(key_id, plan)` — contador Redis `gate_rpm:{key}:{minuto}`: **free 10 · pro 30 · team
60 · enterprise 120** req/min (além do teto de scans/dia do P2). Fail-open sem Redis. Aplicado a toda
requisição autenticada por API key.

## 4. Enterprise

- **CNPJ/contrato/notas:** `POST /admin/gate/accounts/{id}/enterprise` (`set_enterprise_fields`);
  o CNPJ aparece na lista de contas do admin (`list_gate_dev_accounts`).
- **Scan de terceiro redigido:** um plano com `scan_third_party` (Enterprise) pode escanear um
  domínio **NÃO verificado**, mas `_redact_third_party` remove `path` e o detalhe de
  **credenciais/exposição** — só a **categoria + severidade** do risco vaza (nunca o caminho/segredo
  do terceiro). O score/counts continuam corretos (vêm do report cru; só o detalhe/path são redigidos).
- **Audit obrigatório** com `target_domain` em TODO scan (compliance).

## 5. Revogação de acesso pelo dono

Já removia o `gate_project` do dev (P1). Agora também **avisa o dev por e-mail**
(`send_gate_access_revoked`, transacional `klarim@klarim.net`, texto puro) + audit `invite_revoked`.
O dev perde o acesso: um scan futuro do domínio → 403 (o projeto não existe mais).

## Testes

- **Novo `tests/test_kl151_integration.py`** (+14): grace (ok/expirado/revogado-sem-grace), RPM
  (11º request Free → 429 com Redis mockado), Enterprise (redação de terceiro; terceiro sem
  Enterprise → 403; CNPJ salvo e visível no admin), audit (scan/scan_blocked geram entrada; admin
  lista+filtra; dev vê só o próprio; admin exige auth), plano efetivo trial ativo/expirado.
- P1/P2 ajustados (grace period + `insert_gate_audit` no FakeStore); o teste de regeneração agora
  valida o grace (a key antiga ainda autentica).
- **Store P4 validada contra Postgres 16 real** (grace revoke, audit insert/list/filtros, enterprise
  fields, CNPJ no admin list).
- **Suíte completa: 2181 passed, 1 skipped.**

## Docs

- `CLAUDE.md` §9 (P4 + **KL-151 COMPLETO**), `docs/API.md` (audit + enterprise endpoints),
  `docs/SECURITY.md` (rotação/grace, RPM, redação de terceiro, audit).

## Regras atendidas

1. API key NUNCA no audit (só prefixo) ✓
2. Enterprise scan terceiro redige path/credencial ✓
3. Grace period de 1h na regeneração ✓
4. Audit em toda ação ✓
5. Relatório PT-BR ✓

## KL-151 — COMPLETO

P1 (backend core: contas/keys/planos/projetos/convites) · P2 (API REST de scan + CLI + MCP) ·
P3 (landing + portal do dev + admin de planos) · P4 (audit + grace + RPM + Enterprise). Após o
deploy verde, o card é transicionado para **Done** (transition 41) no Jira.
