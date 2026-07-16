# KL-44 P4 — Vigílias avançadas (uptime, mudanças, phishing) + config do boletim no painel

**Data:** 2026-07-16
**Card:** KL-44 (Guardião Digital), fase P4
**Status:** ✅ Concluído — 900 testes passando, deploy pendente de push

---

## 1. Objetivo

Completar o monitoramento contínuo (vigílias, KL-44 P2) com as **3 vigílias avançadas**
prometidas aos planos Pro/Agency, e trazer a operação do **boletim** (KL-44 P3) para o
painel (liga/desliga + hora sem redeploy):

- **`uptime`** (Pro+) — o site está no ar? Verifica a cada 5–30 min (intervalo do plano).
- **`changes`** (Agency) — o conteúdo/estrutura do site mudou de forma suspeita?
- **`phishing`/typosquat** (Agency) — alguém registrou um domínio parecido com o do cliente?

Tudo **100% passivo** (nenhum payload, nenhuma sondagem ativa) e **sem impacto no score
de segurança** — vigília é monitoramento, não é check.

---

## 2. O que já existia (aproveitado)

- A tabela `plans` **já tinha** as colunas `vigilia_uptime`/`vigilia_changes`/
  `vigilia_phishing` + `uptime_interval_minutes` (provisionadas no P2). Logo, o
  enforcement de plano passou a valer **automaticamente** ao adicionar os tipos a
  `VIGILIA_TYPES` — sem migration.
- O `vigilia_worker` (P2) já fazia enforcement de plano, heartbeat, `worker_control`
  (pausa via MCP) e o dispatcher homogêneo `run_vigilia_check`.
- O discovery worker (`ct_poller`) já consome os CT logs — reusamos o buffer.

---

## 3. Bloco 1 — Uptime

- **`api/vigilias.py`:**
  - `check_uptime(target_url)` — GET honesto (User-Agent `KlarimScanner/1.0`), devolve
    `{ok, status_code, response_time_ms, error}`; `ok` = respondeu com status < 500. Nunca
    levanta.
  - `check_uptime_vigilia(store, domain, last_data)` — máquina de estado anti-spam:
    **3 falhas consecutivas** → alerta crítico "fora do ar" (anti-glitch); depois **1
    alerta por hora** (`last_down_alert_at`); ao voltar, 1 alerta de recuperação com a
    duração da queda (`_fmt_duration`). Estado no `last_data` (`consecutive_failures`,
    `down_since`, `up_since`).
- **Cadência própria (`discovery/vigilia_worker.py`):** uptime **não** entra no ciclo de
  6 h. Um `_uptime_loop` roda a cada **5 min** (`VIGILIA_UPTIME_TICK_SECONDS`),
  processa as vigílias vencidas (`get_due_uptime_vigilias`) e **reagenda cada uma pelo
  intervalo do plano** (`COALESCE(uptime_interval_minutes, 30)` — Pro 30 min, Agency 5 min).
  Mesmo enforcement de plano + `worker_control` do ciclo principal.
- **Store:** `get_due_vigilias` ganhou `AND tipo <> 'uptime'` (uptime sai do ciclo 6 h);
  `get_due_uptime_vigilias(limit)` faz o JOIN com `subscriptions`/`plans` para trazer o
  `interval_minutes`.

## 4. Bloco 2 — Mudanças (changes)

- `_snapshot(text, headers, status_code)` — snapshot leve e barato: hash de conteúdo (16
  chars), tamanho, título, hash dos headers de segurança, contagem de `<script>`/`<form>`.
- `check_changes(store, domain, last_data)` — 1 GET, compara com o snapshot anterior
  (`last_data["snapshot"]`). 1º ciclo só grava o baseline. Alerta só em mudança
  **significativa**: conteúdo >30%, título mudou, headers de segurança mudaram, scripts
  aumentaram (possível injeção), formulários apareceram (possível phishing). Roda no ciclo
  de 6 h.

## 5. Bloco 3 — Phishing / typosquatting

- **`discovery/typosquat.py`** (puro, testável, sem deps): `levenshtein`, `is_typosquat
  (monitored, candidate)` devolve `(tipo, distância)` — `levenshtein` (1–2 no rótulo),
  `homoglyph` (o→0, l→1, rn→m…), `tld_variant` (mesmo nome, TLD diferente). Ignora o
  próprio domínio e nomes curtos (<4 chars, exceto variação de TLD exata) para cortar
  falso-positivo.
- **Detecção event-driven (`discovery/worker.py`):** `_scan_typosquats` compara **todo** o
  buffer de CT logs (mesmo domínios já registrados) contra os domínios monitorados por
  contas com vigília `phishing` ativa (`get_typosquat_monitored_domains`, poucas dezenas)
  e grava os suspeitos em `typosquat_alerts` (idempotente por target+domínio). Best-effort:
  falha nunca derruba o ciclo de descoberta.
- **Notificação (`check_typosquat`):** a vigília `phishing` (ciclo 6 h) lê os suspeitos
  pendentes (`get_pending_typosquats`), monta o alerta e marca como notificados
  (`mark_typosquats_notified`).
- **Tabela `typosquat_alerts`** (`target_id`, `user_id`, `suspicious_domain`,
  `similarity_type`, `distance`, `notified`, `dismissed`, `UNIQUE(target_id,
  suspicious_domain)`).

> **Limitação conhecida:** o CT poller filtra só `.com.br`, então `tld_variant` para
> outros TLDs (`.net`/`.com`) não é capturado pela fonte atual — a detecção prática é
> levenshtein/homoglyph dentro de `.com.br`. Fonte multi-TLD fica para uma fase futura.

## 6. Bloco 4 — Config do boletim no painel + entrega

- **`_CONFIG_PARAMS` (`api/main.py`):** `BULLETIN_ENABLED` (novo tipo **`bool`**) e
  `BULLETIN_HOUR_UTC` (int 0–23). O `GET /admin/config` expõe `type`; o `PUT` valida bool
  (true/false) ou int (faixa). O `bulletin_worker` relê `BULLETIN_HOUR_UTC` do banco por
  ciclo (config ao vivo, `admin_settings` > `.env`) — antes só lia no `__init__`.
- **`web/.../ConfigPage.jsx`:** `ParamRow` ganhou o ramo bool — botão toggle
  ● Ligado / ○ Desligado (chama `configPut(key, 'true'|'false')`).
- **E-mail:** `vigilia_generic.html` (novo template data-driven) atende uptime/changes/
  phishing; `send_vigilia_alert` roteia os 3 tipos para ele (`_VIGILIA_GENERIC`), com
  tags e `email_type` (`vigilia_uptime`/`_changes`/`_phishing`) — proativo, respeita a
  blocklist, Reply-To `scan@klarim.net`, registrado no `email_log`.

## 7. Superfícies de gestão

- **Admin REST:** `GET /admin/typosquat-alerts` (JWT admin) — lista + stats.
- **MCP:** tool nova `get_typosquat_alerts` (leitura, passa pelo `_guard`).
- **Dashboard do usuário (`SiteDetail.jsx`):** seção **"Monitoramento contínuo"** — badge
  🟢 no ar / 🔴 fora do ar + tempo de resposta (uptime) e chips por vigília ativa.

## 8. Testes (`tests/test_kl44_p4_vigilias.py`, 22 casos)

- typosquat puro (levenshtein, homoglyph, tld_variant, nome curto, self, label).
- uptime: 3 falhas→alerta, anti-spam 1/h, recuperação, saudável, `check_uptime` nunca
  levanta.
- changes: baseline, mudança significativa, sem mudança, `_snapshot`.
- typosquat vigília: sem pendentes / com pendentes (marca notificado).
- worker uptime cycle: alerta na 3ª falha, enforcement de plano, pausado.
- template genérico renderiza (uptime/changes/phishing, sem `{{`), dispatcher conhece os
  novos tipos.

**Suite completa: 900 passed, 1 skipped.** (`test_mcp_server` atualizado com a tool nova.)

## 9. Regras invioláveis respeitadas

- **Scanner/vigília 100% passivo** — GET honesto, snapshot por leitura, CT logs públicos;
  nenhuma vigília altera o score de segurança.
- **`contact_email` nunca exposto**; e-mail de vigília é proativo → **respeita a
  blocklist** (KL-24/62); transacional continua via `seguranca@klarim.net`; Reply-To
  `scan@klarim.net`.
- **Anti-spam:** 3 falhas → 1 alerta, e no máximo 1 alerta de down por hora.
- **Enforcement de plano** servidor-autoritativo, **nunca** desativa por erro transiente
  de lookup.
- **CSPRNG**, rate limits e config ao vivo (banco > `.env`) preservados.
- Novos endpoints protegidos (`/admin/typosquat-alerts` sob JWT admin).

## 10. Deploy / pós-deploy

- Sem migration manual — `ensure_schema` cria `typosquat_alerts`. Sem flush de Redis
  (vigília não muda score).
- A vigília **começa pausada** (seed do P2); o dono ativa `uptime`/`phishing`/`changes`
  via MCP (`resume_worker vigilia`) quando quiser ligar.
- `BULLETIN_ENABLED`/`BULLETIN_HOUR_UTC` ajustáveis no painel Config.
