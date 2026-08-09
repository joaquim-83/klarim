import { useEffect, useState } from 'react'
import { card, brandBtn } from './shared.js'
import { planName, usageText, gateOnboardingSteps } from '../../lib/gate/ux.js'

// KL-150 (Fix 3) — seção "Security Gate" no dashboard principal (/dashboard), em destaque no topo,
// para contas dev (developer/both). Resolve a irrelevância do "Adicione seu primeiro site" para
// quem usa o Gate. Os dados vêm do gate status (passado pelo DashboardV2) + a contagem de runs
// (buscada aqui p/ marcar o "Primeiro scan"). Não depende do dashboard-summary.
export default function GateDashboardSection({ status }) {
  const [runsCount, setRunsCount] = useState(0)
  const [verifiedProjects, setVerifiedProjects] = useState(0)

  useEffect(() => {
    let alive = true
    const get = (p, fb) => fetch(p, { credentials: 'include' }).then((r) => (r.ok ? r.json() : fb)).catch(() => fb)
    Promise.all([get('/api/gate/runs?limit=1', { runs: [] }), get('/api/gate/projects', { projects: [] })])
      .then(([rd, pd]) => {
        if (!alive) return
        setRunsCount((rd.runs || []).length)
        setVerifiedProjects((pd.projects || []).filter((p) => p.verified).length)
      })
    return () => { alive = false }
  }, [])

  const steps = gateOnboardingSteps(status, runsCount, verifiedProjects)
  const plan = planName(status?.plan_slug || status?.plan || 'free')
  const accessLabel = status?.access_level === 'complete' ? 'Completo' : 'Básico'

  return (
    <section className={card}>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <h2 className="flex items-center gap-2 text-lg font-bold text-white">
            <span aria-hidden="true">🔒</span> Security Gate
          </h2>
          <p className="mt-1 text-sm text-slate-300">
            Plano: <strong className="text-white">{plan}</strong>
            <span className="mx-1.5 text-slate-600">·</span>
            {usageText(status?.scans_used_hour, status?.scans_limit_hour)}
            <span className="mx-1.5 text-slate-600">·</span>
            Nível: <strong className="text-white">{accessLabel}</strong>
          </p>
        </div>
        <a href="/dashboard/gate" className={brandBtn}>Abrir dashboard Gate →</a>
      </div>

      <div className="mt-5 border-t border-slate-800 pt-4">
        <p className="text-sm font-semibold text-slate-200">Primeiros passos</p>
        <ul className="mt-2 space-y-1.5">
          {steps.map((s) => (
            <li key={s.key} className="flex items-center gap-2 text-sm">
              <span aria-hidden="true">{s.done ? '✅' : '☐'}</span>
              <span className={s.done ? 'text-slate-400 line-through' : 'text-slate-200'}>{s.label}</span>
            </li>
          ))}
        </ul>
      </div>
    </section>
  )
}
