import { useState } from 'react'
import { DASHBOARD_PLATFORMS, buildSnippet } from '../../lib/gate/snippets.js'
import GateCodeBlock from './GateCodeBlock.jsx'

// KL-152 P1 — abas de integração por plataforma (substitui o snippet Python raw). CSP-safe:
// estado React (não injeta script inline). A URL do projeto vem pré-preenchida (zero edição).

const FILENAME = {
  github: '.github/workflows/deploy.yml',
  gitlab: '.gitlab-ci.yml',
  bitbucket: 'bitbucket-pipelines.yml',
  curl: 'terminal',
}

export default function GateIntegrationTabs({ url, platforms = DASHBOARD_PLATFORMS }) {
  const [tab, setTab] = useState(platforms[0].id)
  return (
    <div>
      <div className="mb-3 flex flex-wrap gap-2" role="tablist">
        {platforms.map((p) => (
          <button key={p.id} type="button" role="tab" aria-selected={tab === p.id}
            onClick={() => setTab(p.id)}
            className={`min-h-[36px] rounded-lg px-3 py-1.5 text-sm font-medium transition-colors ${
              tab === p.id
                ? 'bg-brand-500 text-[var(--accent-text)]'
                : 'border border-slate-700 text-slate-300 hover:bg-slate-800'}`}>
            {p.label}
          </button>
        ))}
      </div>
      <GateCodeBlock code={buildSnippet(tab, url)} filename={FILENAME[tab]} />
    </div>
  )
}
