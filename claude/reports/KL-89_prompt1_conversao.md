# KL-89 — Fix de Conversão (Prompt 1 de 2): Layout + Primeira Tela + Linguagem

**Card:** KL-89 · **Prioridade:** Highest · **Data:** 2026-07-20
**Escopo:** 100% frontend (Astro/React/Tailwind). Backend **inalterado** — reaproveita os 4
níveis de acesso do KL-82.

---

## Contexto

38.078 targets, 16.380 perfis, 7.628 alertas enviados — **14 contas, conversão ~0%**. O funil
quebrava em 4 pontos, todos resolvidos aqui:

1. Container estreito com espaços vazios laterais em telas > 1280px.
2. Desktop e mobile com experiências opostas (um mostra tudo, o outro esconde atrás de cadeados).
3. CTA de conta enterrado no fim da página, depois de todo o conteúdo técnico.
4. Linguagem genérica ("Este site") para quem veio do alerta vendo o **próprio** site.

---

## O que foi entregue

### 1. Container expandido em todas as páginas públicas

Criado **`web/src/lib/layout.js`** — fonte única da largura dos containers públicos (fim dos
`max-w` ad-hoc espalhados por página, que variavam de `2xl` a `6xl`):

| Constante | Largura | Onde |
|---|---|---|
| `PAGE_CONTAINER` | `max-w-2xl md:max-w-5xl lg:max-w-7xl` + `px-4/6/8` + padding | scan, site/{domain}, setores, setor/{slug}, melhores, estatisticas, planos |
| `FORM_CONTAINER` | `max-w-md` | cadastrar, entrar, recuperar-senha, contato |
| `PROSE_CONTAINER` | `max-w-3xl` | termos, privacidade, sobre (via `Page.astro`) |

- **`index`** (hero buscador do KL-81) e **`confirmar`** (página de status) seguem centralizados
  estreitos **de propósito** — não é espaço vazio esquecido; um formulário/hero a 1440px piora a UX.
- Decisão de engenharia: esticar formulário e texto corrido até 7xl **piora** a leitura. O padrão
  expandido vale para **conteúdo** (listagens/resultado/perfil, que têm grades e tabelas); forms e
  prose ficam legíveis e estreitos, mas agora **via constante compartilhada** (não largura solta).
- Tailwind v4 escaneia `.js` (já era o caso em `components/account/ui.js`), então as classes
  literais das constantes entram no build mesmo interpoladas via `class={PAGE_CONTAINER}`.
  Confirmado no CSS gerado: `md:max-w-5xl`, `lg:max-w-7xl`, `lg:grid-cols-3`, `lg:sticky` etc.

### 2. Nivelamento desktop ↔ mobile (tabela de visibilidade)

A "tabela de visibilidade" do card virou flags **puras** em **`web/src/lib/scanView.js`**
(`viewFlags`). O ponto central: **as flags derivam SÓ do `access_level`, nunca do dispositivo** —
logo desktop e mobile renderizam idêntico. O que muda entre eles é apenas o **layout** (2 colunas
no `lg`), não o conteúdo nem o gate. Mapa por nível (respeitando o filtro server-side do KL-82):

| Seção | anonymous | unconfirmed | confirmed / alert_session |
|---|---|---|---|
| Score + semáforo | ✅ | ✅ | ✅ |
| Compartilhar + PDF | ✅ | ✅ | ✅ |
| CTA criar conta | ✅ | — (confirme e-mail) | — (já tem conta) |
| Benchmark | 🔒 teaser | ✅ | ✅ |
| Riscos (KL-20) | 1 + "mais N 🔒" | 2 + "mais N 🔒" | todos |
| Categorias | barras | resumo c/ números | accordion + evidência |
| LGPD | 🔒 crie conta | 🔒 crie conta | ✅ |

Todo bloqueio mostra **hint explícito** ("Crie conta gratuita para ver") — nunca cadeado vazio
nem conteúdo que some. Nenhum gate exclusivo de mobile ou de desktop.

### 3. Primeira tela reorganizada (`ScanResultDetail.jsx`)

Nova ordem, com o **CTA de conta acima do fold**:

1. Score + semáforo
2. Frase contextual (item 4)
3. **Compartilhar + PDF na MESMA linha** — `[WhatsApp] [LinkedIn] [🔗 Copiar] [📄 Baixar PDF]`
   (antes o PDF era um botão isolado lá embaixo)
4. **Bloco CTA de conta** — "📊 Monitore este site gratuitamente" + 3 benefícios em linguagem
   humana ("Saiba na hora se ele sair do ar", "Receba alertas se os certificados vencerem",
   "Acompanhe a evolução do score") + criação de conta **inline**
5. Barras de categoria + **1 risco** + "Mais N riscos → crie conta"
6. Abaixo do fold: checks detalhados, indicadores LGPD

- **Layout 2 colunas no `lg`**: relatório (2/3) à esquerda + CTA `sticky` (1/3) à direita →
  preenche a tela larga **sem linhas longas**. No mobile empilha na ordem acima (mesmo conteúdo).
- **CTA some para quem tem conta**: `unconfirmed` → "Confirme seu e-mail"; `confirmed` → "+
  Adicionar ao monitoramento".
- **PDF acessível sem conta** (mantido): com o paywall desligado (default freemium), `/report/*`
  é público. `scanView.reportUrls` monta a URL no front → PDF disponível em **todo** nível.

### 4. Linguagem contextual por origem

Detecta a origem pelo **`access_level`** do backend (o HMAC do alerta é validado server-side; o
frontend nunca confia em query params). Adaptação em `scanView.scoreHeadline`/`ctaCopy`/`shareLabel`:

| Elemento | Via alerta (`alert_session`) | Orgânico |
|---|---|---|
| Frase do score | "**Seu site** tem score 83. Veja o que melhorar." | "**Este site** tem score 83. E o seu?" |
| Título do CTA | "Monitore **seu site** gratuitamente" | "Monitore **{domínio}** gratuitamente" |
| Campo de conta | **só senha** (e-mail do cookie HMAC, mostrado mascarado) | **e-mail + senha** |
| Botão | "Criar conta →" | "Criar conta gratuita →" |
| Compartilhar | "Compartilhe **seu** resultado" | "Compartilhe **este** resultado" |

**E-mail mascarado** (`maskEmail`): `joao@empresa.com.br` → `j***o@empresa.com.br` (1ª letra +
`***` + última antes do `@`). O e-mail real **nunca** vai ao HTML público — o valor verdadeiro
fica no cookie HttpOnly e o signup do alerta manda **só a senha** (`/api/account/signup-from-alert`).

---

## Arquivos

**Novos**
- `web/src/lib/layout.js` — constantes de container compartilhadas.
- `web/src/lib/scanView.js` — lógica pura (flags de visibilidade, linguagem contextual, maskEmail,
  reportUrls).
- `web/src/lib/scanView.test.js` — 26 testes.
- `web/src/lib/layout.test.js` — testes de fiação de layout por página.

**Alterados**
- `web/src/components/scan/ScanResultDetail.jsx` — reescrito (ordem above-the-fold, 2 colunas,
  CTA inline por origem, PDF na linha de share, seção LGPD para acesso completo).
- `web/src/components/scan/ScanFlow.jsx` — o resultado preenche o container expandido (não fica
  preso a `max-w-3xl`); progresso/limite/erro seguem cards centralizados estreitos.
- Páginas: `scan`, `site/[domain]`, `setores`, `setor/[slug]`, `melhores`, `estatisticas`,
  `planos` (→ `PAGE_CONTAINER`); `cadastrar`, `entrar`, `recuperar-senha`, `contato` (→
  `FORM_CONTAINER`); `layouts/Page.astro` (→ `PROSE_CONTAINER`).
- `web/package.json` — `test:unit` agora roda os 3 arquivos de teste.
- `CLAUDE.md` — card KL-89, convenção de container, tabela de visibilidade, linguagem contextual.

---

## Testes e validação

- **`npm run test:unit`**: **61 passed** (35 do KL-83 + 26 novos do KL-89). Cobrem: níveis de
  acesso, `isFullAccess`/`hasAccount`, `maskEmail` (incl. entradas inválidas → string vazia),
  linguagem contextual alerta vs orgânico, `ctaCopy` (só-senha vs e-mail+senha, benefícios
  humanos), `viewFlags` para os 4 níveis, prova de que as flags **não dependem de dispositivo**,
  `reportUrls`, e a fiação do container em todas as páginas.
- **`npm run build`** (Astro): **verde**. Classes novas confirmadas no CSS emitido.
- **`pytest`** (backend): **1284 passed, 1 skipped** — nada de Python foi tocado.

### Checklist de sucesso do card

| # | Critério | Status |
|---|---|---|
| 1 | Container expandido nas páginas públicas de conteúdo | ✅ |
| 2 | Desktop e mobile mostram MESMO conteúdo (flags só por nível) | ✅ |
| 3 | CTA de conta acima do fold | ✅ |
| 4 | CTA some quando o visitante tem conta | ✅ |
| 5 | PDF na linha de compartilhamento | ✅ |
| 6 | 3 benefícios em linguagem humana | ✅ |
| 7 | Visitante HMAC: "Seu site" + só senha + e-mail mascarado | ✅ |
| 8 | Orgânico: "Este site" + e-mail + senha | ✅ |
| 9 | "Mais N riscos → crie conta" como gate (não cadeado vazio) | ✅ |
| 10 | Nenhum cadeado exclusivo de mobile/desktop | ✅ |
| 11 | Build Astro verde | ✅ |
| 12 | ≥ 15 testes novos | ✅ (26) |
| 13 | CI verde | ⏳ após push |

---

## Segurança

- E-mail **mascarado** no frontend; o real só existe no cookie HttpOnly (não em HTML público, não
  legível por crawler). Signup do alerta manda **só a senha**.
- A validação HMAC continua **no backend** — o frontend só lê o `access_level` já decidido.
- Rate limit de signup mantido (3/h + 5/d por IP, KL-82) — o signup inline usa os mesmos endpoints.
- O filtro por nível permanece **server-side** (KL-82): anonymous/unconfirmed nunca recebem
  evidência/impacto/LGPD; o frontend só decide o que renderizar do que já veio filtrado.

---

## Notas / decisões

- **`index` e `confirmar`** não foram expandidos — hero-buscador (KL-81) e página de status são
  centralizados estreitos por design. Documentado.
- **Forms/prose** ganharam constante compartilhada, mas seguem estreitos (leitura). A intenção do
  card ("sem espaços vazios") mira páginas de **conteúdo**; um login a 7xl seria pior UX.
- **Testes de componente**: como o projeto não tem runner de DOM/React (padrão KL-83 = lógica pura
  + `node --test`), a lógica de visibilidade/linguagem foi extraída para `scanView.js` e testada
  isoladamente — cobre os cenários pedidos (anonymous/alert_session/confirmed, isAlertVisitor,
  maskedEmail) sem montar o React.

## Pendente (Prompt 2, deferido)

Resultado instantâneo direto do e-mail e feedback por categoria no scanner (o feedback por
categoria foi antecipado no Fix 6 abaixo).

---

# Correções pós-entrega (6 fixes)

Ajustes no componente de resultado após revisão. Quase tudo frontend; **uma** linha de backend
foi necessária (Fix 5) — justificada abaixo.

### Fix 1 — Riscos antes dos detalhes
`ScanResultDetail.jsx`: a coluna do relatório passou a renderizar **Riscos para o negócio →
Benchmark → Detalhes da análise → Indicadores de privacidade**. Os riscos (KL-20, linguagem
humana) são o conteúdo que converte; os detalhes técnicos ficam como complemento.

### Fix 2 — LGPD travado em desktop E mobile para anônimo
`scanView.js`: `showLGPD` renomeado para **`showPrivacy` = `full`** (só confirmed/alert_session).
`PrivacySection` mostra o teaser travado para anonymous **e** unconfirmed. Não havia gate por
breakpoint no código (confirmado por varredura: zero `md:hidden`/`innerWidth`/`matchMedia`) — a
visibilidade deriva **só do nível**, então desktop e mobile já são idênticos.

### Fix 3 — Botão PDF com destaque
Novo estilo `btnAccent` (`bg-brand-500` = #ff6b35 + `text-[var(--accent-text)]`) no botão "Baixar
PDF"; WhatsApp/LinkedIn/Copiar seguem outline neutro. Uso de `--accent-text` (não `text-white`)
garante contraste correto no tema **light e dark** (regra KL-87) — `text-white` sobre laranja teria
contraste ruim.

### Fix 4 — E-mail HMAC (só senha) idêntico no mobile
O `passwordOnly` vem de `viewFlags(access_level)` (device-agnostic); o `access_level` é decidido
pelo backend a partir do cookie `alert_session` enviado com `credentials:'include'` — sem
dependência de viewport. Portanto o visitante do alerta vê **só senha + e-mail mascarado** tanto no
desktop quanto no mobile. Coberto por teste (`passwordOnly` por nível).

### Fix 5 — Benchmark público (sem cadeado) — inclui 1 linha de backend
`viewFlags.showBenchmark = true` para **todos** os níveis; removido `benchmarkLocked` e o
`BenchmarkSection` não trava mais. **Necessário 1 ajuste no backend:** o `_filter_scan_result`
**omitia** o benchmark no payload anônimo (`benchmark_locked=True`, sem dados) — sem os dados o
frontend não tinha o que mostrar. Adicionada 1 linha que inclui o benchmark (média nacional
agregada + contagem) no branch anônimo. **Revisão de segurança:** é dado **agregado público**, já
exposto em `/estatisticas` e `/setores` a qualquer visitante; **nenhum** dado por-site ou PII vaza
(checks/evidência/privacidade continuam travados). Foi a menor mudança possível para cumprir o
requisito "benchmark visível para todos" — impossível só no frontend porque a API não entregava o
dado. Teste `test_filter_anonymous_is_preview_only` atualizado.

### Fix 6 — Scanner com progresso real por categoria
`scanView.js`: `SCAN_CATEGORIES` (6 camadas com faixas de %) + `getCategoryStatus(cat, %)` puros
(`done`/`active`/`pending`). `ScanFlow.jsx::ProgressStep` deriva o estado de cada categoria do
percentual (○ → ⏳ *analisando…* com pulse → ✅ *concluído*), em vez de mostrar as 6 ✅ de uma vez.
A barra de progresso continua acima. Ao completar, um `completing` leva o % a 100 (todas ✅) por
~0,9s antes de transicionar para o resultado. Ex.: ~52% → 3 ✅, 1 ⏳, 2 ○.

### Testes e validação (fixes)
- **`npm run test:unit`: 67 passed** (+6: `getCategoryStatus` nos boundaries/0%/100%/~52%,
  `SCAN_CATEGORIES`, benchmark público em todos os níveis, `showPrivacy` por nível).
- **`npm run build`: verde.**
- **`pytest`: 1284 passed, 1 skipped** (`test_kl82_progressive` atualizado para o benchmark público).

| # | Validação do card | Status |
|---|---|---|
| 1 | Riscos antes de Detalhes (desktop + mobile) | ✅ |
| 2 | LGPD com cadeado p/ anônimo em ambas as versões | ✅ |
| 3 | Botão PDF com cor accent | ✅ |
| 4 | HMAC no mobile: só senha + e-mail mascarado | ✅ |
| 5 | Benchmark visível para todos, sem cadeado | ✅ (1 linha backend, justificada) |
| 6 | Scanner progride categoria a categoria | ✅ |
| 7 | ~50% → 3 ✅ / 1 ⏳ / 2 ○ (não 6 ✅) | ✅ |
| 8 | Testes atualizados e passando | ✅ (67) |
| 9 | Build Astro verde | ✅ |
