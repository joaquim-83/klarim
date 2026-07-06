import { useState } from 'react'
import { useSearchParams, Link } from 'react-router-dom'
import Layout from '../components/Layout'
import Semaphore from '../components/Semaphore'
import { useSummary } from '../lib/useSummary'
import { downloadReport } from '../lib/api'

function DownloadButton({ kind, url, label, hint }) {
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  async function onClick() {
    setBusy(true)
    setErr('')
    try {
      await downloadReport(kind, url)
    } catch (e) {
      setErr(e.message || 'Falha no download.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div>
      <button
        onClick={onClick}
        disabled={busy}
        className="flex w-full items-center justify-center gap-2 rounded-lg bg-klarim-alert px-6 py-4 font-bold text-klarim-bg transition hover:opacity-90 disabled:opacity-60"
      >
        {busy ? (
          <>
            <span className="klarim-spinner h-5 w-5" /> Gerando PDF…
          </>
        ) : (
          label
        )}
      </button>
      <p className="mt-1 text-center text-xs text-klarim-muted">{hint}</p>
      {err && <p className="mt-1 text-center text-sm text-klarim-fail">{err}</p>}
    </div>
  )
}

export default function Report() {
  const [params] = useSearchParams()
  const url = params.get('url') || ''
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

        <div className="mx-auto mt-8 flex max-w-md flex-col gap-5">
          <DownloadButton
            kind="executive"
            url={url}
            label="Baixar Relatório Executivo (PDF)"
            hint="Para o dono do negócio — semáforo e linguagem acessível."
          />
          <DownloadButton
            kind="technical"
            url={url}
            label="Baixar Relatório Técnico (PDF)"
            hint="Para o dev/agência — detalhes e correções."
          />
        </div>

        <p className="mx-auto mt-6 max-w-md text-xs text-klarim-muted">
          A geração de cada PDF executa uma nova varredura e leva ~30 segundos.
        </p>

        {/* Referral */}
        <div className="mx-auto mt-10 max-w-md rounded-lg border border-dashed border-klarim-alert bg-klarim-surface p-5">
          <p className="font-bold">Precisa de ajuda para corrigir?</p>
          <a href="#" className="mt-1 inline-block font-bold text-klarim-alert">
            Conheça nossos parceiros
          </a>
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
