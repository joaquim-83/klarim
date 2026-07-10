import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import Layout from '../components/Layout'
import { Beacon } from '../components/Logo'
import { normalizeUrl, isValidUrl } from '../lib/url'
import { trackEvent } from '../lib/tracker'
import { requestCode, verifyCode } from '../lib/api'

const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/

function urlHost(u) {
  try { return new URL(u).hostname.replace(/^www\./, '') } catch { return u }
}

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

const inputCls =
  'w-full rounded-lg border border-klarim-border bg-klarim-surface px-4 py-3 text-klarim-text placeholder:text-klarim-muted focus:border-klarim-alert focus:outline-none'
const btnCls =
  'w-full rounded-lg bg-klarim-alert px-6 py-3 font-bold text-klarim-bg transition hover:opacity-90 disabled:opacity-50'

export default function Landing() {
  const [step, setStep] = useState('form') // form | code | limit
  const [url, setUrl] = useState('')
  const [email, setEmail] = useState('')
  const [code, setCode] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [resendIn, setResendIn] = useState(0)
  const navigate = useNavigate()

  // Contador do "reenviar código".
  useEffect(() => {
    if (resendIn <= 0) return
    const t = setInterval(() => setResendIn((s) => s - 1), 1000)
    return () => clearInterval(t)
  }, [resendIn])

  async function askCode(target, resend = false) {
    setBusy(true)
    setError('')
    try {
      const r = await requestCode(email.trim(), target)
      if (r.status === 'already_scanned') {
        navigate(`/result?url=${encodeURIComponent(target)}`)
      } else if (r.status === 'limit_reached') {
        trackEvent('scan_limit_reached', { url: target }, target)
        setStep('limit')
      } else if (r.status === 'code_sent') {
        if (!resend) trackEvent('code_requested', { url: target }, target)
        setStep('code')
        setResendIn(45)
      } else {
        setError(r.message || 'Não foi possível enviar o código.')
      }
    } catch (e) {
      setError(e.message || 'Erro ao enviar o código.')
    } finally {
      setBusy(false)
    }
  }

  function onSubmit(e) {
    e.preventDefault()
    if (!isValidUrl(url)) return setError('Digite uma URL válida, ex.: exemplo.com.br')
    if (!EMAIL_RE.test(email.trim())) return setError('Digite um e-mail válido.')
    setError('')
    setUrl(normalizeUrl(url))
    askCode(normalizeUrl(url))
  }

  async function onVerify(e) {
    e.preventDefault()
    if (!/^\d{6}$/.test(code.trim())) return setError('Digite o código de 6 dígitos.')
    setBusy(true)
    setError('')
    try {
      const r = await verifyCode(email.trim(), code.trim(), url)
      if (r.status === 'verified') {
        trackEvent('code_verified', { url }, url)
        navigate(`/scan?url=${encodeURIComponent(url)}`)
      } else {
        trackEvent('code_failed', { url }, url)
        setError(r.message || 'Código inválido ou expirado.')
      }
    } catch (e2) {
      setError(e2.message || 'Erro ao verificar o código.')
    } finally {
      setBusy(false)
    }
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

        <div className="mx-auto mt-8 max-w-xl">
          {step === 'form' && (
            <form onSubmit={onSubmit} className="space-y-3 text-left">
              <input type="text" inputMode="url" value={url} aria-label="URL do site"
                onChange={(e) => setUrl(e.target.value)}
                placeholder="https://www.seusite.com.br" className={inputCls} />
              <input type="email" inputMode="email" value={email} aria-label="Seu e-mail"
                onChange={(e) => setEmail(e.target.value)}
                placeholder="seu@email.com.br" className={inputCls} />
              <button type="submit" disabled={busy} className={btnCls}>
                {busy ? 'Enviando…' : 'Escanear gratuitamente'}
              </button>
              <p className="text-center text-xs text-klarim-muted">
                Seu e-mail é usado apenas para enviar o código de verificação.
              </p>
            </form>
          )}

          {step === 'code' && (
            <form onSubmit={onVerify} className="space-y-3 text-left">
              <p className="text-center text-sm text-klarim-muted">
                Código enviado para <span className="text-klarim-text">{email}</span>
              </p>
              <input type="text" inputMode="numeric" maxLength={6} value={code} autoFocus
                aria-label="Código de verificação"
                onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))}
                placeholder="______"
                className={`${inputCls} text-center text-2xl tracking-[0.5em]`} />
              <button type="submit" disabled={busy} className={btnCls}>
                {busy ? 'Verificando…' : 'Verificar e escanear'}
              </button>
              <div className="text-center text-xs text-klarim-muted">
                {resendIn > 0 ? (
                  <span>Não recebeu? Reenviar código ({resendIn}s)</span>
                ) : (
                  <button type="button" onClick={() => askCode(url, true)}
                    className="text-klarim-alert hover:underline">
                    Não recebeu? Reenviar código
                  </button>
                )}
              </div>
            </form>
          )}

          {step === 'limit' && (
            <div className="rounded-xl border border-klarim-border bg-klarim-surface p-6 text-center">
              <h3 className="text-lg font-bold">Você já utilizou seu scan gratuito</h3>
              <p className="mt-2 text-sm text-klarim-muted">
                Para escanear <span className="text-klarim-text">{urlHost(url)}</span>,
                adquira o relatório de segurança completo.
              </p>
              <button
                onClick={() => { trackEvent('cta_clicked', { from: 'limit', url }, url); navigate(`/pay?url=${encodeURIComponent(url)}`) }}
                className="mt-4 inline-block rounded-lg bg-klarim-alert px-6 py-3 font-bold text-klarim-bg hover:opacity-90">
                Ver relatório completo
              </button>
            </div>
          )}

          {error && <p className="mt-3 text-center text-sm text-klarim-fail">{error}</p>}
        </div>
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
