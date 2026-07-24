// KL-104 P2 — modelo PURO dos filtros da página Alvos (URL <-> estado <-> params da API).
// Sem React → testável com `node --test`. Os filtros combinam com AND no backend.

export const SELECT_FILTERS = ['status', 'platform', 'sector', 'source',
  'score', 'semaphore', 'lead_score', 'last_scan']       // string única
export const MULTI_FILTERS = ['site_type', 'tech']         // CSV (múltiplos valores)
export const BOOL_FILTERS = ['has_email', 'monitored', 'owner_verified',
  'has_ai_profile', 'low_confidence']                      // 3 estados: undefined/true/false
export const TEXT_FILTERS = ['search']

export const ALL_FILTER_KEYS = [
  ...SELECT_FILTERS, ...MULTI_FILTERS, ...BOOL_FILTERS, ...TEXT_FILTERS,
]

// Lê os filtros de uma query string (`?a=1&b=true`). Bools só 'true'/'false'; o resto é texto.
export function readFiltersFromURL(searchStr) {
  const p = new URLSearchParams(searchStr || '')
  const f = {}
  for (const k of [...SELECT_FILTERS, ...MULTI_FILTERS, ...TEXT_FILTERS]) {
    const v = p.get(k)
    if (v != null && v !== '') f[k] = v
  }
  for (const k of BOOL_FILTERS) {
    const v = p.get(k)
    if (v === 'true') f[k] = true
    else if (v === 'false') f[k] = false
  }
  return f
}

// Serializa os filtros ATIVOS numa query string (para a URL e para bookmark/compartilhar).
export function filtersToQueryString(f) {
  const p = new URLSearchParams()
  for (const k of [...SELECT_FILTERS, ...MULTI_FILTERS, ...TEXT_FILTERS]) {
    if (f[k]) p.set(k, f[k])
  }
  for (const k of BOOL_FILTERS) {
    if (f[k] === true) p.set(k, 'true')
    else if (f[k] === false) p.set(k, 'false')
  }
  return p.toString()
}

// Params para o fetch (`admin.targets(...)`). Bools passam como boolean; strings como estão.
export function filtersToApiParams(f) {
  const out = {}
  for (const k of [...SELECT_FILTERS, ...MULTI_FILTERS, ...TEXT_FILTERS]) {
    if (f[k]) out[k] = f[k]
  }
  for (const k of BOOL_FILTERS) {
    if (f[k] === true || f[k] === false) out[k] = f[k]
  }
  return out
}

// Nº de filtros ativos (para mostrar "Limpar filtros" e o badge de avançados).
export function activeFilterCount(f) {
  let n = 0
  for (const k of ALL_FILTER_KEYS) {
    const v = f[k]
    if (v === true || v === false) n += 1
    else if (typeof v === 'string' && v !== '') n += 1
  }
  return n
}

// Toggle 3-estados: undefined → true → false → undefined.
export function nextToggle(v) {
  if (v === undefined || v === null) return true
  if (v === true) return false
  return undefined
}

// Adiciona/remove um valor de um filtro multi (CSV). Retorna o novo CSV (ou undefined se vazio).
export function toggleMultiValue(csv, value) {
  const set = new Set((csv || '').split(',').map((s) => s.trim()).filter(Boolean))
  set.has(value) ? set.delete(value) : set.add(value)
  const arr = [...set]
  return arr.length ? arr.join(',') : undefined
}

export function multiValues(csv) {
  return (csv || '').split(',').map((s) => s.trim()).filter(Boolean)
}
