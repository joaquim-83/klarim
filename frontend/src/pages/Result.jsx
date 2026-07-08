import { useState } from 'react'
import { useSearchParams, useNavigate, Link } from 'react-router-dom'
import Layout from '../components/Layout'
import Semaphore from '../components/Semaphore'
import SeverityChips from '../components/SeverityChips'
import { useSummary, problemLine } from '../lib/useSummary'

export default function Result() {
  const [params] = useSearchParams()
  const url = params.get('url') || ''
  const navigate = useNavigate()
  const { summary, loading, error } = useSummary(url)
  const [copied, setCopied] = useState(false)

  function share() {
    navigator.clipboard?.writeText(window.location.href).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }

  if (loading) {
    return (
      <Layout withFooter={false}>
        <div className="flex flex-col items-center pt-16 text-center">
          <div className="klarim-spinner h-14 w-14" />
          <p className="mt-6 text-klarim-muted">Carregando resultado…</p>
        </div>
      </Layout>
    )
  }

  if (error || !summary) {
    return (
      <Layout>
        <div className="mx-auto max-w-md pt-10 text-center">
          <h1 className="text-2xl font-bold text-klarim-fail">Resultado indisponível</h1>
          <p className="mt-3 text-sm text-klarim-muted">{error || 'Refaça a varredura.'}</p>
          <Link to="/" className="mt-6 inline-block rounded-lg bg-klarim-alert px-6 py-3 font-bold text-klarim-bg">
            Escanear um site
          </Link>
        </div>
      </Layout>
    )
  }

  const counts = summary.severity_counts || {}
  const problems = summary.problems ?? Object.values(counts).reduce((a, b) => a + b, 0)

  return (
    <Layout>
      <div className="text-center">
        <p className="break-all font-mono text-sm text-klarim-muted">{summary.url || url}</p>
        <div className="mt-6 flex justify-center">
          <Semaphore score={summary.score} semaphore={summary.semaphore} />
        </div>
        <p className="mx-auto mt-6 max-w-md text-lg">{problemLine(problems)}</p>

        <div className="mt-6">
          <SeverityChips counts={counts} />
        </div>

        {/* Riscos concretos (KL-20) */}
        {(summary.risk_messages || []).length > 0 && (
          <div className="mx-auto mt-8 max-w-xl text-left">
            <h3 className="text-lg font-bold text-klarim-alert">⚠ O que pode acontecer com o seu site</h3>
            {summary.risk_summary && (
              <p className="mt-1 text-sm text-klarim-muted">{summary.risk_summary}</p>
            )}
            <div className="mt-3 space-y-2">
              {summary.risk_messages.map((risk, i) => (
                <div key={i} className="rounded-lg border-l-4 border-klarim-alert bg-klarim-surface p-3">
                  <p className="font-semibold text-klarim-text">{risk.icon} {risk.headline}</p>
                  <p className="mt-1 text-sm text-klarim-muted">{risk.risk}</p>
                </div>
              ))}
            </div>
            <p className="mt-4 text-xs text-klarim-muted">
              Nota: falhas de segurança também podem resultar em sanções e multas pela LGPD.
            </p>
          </div>
        )}

        {/* CTA principal */}
        <div className="mt-8">
          <button
            onClick={() => navigate(`/pay?url=${encodeURIComponent(url)}`)}
            className="w-full rounded-lg bg-klarim-alert px-6 py-4 text-lg font-bold text-klarim-bg transition hover:opacity-90 sm:w-auto"
          >
            Ver relatório completo — R$ 29
          </button>
          <p className="mt-2 text-sm text-klarim-muted">
            Relatório executivo + técnico com recomendações de correção.
          </p>
        </div>

        {/* Ações secundárias */}
        <div className="mt-8 flex flex-col justify-center gap-3 sm:flex-row">
          <button
            onClick={share}
            className="rounded-lg border border-klarim-border px-5 py-2.5 font-medium text-klarim-text hover:border-klarim-alert"
          >
            {copied ? 'Link copiado!' : 'Compartilhar resultado'}
          </button>
          <Link
            to="/"
            className="rounded-lg border border-klarim-border px-5 py-2.5 text-center font-medium text-klarim-text hover:border-klarim-alert"
          >
            Escanear outro site
          </Link>
        </div>
      </div>
    </Layout>
  )
}
