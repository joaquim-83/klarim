// KL-89 — lógica PURA da visão do resultado do scan (confiança progressiva + linguagem
// contextual por origem). Extraída do componente para ser testável sem DOM (`node --test`),
// no mesmo padrão do KL-83 (`lib/admin/analyticsUtils.js`).
//
// IMPORTANTE: isto NÃO decide segurança. O backend (KL-82) já FILTRA o payload por nível de
// acesso e NUNCA envia evidência/impacto/LGPD a anonymous/unconfirmed. Estas funções só
// escolhem O QUE RENDERIZAR com base no que o backend entregou — e produzem O MESMO resultado
// para desktop e mobile (as flags derivam só do nível, nunca do dispositivo).

// Os 4 níveis do KL-82. Ordem de confiança: anonymous < alert_session < unconfirmed < confirmed.
export const ACCESS_LEVELS = ['anonymous', 'unconfirmed', 'confirmed', 'alert_session'];

// Nível normalizado (default anonymous — o mais restrito, fail-safe).
export function accessLevelOf(result) {
  const lvl = result && result.access_level;
  return ACCESS_LEVELS.includes(lvl) ? lvl : 'anonymous';
}

// Visitante veio do ALERTA (clicou no link HMAC → sessão temporária escopada, KL-82 Slice 3).
// O sinal vem do backend (`access_level='alert_session'` + `alert_email_hint`), NUNCA de query
// params do frontend — o HMAC é validado server-side.
export function isAlertVisitor(result) {
  return accessLevelOf(result) === 'alert_session';
}

// Já tem conta (não faz sentido mostrar CTA de "criar conta").
export function hasAccount(level) {
  return level === 'confirmed' || level === 'unconfirmed';
}

// Acesso COMPLETO ao resultado: todos os riscos, checks com evidência, LGPD, PDF do backend.
export function isFullAccess(level) {
  return level === 'confirmed' || level === 'alert_session';
}

// Máscara de e-mail p/ exibição pública: 1ª letra + *** + última letra antes do @. Nunca
// expõe o e-mail inteiro em HTML público (regra de segurança do card). Ex.: joao@x.com → j***o@x.com.
export function maskEmail(email) {
  const s = String(email || '');
  const at = s.lastIndexOf('@');
  if (at < 1) return '';
  const user = s.slice(0, at);
  const domain = s.slice(at + 1);
  if (!domain) return '';
  if (user.length <= 2) return `${user[0]}***@${domain}`;
  return `${user[0]}***${user[user.length - 1]}@${domain}`;
}

// O hint mascarado já vem pronto do backend (`alert_email_hint`); só cai no maskEmail local se
// o backend não mandou (defensivo).
export function maskedEmailOf(result) {
  if (result && result.alert_email_hint) return result.alert_email_hint;
  return maskEmail(result && result.alert_email);
}

// Frase contextual do score. Alerta (vendo o PRÓPRIO site) → "Seu site"; orgânico → "Este
// site. E o seu?" (a pergunta só faz sentido para quem NÃO é o dono — KL-89 item 4).
export function scoreHeadline(score, alertVisitor) {
  if (alertVisitor) {
    return { lead: `Seu site tem score ${score}.`, tail: 'Veja o que melhorar.', question: null };
  }
  return { lead: `Este site tem score ${score}.`, tail: null, question: 'E o seu?' };
}

// Rótulo da linha de compartilhamento (adapta o possessivo pela origem).
export function shareLabel(alertVisitor) {
  return alertVisitor ? 'Compartilhe seu resultado' : 'Compartilhe este resultado';
}

// Copy do bloco CTA de conta (título/benefícios/botão), adaptada por origem. Benefícios em
// linguagem HUMANA (não features técnicas): "sair do ar", "certificados vencendo", "evolução".
export function ctaCopy(alertVisitor, domain) {
  const benefits = [
    'Saiba na hora se ele sair do ar',
    'Receba alertas se os certificados vencerem',
    'Acompanhe a evolução do score',
  ];
  if (alertVisitor) {
    return {
      title: 'Monitore seu site gratuitamente',
      benefits,
      button: 'Criar conta →',
      passwordOnly: true, // e-mail já confirmado via HMAC → só falta a senha
    };
  }
  return {
    title: domain ? `Monitore ${domain} gratuitamente` : 'Monitore este site gratuitamente',
    benefits,
    button: 'Criar conta gratuita →',
    passwordOnly: false, // orgânico → e-mail + senha
  };
}

// Regras de visibilidade por nível (MESMAS p/ desktop e mobile). É a "tabela de visibilidade"
// do KL-89 item 2 traduzida em flags. Espelha o que o backend já filtrou.
export function viewFlags(result) {
  const level = accessLevelOf(result);
  const full = isFullAccess(level);
  return {
    level,
    full,
    alertVisitor: level === 'alert_session',
    // Alerta → e-mail já confirmado via HMAC (vem do cookie) → o signup pede SÓ senha.
    passwordOnly: level === 'alert_session',
    showScore: true,
    showShare: true,
    showPdf: true, // PDF é público com o paywall desligado (default) → disponível em todo nível
    showCTA: !hasAccount(level), // some para quem já tem conta
    // Benchmark é PÚBLICO (contextualiza o score, dado agregado nacional já exposto em
    // /estatisticas e /setores) → visível em TODO nível, SEM cadeado, desktop e mobile (KL-89 fix 5).
    showBenchmark: true,
    showAllRisks: full,
    // Categorias: barras (anônimo) < resumo com números (unconfirmed) < accordion com evidência (full).
    categoriesMode: level === 'anonymous' ? 'bars' : full ? 'full' : 'summary',
    showEvidence: full,
    // Indicadores de privacidade/LGPD: restritos a acesso COMPLETO (confirmed/alert_session).
    // Travados p/ anonymous E unconfirmed em desktop E mobile — deriva só do nível (KL-89 fix 2).
    showPrivacy: full,
  };
}

// KL-89 fix 6 — progresso do scanner por categoria. O backend só devolve o `percent` global
// (quantos dos 48 checks completaram); o frontend mapeia esse % às 6 camadas por faixas
// proporcionais para dar sensação de progresso real (proxy honesto, não invenção de dados).
export const SCAN_CATEGORIES = [
  { name: 'Transporte & TLS', start: 0, end: 16 },
  { name: 'Headers de segurança', start: 17, end: 33 },
  { name: 'Supply chain', start: 34, end: 50 },
  { name: 'DNS & E-mail', start: 51, end: 66 },
  { name: 'Conteúdo', start: 67, end: 83 },
  { name: 'OSINT & Reputação', start: 84, end: 100 },
];

// Estado de uma categoria dado o % atual: 'done' (✅), 'active' (⏳) ou 'pending' (○).
export function getCategoryStatus(category, currentPercent) {
  if (currentPercent >= category.end) return 'done';
  if (currentPercent >= category.start) return 'active';
  return 'pending';
}

// URLs dos relatórios PDF. O backend só popula `report_urls` nos níveis full; nos demais
// construímos a URL do endpoint público (`/report/*` é gratuito com o paywall desligado —
// default freemium). Assim o PDF fica acessível SEM conta, como manda o card.
export function reportUrls(result, url) {
  if (result && result.report_urls) return result.report_urls;
  const q = `url=${encodeURIComponent(url || '')}`;
  return { executive: `/report/executive?${q}`, technical: `/report/technical?${q}` };
}
