# Klarim — MVP Technical Specification (v2)

**"O alarme que toca antes do ataque."**

Ferramenta de varredura passiva de segurança web para PMEs e desenvolvedores independentes.

---

## 1. Conceito

Klarim é um scanner de superfície de ataque que descobre vulnerabilidades comprovadas em sites e sistemas web, gera relatórios acionáveis, e alerta os responsáveis — tudo de forma passiva, legal e automatizada.

O Klarim opera por **fingerprinting de plataforma**: identifica a tecnologia base de um site (Duda, WordPress, Wix, CRA, etc.), aplica os 12 checks de segurança, e gera um relatório calibrado ao setor do negócio. Uma vulnerabilidade num site de hotel que processa dados de hóspedes (LGPD) tem peso diferente da mesma vulnerabilidade num blog pessoal.

**Público-alvo primário:** PMEs brasileiras de qualquer setor que tenham sistema web exposto e não tenham equipe de segurança — hotéis, clínicas, escolas, e-commerces, condomínios, escritórios de contabilidade, etc.

**Público-alvo secundário (emergente):** Agências de marketing e web builders que constroem sites para esses clientes e precisam responder quando o relatório Klarim chega via WhatsApp do cliente delas.

**Posicionamento:** "Segurança acessível para quem não tem CISO."

---

## 2. As 12 Verificações do MVP

Cada check é binário (PASS/FAIL), comprovável sem invasão, e mapeado para uma severidade.

### Bloco 1 — Transporte e Criptografia

| # | Check | Como funciona | Severidade |
|---|-------|---------------|------------|
| 1 | **HTTPS ativo** | HEAD request na porta 80 e 443. Se 80 responde sem redirect 301→443, FAIL | Crítica |
| 2 | **HSTS presente** | Verifica header `Strict-Transport-Security` na resposta HTTPS | Alta |
| 3 | **Certificado SSL válido** | Verifica expiração, CA confiável, match de domínio | Crítica |
| 4 | **TLS 1.2+ only** | Tenta handshake TLS 1.0 e 1.1. Se aceitar, FAIL | Alta |

### Bloco 2 — Headers de Segurança

| # | Check | Como funciona | Severidade |
|---|-------|---------------|------------|
| 5 | **Content-Security-Policy** | Verifica presença do header CSP | Alta |
| 6 | **X-Frame-Options** | Verifica presença (proteção contra clickjacking) | Média |
| 7 | **X-Content-Type-Options** | Verifica se `nosniff` está presente | Média |
| 8 | **Server header exposto** | Se o header `Server` revela versão (ex: `Apache/2.4.41`), FAIL | Média |

### Bloco 3 — Exposição de Informação

| # | Check | Como funciona | Severidade |
|---|-------|---------------|------------|
| 9 | **Source maps expostos** | Tenta GET em `{url}/static/js/*.js.map` e `asset-manifest.json` | Crítica |
| 10 | **Arquivos sensíveis expostos** | Tenta GET em `.env`, `.git/config`, `wp-config.php.bak`, `debug.log` | Crítica |
| 11 | **Directory listing ativo** | Tenta GET em diretórios comuns (`/static/`, `/uploads/`, `/backup/`) e verifica se retorna listagem | Alta |
| 12 | **Meta tags default** | Verifica se meta description contém fingerprints de framework (CRA, Next.js, etc.) | Baixa |

---

## 3. Estratégia de Descoberta — Fingerprinting de Plataforma

O Klarim não busca sites aleatoriamente. Ele identifica **plataformas com vulnerabilidades sistêmicas** e varre todos os sites construídos nelas.

### Método validado: Google Dorks por CDN/fingerprint

Cada plataforma tem assinaturas únicas em seus assets. Exemplos:

| Plataforma | Fingerprint | Dork |
|-----------|-------------|------|
| **Duda** | `cdn-website.com`, `multiscreensite.com`, `/_dm/s/rt/` | `"cdn-website.com" hotel site:.com.br` |
| **CRA (React)** | `"create-react-app"` na meta description | `"create-react-app" login site:.com.br` |
| **WordPress** | `/wp-content/`, `/wp-includes/` | `"wp-content" clinica site:.com.br` |
| **Wix** | `wixsite.com`, `parastorage.com` | `"parastorage.com" loja site:.com.br` |

Combinando fingerprint de plataforma + palavras-chave de setor, o Discovery Worker encontra alvos com alta probabilidade de vulnerabilidade.

### Métodos futuros

- **BuiltWith / Wappalyzer API** — listar sites por tecnologia + setor + país
- **Certificate Transparency Logs (crt.sh)** — enumerar domínios via CNAME para infraestrutura da plataforma
- **DNS brute-force** — identificar subdomínios de plataformas multi-tenant

---

## 4. Arquitetura Técnica

```
┌─────────────────────────────────────────────────┐
│                   KLARIM SYSTEM                  │
├─────────────────────────────────────────────────┤
│                                                  │
│  ┌──────────┐    ┌──────────┐    ┌───────────┐  │
│  │ Discovery│───▶│ Scanner  │───▶│ Reporter  │  │
│  │  Worker  │    │  Engine  │    │  Engine   │  │
│  └──────────┘    └──────────┘    └───────────┘  │
│       │               │               │         │
│       ▼               ▼               ▼         │
│  ┌──────────┐    ┌──────────┐    ┌───────────┐  │
│  │  Queue   │    │   DB     │    │  Notifier │  │
│  │ (Redis)  │    │(Postgres)│    │  (Email)  │  │
│  └──────────┘    └──────────┘    └───────────┘  │
│                                                  │
│  ┌──────────────────────────────────────────┐   │
│  │           Web Dashboard (React)          │   │
│  │  - Semáforo (versão executiva)           │   │
│  │  - Relatório técnico (versão completa)   │   │
│  │  - Payment gateway (Pix + Stripe)        │   │
│  │  - Partner referral links                │   │
│  └──────────────────────────────────────────┘   │
└─────────────────────────────────────────────────┘
```

### Componentes

**Discovery Worker** — Alimenta a fila com alvos via fingerprinting de plataforma. Fontes: Google Custom Search API com dorks por plataforma + setor, input manual (self-service), Certificate Transparency logs. Classifica cada alvo por setor (turismo, saúde, educação, varejo, etc.) para calibrar severidade e pricing.

**Scanner Engine** — Consome a fila e executa os 12 checks contra cada alvo. Implementado em Python (httpx + ssl + dns.resolver). Cada check é um módulo independente. Timeout de 10s por request. Rate limit de 1 req/s por domínio. Registra também: plataforma detectada, domínios externos, uso de SRI, scripts de fontes arriscadas (GitHub Pages, S3 público), e bibliotecas desatualizadas.

**Reporter Engine** — Gera dois formatos de relatório:
- **Relatório Executivo (semáforo)** — para o dono do negócio. Vermelho/amarelo/verde, linguagem acessível, foco em risco de negócio e LGPD. Ex: "Seu site pode expor dados de clientes."
- **Relatório Técnico** — para dev/agência. Detalhes de cada check, headers, paths testados, recomendações de correção com código.

**Notifier** — Envia e-mail de alerta ao contato público do domínio. Template: "Encontramos [N] problemas de segurança em [domínio]. Veja o resumo gratuito."

**Web Dashboard** — Interface onde o dono do site vê o semáforo gratuito e pode comprar o relatório completo. React + Tailwind.

**Database** — PostgreSQL. Tabelas: `targets`, `scans`, `findings`, `reports`, `payments`, `notifications`, `platforms`, `sectors`.

---

## 5. Stack Técnica (MVP)

| Camada | Tecnologia | Justificativa |
|--------|------------|---------------|
| Scanner | Python 3.12 + httpx + ssl + cryptography | Melhor ecossistema para HTTP/TLS analysis |
| Queue | Redis (BullMQ ou RQ) | Simples, rápido, suficiente para MVP |
| Database | PostgreSQL 16 | Robusto, gratuito, escalável |
| Backend API | FastAPI | Async, rápido, tipado |
| Frontend | React + Tailwind | Consistente com skillset existente |
| PDF | ReportLab ou WeasyPrint | Geração server-side |
| Email | Amazon SES ou Resend | Custo baixo, deliverability alta |
| Hosting | Railway ou Fly.io | Deploy simples, custo previsível |
| Payments | Stripe (cartão) + API Pix | Cobertura BR completa |

---

## 6. Modelo de Negócio (v2)

### Princípio: Bottom-Up

O Klarim vende barato para o dono do negócio (hotel, clínica, loja). O dono encaminha o relatório para a agência que fez o site. A agência, pressionada por múltiplos clientes, procura o Klarim. A venda B2B (agência) acontece organicamente, sem prospecção.

### Camada 1 — Aquisição (gratuito)

- Scan self-service: usuário digita URL, recebe semáforo (🔴🟡🟢) + resumo por categoria
- Scan proativo: Discovery Worker encontra sites vulneráveis e envia alerta por e-mail
- O resumo mostra: "3 problemas críticos, 2 altos, 1 médio" — sem detalhes

### Camada 2 — Relatório Completo (pago, R$ 19-49)

Preço varia por setor e severidade:

| Faixa | Setor típico | Justificativa |
|-------|-------------|---------------|
| **R$ 19** | Comércio local, blogs, portfólios | Baixo risco regulatório, dados limitados |
| **R$ 29** | Hotéis, pousadas, restaurantes | Dados de clientes, LGPD aplicável |
| **R$ 39** | E-commerces, escolas, contabilidade | Dados financeiros ou de menores |
| **R$ 49** | Clínicas, saúde, jurídico | Dados sensíveis (saúde, processos), LGPD Art. 11 |

O relatório inclui:
- **Versão executiva** (semáforo + linguagem de negócio + risco LGPD)
- **Versão técnica** (checks detalhados + recomendações de correção)
- Linha final: "Encaminhe este relatório ao responsável pelo seu site."

Pagamento via Pix (instantâneo) ou cartão (Stripe). Preço baixo = decisão de impulso, sem aprovação.

### Camada 3 — Referral / Afiliação

- No final de cada relatório: "Precisa de ajuda para corrigir?"
- Lista de parceiros (empresas de pentest, consultorias LGPD, devs de segurança)
- Comissão por lead qualificado: 10-20% do primeiro contrato
- Empresas DAST como parceiros premium para upsell de varredura profunda

### Camada 4 — Demanda Orgânica de Agências (emergente)

Quando múltiplos clientes de uma mesma agência encaminham relatórios Klarim, a agência vem até o Klarim por conta própria. Nesse ponto, o Klarim oferece:
- **Auditoria consolidada da carteira** (R$ 500-2.000) — scan de todos os sites da agência
- **Monitoramento contínuo** (R$ 79-199/mês) — re-scan semanal + alertas
- **Badge "Klarim Verified"** para sites limpos — diferencial comercial para a agência

### Camada 5 — Monitoramento Contínuo (SaaS, futuro)

- **R$ 29/mês** — re-scan semanal + alertas de novas vulnerabilidades
- **R$ 79/mês** — scan diário + dashboard em tempo real + badge "Klarim Verified"

---

## 7. Framework Legal

### O que Klarim faz (legal)

- Requisições HTTP GET/HEAD a URLs públicas
- Leitura de headers de resposta HTTP
- Verificação de certificados SSL (informação pública)
- Consulta DNS pública
- Acesso a arquivos que o servidor entrega sem autenticação
- Fingerprinting de plataforma via assets públicos (CDN, meta tags)

### O que Klarim NÃO faz (ilegal sem autorização)

- Envio de payloads de injeção (SQLi, XSS)
- Brute-force de credenciais
- Acesso a áreas autenticadas
- Exploração de vulnerabilidades encontradas
- Extração de dados de qualquer tipo

### Enquadramento

- Serviço de "Security Rating" / "Monitoramento de Superfície de Ataque"
- NÃO é pentest (não requer autorização do alvo para varredura passiva)
- Disclaimer claro em todos os relatórios e comunicações
- Consultar advogado de direito digital antes do lançamento

---

## 8. Roadmap

### Fase 1 — MVP (4-6 semanas)

- [ ] Scanner engine com os 12 checks
- [ ] CLI para scan manual (`klarim scan https://example.com`)
- [ ] Geração de relatório PDF (versão executiva + técnica)
- [ ] Landing page com scan self-service (semáforo gratuito)
- [ ] Integração de pagamento (Pix + Stripe)
- [ ] Deploy em produção

### Fase 2 — Automação (semanas 7-10)

- [ ] Discovery Worker com Google Dorks por plataforma
- [ ] Classificação automática de setor (turismo, saúde, varejo, etc.)
- [ ] Sistema de notificação por e-mail
- [ ] Dashboard web com histórico de scans

### Fase 3 — Escala (semanas 11-16)

- [ ] Expansão de plataformas monitoradas (Duda, WordPress, Wix, CRA, Squarespace)
- [ ] API pública para parceiros
- [ ] Programa de afiliados para consultorias
- [ ] Badge "Klarim Verified"
- [ ] Expansão dos checks (12→20→30)

### Fase 4 — Diferenciação (meses 4-6)

- [ ] Auditoria consolidada para agências (produto B2B emergente)
- [ ] "Klarim Score" público (tipo Reclame Aqui da segurança)
- [ ] Relatório de compliance LGPD automatizado por setor
- [ ] White-label para consultorias revenderem

---

## 9. Métricas de Sucesso (3 meses)

| Métrica | Target |
|---------|--------|
| Sites descobertos via Discovery Worker | 5.000 |
| Scans realizados | 2.000 |
| Relatórios pagos vendidos | 200 |
| Receita de relatórios | R$ 5.000-8.000 |
| Leads de referral enviados | 50 |
| Agências que procuraram o Klarim | 10 |
| Taxa de conversão semáforo→relatório | 5-10% |

---

## 10. Domínios Recomendados

| Domínio | Disponibilidade | Uso |
|---------|----------------|-----|
| `klarim.io` | ✅ Alta | Principal (global) |
| `klarim.com.br` | ✅ Alta | Brasil |
| `klarim.sh` | ✅ Alta | CLI / developer-facing |
| `klarim.security` | ✅ Alta | Institucional |

**Recomendação:** Registrar `klarim.com.br` + `klarim.io` como prioridade.

---

## 11. Identidade Visual (direção)

**Conceito:** Alarme claro na escuridão. Farol cortando névoa.

**Paleta sugerida:**
- Background escuro (dark mode nativo): `#0D1117`
- Accent primário (alarme/alerta): `#FF6B35` (laranja vivo)
- Accent secundário (segurança/ok): `#00D26A` (verde limpo)
- Texto: `#E6EDF3`

**Tipografia:** Display bold para headlines (autoridade), mono para dados técnicos (credibilidade).

**Tom de voz:** Direto, técnico mas acessível, sem jargão desnecessário. "Encontramos 3 portas abertas no seu sistema. Aqui está o que fazer."

---

## Apêndice A — Casos de Validação

### Caso 1 — Sistema de condomínio (severidade: Alta)

**Alvo:** Plataforma SaaS de gestão condominial com múltiplas SPAs React (CRA).

**Achados:** Site institucional servido via HTTP sem criptografia; arquitetura multi-tenant com subdomínios enumeráveis por brute-force DNS; meta description default do CRA expondo framework em todas as instâncias; ausência total de security headers (CSP, HSTS, X-Frame-Options); risco de source maps em produção.

**Contexto agravante:** A plataforma processa dados biométricos faciais (LGPD Art. 11 — dados sensíveis), controle de acesso físico, e dados financeiros de moradores.

**Relevância para Klarim:** Caso ideal do público-alvo principal. Sistema exposto com dados críticos, equipe sem expertise de segurança, vulnerabilidades detectáveis 100% via varredura passiva.

---

### Caso 2 — Cardápio digital de café (severidade: Baixa)

**Alvo:** Estabelecimento comercial usando ImgBB como plataforma de cardápio digital via QR code.

**Achados:** Metadados operacionais expostos; download irrestrito; zero controle de acesso.

**Risco real:** Baixo. Não justifica alerta proativo nem cobrança. Serve como porta de entrada para conscientização.

---

### Caso 3 — Plataforma de hospedagem por temporada (severidade: Alta)

**Alvo:** Plataforma de gestão de apartamentos para short-term stay (1.500+ unidades, SP e RJ).

**Achados:** Repositório GitHub público contendo Terraform (76%), Ansible, e Dockerfile — infraestrutura-as-code completa; backoffice exposto na internet sem proteção de perímetro; GTM injetado em sistema interno; landing page com URL admin do LinkedIn e conteúdo template WordPress não removido.

**Contexto agravante:** A plataforma controla fechaduras eletrônicas de 1.500+ apartamentos. Credenciais AWS no repo público = potencial acesso físico a todos os imóveis.

---

### Caso 4 — Ecossistema Duda hoteleiro (severidade: Média-Alta) ← NOVO

**Alvo:** Três hotéis em João Pessoa (CheckinWeb, Verdegreen, Atlântico Praia) construídos na plataforma Duda.

**Achados sistêmicos (presentes nos 3 sites):**
- Ausência total de security headers (HSTS, CSP, X-Frame-Options, X-Content-Type-Options)
- Script jQuery carregado de repositório pessoal no GitHub Pages (`bigspotteddog.github.io`) — vetor de supply chain attack
- Zero Subresource Integrity em todos os scripts externos (14-19 por site)
- Diferenciação 403 vs 404 em paths sensíveis (`.env`, `.git/config`)
- `robots.txt` totalmente permissivo
- `Server: nginx` sem versão (único PASS consistente)

**Achados específicos do Atlântico Praia (pior score: 40/100):**
- 19 domínios externos carregando scripts (vs 8 do CheckinWeb)
- 3 chatbots/booking engines simultâneos (Omnibees, AskSuite, HSystem)
- 3 scripts carregados de buckets S3 públicos
- Formulário Omnibees via método GET (dados na URL)
- IDs de Google Analytics e Facebook Pixel expostos

**Padrão confirmado:** A plataforma Duda é o denominador comum. As vulnerabilidades não são escolhas dos hotéis — são defaults da plataforma. Isso significa que qualquer site Duda descoberto via fingerprinting (`cdn-website.com`) tem alta probabilidade de apresentar as mesmas falhas.

**Comparativo dos 3 scans:**

| Métrica | CheckinWeb | Verdegreen | Atlântico |
|---------|------------|------------|-----------|
| Score | 70/100 🟡 | 55/100 🟠 | 40/100 🔴 |
| Scripts externos | 14 | 15 | 19 |
| Domínios externos | 8 | 13 | 19 |
| SRI | 0/14 | 0/15 | 0/19 |
| GitHub script | ⚠️ | ⚠️ | ⚠️ |
| S3 buckets | 0 | 0 | 3 |

**Relevância para Klarim:** Este caso validou a estratégia de discovery por plataforma. Um único insight ("sites Duda no turismo") gera um pipeline de centenas de leads com vulnerabilidades comprovadas e padrão repetível. O mesmo método se aplica a Duda em saúde, educação, varejo — qualquer setor.

**Insight de modelo de negócio:** O relatório individual a R$19-29 para o hotel funciona como anzol. Quando múltiplos hotéis encaminham o relatório para a mesma agência que construiu os sites, a agência procura o Klarim organicamente. A venda B2B acontece sem prospecção.

---

*Documento atualizado em 05/07/2026 — Sessão de validação de mercado e correção de modelo de negócio.*
