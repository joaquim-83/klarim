// KL-83 — card colapsável de sessão com timeline (Aba 2 toggle ON + Aba 4 drill-down).
import { useState } from 'react'
import { Badge } from '../ui'

export const CAMPAIGN_COLOR = {
  alerta: '#3B82F6', profile_view: '#00D26A', alerta_score100: '#F0C000', '(sem campanha)': '#8B949E',
}
export const EV_COLOR = {
  page_view: '#8B949E', profile_view: '#2DD4BF', scan_started: '#58A6FF', scan_completed: '#58A6FF',
  scan_anonymous: '#58A6FF', cta_clicked: '#A855F7', payment_created: '#F0C000',
  payment_completed: '#00D26A', account_created: '#FF6B35', account_created_alert: '#FF6B35',
}

function fmtDate(s) {
  if (!s) return '—'
  try { return new Date(/[Z+]/.test(s) ? s : `${s}Z`).toLocaleTimeString('pt-BR') } catch { return s }
}
function secs(s) {
  const n = Math.round(Number(s || 0))
  return n < 60 ? `${n}s` : `${Math.floor(n / 60)}min ${n % 60}s`
}

export default function SessionCard({ s, defaultOpen = false }) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="rounded-lg border border-klarim-border bg-klarim-surface">
      <button onClick={() => setOpen((o) => !o)} aria-expanded={open}
        className="flex w-full items-center justify-between gap-2 px-4 py-3 text-left">
        <span className="text-sm">
          <span aria-hidden="true">{open ? '▼' : '▶'}</span>{' '}
          <span className="font-mono text-klarim-muted">#{(s.session_id || '').slice(0, 8)}…</span>
          <span className="ml-2 text-klarim-muted">{s.event_count} eventos · {secs(s.duration_seconds)}</span>
        </span>
        <span className="flex items-center gap-2">
          {s.campaign && <Badge color={CAMPAIGN_COLOR[s.campaign] || '#8B949E'}>{s.campaign}</Badge>}
          <Badge color={s.converted ? '#00D26A' : '#8B949E'}>
            {s.converted ? '● Converteu' : '○ Abandonou'}
          </Badge>
        </span>
      </button>
      {open && (
        <ul className="border-t border-klarim-border px-4 py-2">
          {(s.events || []).length === 0
            ? <li className="py-1 text-xs text-klarim-muted">Eventos não carregados.</li>
            : s.events.map((e, i) => (
              <li key={i} className="flex items-center gap-3 py-1 text-xs">
                <span className="w-20 shrink-0 text-klarim-muted">{fmtDate(e.created_at)}</span>
                <Badge color={EV_COLOR[e.event_type] || '#8B949E'}>{e.event_type}</Badge>
                <span className="truncate text-klarim-muted">
                  {e.page_url || e.domain || e.target_url || ''}
                  {e.utm_campaign ? ` (via ${e.utm_campaign})` : ''}
                </span>
              </li>
            ))}
        </ul>
      )}
    </div>
  )
}
