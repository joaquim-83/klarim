import { useEffect, useRef, useState } from 'react'
import { useSearchParams, useNavigate, Link } from 'react-router-dom'
import Layout from '../components/Layout'
import { fetchSummary } from '../lib/api'
import { trackEvent } from '../lib/tracker'
import { SCAN_STEPS } from '../lib/constants'

export default function Scan() {
  const [params] = useSearchParams()
  const url = params.get('url') || ''
  const navigate = useNavigate()
  const [stepIdx, setStepIdx] = useState(0)
  const [error, setError] = useState('')
  const started = useRef(false)

  // Mensagens rotativas (feedback visual; a API não envia progresso real).
  useEffect(() => {
    const t = setInterval(() => {
      setStepIdx((i) => (i + 1 < SCAN_STEPS.length ? i + 1 : i))
    }, 3500)
    return () => clearInterval(t)
  }, [])

  // Dispara o scan uma única vez e navega para o resultado.
  useEffect(() => {
    if (!url) {
      navigate('/', { replace: true })
      return
    }
    if (started.current) return
    started.current = true
    fetchSummary(url)
      .then((summary) => {
        trackEvent('scan_completed', { url, score: summary.score, semaphore: summary.semaphore }, url)
        navigate(`/result?url=${encodeURIComponent(url)}`, {
          replace: true,
          state: { summary },
        })
      })
      .catch((e) => setError(e.message || 'Erro ao escanear.'))
  }, [url, navigate])

  if (error) {
    return (
      <Layout>
        <div className="mx-auto max-w-md pt-10 text-center">
          <h1 className="text-2xl font-bold text-klarim-fail">Não foi possível escanear</h1>
          <p className="mt-3 break-all text-klarim-muted">{url}</p>
          <p className="mt-2 text-sm text-klarim-muted">{error}</p>
          <Link
            to="/"
            className="mt-6 inline-block rounded-lg bg-klarim-alert px-6 py-3 font-bold text-klarim-bg"
          >
            Tentar outro site
          </Link>
        </div>
      </Layout>
    )
  }

  return (
    <Layout withFooter={false}>
      <div className="flex flex-col items-center pt-10 text-center sm:pt-16">
        <div className="klarim-spinner h-16 w-16" />
        <h1 className="mt-8 text-2xl font-bold">Escaneando seu site…</h1>
        <p className="mt-2 break-all font-mono text-sm text-klarim-muted">{url}</p>
        <p className="mt-6 h-6 text-klarim-alert transition">{SCAN_STEPS[stepIdx]}</p>
        <p className="mt-8 max-w-sm text-sm text-klarim-muted">
          A varredura leva cerca de 30 segundos. Ela é 100% passiva — nenhum dado do
          seu site é acessado.
        </p>
      </div>
    </Layout>
  )
}
