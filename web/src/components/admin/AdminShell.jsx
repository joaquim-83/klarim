import { useState, useEffect } from 'react'
import { clearToken, isAuthed } from '../../lib/admin/auth'
import { admin } from '../../lib/admin/adminApi'

// Shell do painel admin (sidebar + drawer mobile + badge do inbox), portado de
// frontend/src/components/admin/AdminLayout.jsx (KL-51 fase 1). Diferenças da versão Vite:
// - É um componente React comum (não uma ilha que envolve <slot/>): cada página o usa
//   como wrapper dentro da PRÓPRIA ilha → um único island por página, sem ilha-em-ilha.
// - Sem react-router: NavLink → <a href>, useNavigate → window.location, Outlet → {children}.
// - O item ativo vem da prop `active` (definida por cada página) — SSR-safe, sem mismatch.
// - Auth guard: em useEffect (client-only), !isAuthed() → redireciona ao login (o adminApi
//   também redireciona em qualquer 401, então dado sensível nunca aparece sem token).

function Beacon({ size = 26 }) {
  return (
    <svg viewBox="0 0 64 64" width={size} height={size} aria-hidden="true">
      <g stroke="#FF6B35" strokeWidth="3.5" strokeLinecap="round">
        <line x1="32" y1="4" x2="32" y2="13" />
        <line x1="12" y1="12" x2="18" y2="18" />
        <line x1="52" y1="12" x2="46" y2="18" />
        <line x1="6" y1="30" x2="15" y2="30" />
        <line x1="58" y1="30" x2="49" y2="30" />
      </g>
      <circle cx="32" cy="38" r="17" fill="#FF6B35" />
      <circle cx="32" cy="38" r="7.5" fill="#0D1117" />
    </svg>
  )
}

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
  system: I(<><rect x="2" y="3" width="20" height="14" rx="2" /><line x1="8" y1="21" x2="16" y2="21" /><line x1="12" y1="17" x2="12" y2="21" /></>),
  analytics: I(<><line x1="18" y1="20" x2="18" y2="10" /><line x1="12" y1="20" x2="12" y2="4" /><line x1="6" y1="20" x2="6" y2="14" /></>),
  monitored: I(<><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" /><path d="M9 12l2 2 4-4" /></>),
  inbox: I(<><path d="M4 4h16a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z" /><polyline points="22,6 12,13 2,6" /></>),
  leads: I(<><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" /><circle cx="9" cy="7" r="4" /><path d="M23 21v-2a4 4 0 0 0-3-3.87" /><path d="M16 3.13a4 4 0 0 1 0 7.75" /></>),
  services: I(<><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z" /><polyline points="3.27 6.96 12 12.01 20.73 6.96" /><line x1="12" y1="22.08" x2="12" y2="12" /></>),
  subscribers: I(<><path d="M16 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" /><circle cx="8.5" cy="7" r="4" /><polyline points="17 11 19 13 23 9" /></>),
  vigilias: I(<><path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7-11-7-11-7z" /><circle cx="12" cy="12" r="3" /></>),
  logout: I(<><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" /><polyline points="16 17 21 12 16 7" /><line x1="21" y1="12" x2="9" y2="12" /></>),
}

const NAV = [
  { to: '/painel', label: 'Visão geral', icon: 'overview', key: 'overview' },
  { to: '/painel/alvos', label: 'Alvos', icon: 'targets', key: 'alvos' },
  { to: '/painel/scans', label: 'Scans', icon: 'scans', key: 'scans' },
  { to: '/painel/alertas', label: 'Alertas', icon: 'alerts', key: 'alertas' },
  { to: '/painel/leads', label: 'Leads', icon: 'leads', key: 'leads' },
  { to: '/painel/pagamentos', label: 'Pagamentos', icon: 'payments', key: 'pagamentos' },
  { to: '/painel/inbox', label: 'Inbox', icon: 'inbox', key: 'inbox' },
  { to: '/painel/rescans', label: 'Re-scans', icon: 'rescans', key: 'rescans' },
  { to: '/painel/analytics', label: 'Analytics', icon: 'analytics', key: 'analytics' },
  { to: '/painel/clientes', label: 'Gestão de Clientes', icon: 'monitored', key: 'clientes' },
  { to: '/painel/servicos', label: 'Serviços', icon: 'services', key: 'servicos' },
  { to: '/painel/assinantes', label: 'Assinantes', icon: 'subscribers', key: 'assinantes' },
  { to: '/painel/vigilias', label: 'Vigílias', icon: 'vigilias', key: 'vigilias' },
  { to: '/painel/sistema', label: 'Sistema', icon: 'system', key: 'sistema' },
  { to: '/painel/config', label: 'Configurações', icon: 'config', key: 'config' },
]

export default function AdminShell({ active, children }) {
  const [open, setOpen] = useState(false)
  const [unread, setUnread] = useState(0)  // badge do Inbox (KL-56)

  // Guard de auth (client-only) — sem token válido, vai ao login. O adminApi também
  // redireciona em qualquer 401, então nenhum dado sensível é exibido sem token.
  useEffect(() => {
    if (!isAuthed()) {
      window.location.href = '/painel/login'
    }
  }, [])

  // Contagem de não-lidas do inbox: no mount + a cada 60s (badge do menu).
  useEffect(() => {
    let alive = true
    const load = () => admin.inboxUnread()
      .then((r) => alive && setUnread(r?.unread || 0))
      .catch(() => {})
    load()
    const id = setInterval(load, 60000)
    return () => { alive = false; clearInterval(id) }
  }, [])

  function logout() {
    clearToken()
    window.location.href = '/painel/login'
  }

  const nav = (
    <nav className="flex flex-1 flex-col gap-1">
      {NAV.map((item) => {
        const isActive = item.key === active
        const badge = item.key === 'inbox' ? unread : 0
        return (
          <a
            key={item.to}
            href={item.to}
            onClick={() => setOpen(false)}
            className={`flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition ${
              isActive
                ? 'bg-klarim-alert/15 text-klarim-alert'
                : 'text-klarim-muted hover:bg-klarim-border/40 hover:text-klarim-text'
            }`}
          >
            {ICONS[item.icon]}
            {item.label}
            {badge > 0 && (
              <span className="ml-auto rounded-full bg-klarim-alert px-2 py-0.5 text-[11px] font-bold text-black">
                {badge}
              </span>
            )}
          </a>
        )
      })}
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
        {children}
      </main>
    </div>
  )
}
