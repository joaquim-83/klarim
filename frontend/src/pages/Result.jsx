import { useState, useEffect } from 'react'
import { useSearchParams, useNavigate, Link } from 'react-router-dom'
import Layout from '../components/Layout'
import Semaphore from '../components/Semaphore'
import { useSummary, problemLine } from '../lib/useSummary'
import { downloadReport, monitoringOffer } from '../lib/api'
import { trackEvent } from '../lib/tracker'

const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/

// Oferta de monitoramento gratuito quando o site atinge score 100 (KL-29).
function MonitoringOffer({ url, defaultEmail, chargeId }) {
  const navigate = useNavigate()
  const [email, setEmail] = useState(defaultEmail || '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  async function accept() {
    if (!EMAIL_RE.test(email)) return setError('Digite um e-mail válido.')
    setBusy(true)
    setError('')
    try {
      trackEvent('monitoring_offer_accepted', { url }, url)
      const r = await monitoringOffer(url, email, chargeId)
      if (r.already) {
        navigate('/monitorados')
      } else if (r.approval_token) {
        navigate(`/monitorados/aprovar?token=${encodeURIComponent(r.approval_token)}`)
      }
    } catch (e) {
      setError(e.message || 'Não foi possível ativar o monitoramento.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="mx-auto mt-8 max-w-md rounded-xl border border-klarim-ok bg-klarim-surface p-6 text-left">
      <p className="text-center text-lg font-bold text-klarim-ok">🎉 Parabéns! Score 100/100</p>
      <p className="mt-1 text-center text-sm text-klarim-muted">
        Seu site passou em todas as 29 verificações de segurança.
      </p>
      <hr className="my-4 border-klarim-border" />
      <p className="text-sm font-bold">Monitoramento gratuito</p>
      <p className="mt-1 text-sm text-klarim-muted">
        Quer que o Klarim monitore seu site gratuitamente? Verificamos semanalmente e
        avisamos se algo mudar. Seu site também aparecerá na seção de Sites Monitorados.
      </p>
      <input
        type="email"
        value={email}
        onChange={(e) => setEmail(e.target.value)}
        placeholder="seu@email.com.br"
        className="mt-3 w-full rounded-lg border border-klarim-border bg-klarim-bg px-4 py-2.5 text-klarim-text placeholder:text-klarim-muted focus:border-klarim-ok focus:outline-none"
      />
      <button
        onClick={accept}
        disabled={busy}
        className="mt-3 w-full rounded-lg bg-klarim-ok px-6 py-3 font-bold text-klarim-bg transition hover:opacity-90 disabled:opacity-60"
      >
        {busy ? 'Ativando…' : 'Aceitar monitoramento gratuito'}
      </button>
      {error && <p className="mt-2 text-sm text-klarim-fail">{error}</p>}
    </div>
  )
}

// Linha de um check. No gratuito: só nome + ✅/❌ (falhas ganham 🔒). No completo:
// os FAILs expandem com evidência, impacto e correção.
function CheckRow({ check, locked, full }) {
  const { name, status, evidence, impact, fix } = check
  const hasDetail = full && status === 'FAIL' && (evidence || impact || fix)
  const [open, setOpen] = useState(false)
  const icon = locked ? '🔒' : status === 'PASS' ? '✅' : status === 'FAIL' ? '❌' : '➖'
  const color = locked
    ? 'text-klarim-muted'
    : status === 'FAIL'
    ? 'text-klarim-fail'
    : status === 'PASS'
    ? 'text-klarim-text'
    : 'text-klarim-muted'

  return (
    <div className={`border-b border-klarim-border/60 py-2 ${locked ? 'opacity-60' : ''}`}>
      <button
        type="button"
        onClick={() => hasDetail && setOpen((o) => !o)}
        className={`flex w-full items-center justify-between gap-3 text-left ${hasDetail ? 'cursor-pointer' : 'cursor-default'}`}
      >
        <span className={`text-sm ${color}`}>{name}</span>
        <span className="shrink-0 text-sm">
          {icon}
          {!locked && !full && status === 'FAIL' && <span className="ml-1 opacity-70">🔒</span>}
          {hasDetail && <span className="ml-1 text-xs text-klarim-muted">{open ? '▲' : '▼'}</span>}
        </span>
      </button>
      {hasDetail && open && (
        <div className="mt-2 space-y-1 rounded-lg bg-klarim-bg/60 p-3 text-xs">
          {evidence && <p className="text-klarim-muted"><span className="text-klarim-text">Evidência:</span> {evidence}</p>}
          {impact && <p className="text-klarim-muted"><span className="text-klarim-text">Impacto:</span> {impact}</p>}
          {fix && <p className="text-klarim-ok"><span className="font-semibold">Correção:</span> {fix}</p>}
        </div>
      )}
    </div>
  )
}

function PdfButton({ kind, url, chargeId, label }) {
  const [busy, setBusy] = useState(false)
  const [failed, setFailed] = useState(false)
  async function onClick() {
    setBusy(true)
    setFailed(false)
    trackEvent('report_downloaded', { url, type: kind }, url)
    try {
      await downloadReport(kind, url, chargeId) // usa charge_id e/ou o scan token full guardado
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
  const chargeId = params.get('charge_id') || ''
  const navigate = useNavigate()
  const { summary, loading, error } = useSummary(url, chargeId)
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    if (!summary) return
    trackEvent('result_viewed', {
      url, score: summary.score, semaphore: summary.semaphore,
      fail_count: summary.fail_count, full: !!summary.is_full,
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
  const totalChecks = summary.total_checks || freeChecks.length + paidChecks.length
  const freeCount = summary.free_count || freeChecks.length
  const failCount = summary.fail_count ?? summary.problems ?? 0
  const canRescan = (summary.rescan_credits || 0) > 0

  return (
    <Layout>
      <div className="text-center">
        <p className="break-all font-mono text-sm text-klarim-muted">{summary.url || url}</p>

        {/* Comparação antes/depois (re-verificação) */}
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

        {isFull ? (
          <p className="mx-auto mt-6 max-w-md text-lg">
            Scan completo — {totalChecks} verificações realizadas.
          </p>
        ) : (
          <p className="mx-auto mt-6 max-w-md text-lg">{problemLine(failCount, freeCount)}</p>
        )}
        {summary.risk_summary && !isFull && (
          <p className="mx-auto mt-2 max-w-md text-sm text-klarim-muted">{summary.risk_summary}</p>
        )}

        {/* Verificações básicas */}
        <div className="mx-auto mt-8 max-w-xl text-left">
          <h3 className="mb-1 text-center text-sm font-bold uppercase tracking-wide text-klarim-muted">
            {isFull ? `Verificações básicas (${freeChecks.length})` : 'Verificações realizadas'}
          </h3>
          {freeChecks.map((c) => (
            <CheckRow key={c.check_id} check={c} full={isFull} />
          ))}
        </div>

        {/* Checks avançados */}
        <div className="mx-auto mt-8 max-w-xl text-left">
          <h3 className="mb-1 text-center text-sm font-bold uppercase tracking-wide text-klarim-muted">
            {isFull
              ? `Checks avançados (${paidChecks.length})`
              : `Scan completo (${paidChecks.length} verificações adicionais)`}
          </h3>
          {paidChecks.map((c) => (
            <CheckRow key={c.check_id} check={c} full={isFull} locked={!isFull} />
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

        {/* Relatório em PDF (completo) */}
        {isFull && (
          <div className="mx-auto mt-10 max-w-md">
            <h3 className="mb-3 text-center text-sm font-bold uppercase tracking-wide text-klarim-muted">
              Relatório em PDF
            </h3>
            <div className="flex flex-col gap-4">
              <PdfButton kind="executive" url={url} chargeId={chargeId} label="Baixar Relatório Executivo (PDF)" />
              <PdfButton kind="technical" url={url} chargeId={chargeId} label="Baixar Relatório Técnico (PDF)" />
            </div>
          </div>
        )}

        {/* Monitoramento gratuito — score 100 (KL-29) */}
        {isFull && summary.score === 100 && (
          <MonitoringOffer url={url} defaultEmail={summary.contact_email} chargeId={chargeId} />
        )}

        {/* Re-verificação (retorno médico) */}
        {isFull && canRescan && (
          <div className="mx-auto mt-8 max-w-md rounded-lg border border-klarim-ok bg-klarim-surface p-5 text-left">
            <p className="font-bold text-klarim-ok">
              Você tem {summary.rescan_credits} re-verificação(ões) gratuita(s) incluída(s).
            </p>
            <p className="mt-1 text-sm text-klarim-muted">
              Após corrigir as falhas, verifique novamente sem custo — mostramos a evolução do score.
            </p>
            <button
              onClick={() => navigate(`/?url=${encodeURIComponent(url)}`)}
              className="mt-3 w-full rounded-lg border border-klarim-ok px-5 py-2.5 font-bold text-klarim-ok hover:bg-klarim-ok hover:text-klarim-bg sm:w-auto"
            >
              Fazer re-verificação gratuita
            </button>
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
