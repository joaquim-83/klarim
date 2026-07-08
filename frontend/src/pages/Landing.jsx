import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import Layout from '../components/Layout'
import { Beacon } from '../components/Logo'
import { normalizeUrl, isValidUrl } from '../lib/url'
import { trackEvent } from '../lib/tracker'

function Step({ icon, title, children }) {
  return (
    <div className="rounded-xl border border-klarim-border bg-klarim-surface p-5 text-center">
      <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-full bg-klarim-bg text-klarim-alert">
        {icon}
      </div>
      <h3 className="font-bold">{title}</h3>
      <p className="mt-1 text-sm text-klarim-muted">{children}</p>
    </div>
  )
}

export default function Landing() {
  const [url, setUrl] = useState('')
  const [error, setError] = useState('')
  const navigate = useNavigate()

  function onSubmit(e) {
    e.preventDefault()
    if (!isValidUrl(url)) {
      setError('Digite uma URL válida, ex.: exemplo.com.br')
      return
    }
    setError('')
    const target = normalizeUrl(url)
    trackEvent('scan_started', { url: target }, target)
    navigate(`/scan?url=${encodeURIComponent(target)}`)
  }

  return (
    <Layout>
      {/* Hero */}
      <section className="pt-6 text-center sm:pt-10">
        <div className="mb-4 flex justify-center">
          <Beacon size={64} />
        </div>
        <h1 className="text-3xl font-extrabold sm:text-4xl">
          O alarme que toca antes do ataque.
        </h1>
        <p className="mx-auto mt-3 max-w-xl text-klarim-muted sm:text-lg">
          Descubra as vulnerabilidades do seu site em segundos.{' '}
          <span className="font-semibold text-klarim-ok">Gratuito.</span>
        </p>

        {/* Input de scan */}
        <form onSubmit={onSubmit} className="mx-auto mt-8 max-w-xl">
          <div className="flex flex-col gap-3 sm:flex-row">
            <input
              type="text"
              inputMode="url"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="Digite a URL do seu site"
              className="w-full flex-1 rounded-lg border border-klarim-border bg-klarim-surface px-4 py-3 text-klarim-text placeholder:text-klarim-muted focus:border-klarim-alert focus:outline-none"
              aria-label="URL do site"
            />
            <button
              type="submit"
              className="w-full rounded-lg bg-klarim-alert px-6 py-3 font-bold text-klarim-bg transition hover:opacity-90 sm:w-auto"
            >
              Escanear
            </button>
          </div>
          {error && <p className="mt-2 text-sm text-klarim-fail">{error}</p>}
        </form>
      </section>

      {/* Como funciona */}
      <section className="mt-16">
        <h2 className="mb-5 text-center text-xl font-bold">Como funciona</h2>
        <div className="grid gap-4 sm:grid-cols-3">
          <Step
            title="1. Digite a URL"
            icon={
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M10 13a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-1 1" />
                <path d="M14 11a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l1-1" />
              </svg>
            }
          >
            Informe o endereço do seu site. Sem cadastro.
          </Step>
          <Step
            title="2. Receba o diagnóstico"
            icon={
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <rect x="8" y="2" width="8" height="20" rx="4" />
                <circle cx="12" cy="7" r="1.6" fill="currentColor" stroke="none" />
                <circle cx="12" cy="12" r="1.6" fill="currentColor" stroke="none" />
                <circle cx="12" cy="17" r="1.6" fill="currentColor" stroke="none" />
              </svg>
            }
          >
            Um semáforo claro com o nível de risco do seu site.
          </Step>
          <Step
            title="3. Corrija as falhas"
            icon={
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M12 2l8 3v6c0 5-3.5 8-8 11-4.5-3-8-6-8-11V5l8-3z" />
                <path d="M9 12l2 2 4-4" />
              </svg>
            }
          >
            Relatório com recomendações prontas para o seu time.
          </Step>
        </div>
      </section>

      {/* Para quem é */}
      <section className="mt-14">
        <h2 className="mb-5 text-center text-xl font-bold">Para quem é</h2>
        <ul className="space-y-3">
          {[
            'Donos de negócio que querem saber se seu site está seguro.',
            'Desenvolvedores que querem validar a segurança antes de entregar.',
            'Agências que precisam auditar a carteira de clientes.',
          ].map((t) => (
            <li
              key={t}
              className="flex items-start gap-3 rounded-lg border border-klarim-border bg-klarim-surface px-4 py-3"
            >
              <span className="mt-1 h-2 w-2 shrink-0 rounded-full bg-klarim-alert" />
              <span>{t}</span>
            </li>
          ))}
        </ul>
      </section>
    </Layout>
  )
}
