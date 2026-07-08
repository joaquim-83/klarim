# KL-20 — Mensagens de risco dinâmicas por falha (substituir bloco LGPD genérico)

- **Card Jira:** KL-20
- **Data:** 2026-07-08
- **Executor:** Claude CLI (Opus 4.8)
- **Depende de:** KL-4 (PDF), KL-8 (e-mail alerta), KL-5 (frontend), KL-13 (evolução)
- **Commit:** `feat(KL-20): replace generic LGPD block with dynamic risk messages per failure`

---

## Objetivo

O bloco fixo "LGPD — sanções de até R$ 50 milhões" aparecia em tudo,
independente das falhas. PME ignora (a multa nunca vai atingi-la). Trocado por
**riscos concretos** derivados dos FAILs reais de cada scan — o dono reage a
"seu site pode ser usado para golpes", não a artigos de lei.

## Parte 1/2 — `reporter/risk_messages.py`

Módulo **leve** (sem WeasyPrint):

- **`RISK_MESSAGES`** — `{check_id: {headline, risk, icon}}` para os **15 checks**
  (`check_01_https` … `check_15_external_domains`), em linguagem de dono de negócio.
- **`get_risk_messages(report)`** — só FAILs, ordenados por severidade
  (Crítica > Alta > Média > Baixa), **máx 4**. Aceita ScanReport (`.results`), a
  lista de resultados, ou o dict do `to_dict()` (`_extract_results`).
- **`get_risk_summary(risks)`** — frase-resumo por **categoria**: vazamento de
  dados / golpes / invasão / código de terceiros. 1 categoria → "Seu site
  apresenta risco de …"; várias → "… riscos de A, B e C."; só header sem categoria
  → "Seu site não tem proteções básicas…"; sem FAIL → `""`.

**`reporter/__init__.py` virou lazy** (PEP 562 `__getattr__`): importar
`reporter.risk_messages` **não** puxa mais o WeasyPrint — essencial porque os
workers (discovery/alert/rescan) importam o módulo para compor os e-mails e não
devem carregar libs de PDF.

## Parte 3 — PDF executivo

`executive.html`: bloco LGPD destacado **removido** → seção **"⚠ O que pode
acontecer com o seu site"** (resumo + cards `ícone + headline + risco`, só
`{% if risk_messages %}`) + a **LGPD como nota de rodapé** discreta. `_build_context`
injeta `risk_messages`/`risk_summary`.

## Parte 4 — E-mail de alerta

`alert.html`: LGPD removido → **até 3 riscos** concretos + nota LGPD.
`KlarimMailer.send_alert` ganhou `risk_messages`; o `alert_worker` e o helper da API
computam os riscos do `checks_json`/report e passam.

## Parte 5 — Frontend

- `/result` (público): bloco LGPD → seção "O que pode acontecer" (consome
  `risk_messages`/`risk_summary` de `/scan/summary`) + nota LGPD.
- Admin **Escanear**: o resultado inline mostra os mesmos riscos (de
  `/admin/scan-and-report`).

## Parte 6 — E-mails de evolução

`evolution_worsened.html` (LGPD → riscos) e `evolution_unchanged.html` (riscos
quando há FAILs abertos). `send_evolution` ganhou `risk_messages`; `rescan_target`
e o reenvio de pendentes computam e passam. **`improved` sem FAILs → sem riscos**
(só celebração).

## API

`/scan/summary` e `/admin/scan-and-report` passaram a retornar `risk_messages` +
`risk_summary` — o que garante **consistência** (o mesmo `get_risk_messages` roda
para PDF, e-mail e frontend).

## Validação

- **Testes** (`tests/test_risk_messages.py`, 5): 15 checks mapeados; ordenação por
  severidade + limite 4 + PASS ignorado; vazio sem FAIL; frases-resumo por
  categoria; **consistência** (report vs dict → mesmos riscos). Fakes de
  alert/rescan ajustados ao novo kwarg. **Suíte total: 107 passed, 1 skipped.**
- **Render real:** PDF executivo **com** FAILs (250 KB, com a seção de risco) e
  **sem** FAILs (35 KB, seção omitida). Build do frontend OK.
- **Produção (VM):** validado pós-deploy — ver abaixo.

## Validação em produção (pós-deploy) — confirmada

- [x] **`/scan/summary`:** `verdegreen` (86) → `risk_summary` = "Seu site apresenta
      riscos de uso do seu site para golpes e código malicioso vindo de terceiros."
      + `risk_messages` = [🔗 SRI, ⚡ risky_sources]. `klarim.net` (100/100) →
      `risk_messages: []`, `risk_summary: ""`.
- [x] **PDF executivo** (render local): com FAILs → seção "O que pode acontecer"
      (250 KB); sem FAILs → seção omitida (35 KB).
- [x] **E-mail de alerta:** disparo para o inbox do operador → `email_sent:True`
      (o template de alerta renderiza os riscos, sem LGPD genérica).
- [x] **Consistência:** os riscos do `/admin/scan-and-report`
      (`[check_13_sri, check_14_risky_sources]`) são **idênticos** aos do
      `/scan/summary` — mesmo `get_risk_messages` em todas as superfícies.

## Critérios de aceite

- [x] `RISK_MESSAGES` com mensagem concreta para os 15 checks.
- [x] `get_risk_messages()` (FAIL, ordena por severidade, máx 4).
- [x] `get_risk_summary()` por categoria.
- [x] PDF executivo dinâmico + LGPD como nota.
- [x] E-mail de alerta (máx 3) + nota.
- [x] Frontend resultado (+ admin Escanear) + nota.
- [x] E-mails de evolução (improved sem FAILs → sem riscos).
- [x] `/scan/summary` retorna `risk_messages`/`risk_summary`.
- [x] Consistência (mesmo `get_risk_messages` em todas as superfícies).
- [x] Site sem FAILs → sem seção de risco.
- [x] Documentação (`claude.md` §21, `README.md`).
- [x] Relatório em PT-BR.
- [x] Deploy + validação + commit/push.

## Follow-ups

- As categorias do resumo são heurísticas simples por check_id; dá para refinar a
  copy com A/B no futuro.
- `notifier`/`generator` ainda mantêm `LGPD_TEXT`/`LGPD_SHORT` (usados na nota) —
  preservados de propósito.
