import { useEffect, useRef, useState } from 'react'
import { useSearchParams, Link } from 'react-router-dom'
import Layout from '../components/Layout'
import Semaphore from '../components/Semaphore'
import { useSummary } from '../lib/useSummary'
import { downloadReport, getPaymentStatus } from '../lib/api'
import { trackEvent } from '../lib/tracker'

function DownloadButton({ kind, url, chargeId, label, hint }) {
  const [busy, setBusy] = useState(false)
  const [failed, setFailed] = useState(false)

  async function onClick() {
    setBusy(true)
    setFailed(false)
    trackEvent('report_downloaded', { url, type: kind }, url)
    try {
      await downloadReport(kind, url, chargeId)
    } catch {
      setFailed(true)
    } finally {
      setBusy(false)
    }
  }

  const bg = failed ? 'bg-klarim-fail' : 'bg-klarim-alert'
  return (
    <div>
      <button
        onClick={onClick}
        disabled={busy}
        className={`flex w-full items-center justify-center gap-2 rounded-lg ${bg} px-6 py-4 font-bold text-klarim-bg transition hover:opacity-90 disabled:opacity-60`}
      >
        {busy ? (
          <>
            <span className="klarim-spinner h-5 w-5" /> Gerando PDF…
          </>
        ) : failed ? (
          'Erro — tentar novamente'
        ) : (
          label
        )}
      </button>
      <p className="mt-1 text-center text-xs text-klarim-muted">{hint}</p>
    </div>
  )
}

function EmailStatusBanner({ chargeId }) {
  const [info, setInfo] = useState(null) // { email_status, buyer_email }
  const stop = useRef(false)

  useEffect(() => {
    if (!chargeId) return
    stop.current = false
    async function poll() {
      try {
        const s = await getPaymentStatus(chargeId)
        setInfo({ email_status: s.email_status, buyer_email: s.buyer_email })
        if (s.email_status === 'sent' || s.email_status === 'failed' || !s.buyer_email) {
          stop.current = true
        }
      } catch {
        /* ignora */
      }
    }
    poll()
    const t = setInterval(() => {
      if (stop.current) return
      poll()
    }, 3000)
    return () => clearInterval(t)
  }, [chargeId])

  if (!info || !info.buyer_email) return null
  const email = info.buyer_email
  const st = info.email_status

  if (st === 'sent') {
    return (
      <div className="mx-auto mb-6 max-w-md rounded-lg border border-klarim-ok bg-klarim-surface px-4 py-3 text-sm text-klarim-ok">
        ✅ Relatório enviado para <strong>{email}</strong>. Verifique sua caixa de entrada.
      </div>
    )
  }
  if (st === 'failed') {
    return (
      <div className="mx-auto mb-6 max-w-md rounded-lg border border-klarim-warn bg-klarim-surface px-4 py-3 text-sm text-klarim-warn">
        ⚠️ Não foi possível enviar por e-mail. Use os botões abaixo para baixar.
      </div>
    )
  }
  // pending | sending
  return (
    <div className="mx-auto mb-6 flex max-w-md items-center justify-center gap-2 rounded-lg border border-klarim-border bg-klarim-surface px-4 py-3 text-sm text-klarim-muted">
      <span className="klarim-spinner h-4 w-4" /> 📧 Enviando relatório para <strong className="text-klarim-text">{email}</strong>…
    </div>
  )
}

export default function Report() {
  const [params] = useSearchParams()
  const url = params.get('url') || ''
  const chargeId = params.get('charge_id') || ''
  const { summary, loading, error } = useSummary(url)

  if (loading) {
    return (
      <Layout withFooter={false}>
        <div className="flex flex-col items-center pt-16 text-center">
          <div className="klarim-spinner h-14 w-14" />
          <p className="mt-6 text-klarim-muted">Carregando…</p>
        </div>
      </Layout>
    )
  }

  if (error || !summary) {
    return (
      <Layout>
        <div className="mx-auto max-w-md pt-10 text-center">
          <h1 className="text-2xl font-bold text-klarim-fail">Relatório indisponível</h1>
          <Link to="/" className="mt-6 inline-block rounded-lg bg-klarim-alert px-6 py-3 font-bold text-klarim-bg">
            Escanear um site
          </Link>
        </div>
      </Layout>
    )
  }

  return (
    <Layout>
      <div className="text-center">
        <h1 className="text-2xl font-bold">Seu relatório de segurança</h1>
        <p className="mt-1 break-all font-mono text-sm text-klarim-muted">{summary.url || url}</p>

        <div className="mt-6 flex scale-90 justify-center">
          <Semaphore score={summary.score} semaphore={summary.semaphore} />
        </div>

        <p className="mx-auto mt-6 max-w-md text-klarim-muted">
          Encaminhe o relatório ao responsável pelo seu site (agência, desenvolvedor
          ou equipe de TI).
        </p>

        <div className="mt-8">
          {chargeId && <EmailStatusBanner chargeId={chargeId} />}
        </div>

        <div className="mx-auto flex max-w-md flex-col gap-5">
          <DownloadButton
            kind="executive"
            url={url}
            chargeId={chargeId}
            label="Baixar Relatório Executivo (PDF)"
            hint="Para o dono do negócio — semáforo e linguagem acessível."
          />
          <DownloadButton
            kind="technical"
            url={url}
            chargeId={chargeId}
            label="Baixar Relatório Técnico (PDF)"
            hint="Para o dev/agência — detalhes e correções."
          />
        </div>

        <p className="mx-auto mt-6 max-w-md text-xs text-klarim-muted">
          A geração de cada PDF executa uma nova varredura e leva ~30 segundos.
        </p>

        {/* Re-verificação gratuita incluída (retorno médico — KL-27) */}
        <div className="mx-auto mt-8 max-w-md rounded-lg border border-klarim-ok bg-klarim-surface p-5 text-left">
          <p className="font-bold text-klarim-ok">Você tem 1 re-verificação gratuita incluída.</p>
          <p className="mt-1 text-sm text-klarim-muted">
            Depois de corrigir as falhas apontadas no relatório, volte à página inicial e
            escaneie a mesma URL com este e-mail: rodaremos o scan completo de novo, sem custo,
            e mostraremos a evolução do seu score.
          </p>
        </div>

        {/* Referral */}
        <div className="mx-auto mt-10 max-w-md rounded-lg border border-dashed border-klarim-alert bg-klarim-surface p-5">
          <p className="font-bold">Precisa de ajuda para corrigir?</p>
          <Link to="/parceiros" className="mt-1 inline-block font-bold text-klarim-alert">
            Conheça nossos parceiros
          </Link>
        </div>

        <div className="mt-8">
          <Link to="/" className="text-sm text-klarim-muted hover:text-klarim-text">
            ← Escanear outro site
          </Link>
        </div>
      </div>
    </Layout>
  )
}
