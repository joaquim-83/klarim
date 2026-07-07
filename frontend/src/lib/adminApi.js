// Cliente das APIs de gestão (protegidas por JWT). Anexa o Bearer token e, em
// 401, limpa o token e manda para o login. Em prod o Nginx faz proxy /api -> api.
import { getToken, clearToken, setToken } from './auth'

const BASE = import.meta.env.VITE_API_BASE || '/api'

async function req(path, opts = {}) {
  const headers = { ...(opts.headers || {}) }
  const token = getToken()
  if (token) headers['Authorization'] = `Bearer ${token}`
  if (opts.body && !headers['Content-Type']) headers['Content-Type'] = 'application/json'

  const resp = await fetch(`${BASE}${path}`, { ...opts, headers })
  if (resp.status === 401) {
    clearToken()
    if (window.location.pathname !== '/painel/login') {
      window.location.href = '/painel/login'
    }
    throw new Error('Sessão expirada. Faça login novamente.')
  }
  if (!resp.ok) {
    const detail = await resp.text().catch(() => '')
    throw new Error(`Erro ${resp.status}. ${detail}`)
  }
  return resp.status === 204 ? null : resp.json()
}

const get = (path) => req(path)
const post = (path, body) =>
  req(path, { method: 'POST', body: body ? JSON.stringify(body) : undefined })

// Baixa um arquivo protegido (envia o Bearer token e dispara o download).
export async function adminDownload(path, fallbackName) {
  const token = getToken()
  const resp = await fetch(`${BASE}${path}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  })
  if (resp.status === 401) {
    clearToken()
    window.location.href = '/painel/login'
    throw new Error('Sessão expirada.')
  }
  if (!resp.ok) throw new Error(`Falha ao baixar (${resp.status}).`)
  const blob = await resp.blob()
  const objectUrl = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = objectUrl
  const disposition = resp.headers.get('content-disposition') || ''
  const match = disposition.match(/filename="([^"]+)"/)
  a.download = match ? match[1] : fallbackName
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(objectUrl)
}

function qs(params) {
  const q = new URLSearchParams()
  for (const [k, v] of Object.entries(params || {})) {
    if (v !== undefined && v !== null && v !== '') q.append(k, v)
  }
  const s = q.toString()
  return s ? `?${s}` : ''
}

// --- login ----------------------------------------------------------------- //

export async function login(username, password) {
  const resp = await fetch(`${BASE}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  })
  if (resp.status === 401) throw new Error('Usuário ou senha inválidos.')
  if (resp.status === 503) throw new Error('Autenticação não configurada no servidor.')
  if (!resp.ok) throw new Error(`Falha no login (${resp.status}).`)
  const data = await resp.json()
  setToken(data.token)
  return data
}

// --- endpoints de gestão --------------------------------------------------- //

export const admin = {
  // dashboard
  targetsStats: () => get('/targets/stats'),
  alertsStats: () => get('/alerts/stats'),
  rescansStats: () => get('/rescans/stats'),
  scansStats: () => get('/scans/stats'),
  paymentsStats: () => get('/payments/stats'),
  scansDaily: (days = 30) => get(`/scans/daily?days=${days}`),
  alertsDaily: (days = 30) => get(`/alerts/daily?days=${days}`),

  // alvos
  targets: (params) => get(`/targets${qs(params)}`),
  target: (id) => get(`/targets/${id}`),
  addTarget: (url) => post('/targets/add', { url }),
  scanTarget: (id) => post(`/targets/${id}/scan`),
  alertTarget: (id) => post(`/targets/${id}/alert`),
  rescanTarget: (id) => post(`/targets/${id}/rescan`),
  discardTarget: (id) => post(`/targets/${id}/discard`),

  // scans
  scans: (params) => get(`/scans${qs(params)}`),
  scan: (id) => get(`/scans/${id}`),

  // alertas / re-scans / pagamentos
  alerts: (params) => get(`/alerts${qs(params)}`),
  rescans: (params) => get(`/rescans${qs(params)}`),
  payments: (params) => get(`/payments/list${qs(params)}`),

  // configurações operacionais (read-only)
  config: () => get('/config'),
}
