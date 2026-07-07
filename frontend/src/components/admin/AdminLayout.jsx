import { useState, Suspense } from 'react'
import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import { Beacon } from '../Logo'
import { clearToken } from '../../lib/auth'
import { Loading } from './ui'

const I = (paths) => (
  <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor"
    strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    {paths}
  </svg>
)

const ICONS = {
  overview: I(<><rect x="3" y="3" width="7" height="9" /><rect x="14" y="3" width="7" height="5" /><rect x="14" y="12" width="7" height="9" /><rect x="3" y="16" width="7" height="5" /></>),
  targets: I(<><circle cx="12" cy="12" r="9" /><circle cx="12" cy="12" r="5" /><circle cx="12" cy="12" r="1" /></>),
  scans: I(<><circle cx="11" cy="11" r="7" /><line x1="21" y1="21" x2="16.65" y2="16.65" /></>),
  alerts: I(<><path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9" /><path d="M13.73 21a2 2 0 0 1-3.46 0" /></>),
  payments: I(<><rect x="2" y="5" width="20" height="14" rx="2" /><line x1="2" y1="10" x2="22" y2="10" /></>),
  rescans: I(<><path d="M23 4v6h-6" /><path d="M1 20v-6h6" /><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" /></>),
  config: I(<><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-2.82 1.17V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 8 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.6 15H4.5a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 6 8a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 11 4.6V4.5a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 2.82 1.17l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9v.09" /></>),
  logout: I(<><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" /><polyline points="16 17 21 12 16 7" /><line x1="21" y1="12" x2="9" y2="12" /></>),
}

const NAV = [
  { to: '/painel', label: 'Visão geral', icon: 'overview', end: true },
  { to: '/painel/alvos', label: 'Alvos', icon: 'targets' },
  { to: '/painel/scans', label: 'Scans', icon: 'scans' },
  { to: '/painel/alertas', label: 'Alertas', icon: 'alerts' },
  { to: '/painel/pagamentos', label: 'Pagamentos', icon: 'payments' },
  { to: '/painel/rescans', label: 'Re-scans', icon: 'rescans' },
  { to: '/painel/config', label: 'Configurações', icon: 'config' },
]

export default function AdminLayout() {
  const [open, setOpen] = useState(false)
  const navigate = useNavigate()

  function logout() {
    clearToken()
    navigate('/painel/login', { replace: true })
  }

  const nav = (
    <nav className="flex flex-1 flex-col gap-1">
      {NAV.map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          end={item.end}
          onClick={() => setOpen(false)}
          className={({ isActive }) =>
            `flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition ${
              isActive
                ? 'bg-klarim-alert/15 text-klarim-alert'
                : 'text-klarim-muted hover:bg-klarim-border/40 hover:text-klarim-text'
            }`
          }
        >
          {ICONS[item.icon]}
          {item.label}
        </NavLink>
      ))}
      <button
        onClick={logout}
        className="mt-2 flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium text-klarim-muted transition hover:bg-klarim-fail/10 hover:text-klarim-fail"
      >
        {ICONS.logout}
        Sair
      </button>
    </nav>
  )

  const brand = (
    <div className="mb-6 flex items-center gap-2 px-1">
      <Beacon size={26} />
      <span className="text-lg font-extrabold tracking-widest">
        KLA<span className="text-klarim-alert">R</span>IM
      </span>
      <span className="ml-1 rounded bg-klarim-border/60 px-1.5 py-0.5 text-[10px] font-semibold uppercase text-klarim-muted">
        painel
      </span>
    </div>
  )

  return (
    <div className="min-h-screen bg-klarim-bg text-klarim-text">
      {/* Sidebar desktop */}
      <aside className="fixed inset-y-0 left-0 hidden w-60 flex-col border-r border-klarim-border bg-klarim-surface p-4 md:flex">
        {brand}
        {nav}
      </aside>

      {/* Topbar mobile */}
      <header className="sticky top-0 z-20 flex items-center justify-between border-b border-klarim-border bg-klarim-surface px-4 py-3 md:hidden">
        <div className="flex items-center gap-2">
          <Beacon size={22} />
          <span className="font-extrabold tracking-widest">
            KLA<span className="text-klarim-alert">R</span>IM
          </span>
        </div>
        <button
          onClick={() => setOpen((v) => !v)}
          aria-label="Menu"
          className="rounded-md border border-klarim-border p-2 text-klarim-muted"
        >
          <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
            {open ? <><line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" /></>
              : <><line x1="3" y1="12" x2="21" y2="12" /><line x1="3" y1="6" x2="21" y2="6" /><line x1="3" y1="18" x2="21" y2="18" /></>}
          </svg>
        </button>
      </header>

      {/* Drawer mobile */}
      {open && (
        <div className="fixed inset-0 z-10 md:hidden" onClick={() => setOpen(false)}>
          <div className="absolute inset-0 bg-black/50" />
          <aside
            className="absolute inset-y-0 left-0 flex w-64 flex-col border-r border-klarim-border bg-klarim-surface p-4"
            onClick={(e) => e.stopPropagation()}
          >
            {brand}
            {nav}
          </aside>
        </div>
      )}

      {/* Conteúdo */}
      <main className="px-4 py-6 md:ml-60 md:px-8">
        <Suspense fallback={<Loading />}>
          <Outlet />
        </Suspense>
      </main>
    </div>
  )
}
