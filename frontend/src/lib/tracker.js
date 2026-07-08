// Tracking interno da jornada do lead (KL-21). Fire-and-forget: nunca quebra a UI.
const BASE = import.meta.env.VITE_API_BASE || '/api'
const SID_KEY = 'klarim_sid'
const UTM_KEY = 'klarim_utm'

function getSessionId() {
  try {
    let sid = sessionStorage.getItem(SID_KEY)
    if (!sid) {
      sid = (crypto.randomUUID && crypto.randomUUID()) || String(Date.now()) + Math.random().toString(36).slice(2)
      sessionStorage.setItem(SID_KEY, sid)
    }
    return sid
  } catch {
    return 'nostorage'
  }
}

// UTM: capturado na 1ª página (com params na URL) e persistido; nas seguintes,
// lido do sessionStorage (o UTM some da URL ao navegar internamente).
function getUtm() {
  const empty = { utm_source: null, utm_medium: null, utm_campaign: null, utm_content: null }
  try {
    const p = new URLSearchParams(window.location.search)
    const fromUrl = {
      utm_source: p.get('utm_source'), utm_medium: p.get('utm_medium'),
      utm_campaign: p.get('utm_campaign'), utm_content: p.get('utm_content'),
    }
    if (fromUrl.utm_source) {
      sessionStorage.setItem(UTM_KEY, JSON.stringify(fromUrl))
      return fromUrl
    }
    const stored = sessionStorage.getItem(UTM_KEY)
    return stored ? JSON.parse(stored) : empty
  } catch {
    return empty
  }
}

// Captura o UTM logo no boot (antes de qualquer navegação interna).
export function initTracking() {
  getUtm()
}

export function trackEvent(eventType, metadata = {}, targetUrl = null) {
  try {
    const utm = getUtm()
    fetch(`${BASE}/events`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      keepalive: true, // não cancela se o usuário navegar/fechar
      body: JSON.stringify({
        event_type: eventType,
        session_id: getSessionId(),
        page_url: window.location.pathname + window.location.search,
        referrer: document.referrer || null,
        target_url: targetUrl,
        ...utm,
        metadata,
      }),
    }).catch(() => {})
  } catch {
    /* tracking nunca quebra a app */
  }
}
