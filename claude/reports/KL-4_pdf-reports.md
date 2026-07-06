# KL-4 — Geração de relatórios PDF (executivo + técnico) com WeasyPrint

- **Card Jira:** KL-4
- **Data:** 2026-07-06
- **Executor:** Claude CLI (Opus 4.8)
- **Depende de:** KL-2 (scanner com 15 checks), KL-3 (VM + CI/CD)
- **Commit:** `feat(KL-4): add PDF report generation with WeasyPrint`

---

## Objetivo

Transformar o `ScanReport` (dados) em **dois PDFs** — o produto que o Klarim
vende: um **executivo** (dono do negócio) e um **técnico** (dev/agência).

---

## Parte 1 — Módulo `reporter/`

```
reporter/
├── __init__.py            # exporta generate_executive_pdf / generate_technical_pdf
├── generator.py           # contexto + render (Jinja2 → WeasyPrint)
├── templates/
│   ├── executive.html
│   └── technical.html
└── assets/logo.svg        # beacon Klarim (SVG só com formas — rende bem no WeasyPrint)
```

- **Templating:** **Jinja2** (mais limpo que string-format para as tabelas/loops;
  adicionado a `requirements.txt`). `weasyprint` já era dependência.
- Ambas as funções são `async` e retornam `bytes`; a renderização (CPU-bound)
  roda em `asyncio.to_thread` para não travar o event loop da API.
- **Conteúdo por check** em dicionários indexados por `check_id`:
  - `ACCESSIBLE` — frase de negócio por FALHA (executivo).
  - `TECHNICAL` — `impacto` + `correção` + `exemplo de código` (técnico).
  - Os 15 checks estão mapeados. **Ao criar um check novo, acrescente as entradas.**
- **Identidade visual (spec do MVP):** fundo `#0D1117`, alerta `#FF6B35`, ok
  `#00D26A`, texto `#E6EDF3`; sans-serif no corpo, monospace nos dados técnicos;
  semáforo como círculo grande colorido pela faixa de score.

## Parte 2 — Relatório Executivo (1-2 páginas)

- Página 1: header (logo + "Relatório de Segurança"), dados do alvo, **semáforo
  grande** com score, resumo em linguagem humana, contagem por severidade
  (chips coloridos), **bloco LGPD** (Art. 52 — até R$ 50 mi).
- Página 2: recomendação principal ("encaminhe ao responsável"), lista de
  problemas em linguagem acessível (dicionário `ACCESSIBLE`), referral
  (`klarim.com.br/parceiros`), footer com disclaimer, data e **ID único** do
  relatório (`KLR-<hash>` estável por scan).

## Parte 3 — Relatório Técnico (3-5 páginas)

- Página 1: header + score + **tabela-resumo dos 15 checks** (PASS/FAIL/INCONCLUSO
  com cores + severidade).
- Páginas 2+: **um card por FALHA** — evidência, impacto, correção e exemplo de
  código (HSTS, CSP, SRI, XFO, etc.).
- Inventário: domínios externos, **scripts sem SRI (com URL)**, scripts de fontes
  arriscadas (com URL + motivo), headers HTTP capturados.
- Footer: disclaimer "100% passiva", data, ID, versão do scanner. Rodapé com
  numeração de página.

> Pequeno ajuste no scanner: `check_sri.py` passou a expor `without_sri_urls` em
> `details` (o inventário técnico lista os scripts sem SRI por URL, não só por
> domínio). Mudança retrocompatível — testes existentes seguem passando.

## Parte 4 — Integração CLI e API

- **CLI:** flag `--pdf` em `scanner/main.py` gera os dois arquivos no diretório
  atual: `klarim_executive_<host>_<data>.pdf` e `klarim_technical_<host>_<data>.pdf`
  (import de `reporter` é lazy — só puxa weasyprint/jinja2 quando `--pdf` é usado).
- **API:** `GET /report/executive?url=` e `GET /report/technical?url=` retornam
  `application/pdf` (`Content-Disposition: inline`).

## Parte 5 — Validação (3 alvos de referência)

PDFs gerados e **inspecionados visualmente** (leitura do PDF renderizado):

| Alvo | Score | Executivo | Técnico |
|------|-------|-----------|---------|
| verdegreen.com.br | 86 🟢 | 2 páginas ✓ | 3 páginas ✓ |
| atlanticopraiahotel.com.br | 86 🟢 | 2 páginas ✓ | 3 páginas ✓ |
| checkinweb.com.br | 93 🟢 | 2 páginas ✓ | 3 páginas ✓ |

Os 6 PDFs de referência estão versionados em `claude/reports/klarim_*_*.pdf`
(exceção adicionada no `.gitignore`, que por padrão ignora `*.pdf`).

Correção visual feita na inspeção: o cabeçalho passou de layout inline (logo
sobrepunha 1 caractere do slogan) para **tabela** (logo | marca | documento),
eliminando a sobreposição.

## Parte 6 — Documentação

- `claude.md`: nova seção **9. Relatórios PDF**, árvore com `reporter/`, `--pdf`.
- `README.md`: seção **Relatórios PDF**, endpoints de PDF, estrutura, `--pdf`.
- Este relatório.

## Testes

- `tests/test_reporter.py` — gera os dois PDFs a partir de um `ScanReport`
  sintético (offline) e valida o cabeçalho `%PDF-`. Pulado automaticamente se as
  libs nativas do WeasyPrint faltarem (mantém o CI robusto).
- Suíte completa: **19 passed, 1 skipped** (o skip é o teste online opt-in).

## Arquivos afetados

| Arquivo | Ação |
|---------|------|
| `reporter/__init__.py`, `generator.py` | criados |
| `reporter/templates/{executive,technical}.html` | criados |
| `reporter/assets/logo.svg` | criado |
| `scanner/main.py` | + flag `--pdf` |
| `api/main.py` | + `/report/executive`, `/report/technical` |
| `scanner/checks/check_sri.py` | + `without_sri_urls` em details |
| `tests/test_reporter.py` | criado |
| `requirements.txt` | + `jinja2` |
| `.gitignore` | exceção `!claude/reports/*.pdf` |
| `claude.md`, `README.md` | documentação |
| `claude/reports/klarim_*_*.pdf` | 6 PDFs de referência |

## Critérios de aceite

- [x] `reporter/generator.py` gera PDFs executivo e técnico a partir de ScanReport.
- [x] Executivo: semáforo, linguagem acessível, risco LGPD, 1-2 páginas.
- [x] Técnico: tabela de checks, evidence, recomendações, inventário, 3-5 páginas.
- [x] Identidade visual consistente (dark + laranja/verde).
- [x] CLI com flag `--pdf`.
- [x] API com `/report/executive` e `/report/technical`.
- [x] PDFs gerados e validados para 3 alvos (salvos em `claude/reports/`).
- [x] Documentação atualizada.
- [x] Relatório da tarefa em PT-BR.
- [x] Commit e push.

## Observações / follow-ups

- O endpoint de PDF **roda um scan a cada chamada** (~25-30s por causa do rate
  limit). Numa fase futura convém cachear o `ScanReport` (Redis/Postgres) e gerar
  o PDF a partir do scan já persistido.
- Aviso benigno do WeasyPrint sobre `fsSelection`/`macStyle` de fontes — não afeta
  a saída.
