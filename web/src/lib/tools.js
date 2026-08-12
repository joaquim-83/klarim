// KL-134 P2 — lógica PURA das micro-ferramentas SEO (testável, node --test). Metadados das 5
// ferramentas + construção da URL da API + mensagens de erro amigáveis + cor do score. Nada de
// DOM/React aqui — as ilhas (ToolPage/Results) consomem estas funções.

// As 5 ferramentas: metadados usados pelo index, pela navegação horizontal e pela ilha ToolPage.
// `endpoint`/`paramName`/`placeholder` alimentam o fetch; `navLabel`/`icon`/`short` a UI.
export const TOOLS = [
  {
    slug: 'ssl', path: '/ferramentas/verificar-ssl', name: 'Verificador de SSL',
    navLabel: 'SSL', icon: '🔒', short: 'Validade, protocolo TLS, emissor e nota do certificado.',
    endpoint: '/api/tools/ssl', paramName: 'url',
    placeholder: 'Digite o domínio (ex.: seusite.com.br)',
  },
  {
    slug: 'headers', path: '/ferramentas/verificar-headers', name: 'Headers de Segurança',
    navLabel: 'Headers', icon: '🛡️', short: 'CSP, HSTS, X-Frame-Options e outros headers.',
    endpoint: '/api/tools/headers', paramName: 'url',
    placeholder: 'Digite o domínio (ex.: seusite.com.br)',
  },
  {
    slug: 'lgpd', path: '/ferramentas/teste-lgpd', name: 'Teste de LGPD',
    navLabel: 'LGPD', icon: '⚖️', short: 'Política de privacidade, cookies, DPO e direitos.',
    endpoint: '/api/tools/lgpd', paramName: 'url',
    placeholder: 'Digite o domínio (ex.: seusite.com.br)',
  },
  {
    slug: 'tech', path: '/ferramentas/detectar-tecnologias', name: 'Detector de Tecnologias',
    navLabel: 'Tecnologias', icon: '🧩', short: 'CMS, framework, CDN, analytics e servidor.',
    endpoint: '/api/tools/tech', paramName: 'url',
    placeholder: 'Digite o domínio (ex.: seusite.com.br)',
  },
  {
    slug: 'email', path: '/ferramentas/verificar-email', name: 'Verificador de Email',
    navLabel: 'Email', icon: '📧', short: 'SPF, DKIM, DMARC e MX contra spoofing.',
    endpoint: '/api/tools/email', paramName: 'domain',
    placeholder: 'Domínio do email (ex.: suaempresa.com.br)',
  },
];

export function toolBySlug(slug) {
  return TOOLS.find((t) => t.slug === slug) || null;
}

// Constrói a URL da API: `/api/tools/ssl?url=example.com` (o valor é URL-encodado).
export function buildToolUrl(endpoint, paramName, value) {
  const v = encodeURIComponent(String(value == null ? '' : value).trim());
  return `${endpoint}?${paramName}=${v}`;
}

// Mensagem amigável por status HTTP (o corpo `detail` do backend é o fallback).
export function parseToolError(status, body) {
  if (status === 400) return 'URL inválida. Verifique o endereço e tente novamente.';
  if (status === 429) return 'Muitas consultas. Aguarde 1 minuto e tente novamente.';
  if (status === 504) return 'O site não respondeu em 15 segundos. Tente novamente.';
  if (status === 502) return 'Não foi possível acessar o site. Verifique se ele está no ar.';
  if (body && typeof body.detail === 'string') return body.detail;
  return 'Não foi possível concluir a análise. Tente novamente em instantes.';
}

// Cores de semáforo (constantes nos 2 temas — KL-87).
export const SCORE_GREEN = '#22c55e';
export const SCORE_YELLOW = '#eab308';
export const SCORE_RED = '#ef4444';
export const SCORE_GRAY = '#94a3b8';

// Interpreta "N/M" e devolve texto + cor (verde 100%, amarelo ≥50%, vermelho < 50%).
export function formatScore(score) {
  const m = /^(\d+)\s*\/\s*(\d+)$/.exec(String(score == null ? '' : score).trim());
  if (!m) return { text: String(score == null ? '' : score), ratio: 0, color: SCORE_GRAY, name: 'cinza' };
  const n = Number(m[1]);
  const d = Number(m[2]) || 1;
  const ratio = n / d;
  const color = ratio >= 1 ? SCORE_GREEN : ratio >= 0.5 ? SCORE_YELLOW : SCORE_RED;
  const name = ratio >= 1 ? 'verde' : ratio >= 0.5 ? 'amarelo' : 'vermelho';
  return { text: `${n}/${d}`, n, d, ratio, color, name };
}

// Cor da nota de SSL (A/B/C/F).
export function gradeColor(grade) {
  const g = String(grade || '').toUpperCase();
  if (g === 'A') return SCORE_GREEN;
  if (g === 'B') return '#84cc16';   // lima
  if (g === 'C') return '#f97316';   // laranja
  return SCORE_RED;                  // F / desconhecido
}

// Cor da grade de LGPD (texto do backend).
export function lgpdGradeColor(grade) {
  const g = String(grade || '').toLowerCase();
  if (g.startsWith('adequado')) return SCORE_GREEN;
  if (g.startsWith('parcial')) return SCORE_YELLOW;
  if (g.startsWith('aten')) return '#f97316';   // laranja
  return SCORE_RED;
}

// Ícone/cor de um status de check ('pass'/'fail'/'warn').
export function statusMeta(status) {
  const s = String(status || '').toLowerCase();
  if (s === 'pass') return { icon: '✓', color: SCORE_GREEN, label: 'OK' };
  if (s === 'warn') return { icon: '!', color: SCORE_YELLOW, label: 'Atenção' };
  return { icon: '✕', color: SCORE_RED, label: 'Faltando' };
}

// Agrupa a lista de tecnologias por categoria (preserva a ordem de 1ª aparição).
export function groupTechByCategory(technologies) {
  const order = [];
  const map = {};
  for (const t of technologies || []) {
    const cat = t.category || 'Outro';
    if (!map[cat]) { map[cat] = []; order.push(cat); }
    map[cat].push(t);
  }
  return order.map((cat) => ({ category: cat, items: map[cat] }));
}

// Link do CTA final: o scanner COMPLETO já com o domínio consultado (a home submete a /scan).
export function fullScanHref(domain) {
  return `/scan?url=${encodeURIComponent(String(domain || '').trim())}`;
}

// FAQ por ferramenta (3–5 perguntas) — alimenta o accordion visual (<details>) E o
// FAQPage JSON-LD (SEO). Respostas em texto simples (o Schema.org quer texto). Centralizado
// aqui para ser testável (node --test) e DRY entre a UI e o structured data.
export const FAQS = {
  ssl: [
    { q: 'O que é um certificado SSL?', a: 'É o certificado digital que ativa o HTTPS e criptografa a comunicação entre o navegador e o site, protegendo os dados em trânsito e provando a identidade do domínio.' },
    { q: 'Como saber se meu site tem SSL?', a: 'Use esta ferramenta: digite o domínio e ela verifica em segundos se o certificado é válido, quando expira, o emissor e o protocolo TLS negociado.' },
    { q: 'O que acontece quando o SSL expira?', a: 'O navegador passa a exibir um aviso de segurança ("sua conexão não é privada") e muitos visitantes desistem. Renove o certificado antes do vencimento — o Let\'s Encrypt renova automaticamente a cada 90 dias.' },
    { q: 'Qual a diferença entre TLS 1.2 e TLS 1.3?', a: 'Ambos são seguros e recomendados. O TLS 1.3 é mais novo, mais rápido (handshake menor) e removeu algoritmos antigos. Versões abaixo de 1.2 (TLS 1.0/1.1) são obsoletas.' },
    { q: 'O SSL gratuito do Let\'s Encrypt é seguro?', a: 'Sim. O Let\'s Encrypt emite certificados com a mesma criptografia dos pagos e é reconhecido por todos os navegadores. A diferença dos pagos costuma ser validação estendida (EV) e suporte, não segurança.' },
  ],
  headers: [
    { q: 'O que são headers de segurança?', a: 'São cabeçalhos HTTP que o servidor envia para instruir o navegador a se proteger contra ataques comuns (XSS, clickjacking, sniffing de conteúdo, downgrade de HTTPS).' },
    { q: 'Quais headers de segurança são mais importantes?', a: 'HSTS (força HTTPS), Content-Security-Policy (previne XSS), X-Frame-Options (previne clickjacking) e X-Content-Type-Options (nosniff) são os de maior impacto.' },
    { q: 'Como adicionar headers de segurança no meu site?', a: 'Configure-os no servidor web (Nginx: add_header; Apache: Header set) ou no seu CDN/plataforma. Comece por HSTS e nosniff, depois avance para o CSP com cuidado.' },
    { q: 'O que é Content-Security-Policy (CSP)?', a: 'É o header que define de quais origens o navegador pode carregar scripts, estilos e imagens. É a defesa mais eficaz contra injeção de código (XSS), mas exige ajuste fino para não quebrar recursos legítimos.' },
    { q: 'Headers de segurança afetam o SEO?', a: 'Indiretamente. HTTPS é fator de ranqueamento e headers robustos aumentam a confiança e a segurança dos visitantes, reduzindo abandono — sinais positivos para o SEO.' },
  ],
  lgpd: [
    { q: 'O que é a LGPD?', a: 'A Lei Geral de Proteção de Dados (Lei 13.709/2018) regula como empresas coletam, usam e protegem dados pessoais no Brasil. Vale para praticamente todo site que coleta dados de brasileiros.' },
    { q: 'Meu site precisa ter política de privacidade?', a: 'Sim. Se o site coleta qualquer dado pessoal (formulário, cookies, analytics), a política de privacidade informando o que é coletado e por quê é um requisito da LGPD.' },
    { q: 'O que é um DPO e todo site precisa ter?', a: 'O DPO (Encarregado de Proteção de Dados) é o responsável por atender titulares e a ANPD. A indicação é obrigatória para controladores de dados; identificá-lo publicamente demonstra conformidade.' },
    { q: 'Como adicionar um banner de cookies?', a: 'Use uma ferramenta de consentimento (CMP) como Cookiebot, OneTrust ou uma solução própria que bloqueie cookies não essenciais até o visitante consentir. Esta verificação detecta se há um banner presente.' },
    { q: 'Quais as multas por descumprimento da LGPD?', a: 'A ANPD pode aplicar multa de até 2% do faturamento, limitada a R$ 50 milhões por infração, além de advertência, bloqueio e eliminação dos dados.' },
  ],
  tech: [
    { q: 'Como saber qual tecnologia um site usa?', a: 'Esta ferramenta analisa os cabeçalhos HTTP, o HTML e o DNS do site e identifica CMS, framework, CDN, analytics e servidor — tudo a partir de sinais públicos, sem invasão.' },
    { q: 'O que é um CMS?', a: 'Um CMS (Sistema de Gestão de Conteúdo) permite criar e editar páginas sem programar. WordPress, Wix e Shopify são exemplos populares.' },
    { q: 'WordPress é seguro?', a: 'O núcleo do WordPress é seguro quando atualizado, mas plugins e temas desatualizados são a principal fonte de vulnerabilidades. Mantenha tudo atualizado e reduza plugins ao essencial.' },
    { q: 'O que é uma CDN e por que usar?', a: 'Uma CDN (Rede de Distribuição de Conteúdo) como Cloudflare serve o site a partir de servidores próximos ao visitante, deixando-o mais rápido e absorvendo picos e ataques.' },
    { q: 'Qual a diferença entre Cloudflare e AWS CloudFront?', a: 'Ambas são CDNs. A Cloudflare foca em facilidade, proteção contra DDoS e um plano gratuito robusto; a CloudFront integra-se profundamente ao ecossistema AWS. A escolha depende da sua infraestrutura.' },
  ],
  email: [
    { q: 'O que é SPF e por que configurar?', a: 'O SPF é um registro DNS que lista quais servidores podem enviar email pelo seu domínio. Sem ele, qualquer um pode falsificar mensagens em seu nome.' },
    { q: 'O que é DKIM?', a: 'O DKIM assina digitalmente os emails enviados pelo seu domínio, permitindo que o destinatário verifique que a mensagem é legítima e não foi adulterada no caminho.' },
    { q: 'O que é DMARC e como configurar?', a: 'O DMARC diz aos provedores o que fazer com emails que falham SPF/DKIM (monitorar, colocar em quarentena ou rejeitar). Configure um registro TXT em _dmarc.seudominio com política p=quarantine ou p=reject.' },
    { q: 'Por que meus emails vão para spam?', a: 'A causa mais comum é a ausência ou má configuração de SPF, DKIM e DMARC. Sem esses registros, provedores como Gmail e Outlook desconfiam da origem e filtram as mensagens.' },
    { q: 'Como testar se meu DMARC está funcionando?', a: 'Use esta ferramenta: digite o domínio e ela verifica SPF, DKIM, DMARC e MX, indicando o que está faltando e um exemplo de registro para corrigir.' },
  ],
};

export function faqFor(slug) {
  return FAQS[slug] || [];
}
