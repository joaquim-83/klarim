import { useState } from 'react'
import { admin } from '../../lib/adminApi'
import { useAsync } from '../../lib/useAsync'
import { Card, Loading, ErrorBox, Button, Pagination, formatDate } from '../../components/admin/ui'

const PAGE_SIZE = 25
const BOXES = [
  { key: 'all', label: 'Todas' },
  { key: 'unread', label: 'Não-lidas' },
  { key: 'starred', label: 'Com estrela' },
  { key: 'archived', label: 'Arquivadas' },
]

export default function Inbox() {
  const [box, setBox] = useState('all')
  const [page, setPage] = useState(0)
  const [open, setOpen] = useState(null)   // mensagem aberta (corpo completo)
  const [msg, setMsg] = useState('')
  const [tick, setTick] = useState(0)      // força reload após uma ação

  const { data, loading, error } = useAsync(
    () => admin.inbox({ box, limit: PAGE_SIZE, offset: page * PAGE_SIZE }),
    [box, page, tick],
  )
  const rows = data?.messages || []
  const reload = () => setTick((t) => t + 1)

  async function openMsg(id) {
    setMsg('')
    try {
      const full = await admin.inboxMessage(id)  // o backend marca como lida ao abrir
      setOpen(full)
      reload()                                    // atualiza o ● → ○ na lista + badge
    } catch (e) { setMsg(e.message) }
  }

  async function doAction(id, fn) {
    setMsg('')
    try { await fn(id); reload() } catch (e) { setMsg(e.message) }
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-xl font-bold">
          Inbox <span className="text-sm font-normal text-klarim-muted">scan@klarim.net</span>
        </h1>
      </div>

      <div className="flex flex-wrap gap-2">
        {BOXES.map((b) => (
          <button
            key={b.key}
            onClick={() => { setBox(b.key); setPage(0) }}
            className={`rounded-lg border px-3 py-1.5 text-sm ${
              box === b.key
                ? 'border-klarim-alert bg-klarim-alert/15 text-klarim-text'
                : 'border-klarim-border bg-klarim-surface text-klarim-muted hover:text-klarim-text'
            }`}
          >
            {b.label}
          </button>
        ))}
      </div>

      {msg && <div className="text-sm text-klarim-muted">{msg}</div>}

      <Card>
        {loading ? <Loading /> : error ? <ErrorBox message={error} /> : (
          <div className="divide-y divide-klarim-border">
            {rows.map((m) => (
              <div key={m.id} className="flex items-start gap-3 py-3">
                <button
                  onClick={() => doAction(m.id, admin.inboxStar)}
                  title="Estrela"
                  className="mt-0.5 text-lg"
                  style={{ color: m.is_starred ? '#F0C000' : '#8B949E' }}
                >
                  {m.is_starred ? '★' : '☆'}
                </button>
                <button onClick={() => openMsg(m.id)} className="min-w-0 flex-1 text-left">
                  <div className="flex items-center gap-2">
                    <span className={`inline-block h-2 w-2 shrink-0 rounded-full ${m.is_read ? 'border border-klarim-muted' : 'bg-klarim-alert'}`} />
                    <span className={`truncate ${m.is_read ? 'text-klarim-muted' : 'font-bold text-klarim-text'}`}>
                      {m.from_name || m.from_address}
                    </span>
                    <span className="ml-auto shrink-0 text-xs text-klarim-muted">
                      {formatDate(m.received_at || m.created_at)}
                    </span>
                  </div>
                  <div className={`truncate text-sm ${m.is_read ? 'text-klarim-muted' : 'text-klarim-text'}`}>
                    {m.subject || '(sem assunto)'}
                  </div>
                  <div className="truncate text-xs text-klarim-muted">{m.body_preview}</div>
                </button>
                <div className="flex shrink-0 flex-wrap gap-1">
                  <Button onClick={() => doAction(m.id, (id) => admin.inboxRead(id, !m.is_read))}>
                    {m.is_read ? 'Não-lida' : 'Lida'}
                  </Button>
                  <Button onClick={() => doAction(m.id, (id) => admin.inboxArchive(id, !m.is_archived))}>
                    {m.is_archived ? 'Desarquivar' : 'Arquivar'}
                  </Button>
                </div>
              </div>
            ))}
            {rows.length === 0 && <div className="py-8 text-center text-klarim-muted">Nenhuma mensagem.</div>}
          </div>
        )}
        <Pagination page={page} setPage={setPage} hasNext={rows.length === PAGE_SIZE} />
      </Card>

      {open && <MessageModal message={open} onClose={() => setOpen(null)} />}
    </div>
  )
}

function MessageModal({ message, onClose }) {
  const m = message
  const replyHref = `mailto:${m.from_address}?subject=${encodeURIComponent('Re: ' + (m.subject || ''))}`
  return (
    <div className="fixed inset-0 z-30 flex items-center justify-center bg-black/60 px-4" onClick={onClose}>
      <div className="flex max-h-[90vh] w-full max-w-2xl flex-col rounded-xl border border-klarim-border bg-klarim-surface p-6" onClick={(e) => e.stopPropagation()}>
        <div className="mb-3 flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h3 className="truncate text-lg font-bold">{m.subject || '(sem assunto)'}</h3>
            <div className="text-sm text-klarim-muted">
              De: <span className="text-klarim-text">
                {m.from_name ? `${m.from_name} <${m.from_address}>` : m.from_address}
              </span>
            </div>
            <div className="text-xs text-klarim-muted">
              Para: {m.to_address} · {formatDate(m.received_at || m.created_at)}
            </div>
          </div>
          <button onClick={onClose} className="text-klarim-muted hover:text-klarim-text">✕</button>
        </div>

        {/* O HTML vem de um remetente externo (não confiável) → iframe SANDBOX vazio:
            sem scripts, origem opaca. Nunca dangerouslySetInnerHTML (stored-XSS). */}
        {m.body_html ? (
          <iframe
            title="Conteúdo do e-mail"
            sandbox=""
            srcDoc={m.body_html}
            className="min-h-[45vh] w-full flex-1 rounded-lg border border-klarim-border bg-white"
          />
        ) : (
          <pre className="flex-1 overflow-auto whitespace-pre-wrap rounded-lg border border-klarim-border bg-klarim-bg p-4 font-sans text-sm text-klarim-text">
            {m.body_preview || '(sem conteúdo)'}
          </pre>
        )}

        <div className="mt-4 flex items-center justify-between">
          <a href={replyHref} className="text-sm text-klarim-alert hover:underline">Responder por e-mail ↗</a>
          <a href="https://mail.hostinger.com/" target="_blank" rel="noreferrer" className="text-xs text-klarim-muted hover:underline">
            Abrir no webmail Hostinger ↗
          </a>
        </div>
      </div>
    </div>
  )
}
