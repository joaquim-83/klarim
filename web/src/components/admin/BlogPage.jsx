// KL-133 (complemento) — gestão do blog no painel: lista + filtro por status + ações
// (publicar/despublicar/arquivar/restaurar/editar) + modal de edição (markdown em textarea).
// O backend (CRUD /admin/blog/posts) e as MCP tools já existem; aqui é só a UI do operador.
import { useState } from 'react'
import { admin } from '../../lib/admin/adminApi'
import { useAsync } from '../../lib/admin/useAsync'
import { Card, Loading, ErrorBox, Button, Badge, formatDate } from './ui'
import AdminShell from './AdminShell'

const SITE = 'https://klarim.net'
const CATEGORIES = ['seguranca', 'lgpd', 'dados', 'setor', 'tutorial']
const STATUS_META = {
  draft: { label: 'Rascunho', color: '#F0C000', dot: '🟡' },
  published: { label: 'Publicado', color: '#00D26A', dot: '🟢' },
  archived: { label: 'Arquivado', color: '#8B949E', dot: '⚫' },
}
const FILTERS = [
  { key: '', label: 'Todos' },
  { key: 'draft', label: 'Rascunhos' },
  { key: 'published', label: 'Publicados' },
  { key: 'archived', label: 'Arquivados' },
]

export default function BlogPage() {
  const [filter, setFilter] = useState('')
  const [editing, setEditing] = useState(null)   // post | {} (novo) | null
  const [toast, setToast] = useState('')
  const { data, loading, error, reload } = useAsync(
    () => admin.blogList({ status: filter || undefined, per_page: 100 }), [filter])
  const posts = data?.posts || []

  function flash(msg) { setToast(msg); setTimeout(() => setToast(''), 3000) }

  async function act(fn, msg) {
    try { await fn(); flash(msg); reload() } catch (e) { flash(e.message) }
  }

  return (
    <AdminShell active="blog">
      <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-bold text-klarim-text">Blog</h1>
        <Button variant="primary" onClick={() => setEditing({})}>+ Novo post</Button>
      </div>

      {toast && (
        <div className="mb-4 rounded-lg border border-klarim-border bg-klarim-surface px-4 py-2 text-sm text-klarim-text">{toast}</div>
      )}

      <div className="mb-4 flex flex-wrap gap-2">
        {FILTERS.map((f) => (
          <button key={f.key} type="button" onClick={() => setFilter(f.key)}
            className={`rounded-lg px-3 py-1.5 text-sm font-medium transition ${
              filter === f.key ? 'bg-klarim-alert/15 text-klarim-alert'
                : 'text-klarim-muted hover:bg-klarim-border/40 hover:text-klarim-text'}`}>
            {f.label}
          </button>
        ))}
      </div>

      <Card>
        {loading && <Loading label="Carregando posts…" />}
        {error && <ErrorBox message={error} />}
        {!loading && !error && posts.length === 0 && (
          <p className="py-6 text-center text-sm text-klarim-muted">
            Nenhum post {filter ? `com status “${STATUS_META[filter]?.label}”` : ''}. Use o <strong>+ Novo post</strong> ou as tools MCP.
          </p>
        )}
        {!loading && posts.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-klarim-border text-left text-xs uppercase text-klarim-muted">
                  <th className="py-2 pr-3">Status</th>
                  <th className="py-2 pr-3">Título</th>
                  <th className="py-2 pr-3">Categoria</th>
                  <th className="py-2 pr-3">Data</th>
                  <th className="py-2 pr-3">Leitura</th>
                  <th className="py-2 pr-3 text-right">Ações</th>
                </tr>
              </thead>
              <tbody>
                {posts.map((p) => {
                  const st = STATUS_META[p.status] || STATUS_META.draft
                  const date = p.published_at || p.created_at
                  return (
                    <tr key={p.id} className="border-b border-klarim-border/50">
                      <td className="py-2.5 pr-3"><span title={st.label}>{st.dot}</span> <span style={{ color: st.color }}>{st.label}</span></td>
                      <td className="py-2.5 pr-3 font-medium text-klarim-text">{p.title}</td>
                      <td className="py-2.5 pr-3"><Badge>{p.category}</Badge></td>
                      <td className="py-2.5 pr-3 text-klarim-muted">{formatDate(date)}</td>
                      <td className="py-2.5 pr-3 text-klarim-muted">{p.reading_time_min} min</td>
                      <td className="py-2.5 pr-3">
                        <div className="flex justify-end gap-1">
                          {p.status === 'published' && (
                            <a href={`${SITE}/blog/${p.slug}`} target="_blank" rel="noopener noreferrer"
                              title="Ver público" className="rounded px-1.5 py-1 hover:bg-klarim-border/40">👁️</a>
                          )}
                          {p.status !== 'archived' && (
                            <button title="Editar" onClick={() => setEditing(p)}
                              className="rounded px-1.5 py-1 hover:bg-klarim-border/40">✏️</button>
                          )}
                          {p.status === 'draft' && (
                            <button title="Publicar" onClick={() => act(() => admin.blogUpdate(p.id, { status: 'published' }), `“${p.title}” publicado ✓`)}
                              className="rounded px-1.5 py-1 hover:bg-klarim-border/40">📤</button>
                          )}
                          {p.status === 'published' && (
                            <button title="Despublicar" onClick={() => act(() => admin.blogUpdate(p.id, { status: 'draft' }), `“${p.title}” despublicado ✓`)}
                              className="rounded px-1.5 py-1 hover:bg-klarim-border/40">📥</button>
                          )}
                          {p.status !== 'archived' && (
                            <button title="Arquivar" onClick={() => act(() => admin.blogDelete(p.id), `“${p.title}” arquivado ✓`)}
                              className="rounded px-1.5 py-1 hover:bg-klarim-border/40">🗑️</button>
                          )}
                          {p.status === 'archived' && (
                            <button title="Restaurar" onClick={() => act(() => admin.blogUpdate(p.id, { status: 'draft' }), `“${p.title}” restaurado ✓`)}
                              className="rounded px-1.5 py-1 hover:bg-klarim-border/40">↩️</button>
                          )}
                        </div>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {editing && (
        <EditPostModal post={editing} onClose={() => setEditing(null)}
          onSaved={(msg) => { setEditing(null); flash(msg); reload() }} />
      )}
    </AdminShell>
  )
}

function EditPostModal({ post, onClose, onSaved }) {
  const isNew = !post.id
  const [form, setForm] = useState({
    title: post.title || '', subtitle: post.subtitle || '', category: post.category || 'seguranca',
    tags: (post.tags || []).join(', '), meta_description: post.meta_description || '',
    og_image_url: post.og_image_url || '', content: post.content || '',
  })
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }))

  async function save() {
    if (!form.title.trim() || !form.content.trim()) { setErr('Título e conteúdo são obrigatórios.'); return }
    setBusy(true); setErr('')
    try {
      const payload = {
        title: form.title.trim(), subtitle: form.subtitle.trim() || null,
        category: form.category, meta_description: form.meta_description.trim() || null,
        og_image_url: form.og_image_url.trim() || null, content: form.content,
        tags: form.tags.split(',').map((t) => t.trim()).filter(Boolean),
      }
      if (isNew) await admin.blogCreate(payload)
      else await admin.blogUpdate(post.id, payload)
      onSaved(isNew ? 'Post criado (rascunho) ✓' : 'Post atualizado ✓')
    } catch (e) { setErr(e.message) } finally { setBusy(false) }
  }

  const inputCls = 'w-full rounded-lg border border-klarim-border bg-klarim-bg px-3 py-1.5 text-sm text-klarim-text outline-none focus:border-klarim-alert'

  return (
    <div className="fixed inset-0 z-30 flex items-center justify-center bg-black/60 px-4" onClick={onClose}>
      <div className="max-h-[90vh] w-full max-w-3xl overflow-y-auto rounded-xl border border-klarim-border bg-klarim-surface p-6" onClick={(e) => e.stopPropagation()}>
        <h3 className="mb-4 text-lg font-bold text-klarim-text">{isNew ? 'Novo post' : `Editar post`}</h3>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div className="sm:col-span-2">
            <label className="mb-1 block text-xs uppercase text-klarim-muted">Título *</label>
            <input className={inputCls} value={form.title} onChange={(e) => set('title', e.target.value)} />
          </div>
          <div className="sm:col-span-2">
            <label className="mb-1 block text-xs uppercase text-klarim-muted">Subtítulo</label>
            <input className={inputCls} value={form.subtitle} onChange={(e) => set('subtitle', e.target.value)} />
          </div>
          <div>
            <label className="mb-1 block text-xs uppercase text-klarim-muted">Categoria</label>
            <select className={inputCls} value={form.category} onChange={(e) => set('category', e.target.value)}>
              {CATEGORIES.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          </div>
          <div>
            <label className="mb-1 block text-xs uppercase text-klarim-muted">Tags (vírgula)</label>
            <input className={inputCls} value={form.tags} onChange={(e) => set('tags', e.target.value)} placeholder="https, lgpd" />
          </div>
          <div className="sm:col-span-2">
            <label className="mb-1 block text-xs uppercase text-klarim-muted">Meta description (SEO)</label>
            <input className={inputCls} value={form.meta_description} onChange={(e) => set('meta_description', e.target.value)} />
          </div>
          <div className="sm:col-span-2">
            <label className="mb-1 block text-xs uppercase text-klarim-muted">OG Image URL</label>
            <input className={inputCls} value={form.og_image_url} onChange={(e) => set('og_image_url', e.target.value)} />
          </div>
          <div className="sm:col-span-2">
            <label className="mb-1 block text-xs uppercase text-klarim-muted">Conteúdo (markdown) *</label>
            <textarea className={`${inputCls} min-h-[300px] font-mono`} value={form.content}
              onChange={(e) => set('content', e.target.value)}
              placeholder={'# Título\\n\\nParágrafo com **negrito** e [links](url).'} />
          </div>
        </div>
        {err && <div className="mt-3"><ErrorBox message={err} /></div>}
        <div className="mt-5 flex justify-end gap-2">
          <Button onClick={onClose}>Cancelar</Button>
          <Button variant="primary" disabled={busy} onClick={save}>{busy ? 'Salvando…' : 'Salvar'}</Button>
        </div>
      </div>
    </div>
  )
}
