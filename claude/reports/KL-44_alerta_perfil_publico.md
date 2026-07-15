# KL-44 — Alerta direciona para o perfil público `/site/{domain}` + anti-loop

**Card:** KL-44 (pré-requisito de conversão)
**Data:** 2026-07-15
**Tipo:** correção de fluxo (CTA do e-mail de alerta) + anti-loop de notificação + diagnóstico

## Problema

O e-mail de alerta levava o usuário para `/cadastrar` (formulário de cadastro num site
desconhecido). O usuário quer **ver o que aconteceu** — score, semáforo, o que melhorar.
Resultado medido: **94,5% de abandono** (6 cadastros em 110 cliques). O destino correto é
`/site/{domain}` (perfil público), onde o usuário **vê o valor** e encontra o CTA
"Reivindicar este site →" que já existe na página.

## O que foi alterado

### Parte 1 — CTA dos templates (apenas href + texto do botão; layout intacto)

| Template | Antes (href / texto) | Depois (href / texto) |
|---|---|---|
| `notifier/templates/alert.html` | `/cadastrar?…utm_campaign=alerta` · "Criar conta e monitorar →" | `/site/{{ site_name }}?…utm_campaign=alerta` · **"Ver score do site →"** |
| `notifier/templates/alert_score100.html` | `/cadastrar?…utm_campaign=alerta` · "Criar conta e monitorar →" | `/site/{{ site_name }}?…utm_campaign=alerta_score100` · **"Ver score do site →"** |

- A variável usada é **`{{ site_name }}`** — é o que o `_alert_params` (em
  `notifier/email_client.py`) passa aos dois templates (`site_name = site_name(target_url)`,
  ou seja o hostname sem `www.`, ex.: `igoove.com`). É o mesmo valor que `/site/{domain}`
  espera (o endpoint `GET /public/profile/{domain}` normaliza `www.`), então o link abre o
  perfil correto.
- **Comparação com `profile_view.html`:** aquele template usa `{{ domain }}` + um
  `{{ cta_url }}` montado no backend; os templates de **alerta** não recebem `domain`/`cta_url`,
  recebem `site_name` — por isso o link usa `{{ site_name }}`. **`profile_view.html` NÃO foi
  alterado** (fora do escopo; é o outro e-mail, o de "perfil consultado").
- **Nada além de href + texto** foi tocado (cores, tabela, estilos inline, disclaimer,
  unsubscribe — tudo preservado). A lógica do alert worker (seleção/batch/envio) **não** foi
  tocada — os templates são hardcoded no HTML, o worker só os renderiza.

### Parte 2 — Anti-loop de notificação de perfil

**Risco:** o dono clicando no link do alerta chega em `/site/{domain}`, cujo SSR chama
`POST /notify/profile-view` → dispararia o e-mail "Alguém consultou seu site" → outro clique →
**loop de e-mail**.

**Solução (server-side, no único ponto de disparo):**
- `api/main.py` — `ProfileViewBody` ganhou o campo opcional `utm_campaign: str = ""`; o
  endpoint `notify_profile_view` retorna cedo **`{"ok": True, "notified": False}`** (sem
  agendar o envio) quando `utm_campaign` começa com **`alerta`** (cobre `alerta` e
  `alerta_score100`).
- `web/src/pages/site/[domain].astro` — o SSR lê `Astro.url.searchParams.get('utm_campaign')`
  e o encaminha no corpo do POST para o backend. **Só isso** mudou na página (uma constante +
  o campo no body); o conteúdo/renderização do perfil **não** foi alterado.

**Por que a regra fica no backend e não só no Astro:** é o ponto único de disparo (single
choke point), é testável no pytest (Parte 5 exige teste do anti-loop) e vale para qualquer
chamador do endpoint. O Astro apenas repassa a origem.

Resultado:
- Visitante orgânico em `/site/meusite.com.br` (sem utm de alerta) → **notifica** o dono ✅
- Dono vindo do e-mail de alerta (`utm_campaign=alerta` / `alerta_score100`) → **não
  notifica** (anti-loop) ✅

### Parte 5 — Testes

- **Atualizados** (CTA antigo → novo) — assertions passaram de `/cadastrar` + "Criar conta e
  monitorar" para `/site/{domain}` + "Ver score do site":
  - `tests/test_alert_template_freemium.py` (2 testes renomeados + docstring)
  - `tests/test_notifier.py` (2 assertions)
  - `tests/test_kl31_score100.py` (2 testes)
- **Novos** (anti-loop) em `tests/test_kl51_f4_profiles.py`:
  - `test_notify_skips_alert_utm` — `utm_campaign=alerta` → `notified:false`, nenhuma tarefa
    agendada.
  - `test_notify_skips_alert_score100_utm` — `alerta_score100` idem.
  - `test_notify_organic_still_notifies` — visita orgânica ainda agenda a notificação.
  - (helper `_capture_spawn` intercepta `_spawn` para verificar o agendamento sem rodar o
    envio.)

## Parte 3 — Diagnóstico das descrições em inglês (só investigação, sem correção)

> **Correção de premissa do card:** o schema real é a tabela **`site_profile`** (singular)
> com a coluna **`description`** — **não** existe `site_profiles.ai_description`. As queries do
> card precisam ser ajustadas (abaixo). **Não há Postgres acessível nesta sessão local** (o
> `.env`/DB de produção vive só na VM), então as contagens têm de ser rodadas na VM; o
> diagnóstico de causa-raiz abaixo é por **análise de código** (conclusivo).

### Causa-raiz (confirmada no código)

1. `scanner/profiler.py::extract_structured_data` (linha ~230) extrai `description` do
   **JSON-LD do próprio site** (`<script type="application/ld+json">`, campo `description`) —
   **sem** verificação de idioma.
2. `scanner/profiler.py::build_profile` (linha 522) grava `profile["description"] =
   structured.get("description")` — direto do scraper.
3. `scanner/ai_enrichment.py::merge_ai_into_profile` (linha 198) aplica a **"regra de ouro"**:
   `if value and not profile.get(field)` — a `description` da IA **só preenche o campo vazio**.

**Conclusão:** quando o site já expõe uma `description` (JSON-LD/Schema.org) **em inglês** — ex.:
sites que embutem/copiam o texto padrão do YouTube ("Enjoy the videos and music that you
love…"), templates em inglês, multinacionais — o scraper grava essa string e a IA **nunca a
sobrescreve** (o campo já está preenchido). O prompt da IA está **correto** (pede PT-BR
explicitamente, `ai_enrichment.py:53`) — portanto **é um problema de scraper/entrada, não do
prompt da IA**.

- **Scraper ou prompt IA?** → **Scraper (entrada)** + a regra de merge "só preenche vazio".
  A IA não é a causa (ela pede e geraria PT-BR, mas é descartada quando o scraper já preencheu).
- **Isolado ou recorrente?** → **Padrão recorrente**: afeta qualquer site cujo JSON-LD/meta
  `description` esteja em inglês (embed de YouTube/Vimeo/Shopify, CMS/template em inglês,
  Schema.org default). Não é caso único.

### Queries corrigidas (rodar na VM — `docker compose exec -T db psql -U <user> -d <db>`)

```sql
-- Quantos perfis têm descrição provavelmente em inglês (heurística)
SELECT COUNT(*) AS total_en
FROM site_profile
WHERE description IS NOT NULL
  AND ( description ILIKE '%enjoy the%' OR description ILIKE '%welcome to%'
     OR description ILIKE '%the best%'  OR description ILIKE '%discover the%'
     OR description ILIKE '%share it all%' OR description ILIKE '% your %' );

-- Exemplos (com o site) para inspeção
SELECT t.url, sp.description
FROM site_profile sp
JOIN targets t ON t.id = sp.target_id
WHERE sp.description IS NOT NULL
  AND sp.description ~ '[A-Za-z]{20,}'
  AND sp.description NOT ILIKE '% site%'
  AND sp.description NOT ILIKE '%empresa%'
LIMIT 20;
```

### Sugestão para o card de correção (separado, fora deste escopo)

Deixar a IA **sobrescrever** a `description` quando o texto do scraper **não estiver em PT-BR**
(detecção de idioma leve) — ou não aceitar `description` do JSON-LD quando o idioma diverge do
esperado. Isso mantém a "regra de ouro" para PT-BR e corrige o caso das descrições em inglês.
**Não implementado aqui** (o card pede só diagnóstico).

## Parte 4 — Validação (greps)

```
alert.html            → /site/ : 1   · /cadastrar : 0
alert_score100.html   → /site/ : 1   · /cadastrar : 0
"Ver score do site"   → alert.html : 1 · alert_score100.html : 1
api/main.py           → if (body.utm_campaign or "").startswith("alerta")  ✔
[domain].astro        → const utmCampaign = … + body { domain, utm_campaign }  ✔
```

## Parte 6 — Deploy e teste em produção

_(preenchido após push + CI verde — ver abaixo.)_

- Commit: `<hash>` — push para `main`.
- GitHub Actions (test + deploy): `<status>`.
- Alerta de teste → target 8172 (igoove.com → jscidinei@gmail.com): `email_id=<id>`.
- Link confirmado no e-mail: `https://klarim.net/site/igoove.com?utm_source=klarim&utm_medium=email&utm_campaign=alerta`.

**Nota:** o alert worker **não** foi pausado nem reativado — o template é atualizado pelo deploy.
