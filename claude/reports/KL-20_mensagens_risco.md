# KL-20 — Mensagens de risco dinâmicas por falha e setor

**Card:** KL-20 · **Prioridade:** High · **Data:** 2026-07-18
**Dependências:** KL-74 ✅ (páginas/benchmark de setor), KL-55 ✅ (setor)

---

## Contexto

O e-mail de alerta e o boletim eram genéricos, independentes de quais checks falharam. A
base de mensagens por check (linguagem de negócio) **já existia** — `reporter/risk_messages.py`
(`RISK_MESSAGES`, 48 checks; `get_risk_messages`/`get_risk_summary`, KL-20 fase 1). Esta fase
**adiciona a dimensão setorial** (KL-55) + **benchmark** (KL-74) + **CTA duplo**, e integra nas
superfícies. Nada foi duplicado — o módulo existente foi estendido.

---

## 1–3. Módulo (`reporter/risk_messages.py`, estendido)

- **`SECTOR_RISK_MESSAGES`** (por slug real: hotel, juridico, ecommerce, contabilidade,
  restaurante, imobiliaria, consultoria, agencia, clinica) + **`MACRO_RISK_MESSAGES`** (por
  macro-setor: saude, alimentacao, comercio, educacao, servicos, imoveis, turismo, beleza,
  automotivo) + **`DEFAULT_RISK`**. Cada um: `data_risk`/`audience`/`plural`. Lookup **slug >
  macro > default** (`sector_risk_info`) — cobre toda a taxonomia de ~49 slugs com poucos mapas.
  > Correção: os setores do card ("hotelaria"/"saude") são ilustrativos; os slugs reais são
  > `hotel` e o macro `saude` (que agrupa clinica/odontologia/…). Mapeei aos slugs/macros reais.
- **`CHECK_SECTOR_RISK`** — variação setorial da mensagem só onde muda a consequência
  (check_01_https, check_25_form_security, check_28_hibp, check_23_dmarc × setores/macros
  relevantes). Os 48 checks mantêm a mensagem-base de `RISK_MESSAGES`.
- **`build_risk_summary(results, sector, limit)`** → `{risks:[{check_id,message,severity,
  headline,icon}], remaining_count, sector_context, audience, plural}`. Ordena por severidade,
  aplica a variação setorial (slug > macro > base). Sem FAILs → vazio.
- **`build_benchmark_line(score, sector, benchmark)`** — pura (o chamador passa o benchmark do
  `store.sector_benchmark`): score 100 → destaque; sem benchmark → só score; acima/abaixo da
  média → comparação. Gênero-neutro ("Com base em N lojas", "setor de lojas").

## 4–5. Integração nas superfícies

| Superfície | Onde | O que |
|---|---|---|
| **4A/5 E-mail de alerta** | `email_client.build_alert_text` + `_alert_params`/`send_alert_batch`; `alert_worker.build_alert_payload` | até 3 riscos setorizados (linguagem de negócio) + linha de benchmark + **CTA duplo** (perfil + `/setor/{slug}` com UTM). Score 100 → mensagem positiva. O worker computa setor+benchmark (`t.*` já traz o setor) |
| **4B Boletim** | `bulletin.build_owner_bulletin`; `bulletin_worker` | linha de consequência de negócio (`build_risk_summary` limit=1) na "Ação prioritária", antes do texto técnico |
| **4C PDF executivo/técnico** | `reporter/generator` (`generate_*_pdf`/`_build_context` ganham `sector` opcional) + `_safe_pdf`/`_sector_for_url` nos endpoints `/report/*` | mensagens de risco ganham variação setorial quando o setor é conhecido; sem setor → comportamento anterior (retrocompatível) |
| **4D Dashboard** | `GET /account/sites/{id}` (+`risk_summary`/`benchmark`/`benchmark_line`); `SiteDetail.jsx` | nova seção "Riscos para o seu negócio" (ícone + headline + consequência de negócio + benchmark) |

---

## Exemplo de e-mail gerado (ecommerce, score 58)

```
Olá,

Score: 58/100 — abaixo da média do setor (70). Há espaço para melhorar.

O que isso pode significar para o seu negócio:
⚠ Clientes que compram no seu site enviam dados de pagamento sem criptografia.
⚠ O formulário de compra do seu site envia dados de pagamento sem criptografia.
⚠ Golpistas podem enviar e-mails como se fossem da sua loja aos seus clientes.
E mais 2 itens que podem ser melhorados.

Veja seu resultado completo:
https://klarim.net/site/lojaexemplo.com.br?utm_source=klarim&utm_medium=email&utm_campaign=alerta

Compare com o setor de lojas:
https://klarim.net/setor/ecommerce?utm_source=klarim&utm_medium=email&utm_campaign=alerta

O Klarim é uma ferramenta gratuita ... e mais 48 pontos.
Se este é o seu site, crie uma conta gratuita ...
--
Klarim Scanner / klarimscan.com
Não quer receber mais avisos? https://klarim.net/unsub?...
```

Linguagem de negócio (sem "HSTS ausente"), sem menção a multa, plain text, máx 3 riscos.

---

## Testes

`tests/test_kl20_risk_messages.py` (13): cobertura dos 48 checks, top-N + "e mais N",
setor desconhecido → default, override setorial (ecommerce → pagamento; setor sem override →
base), fallback por macro (odontologia → saude), benchmark acima/abaixo/100/sem-benchmark,
e-mail com riscos setoriais + CTA duplo + sem jargão, score 100 positivo, fallback genérico.

`pytest` → **1024 passed, 1 skipped**. Build Astro verde. (Atualizei `test_alert_plain_text`
para a nova redação do corpo genérico.)

## Regras respeitadas

Linguagem de negócio; **sem valores de multa**; plain text; **máx 3 riscos**/e-mail; disclaimer
LGPD mantido nas mensagens que o citam; UTM por campanha (analytics KL-57 — perfil vs setor).

## Decisões

- **Estendi** `reporter/risk_messages.py` em vez de criar `scanner/risk_messages.py` (o card
  sugeria o novo arquivo, mas duplicaria os 48 checks já existentes; o módulo é puro e já é
  importado por reporter/API/e-mails/workers).
- `build_benchmark_line` é **pura** (recebe o benchmark) — o módulo não toca o banco.
- PDF setorial só nos endpoints `/report/*` (que fazem lookup por URL); os caminhos anônimos/
  recovery seguem sem setor (retrocompatível) — já usam a mensagem-base de negócio.
