import { Fragment } from 'react'
import { gateDashboardNav } from '../../lib/nav.js'

// KL-150 P2 (item 2) — barra de navegação do dashboard do Gate. O dev ficava preso em
// /dashboard/gate sem link para voltar. Menu horizontal (Dashboard · Security Gate · Minha conta
// [· Meus sites p/ `both`]) + "Sair". Renderiza ACIMA do conteúdo. Links de `lib/nav.js` (testável).
async function logout() {
  try { await fetch('/api/account/logout', { method: 'POST', credentials: 'include' }) } catch { /* */ }
  window.location.href = '/'
}

export default function DashboardNav({ accountType }) {
  const links = gateDashboardNav(accountType)
  return (
    <nav aria-label="Navegação do dashboard"
      className="mb-5 flex flex-wrap items-center gap-x-2 gap-y-1 border-b border-slate-800 pb-3 text-sm">
      {links.map((l, i) => (
        <Fragment key={`${l.href}:${l.label}`}>
          {i > 0 && <span className="text-slate-600" aria-hidden="true">·</span>}
          {l.current
            ? <span className="font-semibold text-white" aria-current="page">{l.label}</span>
            : <a href={l.href} className="text-brand-400 transition-colors hover:text-brand-300">{l.label}</a>}
        </Fragment>
      ))}
      <span className="text-slate-600" aria-hidden="true">·</span>
      <button type="button" onClick={logout} className="text-slate-400 transition-colors hover:text-slate-200">Sair</button>
    </nav>
  )
}
