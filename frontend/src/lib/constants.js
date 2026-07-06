// Cor do semáforo por classificação (mesma paleta do PDF).
export const SEMAPHORE_COLORS = {
  verde: '#00D26A',
  amarelo: '#F0C000',
  vermelho: '#F85149',
}

export const LGPD_TEXT =
  'Se o seu site coleta dados pessoais (nome, CPF, e-mail, cartão de crédito), ' +
  'você está sujeito à Lei Geral de Proteção de Dados (LGPD). Falhas de segurança ' +
  'podem resultar em sanções de até R$ 50 milhões por infração (Art. 52).'

// Mensagens rotativas exibidas durante o scan (feedback visual — a API não
// envia progresso; retorna tudo de uma vez).
export const SCAN_STEPS = [
  'Verificando HTTPS e redirecionamento...',
  'Validando certificado SSL/TLS...',
  'Analisando headers de segurança...',
  'Procurando arquivos sensíveis expostos...',
  'Checando scripts de terceiros e SRI...',
  'Avaliando fontes de scripts e domínios externos...',
  'Calculando o score final...',
]
