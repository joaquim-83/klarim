import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import Layout from '../components/Layout'
import { monitoringSites } from '../lib/api'

function fmtDate(iso) {
  if (!iso) return ''
  try {
    return new Date(iso).toLocaleDateString('pt-BR')
  } catch {
    return ''
  }
}

function SiteCard({ site }) {
  const [imgOk, setImgOk] = useState(true)
  const href = site.url || `https://${site.domain}`
  return (
    <div className="flex flex-col items-center rounded-xl border border-klarim-border bg-klarim-surface p-5 text-center transition hover:border-klarim-ok">
      <div className="relative">
        <div className="flex h-16 w-16 items-center justify-center overflow-hidden rounded-lg bg-klarim-bg">
          {imgOk && site.logo_url ? (
            <img
              src={site.logo_url}
              alt={site.display_name}
              className="h-10 w-10 object-contain"
              onError={() => setImgOk(false)}
            />
          ) : (
            <span className="text-2xl">🔒</span>
          )}
        </div>
        <span className="absolute -right-1 -top-1 text-lg">🔒</span>
      </div>
      <h3 className="mt-3 font-bold text-klarim-text">{site.display_name}</h3>
      <a
        href={href}
        target="_blank"
        rel="noopener noreferrer nofollow"
        className="mt-0.5 break-all font-mono text-xs text-klarim-alert hover:underline"
      >
        {site.domain}
      </a>
      <div className="mt-3 rounded-full border border-klarim-ok px-3 py-1 text-sm font-bold text-klarim-ok">
        Score {site.score}/100 🟢
      </div>
      {site.last_check_at && (
        <p className="mt-2 text-xs text-klarim-muted">Verificado em {fmtDate(site.last_check_at)}</p>
      )}
    </div>
  )
}

export default function Monitorados() {
  const [sites, setSites] = useState(null)

  useEffect(() => {
    monitoringSites().then((d) => setSites(d.sites || [])).catch(() => setSites([]))
  }, [])

  return (
    <Layout>
      <section className="text-center">
        <h1 className="text-3xl font-extrabold sm:text-4xl">Sites Monitorados pelo Klarim</h1>
        <p className="mx-auto mt-3 max-w-xl text-klarim-muted">
          Estes sites passaram em todas as 29 verificações de segurança e são
          monitorados continuamente pelo Klarim.
        </p>

        {sites === null ? (
          <div className="flex justify-center pt-12">
            <div className="klarim-spinner h-12 w-12" />
          </div>
        ) : sites.length === 0 ? (
          <p className="mt-12 text-klarim-muted">
            Ainda não há sites monitorados publicamente. Seja o primeiro — faça o scan
            completo e atinja score 100.
          </p>
        ) : (
          <div className="mt-10 grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
            {sites.map((s) => (
              <SiteCard key={s.domain} site={s} />
            ))}
          </div>
        )}

        {/* CTA */}
        <div className="mx-auto mt-14 max-w-md rounded-xl border border-dashed border-klarim-ok bg-klarim-surface p-6">
          <p className="font-bold">Quer seu site aqui?</p>
          <p className="mt-1 text-sm text-klarim-muted">
            Faça o scan completo e atinja score 100 para ganhar o selo e o
            monitoramento gratuito.
          </p>
          <Link
            to="/"
            className="mt-4 inline-block rounded-lg bg-klarim-alert px-6 py-3 font-bold text-klarim-bg hover:opacity-90"
          >
            Escanear meu site
          </Link>
        </div>
      </section>
    </Layout>
  )
}
