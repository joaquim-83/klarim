# KL-31 — Scan completo gratuito para score 100 + convite de monitoramento

**Card:** KL-31 · **Dependências:** KL-22 (29 checks), KL-27 (funil 15/29), KL-29
(monitoramento).

**REGRA INVIOLÁVEL:** zero cobrança no fluxo de score 100. O scan completo é
**gratuito** e o monitoramento é **gratuito**. Cobrança (R$ 19) só existe se o site
**não** passou nos 29 e quer re-verificar após correções.

---

## Fluxo

```
Discovery (15 checks) → score 100 🟢
  → Alert Worker envia e-mail de PARABÉNS (convite, não alerta) + concede crédito
  → cliente clica → /result?bonus=full&t=<token> → vê 15 ✅ + botão gratuito
  → clica → 29 checks SEM cobrança (consome o crédito, uso único)
     ├─ 100/29 → PDFs + oferta de monitoramento gratuito (selo)
     └─ <100/29 → FAILs detalhados + PDFs + "Re-verificar após correções — R$ 19"
```

## 1. Crédito (email + URL) — `scan_credits`

Colunas novas: **`full_scan_credits`** (INTEGER) + **`full_scan_url`** (TEXT) — o bônus
é vinculado ao par (e-mail, URL), não transferível, não acumula (fixa em 1). Métodos:
`grant_full_scan_credit(email, url)` (upsert, troca a URL se o mesmo e-mail ganhar de
novo) e `consume_full_scan_credit(email, url)` (uso único, casamento de URL tolerante
a caixa/'/').

## 2. E-mail condicional (Alert Worker)

- **Elegibilidade ampliada:** `get_eligible_targets_for_alert` agora inclui
  `s.fail_count > 0` **OU** `(s.score = 100 AND s.semaphore = 'verde')` — antes um site
  100 (0 falhas) nunca era contatado.
- **`_alert_params` (notifier):** `score == 100 && verde` → template
  **`alert_score100.html`** + assunto `{domínio} — parabéns, nota máxima em segurança`
  + CTA "Fazer análise completa gratuita" com o link `?bonus=full&t=<token>`. Senão o
  alerta normal (KL-27), inalterado.
- **Concessão do crédito:** ao enviar o convite (batch e envio único), o worker chama
  `grant_full_scan_credit(email, url)`. O link carrega um **token de bônus**
  (`bonus_scan_token`, HMAC, `full=false, bonus=true`, **TTL 30 dias** — o e-mail pode
  ser clicado depois). Formato idêntico ao `api._make_scan_token` (verificado
  cross-módulo nos testes).

## 3. Autorização do scan completo (`/scan/summary`)

Prioridade (KL-31): **admin** → **charge_id pago** → **bônus** (`use_bonus` + crédito
no banco) → **re-verificação** (token `full`) → **básico (15)**. O token de bônus
**sozinho não basta**: o scan completo só roda quando o frontend pede (`use_bonus=true`,
botão) **e** `consume_full_scan_credit` confirma o crédito no banco — que é consumido ali
(uso único). Sem crédito → cai no básico (15 + R$ 19). `/scan/check-credit` devolve
`full_scan_credits` + `can_full_scan_free`.

## 4. Frontend (`Result.jsx`)

- Lê `?bonus=full&t=<token>`, guarda o token **antes** do fetch inicial. Visão inicial:
  15 ✅ + bloco "🎁 Você ganhou a análise completa" com botão **verde gratuito** (sem
  R$ 19).
- Clique → tela de progresso "Executando análise completa…" → `fetchSummary(url,
  {useBonus:true})` → resultado completo (29). **100/29** → oferta de monitoramento +
  PDFs; **<100/29** → FAILs com evidência/impacto/correção + PDFs + CTA
  "Re-verificar após correções — R$ 19" (→ `/pay`).
- Bônus já usado → mensagem + fallback ao scan pago.

## 5. Monitoramento a cada 30 dias

`MONITOR_INTERVAL_DAYS` padrão **30** (era 7). `.env.example` e VM atualizados.

## 6. Tracking (KL-21)

Eventos novos (registrados no `_KNOWN_EVENTS`): `score100_full_scan_started`,
`score100_full_scan_completed`, `score100_monitoring_offered`,
`score100_monitoring_accepted`. O envio do e-mail de score 100 já é registrado no
`alert_log` (score=100).

## 7. Segurança do bônus

- Crédito vinculado a **(e-mail, URL)**, consumido **ao rodar** o scan (não ao
  visualizar — a visão inicial de 15 checks não consome).
- `bonus=full` na URL **não basta**: o backend exige o crédito no banco (consume).
- Uso único (após consumir, `full_scan_credits=0` → próximas tentativas caem no R$ 19).
- Token de bônus HMAC-assinado (email+url), TTL 30d — mesmo interceptado, vale só 1 scan
  completo de um site que já é 100 (baixo valor) e depois o crédito acaba.

## 8. Testes

`tests/test_kl31_score100.py` (8): template condicional (parabéns/normal), `_is_score100`,
token de bônus cross-verificado na API, `/scan/summary` com `use_bonus` consome + completo,
sem crédito cai no básico, token de bônus sem `use_bonus` = básico (não consome).
`test_alert_worker` atualizado (FakeMailer/FakeStore com `bonus_token`/crédito). Suíte
completa verde. Build do frontend limpo.

## 9. Validação (spec)

Zero cobrança no fluxo 100 ✅; e-mail diferenciado ✅; crédito por email+URL ✅;
`can_full_scan_free` ✅; `/scan/summary` aceita o bônus ✅; botão gratuito (sem R$ 19)
✅; 100/29 → monitoramento ✅; <100/29 → FAILs + R$ 19 ✅; crédito uso único ✅;
`MONITOR_INTERVAL_DAYS=30` ✅; eventos de tracking ✅.

## Arquivos

**Backend:** `discovery/store.py` (colunas + métodos + eligibilidade), `api/main.py`
(`_make_scan_token` bonus/ttl, check-credit, summary use_bonus, eventos),
`discovery/alert_worker.py` (token de bônus, e-mail condicional, grant),
`discovery/rescan_worker.py` (30 dias), `notifier/email_client.py`
(`_alert_params`/`utm_result_link` condicionais) + `templates/alert_score100.html`.
**Frontend:** `lib/api.js` (`fetchSummary` use_bonus), `pages/Result.jsx` (fluxo do
bônus). **Config/docs:** `.env.example`, `claude.md`, `README.md`, este relatório.
**Testes:** `test_kl31_score100.py`, `test_alert_worker.py`.
