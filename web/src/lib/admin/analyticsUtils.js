// KL-83 Prompt 2 — lógica PURA do analytics admin (sorting, paginação, classificação de passo
// de jornada, cores por threshold, filtro de setores, escape). Sem React/DOM → testável com o
// runner nativo do Node (`node --test`), sem dependências novas.

// Ordena linhas por chave (números numericamente; strings via localeCompare; null por último).
export function sortRows(rows, key, order = 'desc') {
  const dir = order === 'asc' ? 1 : -1
  return [...(rows || [])].sort((a, b) => {
    const av = a?.[key]
    const bv = b?.[key]
    if (av == null && bv == null) return 0
    if (av == null) return 1 // nulls sempre por último, independe da direção
    if (bv == null) return -1
    if (typeof av === 'number' && typeof bv === 'number') return (av - bv) * dir
    return String(av).localeCompare(String(bv), 'pt-BR') * dir
  })
}

// Paginação client-side: fatia + total + nº de páginas, com clamp do page ao intervalo válido.
export function paginate(items, page, limit) {
  const arr = items || []
  const total = arr.length
  const pages = Math.max(1, Math.ceil(total / limit))
  const p = Math.min(Math.max(1, page || 1), pages)
  return { slice: arr.slice((p - 1) * limit, p * limit), total, pages, page: p }
}

// Classifica um passo do breadcrumb de jornada → cor do chip.
const CONVERSION_STEPS = new Set(['/cadastrar', '/scan', '/entrar', '/confirmar'])
export function journeyStepKind(step) {
  if (step === 'alerta') return 'entry'
  if (step === '[saiu]') return 'exit'
  if (CONVERSION_STEPS.has((step || '').split('?')[0])) return 'conversion'
  return 'normal'
}
export const STEP_COLOR = { entry: '#58A6FF', exit: '#F85149', conversion: '#00D26A', normal: '#8B949E' }

// Cor do bounce_rate: ≥70 vermelho · ≥50 amarelo · <50 verde.
export function bounceColor(rate) {
  if (rate >= 70) return '#F85149'
  if (rate >= 50) return '#F0C000'
  return '#00D26A'
}

// Cor da click_rate (funil por setor): ≥15 verde · ≥8 amarelo · <8 vermelho.
export function clickRateColor(rate) {
  if (rate >= 15) return '#00D26A'
  if (rate >= 8) return '#F0C000'
  return '#F85149'
}

// Δ (delta_views): cor + prefixo de sinal.
export function deltaMeta(delta) {
  if (!delta) return { text: '—', color: '#8B949E' }
  return delta > 0 ? { text: `+${delta}`, color: '#00D26A' } : { text: `${delta}`, color: '#F85149' }
}

// Filtra setores sem alertas + limita aos N mais ativos; devolve {shown, hidden}.
export function filterSectors(sectors, max = 20) {
  const active = (sectors || []).filter((s) => (s.alerts_sent ?? s.clicks ?? 0) > 0)
  return { shown: active.slice(0, max), hidden: Math.max(0, active.length - max) }
}

// Escapa HTML de um input de busca (defesa extra; o React já escapa {}).
export function escapeHtml(s) {
  const map = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }
  return String(s ?? '').replace(/[&<>"']/g, (c) => map[c])
}

// Parse do hash das abas: `#events?path=/x&group=session` → {tab, params}.
export function parseTabHash(hash, validTabs) {
  const raw = (hash || '').replace(/^#/, '')
  const [tab, qs] = raw.split('?')
  const params = Object.fromEntries(new URLSearchParams(qs || ''))
  return { tab: validTabs.includes(tab) ? tab : validTabs[0], params }
}

export function buildTabHash(tab, params = {}) {
  const qs = new URLSearchParams(
    Object.entries(params).filter(([, v]) => v != null && v !== '')).toString()
  return `#${tab}${qs ? `?${qs}` : ''}`
}

// =========================================================================== #
// KL-92 Prompt 2 — dashboard server-side (access_log). Lógica PURA (testável).
// =========================================================================== #

// Fonte de cada número no dashboard durante a transição (badge discreto).
export const DATA_SOURCE = {
  server: { icon: '📡', label: 'server', title: 'access_log (IP real, server-side)' },
  tracker: { icon: '📱', label: 'tracker', title: 'site_events (tracker.js, client-side)' },
}

// daily_series ({dates, visitors_br[], scans[], accounts[]}) → linhas p/ o LineChart.
export function dailySeriesToTrend(series) {
  const dates = series?.dates || []
  return dates.map((date, i) => ({
    date,
    visitors_br: series.visitors_br?.[i] ?? 0,
    scans: series.scans?.[i] ?? 0,
    accounts: series.accounts?.[i] ?? 0,
  }))
}

// Sparkline de um KPI a partir da série diária (array de números).
export function sparkFromDaily(series, key) {
  return (series?.[key] || []).map((v) => v ?? 0)
}

// server_funnel → etapas ordenadas p/ render (label + total + taxa da etapa anterior).
const SERVER_FUNNEL_ORDER = [
  ['visitors_br', 'Visitantes BR', null],
  ['viewed_profile', 'Viu perfil', 'visit_to_profile'],
  ['started_scan', 'Iniciou scan', 'profile_to_scan'],
  ['completed_scan', 'Concluiu scan', null],
  ['created_account', 'Criou conta', 'scan_to_account'],
  ['downloaded_pdf', 'Baixou PDF', 'account_to_pdf'],
]
export function serverFunnelStages(funnel) {
  const rates = funnel?.conversion_rates || {}
  return SERVER_FUNNEL_ORDER.map(([key, label, rateKey]) => ({
    key, label, total: funnel?.[key] ?? 0,
    rate: rateKey ? (rates[rateKey] ?? null) : null,
  }))
}

// Retenção {day_1,day_3,day_7} → barras horizontais ordenadas.
export function retentionBars(retention) {
  return [['day_1', 'D1'], ['day_3', 'D3'], ['day_7', 'D7']].map(([k, label]) => {
    const r = retention?.[k] || { returned: 0, total: 0, pct: 0 }
    return { key: k, label, pct: r.pct ?? 0, returned: r.returned ?? 0, total: r.total ?? 0 }
  })
}

// Cor de uma célula do mapa de calor (laranja da marca, opacidade ∝ volume). max=0 → transparente.
export function heatColor(count, max) {
  if (!max || count <= 0) return 'transparent'
  const op = Math.max(0.08, Math.min(1, count / max))
  return `rgba(255,107,53,${op.toFixed(2)})`
}
export const DOW_LABELS = ['Dom', 'Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb']
