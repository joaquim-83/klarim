# KL-2 — Checks 13–15 (supply chain) e correção de "12 checks" na documentação

- **Card Jira:** KL-2
- **Data:** 2026-07-06
- **Executor:** Claude CLI (Opus 4.8)
- **Depende de:** KL-1 (governança)
- **Commit anterior:** `2a38b15` (governança)
- **Commit desta tarefa:** `feat(KL-2): add supply chain checks 13-15, fix hardcoded check count in docs`

---

## Objetivo

1. Corrigir a documentação que tratava **"12 checks"** como número fixo/identidade
   do produto — o conjunto é dinâmico e cresce.
2. Implementar 3 checks de **supply chain / third-party risk** (13–15).
3. Tornar a descoberta de checks **dinâmica** (sem lista hardcoded).
4. Validar contra 3 alvos reais.

---

## Parte 1 — Correção de rota na documentação

Referências fixas a "12 checks/verificações" substituídas por linguagem dinâmica
nos arquivos **vivos** (que descrevem o sistema atual):

- `README.md` — headline, seção de checks (agora tabela de 15 + nota de que o
  número cresce), bloco de estrutura, roadmap; nova subseção **"Como adicionar um
  check"**.
- `claude.md` — §4.2 reescrita (descoberta dinâmica + tabela dos 15 checks +
  nota explícita de que o número não é identidade do produto); árvore de diretórios.
- `scanner/runner.py`, `scanner/__init__.py`, `scanner/main.py`,
  `scanner/scoring.py`, `scanner/checks/__init__.py` — docstrings/comentários.
- `tests/test_checks.py` — teste de registro agora valida o **contrato** (≥15,
  ids únicos, ordem), não um número fixo.
- `claude/sessions/2026-07-05_scaffold-inicial.md` — prosa ajustada para deixar
  claro que "12" era o conjunto **daquela sessão**.
- `klarim_mvp_spec.md` — removida a moldura de "12 = identidade"; a Seção 2 ganhou
  nota de que o número é dinâmico. **As tabelas históricas do MVP (12 itens) foram
  preservadas** por serem registro fiel do escopo inicial.

### Decisões conscientes (não alterado)

- **Mensagem do commit `325e6d0`** citada em `claude/sessions/...` e no relatório
  KL-1 — é o texto real de um commit histórico; alterá-lo falsificaria o registro.
- **Relatório `KL-1_governanca-setup.md`** — as menções a "12 checks" são contexto
  factual de um artefato fechado. A própria regra em `claude/README.md` diz para
  não reescrever relatórios antigos. Mantido verbatim.

> Regra aplicada (do próprio card): o número exato pode aparecer em **contexto
> factual/histórico**, mas nunca como identidade ou limite do produto.

---

## Parte 2 — Checks 13–15 + descoberta dinâmica

### Novos checks

| # | Módulo | Severidade | Lógica |
|---|--------|-----------|--------|
| 13 | `check_sri.py` | Alta | FAIL se **> 50%** dos scripts externos não têm `integrity` (SRI). |
| 14 | `check_risky_sources.py` | Alta | FAIL se **qualquer** script vem de fonte arriscada: `*.github.io`, S3 público (`s3.amazonaws.com`, `*.s3(.-região).amazonaws.com`), `raw.githubusercontent.com`, `pastebin.com`, `paste.ee`. CloudFront **não** é arriscado. |
| 15 | `check_external_domains.py` | Média/Alta | Conta domínios externos únicos (eTLD+1): ≤5 PASS · 6–10 PASS (observação) · 11–15 FAIL Média · 16+ FAIL Alta. |

### Infraestrutura compartilhada (`checks/base.py`)

- **`extract_script_refs(html, page_url)`** — parser de `<script src>` usando
  `html.parser` da stdlib (**sem BeautifulSoup**, conforme o card). Retorna
  `ScriptRef(src, host, registrable, integrity, is_external)`. Reaproveitado pelos
  3 checks.
- **`registrable_domain(host)`** — aproximação leve da Public Suffix List (inclui
  `com.br`, `co.uk`, … e `github.io` como sufixo privado, para que cada conta
  GitHub Pages conte como site próprio). Usado para decidir "mesmo site" × "terceiro".

### Descoberta dinâmica (sem lista hardcoded)

- `scanner/checks/__init__.py` agora tem **`discover_checks()`**: importa todos os
  módulos `check_*`, coleta os que expõem `check` callable e ordena por `ORDER`.
- Cada módulo de check ganhou `ORDER` + `CHECK_ID`.
- `ALL_CHECKS` é construído por essa descoberta; `runner.py` e `scoring.py` já
  funcionavam com N checks — nenhuma fórmula assume 12.

### Testes

`tests/test_checks.py` ganhou testes offline (HTTP mockado via monkeypatch, sem
rede): helpers (`registrable_domain`, `extract_script_refs`) e os 3 checks
(SRI maioria-ausente/protegido/sem-externos; risky sources GitHub Pages + 4
variantes de S3 + CloudFront ignorado; external domains poucos/média/alta).

**Resultado:** `17 passed, 1 skipped` (o skip é o teste online opt-in).

---

## Parte 3 — Validação (scans reais, 2026-07-06)

| Alvo | Score | PASS/FAIL/INC | Check 13 (SRI) | Check 14 (risky) | Check 15 (domínios) |
|------|-------|---------------|----------------|------------------|---------------------|
| verdegreen.com.br | **86** 🟢 | 12 / 2 / 1 | FAIL 12/12 s/ SRI | FAIL: `bigspotteddog.github.io` | PASS (6) |
| atlanticopraiahotel.com.br | **86** 🟢 | 12 / 2 / 1 | FAIL 11/11 s/ SRI | FAIL: S3 `omnibees-chatbot.s3.amazonaws.com` + `bigspotteddog.github.io` | PASS (8) |
| checkinweb.com.br | **93** 🟢 | 13 / 1 / 1 | FAIL 6/6 s/ SRI | PASS | PASS (3) |

### Leitura dos resultados

- **Os novos checks funcionam e agregam sinal:** todos os FAILs dos 3 alvos vêm
  dos checks 13–15. O check 14 detectou **exatamente** o script de GitHub Pages
  (`bigspotteddog.github.io/ScrollToFixed/…`) citado no spec e um **bucket S3
  público** da Omnibees no Atlântico.
- **Verdegreen caiu de 100 → 86**, como esperado pelo card.

### Divergência honesta vs. o esperado no card

O card previa Atlântico com "**score baixo**, 19 domínios externos, 3 S3 buckets".
Medimos **86/100, 8 domínios externos, 1 S3 bucket**. Duas causas:

1. **A plataforma Duda foi endurecida desde a validação manual.** Os 3 sites hoje
   têm HSTS, CSP, X-Frame-Options e `nosniff` (o spec dizia "ausência total de
   security headers"). Por isso os scores 40/55/70 do spec viraram 86/86/93.
2. **Parse passivo do HTML servido não vê scripts injetados em runtime.** Os
   chatbots/booking engines (Omnibees, AskSuite, HSystem) injetam a maior parte
   dos 19 domínios via JavaScript **após** o carregamento — invisíveis a um GET
   simples. Nós só contamos `<script src>` presentes no HTML inicial.

**Trade-off documentado:** manter o scanner leve e 100% passivo (sem navegador
headless) é uma escolha deliberada. Cobrir terceiros injetados em runtime é um
candidato a check futuro (render headless ou detecção de loaders conhecidos).
A ordenação comparativa do spec se mantém direcionalmente (CheckinWeb é o melhor).

---

## Arquivos afetados

| Arquivo | Ação |
|---------|------|
| `scanner/checks/check_sri.py` | criado (check 13) |
| `scanner/checks/check_risky_sources.py` | criado (check 14) |
| `scanner/checks/check_external_domains.py` | criado (check 15) |
| `scanner/checks/base.py` | + `extract_script_refs`, `ScriptRef`, `registrable_domain` |
| `scanner/checks/__init__.py` | reescrito para descoberta dinâmica (`discover_checks`) |
| `scanner/checks/check_*.py` (12 existentes) | + `ORDER`/`CHECK_ID` |
| `scanner/runner.py`, `scanner/__init__.py`, `scanner/main.py`, `scanner/scoring.py` | docstrings/comentários dinâmicos |
| `tests/test_checks.py` | testes dos 3 novos checks + registro dinâmico |
| `README.md`, `claude.md`, `klarim_mvp_spec.md` | de-hardcode + tabela/nota de 15 checks |
| `claude/sessions/2026-07-05_scaffold-inicial.md` | prosa ajustada |

## Critérios de aceite

- [x] Referências fixas a "12 checks" removidas da documentação viva.
- [x] `check_sri.py`, `check_risky_sources.py`, `check_external_domains.py` funcionais.
- [x] Runner descobre checks dinamicamente (sem lista hardcoded).
- [x] Testes passando para os 3 novos checks (`17 passed, 1 skipped`).
- [x] Scan dos 3 alvos executado e documentado.
- [x] `claude.md` e `README.md` atualizados.
- [x] Relatório criado.
- [x] Commit e push.

## Próximos passos sugeridos

- **Check 16 (futuro):** cobertura de scripts injetados em runtime (render headless
  ou detecção de loaders de chatbot/booking) — fecharia o gap Atlântico.
- Refinar `registrable_domain` para não colapsar buckets S3 distintos em
  `amazonaws.com` na contagem do check 15.
