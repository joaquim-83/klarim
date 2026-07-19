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
