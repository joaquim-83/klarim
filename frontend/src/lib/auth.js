// Gestão do token JWT do dashboard admin (KL-14). Guardado no localStorage.
const KEY = 'klarim_admin_token'

export function getToken() {
  return localStorage.getItem(KEY) || ''
}

export function setToken(token) {
  localStorage.setItem(KEY, token)
}

export function clearToken() {
  localStorage.removeItem(KEY)
}

// Lê o payload do JWT sem verificar assinatura (só para checar expiração no client;
// a validação real é feita pelo backend em toda chamada protegida).
function decodePayload(token) {
  try {
    return JSON.parse(atob(token.split('.')[1]))
  } catch {
    return null
  }
}

export function isAuthed() {
  const token = getToken()
  if (!token) return false
  const payload = decodePayload(token)
  return !!payload && typeof payload.exp === 'number' && payload.exp * 1000 > Date.now()
}
