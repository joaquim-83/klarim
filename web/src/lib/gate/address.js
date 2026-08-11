// KL-163 P2 — lógica PURA de endereço estruturado (CEP + ViaCEP), testável (node --test). Sem DOM.
// Os componentes React (AddressFields/KycModal) consomem estas funções. A validação FINAL é no
// backend (`api/gate.py::_validate_and_normalize_address`); aqui é feedback imediato + parsing.

// 27 UFs (26 estados + DF) para o <select>. Ordem alfabética.
export const UF_LIST = [
  'AC', 'AL', 'AP', 'AM', 'BA', 'CE', 'DF', 'ES', 'GO', 'MA', 'MT', 'MS', 'MG', 'PA',
  'PB', 'PR', 'PE', 'PI', 'RJ', 'RN', 'RS', 'RO', 'RR', 'SC', 'SP', 'SE', 'TO',
];
const UF_SET = new Set(UF_LIST);

// Campos obrigatórios do endereço (complemento é opcional). Espelha `_ADDRESS_REQUIRED` do backend.
export const ADDRESS_REQUIRED = ['cep', 'street', 'number', 'neighborhood', 'city', 'state'];

// Máscara progressiva "80010000" → "80010-000" (aceita já formatado; máx 8 dígitos).
export function maskCep(value) {
  const d = (value || '').replace(/\D/g, '').slice(0, 8);
  return d.length > 5 ? `${d.slice(0, 5)}-${d.slice(5)}` : d;
}

// CEP válido = exatamente 8 dígitos (com ou sem traço).
export function isValidCep(cep) {
  return /^\d{5}-?\d{3}$/.test((cep || '').trim());
}

// Resposta do ViaCEP → campos do nosso formato, ou null se erro/vazio. O ViaCEP devolve
// `{erro:true}` (ou `{erro:'true'}`) para CEP inexistente. Só preenche rua/bairro/cidade/UF; o
// número e o complemento são do usuário. Campos preenchidos ficam editáveis.
export function parseViaCepResponse(data) {
  if (!data || data.erro) return null;
  const state = String(data.uf || '').toUpperCase();
  const out = {
    cep: maskCep(data.cep || ''),
    street: data.logradouro || '',
    neighborhood: data.bairro || '',
    city: data.localidade || '',
    state: UF_SET.has(state) ? state : '',
  };
  // ViaCEP às vezes devolve só o CEP (sem logradouro em CEPs de cidade inteira) — ainda é útil.
  if (!out.cep && !out.city) return null;
  return out;
}

// Endereço completo o bastante para submeter (espelha o backend). `complement` é opcional.
export function isAddressComplete(addr) {
  if (!addr || typeof addr !== 'object') return false;
  for (const f of ADDRESS_REQUIRED) {
    if (!String(addr[f] || '').trim()) return false;
  }
  if (!isValidCep(addr.cep)) return false;
  if (!UF_SET.has(String(addr.state || '').toUpperCase())) return false;
  return true;
}

// Endereço vazio (para inicializar o formulário).
export function emptyAddress() {
  return { cep: '', street: '', number: '', complement: '', neighborhood: '', city: '', state: '' };
}
