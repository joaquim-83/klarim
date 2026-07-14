# Fix — Template do alert worker para o modelo freemium

## Problema

O **alert worker está pausado desde 10/07** porque o template `alert.html` ainda usava
a linguagem do modelo antigo de cobrança (CTA "Veja o relatório", fluxo de R$ 19). O
template de "Perfil público consultado" (`profile_view.html`) já está no modelo freemium
e vem gerando contas orgânicas. Para reativar o alert worker, os templates de alerta
precisam usar a mesma linguagem e CTA.

## O que foi feito (só template HTML)

### `notifier/templates/alert.html`
- **Corpo:** "Verificamos a segurança do site {site_name} e encontramos {fail_count}
  ponto(s) de atenção." + bloco novo dizendo que a **verificação é gratuita** e convidando
  a criar conta para acompanhar o score.
- **CTA:** **"Criar conta e monitorar →"** apontando para
  `https://klarim.net/cadastrar?utm_source=klarim&utm_medium=email&utm_campaign=alerta`
  (era "Veja o relatório" → `result_link`).
- **Footer:** disclaimer **"O Klarim avalia a segurança do site, não do negócio"** + nota
  de análise 100% passiva + link de unsubscribe.
- Zero menção a preço/pagamento/relatório pago.

### `notifier/templates/alert_score100.html`
- **Corpo celebratório:** "Parabéns! O site {site_name} alcançou nota máxima em segurança
  digital." + convite a criar conta para monitorar e manter a nota. Removida a linguagem
  antiga ("análise completa gratuita", "15/29 verificações", "Sites Monitorados").
- **CTA:** **"Criar conta e monitorar →"** → `/cadastrar` (botão verde, tom celebratório).
- **Footer:** mesmo disclaimer + unsubscribe.

### O que **não** mudou (conforme o pedido)
- **Lógica do alert worker:** intacta. O `_alert_params` ainda computa `result_link`/
  `bonus_token` (agora não usados pelos templates) e `send_alert_for_target` ainda concede
  o crédito de score 100 (`grant_full_scan_credit`).
- **Assunto do e-mail:** inalterado (`{site} — resultado da avaliação de segurança`;
  score 100 → `{site} — parabéns, nota máxima em segurança`).
- **`profile_view.html`:** não tocado (só usado como referência).
- **Worker não reativado** — isso é feito manualmente via MCP após o deploy.

## Testes

- **`tests/test_alert_template_freemium.py`** (10 testes): renderiza os dois templates via
  Jinja e valida — sem `R$`/`pagar`/`comprar`/`relatório completo`/`desbloquear`/`preço`;
  CTA "Criar conta e monitorar" → `/cadastrar`; menção a "gratuita"; disclaimer + unsubscribe
  preservados; variáveis (`site_name`, `fail_count`, `score`) resolvidas (sem `{{`/`{%`
  pendentes); score 100 celebratório (parabéns/nota máxima, sem "pontos de atenção").
- Atualizados os testes existentes que fixavam a CTA antiga: `test_notifier.py` (2) e
  `test_kl31_score100.py` (2) — o assunto de score 100 e o `_is_score100` seguem inalterados.

**Suíte offline completa:** verde (ver execução do CI).

## Arquivos

**Novos:** `tests/test_alert_template_freemium.py`.
**Alterados:** `notifier/templates/alert.html`, `notifier/templates/alert_score100.html`,
`tests/test_notifier.py`, `tests/test_kl31_score100.py`, `claude.md`.

## Próximo passo (operação)

Após o deploy verde, **reativar o alert worker manualmente via MCP** (`resume_worker`
`alert`) — o `STOP_ALERTS`/`worker_control` continua sendo o controle.
