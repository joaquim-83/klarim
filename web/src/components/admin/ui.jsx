// Primitivas de UI do dashboard admin (KL-14) — paleta dark do Klarim.
// Portado de frontend/src/components/admin/ui.jsx (KL-51 fase 1) — sem alteração de
// comportamento. Os tokens klarim-* vivem em web/src/styles/global.css.

export const SEMAPHORE_COLOR = { verde: '#00D26A', amarelo: '#F0C000', vermelho: '#F85149' }

export const PLATFORM_COLOR = {
  duda: '#2DD4BF', wordpress: '#3B82F6', cra: '#FF6B35', wix: '#A855F7',
  shopify: '#22C55E', squarespace: '#94A3B8', unknown: '#8B949E',
}

export const STATUS_COLOR = {
  discovered: '#8B949E', scanned: '#3B82F6', alerted: '#FF6B35',
  converted: '#00D26A', sem_contato: '#6E7681', unsubscribed: '#F85149',
  descartado: '#484F58',
}

export const STATUS_LABEL = {
  discovered: 'descoberto', scanned: 'escaneado', alerted: 'alertado',
  converted: 'convertido', sem_contato: 'sem contato', unsubscribed: 'descadastrado',
  descartado: 'descartado',
}

export const SOURCE_META = {
  public: { label: 'público', color: '#8B949E' },
  discovery: { label: 'discovery', color: '#2DD4BF' },
  admin: { label: 'admin', color: '#FF6B35' },
  manual: { label: 'manual', color: '#3B82F6' },
  rescan: { label: 'rescan', color: '#A855F7' },
}

export function SourceBadge({ source }) {
  const m = SOURCE_META[source] || { label: source || '—', color: '#8B949E' }
  return <Badge color={m.color}>{m.label}</Badge>
}

export const EVOLUTION_META = {
  improved: { label: 'melhorou', icon: '🟢', color: '#00D26A' },
  worsened: { label: 'piorou', icon: '🔴', color: '#F85149' },
  unchanged: { label: 'igual', icon: '⚪', color: '#8B949E' },
  first_rescan: { label: '1º re-scan', icon: '🔵', color: '#3B82F6' },
}

export function Spinner({ size = 32 }) {
  return (
    <span
      className="klarim-spinner inline-block"
      style={{ width: size, height: size }}
      role="status"
      aria-label="Carregando"
    />
  )
}

export function Loading({ label = 'Carregando…' }) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-16 text-klarim-muted">
      <Spinner />
      <span>{label}</span>
    </div>
  )
}

export function ErrorBox({ message }) {
  if (!message) return null
  return (
    <div className="rounded-lg border border-klarim-fail/40 bg-klarim-fail/10 px-4 py-3 text-sm text-klarim-fail">
      {message}
    </div>
  )
}

export function Card({ title, children, className = '' }) {
  return (
    <div className={`rounded-xl border border-klarim-border bg-klarim-surface p-4 ${className}`}>
      {title && <h3 className="mb-3 text-sm font-semibold text-klarim-muted">{title}</h3>}
      {children}
    </div>
  )
}

export function StatCard({ label, value, sub, accent = '#E6EDF3' }) {
  return (
    <div className="rounded-xl border border-klarim-border bg-klarim-surface p-4">
      <div className="text-xs uppercase tracking-wide text-klarim-muted">{label}</div>
      <div className="mt-1 text-2xl font-extrabold" style={{ color: accent }}>{value}</div>
      {sub && <div className="mt-0.5 text-xs text-klarim-muted">{sub}</div>}
    </div>
  )
}

export function Badge({ children, color = '#8B949E' }) {
  return (
    <span
      className="inline-block rounded-full px-2 py-0.5 text-xs font-semibold"
      style={{ color, backgroundColor: `${color}22`, border: `1px solid ${color}55` }}
    >
      {children}
    </span>
  )
}

export function PlatformBadge({ platform }) {
  return <Badge color={PLATFORM_COLOR[platform] || PLATFORM_COLOR.unknown}>{platform || 'unknown'}</Badge>
}

export function StatusBadge({ status }) {
  return <Badge color={STATUS_COLOR[status] || '#8B949E'}>{STATUS_LABEL[status] || status || '—'}</Badge>
}

export function SemaphoreDot({ semaphore, score }) {
  const color = SEMAPHORE_COLOR[semaphore] || '#8B949E'
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className="inline-block h-2.5 w-2.5 rounded-full" style={{ backgroundColor: color }} />
      {score !== undefined && score !== null && (
        <span className="font-semibold" style={{ color }}>{score}</span>
      )}
    </span>
  )
}

export function Button({ children, onClick, variant = 'ghost', disabled, size = 'sm', type = 'button', className = '' }) {
  const base = 'inline-flex items-center justify-center rounded-md font-semibold transition disabled:opacity-40 disabled:cursor-not-allowed'
  const sizes = { sm: 'px-2.5 py-1 text-xs', md: 'px-4 py-2 text-sm' }
  const variants = {
    primary: 'bg-klarim-alert text-klarim-bg hover:brightness-110',
    ghost: 'border border-klarim-border text-klarim-text hover:bg-klarim-border/40',
    danger: 'border border-klarim-fail/50 text-klarim-fail hover:bg-klarim-fail/10',
  }
  return (
    <button type={type} onClick={onClick} disabled={disabled}
      className={`${base} ${sizes[size]} ${variants[variant]} ${className}`}>
      {children}
    </button>
  )
}

export function Pagination({ page, setPage, hasNext }) {
  return (
    <div className="mt-4 flex items-center justify-between text-sm text-klarim-muted">
      <Button onClick={() => setPage(Math.max(0, page - 1))} disabled={page === 0}>← Anterior</Button>
      <span>Página {page + 1}</span>
      <Button onClick={() => setPage(page + 1)} disabled={!hasNext}>Próxima →</Button>
    </div>
  )
}

// --- helpers --------------------------------------------------------------- //

function parseUTC(iso) {
  if (!iso) return NaN
  // timestamps do Postgres vêm naive (UTC); força UTC se não houver timezone.
  const s = /[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`
  return new Date(s).getTime()
}

export function relativeTime(iso) {
  const then = parseUTC(iso)
  if (isNaN(then)) return '—'
  const s = Math.floor((Date.now() - then) / 1000)
  if (s < 60) return 'agora'
  const m = Math.floor(s / 60)
  if (m < 60) return `há ${m}min`
  const h = Math.floor(m / 60)
  if (h < 24) return `há ${h}h`
  const d = Math.floor(h / 24)
  if (d < 30) return `há ${d}d`
  const mo = Math.floor(d / 30)
  if (mo < 12) return `há ${mo}mes`
  return `há ${Math.floor(mo / 12)}a`
}

export function formatDate(iso) {
  const t = parseUTC(iso)
  if (isNaN(t)) return '—'
  return new Date(t).toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' })
}

// KL-85 — cor do lead score de alerta: ≥40 verde · 20-39 amarelo · 0-19 laranja · <0 vermelho.
export function alertScoreColor(score) {
  if (score == null) return '#8B949E'
  if (score >= 40) return '#00D26A'
  if (score >= 20) return '#F0C000'
  if (score >= 0) return '#FF6B35'
  return '#F85149'
}

export function AlertScoreBadge({ score }) {
  if (score == null) return <span className="text-klarim-muted">—</span>
  const c = alertScoreColor(score)
  return (
    <span className="rounded px-1.5 py-0.5 text-xs font-semibold"
      style={{ color: c, background: `${c}22` }} aria-label={`Lead score de alerta ${score}`}>
      {score}
    </span>
  )
}
