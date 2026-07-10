import { useState, useEffect } from 'react'
import { useSearchParams, useNavigate, Link } from 'react-router-dom'
import Layout from '../components/Layout'
import Semaphore from '../components/Semaphore'
import { useSummary, problemLine } from '../lib/useSummary'
import { downloadReport } from '../lib/api'
import { trackEvent } from '../lib/tracker'

// Linha de um check no resultado. Sem detalhes — só nome + estado.
function CheckRow({ name, status, locked }) {
  const icon = locked ? '🔒' : status === 'PASS' ? '✅' : status === 'FAIL' ? '❌' : '➖'
  const color = locked
    ? 'text-klarim-muted'
    : status === 'FAIL'
    ? 'text-klarim-fail'
    : status === 'PASS'
    ? 'text-klarim-text'
    : 'text-klarim-muted'
  return (
    <div className={`flex items-center justify-between gap-3 border-b border-klarim-border/60 py-2 ${locked ? 'opacity-60' : ''}`}>
      <span className={`text-sm ${color}`}>{name}</span>
      <span className="shrink-0 text-sm">
        {icon}
        {/* falha no gratuito também mostra o cadeado: detalhe está no relatório pago */}
        {!locked && status === 'FAIL' && <span className="ml-1 opacity-70">🔒</span>}
      </span>
    </div>
  )
}

function PdfButton({ kind, url, label }) {
  const [busy, setBusy] = useState(false)
  const [failed, setFailed] = useState(false)
  async function onClick() {
    setBusy(true)
    setFailed(false)
    trackEvent('report_downloaded', { url, type: kind }, url)
    try {
      await downloadReport(kind, url) // usa o scan token (full) guardado
    } catch {
      setFailed(true)
    } finally {
      setBusy(false)
    }
  }
  return (
    <button
      onClick={onClick}
      disabled={busy}
      className={`flex w-full items-center justify-center gap-2 rounded-lg ${failed ? 'bg-klarim-fail' : 'bg-klarim-alert'} px-6 py-3 font-bold text-klarim-bg transition hover:opacity-90 disabled:opacity-60`}
    >
      {busy ? <><span className="klarim-spinner h-5 w-5" /> Gerando PDF…</> : failed ? 'Erro — tentar de novo' : label}
    </button>
  )
}

export default function Result() {
  const [params] = useSearchParams()
  const url = params.get('url') || ''
  const navigate = useNavigate()
  const { summary, loading, error } = useSummary(url)
  const [copied, setCopied] = useState(false)

  // KL-21: result_viewed quando o resultado carrega (uma vez por scan).
  useEffect(() => {
    if (!summary) return
    trackEvent('result_viewed', {
      url, score: summary.score, semaphore: summary.semaphore,
      fail_count: summary.fail_count,
    }, url)
  }, [summary, url])

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

  const isFull = !!summary.is_full
  const comparison = summary.comparison
  const freeChecks = summary.free_checks || []
  const paidChecks = summary.paid_checks || []
  const freeCount = summary.free_count || freeChecks.length
  const failCount = summary.fail_count ?? summary.problems ?? 0

  return (
    <Layout>
      <div className="text-center">
        <p className="break-all font-mono text-sm text-klarim-muted">{summary.url || url}</p>

        {/* Comparação antes/depois (re-verificação — KL-27) */}
        {comparison && comparison.old_score != null && (
          <div className="mx-auto mt-4 max-w-md rounded-lg border border-klarim-ok bg-klarim-surface px-4 py-3">
            <p className="text-sm text-klarim-muted">Re-verificação concluída</p>
            <p className="mt-1 text-lg font-bold">
              Antes: <span className="text-klarim-muted">{comparison.old_score}</span>
              {' → '}
              Agora: <span className="text-klarim-ok">{comparison.new_score}</span>
              {comparison.evolution === 'improved' && ' ✅'}
              {comparison.evolution === 'worsened' && ' ⚠️'}
            </p>
          </div>
        )}

        <div className="mt-6 flex justify-center">
          <Semaphore score={summary.score} semaphore={summary.semaphore} />
        </div>
        <p className="mx-auto mt-6 max-w-md text-lg">{problemLine(failCount, freeCount)}</p>
        {summary.risk_summary && (
          <p className="mx-auto mt-2 max-w-md text-sm text-klarim-muted">{summary.risk_summary}</p>
        )}

        {/* Verificações realizadas (tier gratuito) */}
        <div className="mx-auto mt-8 max-w-xl text-left">
          <h3 className="mb-1 text-center text-sm font-bold uppercase tracking-wide text-klarim-muted">
            Verificações realizadas
          </h3>
          {freeChecks.map((c) => (
            <CheckRow key={c.check_id} name={c.name} status={c.status} />
          ))}
        </div>

        {/* Checks do scan completo (bloqueados no gratuito) */}
        <div className="mx-auto mt-8 max-w-xl text-left">
          <h3 className="mb-1 text-center text-sm font-bold uppercase tracking-wide text-klarim-muted">
            {isFull ? 'Verificações avançadas' : `Scan completo (${paidChecks.length} verificações adicionais)`}
          </h3>
          {paidChecks.map((c) => (
            <CheckRow key={c.check_id} name={c.name} status={c.status} locked={!isFull} />
          ))}
        </div>

        {/* CTA de compra (só no gratuito) */}
        {!isFull && (
          <div className="mt-8">
            <button
              onClick={() => {
                trackEvent('cta_clicked', { url, price: summary.price, score: summary.score }, url)
                navigate(`/pay?url=${encodeURIComponent(url)}`)
              }}
              className="w-full rounded-lg bg-klarim-alert px-6 py-4 text-lg font-bold text-klarim-bg transition hover:opacity-90 sm:w-auto"
            >
              Fazer scan completo — {summary.price_display || 'R$ 19'}
            </button>
            <p className="mt-2 text-sm text-klarim-muted">
              Relatório executivo + técnico com todos os 29 pontos, evidências e correções.
            </p>
          </div>
        )}

        {/* Downloads (re-verificação / scan completo — usa o scan token full) */}
        {isFull && (
          <div className="mx-auto mt-8 flex max-w-md flex-col gap-4">
            <PdfButton kind="executive" url={url} label="Baixar Relatório Executivo (PDF)" />
            <PdfButton kind="technical" url={url} label="Baixar Relatório Técnico (PDF)" />
          </div>
        )}

        {/* LGPD — nota de rodapé discreta */}
        <p className="mx-auto mt-8 max-w-md text-xs text-klarim-muted">
          Nota: falhas de segurança também podem resultar em sanções e multas pela LGPD.
        </p>

        {/* Ações secundárias */}
        <div className="mt-6 flex flex-col justify-center gap-3 sm:flex-row">
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
