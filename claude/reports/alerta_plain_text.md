# Alerta em plain text + verificação do `ALERT_DAILY_LIMIT` no painel Config

**Data:** 2026-07-16
**Card:** KL-44 (trilha de reputação/warmup — isolamento `klarimscan.com`)
**Motivo:** os alertas usavam template HTML (dark mode, botões, cards) com cara de
e-mail marketing e caíam no spam. Trocamos os e-mails **proativos** para **texto puro**.

---

## Parte 1 — Alertas e notificação de perfil em plain text ✅

### O que mudou (`notifier/email_client.py`)

Os e-mails **proativos** (cold, remetente `klarimscan.com`) agora saem em **texto puro**
(`text` no payload Resend, **sem `html`**):

- **Novos helpers puros** (testáveis offline, sem Jinja):
  - `proactive_profile_link(domain, campaign)` → `https://klarim.net/site/{domain}?utm_source=klarim&utm_medium=email&utm_campaign=<campaign>`
  - `alert_subject(domain, is_score100)` → assunto do alerta (normal / parabéns).
  - `build_alert_text(domain, score, unsubscribe_url, is_score100)` → corpo do alerta.
  - `build_profile_view_text(domain, score, unsubscribe_url)` → corpo da notificação de perfil.
  - `_unsub_line(...)` → rodapé de descadastro, **omitido** quando não há link (evita "None").
- **`_alert_params`** reescrito: retorna `{from, to, subject, text}` (era `{…, html}`).
  Score 100 verde → assunto/corpo de **parabéns**; senão o alerta normal. Assinatura
  inalterada (`fail_count`/`severity_counts`/`risk_messages`/`target_id`/`bonus_token`
  seguem aceitos, mas o corpo em texto não os usa) → **`send_alert` e `send_alert_batch`
  não precisaram mudar**.
- **`send_profile_view`** reescrito para `text`. **Assunto mudou** de "Alguém verificou…"
  para **"Alguém consultou a segurança do site {domain}"** (spec da tarefa).

### Conteúdo (conforme a spec)

| E-mail | Assunto | CTA (link) |
|---|---|---|
| Alerta normal | `Alguém verificou a segurança do site {domain}` | `/site/{domain}?…utm_campaign=alerta` |
| Alerta score 100 | `Parabéns! O site {domain} alcançou nota máxima em segurança` | `/site/{domain}?…utm_campaign=alerta_score100` |
| Perfil consultado | `Alguém consultou a segurança do site {domain}` | `/site/{domain}?…utm_campaign=profile_view` |

Todos os corpos: saudação "Olá,", explicação da natureza gratuita/passiva do Klarim,
convite a criar conta, assinatura `-- / Klarim Scanner / klarimscan.com` e a linha de
descadastro (`{unsubscribe_url}`) quando há `UNSUBSCRIBE_SECRET`.

### Decisões / observações

- **Templates HTML mantidos** (`alert.html`, `alert_score100.html`, `profile_view.html`)
  como referência — apenas deixaram de ser usados no envio (os testes que renderizam o
  template diretamente continuam passando).
- **Outros e-mails inalterados** (transacionais/HTML): evolução (rescan), vigília,
  verificação, recuperação, relatório, monitoramento, conta.
- **Regressão intencional do KL-31:** o link de bônus (`?bonus=full&t=<token>`) saiu do
  corpo do alerta score 100 — o CTA agora é o perfil público `/site/{domain}`. O
  mecanismo de token (`bonus_scan_token`) segue existindo (não removido); apenas não é
  mais exposto no e-mail. Sem impacto real no modelo freemium (paywall off, 48 checks
  já são gratuitos).
- **Segurança:** o corpo é texto puro (sem HTML → sem superfície de injeção no e-mail);
  o link de descadastro continua sendo HMAC (`UNSUBSCRIBE_SECRET`); proativo continua
  **respeitando a blocklist** e registrando no `email_log` (KL-24/62). Nada muda na
  autenticação/validação dos endpoints.

---

## Parte 2 — `ALERT_DAILY_LIMIT` no painel Config: **já estava editável** ⚠️

**Diagnóstico diferente do previsto na tarefa.** A premissa ("provavelmente não foi
adicionado ao `_CONFIG_PARAMS`") está **desatualizada** — o parâmetro **já existe e é
editável** desde o commit `2070442` (migração klarimscan.com):

```python
# api/main.py:3708
"ALERT_DAILY_LIMIT": {"label": "Limite diário de alertas (warmup)",
                      "default": "5000", "min": 0, "max": 50000, "unit": "e-mails/dia"},
```

Verificações feitas:
- **`GET /admin/config`** injeta `type: "int"` para **todos** os params e itera o dict
  inteiro → `ALERT_DAILY_LIMIT` é retornado.
- **`PUT /admin/config/ALERT_DAILY_LIMIT`** valida int + faixa (0–50000) para qualquer
  chave da whitelist → o valor é gravado em `admin_settings` (banco > .env > default).
- **Frontend** `web/src/components/admin/ConfigPage.jsx` renderiza os params de forma
  **genérica** (`(cfg.data?.params || []).map(...)`) → aparece automaticamente.
- Já existe teste de presença: `tests/test_alert_sender_migration.py::test_alert_daily_limit_is_editable`.

**Portanto NÃO adicionei o parâmetro de novo** (seria uma chave duplicada no dict). A
premissa provavelmente vinha de um snapshot de produção anterior ao deploy da migração
klarimscan; hoje o campo já está no painel.

### Ajuste do valor para 3000 — **ação operacional do dono** (não é código)

O valor efetivo hoje é o do `.env` da VM (`ALERT_DAILY_LIMIT=30`, warmup). Para subir
para **3000**, o caminho preferido é o **painel** (`/painel/config` → editar "Limite
diário de alertas") — isso grava em `admin_settings` (banco) e passa a valer no próximo
ciclo do Alert Worker, sem redeploy. Não fiz esse ajuste porque exige credenciais de
admin/estado de produção que não estão nesta sessão (e alterar `.env` na VM só valeria
até o banco sobrescrever). **Recomendação:** subir gradualmente durante o warmup do
domínio, não saltar direto para o teto.

---

## Parte 3 — Testes ✅

- **Novo `tests/test_alert_plain_text.py`** (10 testes): builders puros (corpo normal /
  score 100 / perfil), assuntos, omissão do descadastro quando ausente, e 3 testes de
  integração confirmando que `send_alert`/`send_profile_view` entregam `text` (sem `html`)
  ao Resend, com o link `/site/{domain}` + UTM.
- **Adaptados** para plain text: `tests/test_kl31_score100.py` (assertivas de `_alert_params`)
  e `tests/test_notifier.py` (batch de alertas).
- **Config:** `test_alert_daily_limit_is_editable` já cobre a presença do parâmetro.
- **Suíte completa offline:** `835 passed, 1 skipped`.

---

## Deploy

Commit em `main`. Pós-deploy (ação do dono):
1. `/painel/config` → confirmar "Limite diário de alertas" editável (já deve estar).
2. Ajustar para **3000** (ou subir gradualmente no warmup).
3. Disparar um alerta de teste (ex.: target 8172, jscidinei@gmail.com) e confirmar que
   chega em **texto puro** na inbox (não spam).

## Arquivos alterados

- `notifier/email_client.py` (helpers de texto puro + `_alert_params` + `send_profile_view`)
- `tests/test_alert_plain_text.py` (novo), `tests/test_kl31_score100.py`, `tests/test_notifier.py`
- `claude.md` (regra de e-mail: alerta agora é plain text)
- `claude/reports/alerta_plain_text.md` (este relatório)
