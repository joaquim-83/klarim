// Cliente das APIs de gestão (protegidas por JWT). Anexa o Bearer token e, em
// 401, limpa o token e manda para o login. Em prod o Nginx faz proxy /api -> api.
// Portado de frontend/src/lib/adminApi.js na migração Vite → Astro (KL-51 fase 1):
// mesmo padrão Bearer/localStorage e o mesmo objeto `admin` com TODOS os endpoints.
// A base é sempre same-origin `/api` (Nginx → FastAPI), como o cliente público do Astro.
import { getToken, clearToken, setToken } from './auth'

const BASE = '/api'

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
const patch = (path, body) =>
  req(path, { method: 'PATCH', body: body ? JSON.stringify(body) : undefined })
const put = (path, body) =>
  req(path, { method: 'PUT', body: body ? JSON.stringify(body) : undefined })

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
  dashboardStats: () => get('/admin/dashboard-stats'),  // totalizadores KL-57
  targetsStats: () => get('/targets/stats'),
  alertsStats: () => get('/alerts/stats'),
  rescansStats: () => get('/rescans/stats'),
  scansStats: () => get('/scans/stats'),
  paymentsStats: () => get('/payments/stats'),
  subscriptionPaymentStats: () => get('/payments/subscription-stats'),  // KL-44 P6
  scansDaily: (days = 30) => get(`/scans/daily?days=${days}`),
  alertsDaily: (days = 30) => get(`/alerts/daily?days=${days}`),

  // alvos
  targets: (params) => get(`/targets${qs(params)}`),
  target: (id) => get(`/targets/${id}`),
  addTarget: (url) => post('/targets/add', { url }),
  // FIX scan admin: síncrono (sync=1) devolve score/semaphore imediatamente
  scanTarget: (id) => post(`/targets/${id}/scan?sync=1`),
  enqueueScan: (id) => post(`/targets/${id}/scan`),  // assíncrono (fila) — legado
  alertTarget: (id) => post(`/targets/${id}/alert`),
  rescanTarget: (id) => post(`/targets/${id}/rescan`),
  discardTarget: (id) => post(`/targets/${id}/discard`),
  updateStatus: (id, status) => patch(`/targets/${id}/status`, { status }),
  updateEmail: (id, email) => patch(`/targets/${id}/email`, { contact_email: email }),

  // propriedade / ownership (KL-68)
  revokeOwnership: (id) => post(`/targets/${id}/revoke-ownership`),
  ownershipStats: () => get('/admin/ownership-stats'),
  cleanBlockedSites: (dryRun) => post(`/admin/clean-blocked-sites${dryRun ? '?dry_run=1' : ''}`),

  // gestão de usuários (KL-69) — página unificada
  users: () => get('/admin/clients'),
  removeUserSite: (userId, targetId, notify = true) =>
    post(`/admin/users/${userId}/remove-site`, { target_id: targetId, notify }),
  deactivateUser: (userId, notify = true) => post(`/admin/users/${userId}/deactivate`, { notify }),
  reactivateUser: (userId, notify = true) => post(`/admin/users/${userId}/reactivate`, { notify }),
  // FIX gestão de planos: aliases sobre /admin/subscriptions/* (account_id == users.id).
  // change_plan já ajusta as vigílias (via _sync_user_vigilias) e o status (free → 'free').
  changeUserPlan: (userId, plan) =>
    patch(`/admin/subscriptions/${userId}/plan`, { plan_id: plan, reason: 'gestão de usuários' }),
  extendUserTrial: (userId, days = 30) =>
    patch(`/admin/subscriptions/${userId}/trial`, { days }),
  resetUserFree: (userId) =>
    patch(`/admin/subscriptions/${userId}/plan`, { plan_id: 'free', reason: 'reset para free' }),

  // landing pública / perfil (KL-56)
  updateProfile: (id, fields) => put(`/targets/${id}/profile`, fields),
  setProfileVisibility: (id, visible) =>
    patch(`/targets/${id}/profile/visibility`, { visible }),
  // KL-67: revalidação retroativa dos perfis (dry-run mostra o impacto)
  revalidateProfiles: (dryRun) => post(`/admin/revalidate-profiles${dryRun ? '?dry_run=1' : ''}`),

  // scans
  scans: (params) => get(`/scans${qs(params)}`),
  scan: (id) => get(`/scans/${id}`),

  // inbox scan@klarim.net (KL-56)
  inbox: (params) => get(`/admin/inbox${qs(params)}`),
  inboxMessage: (id) => get(`/admin/inbox/${id}`),
  inboxUnread: () => get('/admin/inbox/unread-count'),
  inboxRead: (id, read = true) => post(`/admin/inbox/${id}/read?read=${read}`),
  inboxStar: (id) => post(`/admin/inbox/${id}/star`),
  inboxArchive: (id, archived = true) => post(`/admin/inbox/${id}/archive?archived=${archived}`),

  // alertas / re-scans / pagamentos
  alerts: (params) => get(`/alerts${qs(params)}`),
  rescans: (params) => get(`/rescans${qs(params)}`),
  payments: (params) => get(`/payments/list${qs(params)}`),

  // sites monitorados — KL-29
  monitoredList: (status) => get(`/monitoring/admin/list${status ? `?status=${status}` : ''}`),
  monitoredStats: () => get('/monitoring/admin/stats'),
  monitoredSetStatus: (id, status, reason) =>
    post(`/monitoring/admin/${id}/status`, { status, reason }),

  // gestão de clientes — contas de usuário + sites (KL-51 f3 fix)
  clients: () => get('/admin/clients'),

  // configurações operacionais (read-only, legado)
  config: () => get('/config'),

  // configurações editáveis + segurança (KL-44)
  configList: () => get('/admin/config'),
  configPut: (key, value) => put(`/admin/config/${key}`, { value: String(value) }),
  configReset: (key) => post(`/admin/config/reset/${key}`, {}),
  changePassword: (body) => patch('/admin/password', body),
  rotateMcpToken: (currentPassword) => post('/admin/rotate-mcp-token', { current_password: currentPassword }),
  systemInfo: () => get('/admin/system-info'),

  // status do Discovery Worker — KL-15
  discoveryStatus: () => get('/discovery/status'),

  // dashboard operacional — KL-16
  systemStatus: () => get('/system/status'),
  systemActivity: (limit = 50) => get(`/system/activity?limit=${limit}`),

  // saúde de e-mail / bounce (KL-24)
  emailHealth: () => get('/system/email-health'),
  processBounces: () => post('/admin/process-bounces'),

  // analytics da jornada do lead — KL-21
  analyticsFunnel: (period = '7d') => get(`/analytics/funnel?period=${period}`),
  analyticsAbandoned: (period = '7d') => get(`/analytics/abandoned?period=${period}`),
  analyticsCampaigns: (period = '7d') => get(`/analytics/campaigns?period=${period}`),
  analyticsPages: (period = '7d') => get(`/analytics/pages?period=${period}`),
  analyticsEvents: (limit = 50, eventType) =>
    get(`/analytics/events?limit=${limit}${eventType ? `&event_type=${eventType}` : ''}`),
  publicScans: () => get('/analytics/public-scans'),  // KL-25

  // KL-83 — analytics admin redesenhado (8 endpoints /admin/analytics/*)
  aaMetrics: (period = '7d') => get(`/admin/analytics/metrics?period=${period}`),
  aaTrend: (period = '30d', metrics = 'visitors,scans,accounts') =>
    get(`/admin/analytics/trend?period=${period}&metrics=${metrics}`),
  aaFunnel: (period = '7d') => get(`/admin/analytics/funnel?period=${period}`),
  aaEvents: (params) => get(`/admin/analytics/events${qs(params)}`),
  aaSessions: (params) => get(`/admin/analytics/sessions${qs(params)}`),
  aaPages: (params) => get(`/admin/analytics/pages${qs(params)}`),
  aaJourneys: (period = '7d', limit = 10) =>
    get(`/admin/analytics/journeys?period=${period}&limit=${limit}`),
  aaFunnelBySector: (period = '7d') => get(`/admin/analytics/funnel-by-sector?period=${period}`),

  // reclassificação de setor (refino KL-11)
  reclassifyDomains: () => post('/admin/reclassify-domains'),
  reclassifyAll: () => post('/admin/reclassify-all'),
  reclassifyStatus: () => get('/admin/reclassify-status'),

  // classificação manual (operador)
  classifyTarget: (id, sector, priceTier) =>
    patch(`/targets/${id}/classify`, { sector, price_tier: priceTier }),
  classifyBatch: (ids, sector, priceTier) =>
    post('/admin/classify-batch', { target_ids: ids, sector, price_tier: priceTier }),

  // leads — KL-61
  leads: (params) => get(`/leads${qs(params)}`),
  lead: (id) => get(`/leads/${id}`),
  leadStats: () => get('/leads/stats'),
  leadFunnel: () => get('/leads/funnel'),
  updateLead: (id, fields) => patch(`/leads/${id}`, fields),
  recalcLeads: () => post('/leads/recalculate'),

  // planos & assinaturas — KL-44 (Guardião Digital)
  plans: () => get('/admin/plans'),
  plan: (id) => get(`/admin/plans/${id}`),
  updatePlan: (id, fields) => put(`/admin/plans/${id}`, fields),
  subStats: () => get('/admin/subscriptions/stats'),
  subscribers: (params) => get(`/admin/subscriptions${qs(params)}`),
  subscription: (accountId) => get(`/admin/subscriptions/${accountId}`),
  subHistory: (accountId) => get(`/admin/subscriptions/${accountId}/history`),
  subChangePlan: (accountId, planId, reason) =>
    patch(`/admin/subscriptions/${accountId}/plan`, { plan_id: planId, reason }),
  subExtendTrial: (accountId, days) =>
    patch(`/admin/subscriptions/${accountId}/trial`, { days }),
  subSetStatus: (accountId, status, reason) =>
    patch(`/admin/subscriptions/${accountId}/status`, { status, reason }),
  subBulk: (body) => post('/admin/subscriptions/bulk', body),

  // vigílias (KL-44 P2)
  vigiliaStats: () => get('/admin/vigilias/stats'),
  vigilias: (params) => get(`/admin/vigilias${qs(params)}`),
  vigilia: (id) => get(`/admin/vigilias/${id}`),
  vigiliaAlerts: (params) => get(`/admin/vigilia-alerts${qs(params)}`),

  // fluxo integrado (KL-17)
  scanAndReport: (body) => post('/admin/scan-and-report', body),
  resendAlert: (targetId) => post('/admin/resend-alert', { target_id: targetId }),
  sendReport: (targetId, emailTo) => post('/admin/send-report', { target_id: targetId, email_to: emailTo }),
  resendPayment: (chargeId) => post('/admin/resend-payment', { charge_id: chargeId }),
  targetPayments: (id) => get(`/targets/${id}/payments`),

  // --- KL-84: taxonomia aberta de setores ---------------------------------- //
  sectors: (status = 'all') => get(`/admin/sectors?status=${status}`),
  sectorExamples: (slug, limit = 5) => get(`/admin/sectors/${slug}/examples?limit=${limit}`),
  approveSector: (slug, body) => post(`/admin/sectors/${slug}/approve`, body || {}),
  mergeSector: (slug, mergeInto) => post(`/admin/sectors/${slug}/merge`, { merge_into: mergeInto }),
  rejectSector: (slug) => post(`/admin/sectors/${slug}/reject`),
}
