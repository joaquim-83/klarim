# KL-141 Prompt 2/4 — Check de credenciais no HTML/JS

**Data:** 2026-08-03

Check 4 do KL-139 — o mais sensível do Security Gate: detecção de **credenciais expostas** no HTML
e no JavaScript público. Ganhou prompt dedicado (comentário 5 do card: cobertura COMPLETA, zero
escopo reduzido). **Módulo:** `security_gate/checks/credentials.py` (novo, dentro do módulo do
Prompt 1); registrado no `engine.py` como check `"credentials"` (agora no default order).

## Regra inviolável (implementada e testada)
**O VALOR da credencial NUNCA é armazenado, logado ou transmitido.** O `Result` só carrega
`tipo + localização (arquivo:linha) + severidade` — nunca `match.group()` nem parte do valor. Há um
teste dedicado (`test_value_never_in_any_result` + `_no_value_leak` em cada detecção) que falha se
qualquer fragmento do segredo aparecer no `detail`/`path`.

## Cobertura (completa)
- **~50 patterns fixos** (regex) em 7 categorias: `payment` (Stripe/Meta), `cloud` (AWS/Google/
  Azure), `baas_database` (Supabase/Firebase/Mongo/Postgres/MySQL/Redis/DATABASE_URL/DB_PASSWORD),
  `ai_ml` (OpenAI/Anthropic/HuggingFace), `auth_identity` (NextAuth/JWT/Session/OAuth/Auth0/Clerk),
  `communication` (SendGrid/Slack/Twilio/Mailgun), `generic` (private keys, GitHub/GitLab/npm/PyPI
  tokens, atribuição genérica `_SECRET|_TOKEN|_KEY|…`). Severidade por pattern (CRÍTICO→LOW).
- **Fontes:** HTML da homepage (inline scripts/meta/data-*/forms — tudo é scaneado como texto) +
  **TODOS** os `<script src>` da MESMA origem (sem limite, com dedup) + até **9 páginas internas**
  linkadas (crawl mesma-origem, 10 páginas no total). CDN de terceiros é ignorado.
- **Entropia (reforço):** atribuição a variável de nome "de segredo" (`secret/apikey/token/…`) cujo
  valor tem entropia de Shannon > 4.5 e comprimento > 20 → MEDIUM. Pega casos que escapam dos
  patterns (ex.: `window.SECRET = "<32 random>"`, que o `_SECRET` com underscore não pega). O gate
  do LHS-de-segredo evita flood em JS minificado (variáveis mangladas não têm nome de segredo).

## Anti-falso-positivo (robusto)
- **Placeholders:** `YOUR_`/`_HERE`/`xxx`/`placeholder`/`changeme`/`TODO`/`REPLACE`/`example`/
  `test_key`/`demo`/`sample`/`...`/`***` (regex `PLACEHOLDER_PATTERNS`) → ignorado.
- **Documentação:** match dentro de `<code>`/`<pre>` ou contexto "exemplo/example/docs" (heurística
  nos 200 chars anteriores) → ignorado (só p/ HTML; num JS o mesmo texto ainda é flagado).
- **Valores curtos / vazios:** os patterns exigem comprimento mínimo; `API_KEY = ""` não dispara.
- `pk_test_` (Stripe publishable test) = LOW (público por design).

## Dogfooding real (klarim.net)
`run_all(..., checks=["credentials"])` real: **score 100, passed=True, 0 findings, ~16s** — o crawl
das 10 páginas + todos os JS minificados (Astro público + bundle Vite do /painel) **não** gerou
nenhum falso positivo (nem CRÍTICO que reprovaria o gate, nem ruído de entropia). Contraste com o
falso-positivo de SPA do Prompt 1 (exposição): aqui os patterns são específicos o suficiente e a
entropia é gateada por LHS-de-segredo.

## Testes — `tests/test_kl141_credentials.py` (29)
Detecção por categoria (Stripe live/pk_test, AWS, Mongo, OpenAI, GitHub PAT, SendGrid, private key,
Slack, Firebase) com asserts de **não-vazamento** do valor; falsos positivos (placeholder xxx/
changeme, `<code>` doc, empty assignment, pk_test=LOW); extração de JS (mesma-origem, CDN ignorado,
`//` protocol-relative) + links internos; crawl (teto 10 páginas, JS dedup, CDN não-buscado, PASS
explícito sem findings); integração `run_all(checks=["credentials"])` (Stripe live → gate reprova);
entropia (secret-LHS alta-entropia → MEDIUM; valor normal e LHS não-secreto → nada). Os 2 testes do
Prompt 1 que fixavam "3 checks" foram atualizados p/ 4 (credentials no default). **2026 pytest passed.**

## Não entregue (Prompts 3-4, por design)
CLI, config YAML/allowlist, formatters, integração GitHub Actions.
