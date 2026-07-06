// Normaliza a URL digitada pelo usuário: adiciona https:// se faltar esquema.
export function normalizeUrl(raw) {
  const value = (raw || '').trim()
  if (!value) return ''
  if (/^https?:\/\//i.test(value)) return value
  return `https://${value}`
}

// Validação básica: precisa ter um host plausível após normalizar.
export function isValidUrl(raw) {
  try {
    const u = new URL(normalizeUrl(raw))
    return !!u.hostname && u.hostname.includes('.')
  } catch {
    return false
  }
}
